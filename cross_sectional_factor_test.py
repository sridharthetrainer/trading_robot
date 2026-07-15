"""
cross_sectional_factor_test.py — tests the one category of "well-documented
elsewhere" strategy this system has never tried: cross-sectional ranking
across the stock universe, monthly rebalance (2026-07-15, following up on
"is there any proven strategy" -> momentum / low-vol / value).

VALUE FACTOR EXCLUDED: this repo has no fundamental/valuation data source
(no book value, EPS, or earnings anywhere) — grep hits on "eps" were false
positives on "steps". Building a value factor would mean fabricating a
proxy for real financial data, which this project's rules explicitly rule
out. Only momentum and low-volatility are tested here — both purely
price-derived, no fabricated inputs.

DATA-DEPTH ADAPTATION, stated plainly: classic academic momentum uses a
12-month lookback skipping the most recent month (Jegadeesh & Titman
1993). This system's COMMON cross-symbol daily history is only ~1 year for
most of the ~190-stock universe (some blue-chips go back to 2022, most
don't) — a 12-month lookback would leave almost no months left to actually
rebalance and test. Using a 6-month lookback (skip most recent 1 month)
instead, which J&T's own paper also tested as one of several formation
periods — a real, documented variant, not an invented one. This is a
genuine limitation, not hidden: few monthly rebalance periods means low
statistical power regardless of what's found.

Method:
  - Universe: symbols with >= LOOKBACK_DAYS + MIN_LIVE_MONTHS*21 days of
    common 1d history (excludes very short-history recent listings).
  - Monthly rebalance (roughly every 21 trading days): rank by trailing
    6-month return (momentum) or trailing 3-month realized volatility
    (low-vol, ascending — lowest vol ranked best).
  - Quintile long-short AND long-only-top-quintile-vs-equal-weight-
    benchmark, both reported.
  - Real delivery equity costs: EQ_STT_DELIVERY (capital_compounder.py) =
    0.1% each side; total round-trip cost per position modeled as
    ROUND_TRIP_COST_PCT below (STT both sides + exchange/GST/stamp
    allowance), applied every month a position is held (rebalanced).
  - Period-based (month) train/holdout split — NOT day-based, since the
    unit of independence here is the monthly rebalance, not the day.
    Bonferroni across the 2 factors x 2 portfolio constructions tested.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List

import numpy as np
import pandas as pd

CANDLE_DB = "candle_cache.db"
LOOKBACK_DAYS = 126       # ~6 trading months
SKIP_DAYS = 21            # ~1 month, avoids short-term reversal contamination
VOL_LOOKBACK_DAYS = 63    # ~3 trading months
REBALANCE_STEP_DAYS = 21  # ~1 month
MIN_LIVE_MONTHS = 8       # minimum months of usable rebalance history required
ROUND_TRIP_COST_PCT = 0.0022  # 0.22% -- STT 0.1% both sides + exchange/GST/stamp allowance
QUINTILE = 5
TRAIN_FRAC = 0.70
ALPHA = 0.05


def _load_universe() -> Dict[str, pd.Series]:
    with sqlite3.connect(CANDLE_DB) as conn:
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM candles WHERE interval='1d'")]
        out = {}
        for sym in symbols:
            if sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"):
                continue  # indices, not part of the tradeable stock universe
            rows = conn.execute(
                "SELECT timestamp, close FROM candles WHERE symbol=? AND interval='1d' "
                "ORDER BY timestamp", (sym,)).fetchall()
            if len(rows) < LOOKBACK_DAYS + MIN_LIVE_MONTHS * REBALANCE_STEP_DAYS:
                continue
            idx = pd.to_datetime([str(r[0])[:10] for r in rows])
            out[sym] = pd.Series([float(r[1]) for r in rows], index=idx)
    return out


def _stat(rets: List[float]) -> Dict[str, Any]:
    n = len(rets)
    if n == 0:
        return {"n": 0}
    mean = sum(rets) / n
    sd = 0.0
    if n > 1:
        var = sum((x - mean) ** 2 for x in rets) / (n - 1)
        sd = math.sqrt(var)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))
    win = sum(1 for x in rets if x > 0) / n
    return {"n": n, "mean_monthly_pct": round(mean, 4), "win_rate": round(win, 3),
            "t": round(t, 2), "p": round(p, 5)}


def _rebalance_dates(all_dates: pd.DatetimeIndex) -> List[pd.Timestamp]:
    return list(all_dates[LOOKBACK_DAYS::REBALANCE_STEP_DAYS][:-1])


def _factor_scores(prices: Dict[str, pd.Series], as_of: pd.Timestamp,
                   factor: str) -> Dict[str, float]:
    scores = {}
    for sym, s in prices.items():
        window = s[s.index <= as_of]
        if len(window) < LOOKBACK_DAYS:
            continue
        if factor == "momentum":
            p_now = window.iloc[-1 - SKIP_DAYS]
            p_then = window.iloc[-LOOKBACK_DAYS]
            if p_then > 0:
                scores[sym] = (p_now / p_then) - 1.0
        elif factor == "low_vol":
            recent = window.tail(VOL_LOOKBACK_DAYS)
            if len(recent) >= VOL_LOOKBACK_DAYS:
                rets = recent.pct_change().dropna()
                scores[sym] = -float(rets.std())  # negate: lower vol -> higher score
    return scores


def _forward_return(prices: Dict[str, pd.Series], sym: str,
                     entry: pd.Timestamp, exit_: pd.Timestamp) -> float | None:
    s = prices.get(sym)
    if s is None:
        return None
    entry_px = s[s.index <= entry]
    exit_px = s[s.index <= exit_]
    if len(entry_px) == 0 or len(exit_px) == 0:
        return None
    e, x = entry_px.iloc[-1], exit_px.iloc[-1]
    if e <= 0:
        return None
    return (x - e) / e * 100.0  # pct


def run(factor: str) -> Dict[str, Any]:
    prices = _load_universe()
    if len(prices) < 20:
        return {"error": f"only {len(prices)} symbols with enough common history"}
    all_dates = sorted(set().union(*[set(s.index) for s in prices.values()]))
    all_dates = pd.DatetimeIndex(all_dates)
    rebal_dates = _rebalance_dates(all_dates)
    if len(rebal_dates) < 8:
        return {"error": f"only {len(rebal_dates)} monthly rebalance points "
                          f"available (need >= 8) — data too shallow yet"}

    cut_idx = max(1, int(len(rebal_dates) * TRAIN_FRAC) - 1)
    cutoff_date = rebal_dates[cut_idx]

    long_short_train, long_short_holdout = [], []
    long_only_train, long_only_holdout = [], []
    for i, d in enumerate(rebal_dates[:-1]):
        next_d = rebal_dates[i + 1]
        scores = _factor_scores(prices, d, factor)
        if len(scores) < QUINTILE * 4:
            continue
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        q = max(1, len(ranked) // QUINTILE)
        top, bottom = ranked[:q], ranked[-q:]

        top_rets = [_forward_return(prices, s, d, next_d) for s, _ in top]
        bottom_rets = [_forward_return(prices, s, d, next_d) for s, _ in bottom]
        all_rets = [_forward_return(prices, s, d, next_d) for s in scores]
        top_rets = [r for r in top_rets if r is not None]
        bottom_rets = [r for r in bottom_rets if r is not None]
        all_rets = [r for r in all_rets if r is not None]
        if not top_rets or not bottom_rets or not all_rets:
            continue

        cost = ROUND_TRIP_COST_PCT * 100  # to pct points, applied per leg per month
        ls_ret = (np.mean(top_rets) - np.mean(bottom_rets)) - 2 * cost
        lo_ret = np.mean(top_rets) - np.mean(all_rets) - cost  # active return vs equal-weight universe

        train_slot = d <= cutoff_date
        (long_short_train if train_slot else long_short_holdout).append(ls_ret)
        (long_only_train if train_slot else long_only_holdout).append(lo_ret)

    return {
        "factor": factor,
        "symbols_in_universe": len(prices),
        "rebalance_points": len(rebal_dates),
        "cutoff_date": str(cutoff_date.date()),
        "long_short": {"train": _stat(long_short_train), "holdout": _stat(long_short_holdout)},
        "long_only_active": {"train": _stat(long_only_train), "holdout": _stat(long_only_holdout)},
    }


def _verdict(train: Dict[str, Any], holdout: Dict[str, Any], bonferroni: int) -> str:
    if train.get("n", 0) < 6:
        return "INSUFFICIENT_DATA"
    sig = train["p"] * bonferroni < ALPHA
    held = holdout.get("n", 0) >= 3 and holdout.get("mean_monthly_pct", 0) * train["mean_monthly_pct"] > 0
    if sig and train["mean_monthly_pct"] > 0 and held:
        return "CANDIDATE"
    if sig and train["mean_monthly_pct"] > 0:
        return "TRAIN_ONLY_OVERFIT"
    if sig and train["mean_monthly_pct"] < 0:
        return "HURTS"
    return "NOISE"


def main() -> int:
    import json
    from datetime import datetime
    from pathlib import Path

    results = {}
    for factor in ("momentum", "low_vol"):
        results[factor] = run(factor)

    print("=== CROSS-SECTIONAL FACTOR TEST (momentum, low-vol; value excluded, no data) ===\n")
    bonferroni = 4  # 2 factors x 2 constructions
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "bonferroni_tests": bonferroni, "value_factor": "excluded_no_fundamental_data",
              "factors": {}}
    for factor, rep in results.items():
        if rep.get("error"):
            print(f"{factor}: {rep['error']}\n")
            report["factors"][factor] = {"error": rep["error"]}
            continue
        print(f"--- {factor} | universe={rep['symbols_in_universe']} symbols | "
              f"{rep['rebalance_points']} monthly rebalances | cutoff={rep['cutoff_date']} ---")
        verdicts = {}
        for construction in ("long_short", "long_only_active"):
            tr = rep[construction]["train"]
            ho = rep[construction]["holdout"]
            verdict = _verdict(tr, ho, bonferroni)
            verdicts[construction] = {"verdict": verdict, "train": tr, "holdout": ho}
            print(f"  {construction:<18} verdict={verdict:<20} "
                  f"train n={tr.get('n')} {tr.get('mean_monthly_pct')}%/mo t={tr.get('t')} p={tr.get('p')} | "
                  f"holdout n={ho.get('n')} {ho.get('mean_monthly_pct')}%/mo")
        report["factors"][factor] = {**rep, "verdicts": verdicts}
        print()

    try:
        Path("cross_sectional_factor_report.json").write_text(json.dumps(report, indent=2))
    except Exception as exc:
        print(f"report write failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
