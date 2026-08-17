"""
time_to_power.py — converts INSUFFICIENT_POWER from a permanent verdict into
a decision, per an external review's point: "5 of 6 are underpowered" is a
fact about ~15 months of history and low firing rates, not about the
strategies. Without a decision rule, INSUFFICIENT_POWER just means "keep
shadowing forever" -- the same failure mode already named for
drift_monitor.py's calendar window, now showing up in validation too.

Computes, per strategy already scored by minimum_detectable_edge.py /
minimum_detectable_edge_original11.py:

  n_star            trades needed at 80% power to detect the OBSERVED
                     per-trade net effect size (inverts the MDE formula:
                     MDE = k*std/sqrt(n)  =>  n* = (k*std/mean)^2)
  firing_rate/year   observed trade count / holdout span, annualized
  time_to_power      n_star / firing_rate, in years -- how long until this
                     strategy could even in principle clear its own bar,
                     at its own observed rate and its own observed edge

Futility rule: DEAD_ON_ARRIVAL if time_to_power > FUTILITY_YEARS (default 5)
-- effectively unvalidatable within a reasonable research horizon, not
"promising pending more data". WORTH_WAITING otherwise.

NOTE: excludes adx_long_straddle. Its earlier MDE run used a split date
(2026-05-19) that predates its 1-minute data entirely (starts 2026-05-25),
so that run measured the FULL sample, not a genuine holdout -- computing a
firing rate from it would be measuring the wrong span. Flagged rather than
silently included.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from minimum_detectable_edge import MDE_MULTIPLIER

FUTILITY_YEARS = 5.0

# (report_file, strategy_key, holdout_trading_days) -- holdout spans computed
# directly from each population's actual data/split, not assumed equal.
# Seminar strategies (5m-based): confirmed 63 trading days (2026-05-19 to
# 2026-08-17, per load_nifty_5m()). Original-11: split_holdout() is ROW-based
# on 210 days of 5m data at HOLDOUT_RATIO=0.20 -- holdout_df's own trading-day
# count, computed once via the same call minimum_detectable_edge_original11.py
# makes.
SEMINAR_HOLDOUT_DAYS = 63     # 5m-based strategies (2026-05-19 split)
ADX_HOLDOUT_DAYS = 12         # 1-min-based (2026-07-31 split) -- see
                               # minimum_detectable_edge.py's ADX_SPLIT_DATE

ENTRIES = [
    ("minimum_detectable_edge_report.json", "bollinger_otm_reversal", SEMINAR_HOLDOUT_DAYS),
    ("minimum_detectable_edge_report.json", "bollinger_otm_momentum", SEMINAR_HOLDOUT_DAYS),
    ("minimum_detectable_edge_report.json", "sma20_atm_option", SEMINAR_HOLDOUT_DAYS),
    ("minimum_detectable_edge_report.json", "di_momentum_call", SEMINAR_HOLDOUT_DAYS),
    ("minimum_detectable_edge_report.json", "rolling_short_straddle", SEMINAR_HOLDOUT_DAYS),
    ("minimum_detectable_edge_report.json", "adx_long_straddle", ADX_HOLDOUT_DAYS),
]


def _original11_holdout_days() -> int:
    from candle_cache import get_cached_candles
    from validation_harness import split_holdout, HOLDOUT_RATIO
    df = get_cached_candles("NIFTY", interval="5m", days=210)
    _, holdout_df = split_holdout(df, HOLDOUT_RATIO)
    return len(set(holdout_df.index.date))


MIN_N_FOR_EXTRAPOLATION = 10   # below this, std itself is too unstable to extrapolate from

def compute(stats: Dict[str, Any], holdout_days: int) -> Optional[Dict[str, Any]]:
    n = stats.get("n", 0)
    net_mean = stats.get("net_mean")
    net_std = stats.get("net_std")
    if n == 0 or net_mean is None or net_std in (None, 0.0):
        return None
    if n < MIN_N_FOR_EXTRAPOLATION:
        return {"n_observed": n, "verdict": "SAMPLE_TOO_SMALL_TO_EXTRAPOLATE",
                "reason": f"n={n} < {MIN_N_FOR_EXTRAPOLATION} -- std estimate itself "
                          "too unstable to extrapolate a firing rate from"}

    firing_rate_per_year = n / holdout_days * 252.0
    if abs(net_mean) < 1e-9:
        n_star = float("inf")
    else:
        n_star = (MDE_MULTIPLIER * net_std / net_mean) ** 2

    time_to_power_years = (n_star / firing_rate_per_year) if firing_rate_per_year > 0 else float("inf")
    verdict = "DEAD_ON_ARRIVAL" if time_to_power_years > FUTILITY_YEARS else "WORTH_WAITING"

    return {
        "n_observed": n, "holdout_trading_days": holdout_days,
        "firing_rate_per_year": round(firing_rate_per_year, 1),
        "net_mean": round(net_mean, 2), "net_std": round(net_std, 2),
        "n_star": round(n_star, 1) if n_star != float("inf") else None,
        "time_to_power_years": round(time_to_power_years, 1) if time_to_power_years != float("inf") else None,
        "verdict": verdict,
    }


def run() -> Dict[str, Any]:
    results: Dict[str, Any] = {}

    for report_file, key, holdout_days in ENTRIES:
        stats = json.load(open(report_file))[key]
        if stats.get("verdict") != "INSUFFICIENT_POWER":
            continue  # only meaningful for strategies actually stuck there
        r = compute(stats, holdout_days)
        if r is not None:
            results[key] = r

    orig11_days = _original11_holdout_days()
    orig11 = json.load(open("minimum_detectable_edge_original11_report.json"))
    for key, stats in orig11.items():
        if stats.get("verdict") != "INSUFFICIENT_POWER" or stats.get("n", 0) == 0:
            continue
        r = compute(stats, orig11_days)
        if r is not None:
            results[key] = r

    Path("time_to_power_report.json").write_text(json.dumps(results, indent=2, default=str))
    return results


if __name__ == "__main__":
    rep = run()
    for name, r in rep.items():
        if r["verdict"] == "SAMPLE_TOO_SMALL_TO_EXTRAPOLATE":
            print(f"{name:18s} n={r['n_observed']:4d}  -> {r['verdict']} ({r['reason']})")
        else:
            print(f"{name:18s} n={r['n_observed']:4d}  rate={r['firing_rate_per_year']:6.1f}/yr  "
                  f"n*={r['n_star']}  time_to_power={r['time_to_power_years']}yr  -> {r['verdict']}")
