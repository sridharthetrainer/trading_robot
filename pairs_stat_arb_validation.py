#!/usr/bin/env python3
"""
pairs_stat_arb_validation.py — VALIDATION EXPERIMENT for a NIFTY↔BANKNIFTY
statistical-arbitrage (pairs mean-reversion) edge. NOT a live strategy.

Rationale: every directional strategy here measures no edge (speed/structure are
retail-impossible). Relative-value (pairs) is a *different* edge class — worth
MEASURING before believing. This does exactly that and claims nothing:

  1. Align daily NIFTY + BANKNIFTY closes.
  2. OLS hedge ratio β; spread = log(BANKNIFTY) - β·log(NIFTY).
  3. Cointegration test: ADF(0) t-stat on the spread (Engle-Granger residual
     test) + mean-reversion half-life + correlation.
  4. Backtest a z-score mean-reversion rule WITH costs, on an OUT-OF-SAMPLE
     holdout (dev fits β + z-params; holdout is untouched until the end).
  5. Honest verdict: PASS only if cointegrated AND OOS net-Sharpe clears a
     deflated bar AND >= min trades. Otherwise NO_EDGE / INSUFFICIENT_DATA.

Lighter than validation_harness's full DSR (no statsmodels here), but same
discipline: out-of-sample, after costs, min-trades, no overfit claims.

Usage:  python pairs_stat_arb_validation.py
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPORT_FILE = "pairs_validation_report.json"
ADF_CRIT_5PCT = -2.86          # ADF critical value (constant, ~5%)
COST_BPS_PER_LEG = 3.0         # round-trip ≈ 2 legs × entry+exit; conservative
Z_IN, Z_OUT = 2.0, 0.5
LOOKBACK = 20                  # rolling window for the z-score
MIN_OOS_TRADES = 20
DSR_SHARPE_BAR = 1.0           # OOS annualised net-Sharpe must beat this


# Candidate same-sector pairs (liquid NSE names likely to share a common driver
# → better cointegration odds than two indices). Scanned by default.
CANDIDATE_PAIRS = [
    ("HDFCBANK", "ICICIBANK"), ("ICICIBANK", "AXISBANK"), ("SBIN", "BANKBARODA"),
    ("AXISBANK", "KOTAKBANK"),
    ("TCS", "INFY"), ("INFY", "WIPRO"), ("HCLTECH", "TECHM"), ("TCS", "HCLTECH"),
    ("BPCL", "HPCL"), ("BPCL", "IOC"), ("ONGC", "OIL"),
    ("TATASTEEL", "JSWSTEEL"), ("HINDALCO", "JSWSTEEL"),
    ("ULTRACEMCO", "SHREECEM"), ("ULTRACEMCO", "GRASIM"),
    ("SUNPHARMA", "DRREDDY"), ("DRREDDY", "CIPLA"),
    ("MARUTI", "M&M"), ("TATAMOTORS", "M&M"),
    ("HDFCLIFE", "ICICIPRULI"),
]

_FETCHER = None
_PRICE_CACHE: Dict[str, Optional[pd.Series]] = {}


def _load_one(sym: str, days: int = 1500) -> Optional[pd.Series]:
    """date-indexed daily close Series for a symbol. NIFTY uses the long
    nifty_daily history; everything else via DataFetcher (cached per symbol)."""
    sym = sym.upper()
    if sym in _PRICE_CACHE:
        return _PRICE_CACHE[sym]
    s: Optional[pd.Series] = None
    if sym == "NIFTY":
        try:
            import sqlite3
            con = sqlite3.connect("file:participant_oi.db?mode=ro", uri=True)
            d = pd.read_sql("SELECT date, close FROM nifty_daily WHERE close>0 ORDER BY date", con)
            con.close()
            s = pd.Series(d["close"].astype(float).values,
                          index=d["date"].astype(str).str[:10], name=sym)
        except Exception as exc:
            logger.debug("load NIFTY: %s", exc)
    if s is None:
        try:
            global _FETCHER
            if _FETCHER is None:
                from data_fetcher import DataFetcher
                _FETCHER = DataFetcher()
            df = _FETCHER.get_market_data(sym, "1d", days=days)
            if df is not None and len(df) >= 60:
                df = df.copy(); df.columns = [c.lower() for c in df.columns]
                s = pd.Series(df["close"].astype(float).values,
                              index=pd.to_datetime(df.index).astype(str).str[:10], name=sym)
        except Exception as exc:
            logger.debug("load %s: %s", sym, exc)
    if s is not None:
        s = s[~s.index.duplicated(keep="last")]
    _PRICE_CACHE[sym] = s
    return s


def _load_aligned(sym_a: str = "NIFTY", sym_b: str = "BANKNIFTY") -> Optional[pd.DataFrame]:
    """date-indexed df with aligned daily closes for two symbols."""
    a, b = _load_one(sym_a), _load_one(sym_b)
    if a is None or b is None:
        return None
    df = pd.DataFrame({"a": a, "b": b}).dropna()
    df = df[(df["a"] > 0) & (df["b"] > 0)]
    # keep the original column names available for reporting
    df.attrs["sym_a"], df.attrs["sym_b"] = sym_a.upper(), sym_b.upper()
    return df if len(df) >= 80 else None


def _hedge_ratio(x: np.ndarray, y: np.ndarray) -> float:
    """OLS slope of y on x (with intercept)."""
    X = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(beta[1])


def _adf_tstat(s: np.ndarray) -> float:
    """ADF(0): t-stat on the lagged level in Δs_t = a + ρ·s_{t-1}. More negative
    ⇒ more stationary (mean-reverting). Engle-Granger residual cointegration test."""
    s = np.asarray(s, float)
    ds, lag = np.diff(s), s[:-1]
    X = np.column_stack([np.ones_like(lag), lag])
    beta, *_ = np.linalg.lstsq(X, ds, rcond=None)
    resid = ds - X @ beta
    n = len(ds)
    if n <= 3:
        return 0.0
    sigma2 = (resid @ resid) / (n - 2)
    try:
        se = math.sqrt(sigma2 * np.linalg.inv(X.T @ X)[1, 1])
    except Exception:
        return 0.0
    return float(beta[1] / se) if se > 0 else 0.0


def _half_life(s: np.ndarray) -> float:
    """Mean-reversion half-life (days) from the AR(1) coefficient."""
    s = np.asarray(s, float)
    ds, lag = np.diff(s), s[:-1]
    X = np.column_stack([np.ones_like(lag), lag])
    beta, *_ = np.linalg.lstsq(X, ds, rcond=None)
    rho = beta[1]
    return float(-math.log(2) / math.log(1 + rho)) if -1 < rho < 0 else float("inf")


def _backtest(df: pd.DataFrame, beta: float) -> Dict[str, Any]:
    """z-score mean-reversion on the spread, after costs. Returns per-trade net
    returns (in spread-return units) + summary."""
    spread = np.log(df["b"].values) - beta * np.log(df["a"].values)
    s = pd.Series(spread, index=df.index)
    mu = s.rolling(LOOKBACK).mean()
    sd = s.rolling(LOOKBACK).std(ddof=0)
    z = (s - mu) / sd.replace(0, np.nan)
    cost = COST_BPS_PER_LEG * 2 / 1e4 * 2   # 2 legs, entry+exit

    pos = 0           # +1 long spread, -1 short spread
    entry_s = 0.0
    rets = []
    for i in range(LOOKBACK, len(s)):
        zi = z.iloc[i]
        if np.isnan(zi):
            continue
        if pos == 0:
            if zi <= -Z_IN:
                pos, entry_s = 1, s.iloc[i]
            elif zi >= Z_IN:
                pos, entry_s = -1, s.iloc[i]
        elif (pos == 1 and zi >= -Z_OUT) or (pos == -1 and zi <= Z_OUT):
            raw = (s.iloc[i] - entry_s) * pos       # spread P&L in log units
            rets.append(raw - cost)
            pos = 0
    rets = np.array(rets, float)
    if len(rets) == 0:
        return {"trades": 0, "sharpe": 0.0, "win_rate": 0.0, "net": 0.0}
    ann = math.sqrt(252.0 / max(_avg_hold(df, beta), 1.0))
    sharpe = float(rets.mean() / rets.std(ddof=0) * ann) if rets.std(ddof=0) > 0 else 0.0
    return {"trades": int(len(rets)), "sharpe": round(sharpe, 3),
            "win_rate": round(100.0 * float((rets > 0).mean()), 1),
            "net": round(float(rets.sum()), 4), "avg_ret": round(float(rets.mean()), 5)}


def _avg_hold(df, beta) -> float:
    return 5.0   # rough mean holding (days) for annualisation; conservative


def validate(sym_a: str = "NIFTY", sym_b: str = "BANKNIFTY") -> Dict[str, Any]:
    df = _load_aligned(sym_a, sym_b)
    if df is None:
        return {"pair": f"{sym_a.upper()}-{sym_b.upper()}", "verdict": "INSUFFICIENT_DATA",
                "reason": "could not align (need >=80 common days)"}
    n = len(df)
    cut = int(n * 0.7)
    dev, hold = df.iloc[:cut], df.iloc[cut:]

    # β + cointegration fit on DEV ONLY (holdout never used for fitting)
    beta = _hedge_ratio(np.log(dev["a"].values), np.log(dev["b"].values))
    dev_spread = np.log(dev["b"].values) - beta * np.log(dev["a"].values)
    adf = _adf_tstat(dev_spread)
    hl = _half_life(dev_spread)
    corr = float(np.corrcoef(np.log(dev["a"].values), np.log(dev["b"].values))[0, 1])
    cointegrated = adf < ADF_CRIT_5PCT and 1.0 < hl < 60.0

    dev_bt = _backtest(dev, beta)
    oos_bt = _backtest(hold, beta)

    deflated_ok = (oos_bt["sharpe"] >= DSR_SHARPE_BAR and oos_bt["trades"] >= MIN_OOS_TRADES
                   and oos_bt["net"] > 0)
    if oos_bt["trades"] < MIN_OOS_TRADES:
        verdict = "INSUFFICIENT_DATA"
    elif cointegrated and deflated_ok:
        verdict = "PASS"
    else:
        verdict = "NO_EDGE"

    return {
        "pair": f"{sym_a.upper()}-{sym_b.upper()}",
        "verdict": verdict,
        "common_days": n, "dev_days": cut, "holdout_days": n - cut,
        "hedge_beta": round(beta, 4),
        "cointegration": {"adf_tstat": round(adf, 3), "adf_crit_5pct": ADF_CRIT_5PCT,
                          "stationary": bool(adf < ADF_CRIT_5PCT),
                          "half_life_days": round(hl, 1) if math.isfinite(hl) else None,
                          "corr": round(corr, 3), "cointegrated": bool(cointegrated)},
        "dev_backtest": dev_bt, "oos_backtest": oos_bt,
        "gate": {"min_oos_trades": MIN_OOS_TRADES, "oos_sharpe_bar": DSR_SHARPE_BAR,
                 "cost_bps_per_leg": COST_BPS_PER_LEG, "passed": bool(verdict == "PASS")},
    }


def scan_pairs(pairs=None) -> Dict[str, Any]:
    """Validate candidate pairs; rank by ADF (most stationary spread first)."""
    pairs = pairs or CANDIDATE_PAIRS
    results = []
    for a, b in pairs:
        try:
            results.append(validate(a, b))
        except Exception as exc:
            results.append({"pair": f"{a}-{b}", "verdict": "ERROR", "reason": str(exc)[:80]})
    results.sort(key=lambda r: (r.get("cointegration") or {}).get("adf_tstat", 0.0) or 0.0)
    return {
        "scanned": len(results),
        "cointegrated": sum(1 for r in results if (r.get("cointegration") or {}).get("cointegrated")),
        "passed": sum(1 for r in results if r.get("verdict") == "PASS"),
        "ranked": results,
    }


def main() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    ap = argparse.ArgumentParser(description="NIFTY/sector pairs stat-arb validation")
    ap.add_argument("--pair", help="A,B e.g. HDFCBANK,ICICIBANK (default: scan sector pairs)")
    args = ap.parse_args()
    res = validate(*[s.strip() for s in args.pair.split(",")[:2]]) if (args.pair and "," in args.pair) else scan_pairs()
    try:
        json.dump(res, open(REPORT_FILE, "w"), indent=2)
    except Exception:
        pass
    print(json.dumps(res, indent=2)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
