#!/usr/bin/env python3
"""
trend_basket_research.py — RESEARCH (report-only) for EDGE_STRATEGY #4c:
long-horizon, diversified TREND-FOLLOWING on a BASKET.

This is the one retail-accessible edge with a real academic prior (Carver, Kaufman)
that this project never properly tested. It is NOT wired to live trading. It runs
under the same discipline as everything else: walk-forward dev/holdout split +
Deflated Sharpe (reused from validation_harness). Measured > believed.

Method (long-FLAT, no shorting — standard for equity trend):
  • signal[t] = 1 if close[t] > SMA(lookback)[t] else 0   (causal)
  • next-day return = signal[t-1] * pct_change[t]  − cost on position changes
  • basket = equal-weight mean of per-instrument strategy returns (diversification)
  • grid over lookbacks on the dev set → DSR on the best → evaluate locked holdout

Data: needs daily closes per basket symbol. None are stored yet (the index candle
cache is zeroed); use _load_nifty_daily() (real, 6yr) as a single-instrument check
and backfill a real basket (equity bhavcopy / Angel) before trusting a verdict.

Usage:
    python trend_basket_research.py            # runs on available data
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

import pandas as pd

try:
    from validation_harness import deflated_sharpe_ratio
except Exception:                                  # pragma: no cover
    def deflated_sharpe_ratio(sr, n_trades, n_trials, **k):
        return 0.0

DSR_STRONG = 0.95
ANN = 252 ** 0.5


# ── data loaders ──────────────────────────────────────────────────────────────
def _load_nifty_daily(db_path: str = "participant_oi.db") -> Optional[pd.Series]:
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT date, close FROM nifty_daily WHERE close>0 ORDER BY date"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return None
        s = pd.Series({pd.to_datetime(d): float(c) for d, c in rows}).sort_index()
        return s
    except Exception:
        return None


def load_basket(symbols: Optional[List[str]] = None) -> Dict[str, pd.Series]:
    """Return {symbol: close-series}. Only real (non-zero) daily data is returned.
    Currently only NIFTY is available on disk — a real basket needs a backfill."""
    data: Dict[str, pd.Series] = {}
    nifty = _load_nifty_daily()
    if nifty is not None and len(nifty) > 250:
        data["NIFTY"] = nifty
    # candle_cache stock dailies are zeroed today; include any that become real:
    try:
        conn = sqlite3.connect("candle_cache.db")
        try:
            syms = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM candles WHERE interval='1d' AND close>0"
            ).fetchall()]
            for sym in syms:
                rows = conn.execute(
                    "SELECT timestamp, close FROM candles WHERE symbol=? AND interval='1d' "
                    "AND close>0 ORDER BY timestamp", (sym,)).fetchall()
                if len(rows) > 250:
                    data[sym] = pd.Series(
                        {pd.to_datetime(t): float(c) for t, c in rows}).sort_index()
        finally:
            conn.close()
    except Exception:
        pass
    return data


# ── strategy ──────────────────────────────────────────────────────────────────
def instrument_returns(close: pd.Series, lookback: int, cost_bps: float = 5.0) -> pd.Series:
    """Daily strategy return for one instrument (long-flat MA trend, causal, net of
    cost on position changes)."""
    sma = close.rolling(lookback).mean()
    signal = (close > sma).astype(float)            # 1 long / 0 flat, at close[t]
    held = signal.shift(1).fillna(0.0)              # hold from next bar (no lookahead)
    ret = close.pct_change().fillna(0.0) * held
    turn = held.diff().abs().fillna(0.0)           # 1 when entering/exiting
    ret = ret - turn * (cost_bps / 1e4)
    return ret


def basket_returns(data: Dict[str, pd.Series], lookback: int, cost_bps: float = 5.0) -> pd.Series:
    cols = {sym: instrument_returns(s, lookback, cost_bps) for sym, s in data.items()}
    if not cols:
        return pd.Series(dtype=float)
    df = pd.DataFrame(cols).fillna(0.0)
    return df.mean(axis=1)                           # equal-weight basket


def _sharpe(ret: pd.Series) -> float:
    r = ret.dropna()
    sd = float(r.std())
    return float(r.mean() / sd * ANN) if sd > 0 and len(r) > 2 else 0.0


def _n_trades(data: Dict[str, pd.Series], lookback: int) -> int:
    n = 0
    for s in data.values():
        sma = s.rolling(lookback).mean()
        sig = (s > sma).astype(float)
        n += int(sig.shift(1).fillna(0.0).diff().abs().fillna(0.0).sum())
    return n


def validate(data: Dict[str, pd.Series],
             lookbacks: List[int] = [50, 100, 150, 200],
             holdout_ratio: float = 0.2, cost_bps: float = 5.0) -> Dict[str, object]:
    if not data:
        return {"status": "NO_DATA",
                "reason": "no basket daily prices on disk — backfill equity bhavcopy/Angel first"}
    # build full-history portfolio returns per lookback, split dev/holdout by time
    best = None
    for lb in lookbacks:
        port = basket_returns(data, lb, cost_bps).dropna()
        if len(port) < 252:
            continue
        split = int(len(port) * (1 - holdout_ratio))
        dev, hold = port.iloc[:split], port.iloc[split:]
        dev_sh = _sharpe(dev)
        if best is None or dev_sh > best["dev_sharpe"]:
            best = {"lookback": lb, "dev_sharpe": dev_sh,
                    "holdout_sharpe": _sharpe(hold),
                    "dev_n": len(dev), "hold_n": len(hold)}
    if best is None:
        return {"status": "INSUFFICIENT_DATA", "reason": "need >252 daily bars"}
    n_trades = _n_trades(data, best["lookback"])
    dsr = deflated_sharpe_ratio(sr=best["dev_sharpe"], n_trades=max(n_trades, 5),
                                n_trials=len(lookbacks))
    passed = (dsr >= DSR_STRONG and best["holdout_sharpe"] > 0)
    return {
        "status": "OK",
        "instruments": len(data),
        "best_lookback": best["lookback"],
        "dev_sharpe": round(best["dev_sharpe"], 3),
        "holdout_sharpe": round(best["holdout_sharpe"], 3),
        "n_trades": n_trades,
        "deflated_sharpe": round(dsr, 3),
        "verdict": "PASS" if passed else "FAIL",
        "note": ("single-instrument only — NOT a basket; backfill more symbols"
                 if len(data) < 5 else "research only — not wired to live"),
    }


def main() -> int:
    data = load_basket()
    res = validate(data)
    print("\nTREND-ON-BASKET RESEARCH (#4c, report-only)")
    print("-" * 50)
    for k, v in res.items():
        print(f"  {k}: {v}")
    if res.get("status") == "OK" and res.get("instruments", 0) < 5:
        print("\n  ⚠ Only NIFTY available — this is a single-instrument check, not the")
        print("    diversified basket the edge needs. Backfill ~50+ symbols to validate #4c.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
