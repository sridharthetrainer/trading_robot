"""
minimum_detectable_edge.py — decomposes "0/6 seminar strategies pass" into
WHY each one failed, per an idea independently proposed across multiple
external reviews of this session's work: "no strategies passed" conflates
several genuinely different situations that call for different next actions.

For each strategy (at its already-found best parameters, on the SAME locked
holdout period used throughout this session), computes:

  1. gross_mean_per_trade, net_mean_per_trade (after real costs)
  2. Minimum Detectable Edge (MDE) at 80% power, two-sided alpha=0.05:
         MDE ≈ (z_a/2 + z_beta) * sigma / sqrt(n)
              = 2.8 * sigma / sqrt(n)     (z_0.025=1.96, z_0.20=0.84)
     using the ACTUAL trade count n and the ACTUAL per-trade return std --
     NOT the annualized Sharpe (mean/std * sqrt(252)), which is a DIFFERENT
     quantity on a different scale. Conflating the two was a concrete error
     an earlier external review made this session; this script exists partly
     to not repeat it.

Classification per strategy:
  INSUFFICIENT_POWER  n<30, OR |net_mean| < MDE -- can't distinguish this
                       result from zero at 80% power. "No edge found" would
                       overclaim; the honest statement is "can't tell yet."
  NO_EDGE              gross_mean itself is not meaningfully positive
                       (|gross_mean| clears MDE but isn't positive, or is
                       indistinguishable from zero) -- costs aren't even the
                       question, there's no signal to erode.
  COST_ERODED          gross_mean is meaningfully positive (clears MDE) but
                       net_mean is not -- real transaction costs consumed a
                       genuine gross signal.
  EDGE_DETECTED         net_mean clears MDE and is positive -- doesn't by
                       itself mean "validated" (that still requires the DSR
                       + holdout + benchmark gates already applied in
                       seminar_param_search.py), just that this specific
                       result isn't a power problem.

Scoped to the 6 seminar strategies only (not the original 11 rule
strategies) -- those store dev_avg_sharpe (already annualized) rather than
raw per-trade returns, and reverse-engineering per-trade std from an
annualized Sharpe number correctly is exactly the kind of unit conversion
this script exists to get right, not risk getting wrong under time pressure.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import numpy as np

from backtest_bollinger_otm_reversal import backtest_bollinger_otm_reversal
from backtest_bollinger_otm_momentum import backtest_bollinger_otm_momentum
from backtest_sma20_atm_option import backtest_sma20_atm_option
from backtest_di_momentum_call import backtest_di_momentum_call
from backtest_adx_long_straddle import backtest_adx_long_straddle
from backtest_rolling_short_straddle import backtest_rolling_short_straddle

Z_ALPHA_2 = 1.96   # two-sided, alpha=0.05
Z_BETA = 0.84      # 80% power
MDE_MULTIPLIER = Z_ALPHA_2 + Z_BETA
MIN_N_FOR_ANY_VERDICT = 30

SPLIT_DATE = "2026-05-19"           # 80/20 split for 5m-based strategies
ADX_SPLIT_DATE = "2026-07-31"       # separate 80/20 split for ADX's 1-min data
                                     # (2026-05-19 predates 1-min data entirely,
                                     # which only starts 2026-05-25 -- using it
                                     # would silently include the full sample
                                     # as "holdout", not a genuine split)

STRATEGIES = {
    "bollinger_otm_reversal": (backtest_bollinger_otm_reversal,
                                {"period": 14, "std_mult": 1.5, "lots": 10}, SPLIT_DATE),
    "bollinger_otm_momentum": (backtest_bollinger_otm_momentum,
                                {"period": 14, "std_mult": 2.5, "lots": 10}, SPLIT_DATE),
    "sma20_atm_option": (backtest_sma20_atm_option, {"period": 30, "lots": 10}, SPLIT_DATE),
    "di_momentum_call": (backtest_di_momentum_call,
                          {"di_period": 21, "mom_period": 5, "di_threshold": 30, "lots": 10}, SPLIT_DATE),
    "adx_long_straddle": (backtest_adx_long_straddle,
                           {"period": 14, "threshold": 50, "lots": 10}, ADX_SPLIT_DATE),
    "rolling_short_straddle": (backtest_rolling_short_straddle,
                                {"leg_sl_pct": 0.15, "lots": 10}, SPLIT_DATE),
}


def _per_trade_stats(trades: list) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    gross = np.array([t.get("gross_pnl", t.get("pnl", 0.0)) for t in trades], dtype=float)
    net = np.array([t["pnl"] for t in trades], dtype=float)
    return {
        "n": n,
        "gross_mean": float(gross.mean()), "gross_std": float(gross.std(ddof=1)) if n > 1 else 0.0,
        "net_mean": float(net.mean()), "net_std": float(net.std(ddof=1)) if n > 1 else 0.0,
    }


def _confidence_interval_95(mean: float, std: float, n: int) -> Any:
    """95% CI on the per-trade mean, t-distributed (more honest than a fixed
    z=1.96 at small n). Per external review: a binary verdict hides whether
    the data is consistent with a tight range around zero (genuinely dead)
    or a wide range mostly on one side (worth waiting on) -- the same
    INSUFFICIENT_POWER label covers both very differently-informative cases."""
    if n < 2 or std <= 0:
        return None
    from scipy import stats as _stats
    t_crit = float(_stats.t.ppf(0.975, df=n - 1))
    half_width = t_crit * std / (n ** 0.5)
    return [round(mean - half_width, 2), round(mean + half_width, 2)]


def classify(stats: Dict[str, Any]) -> Dict[str, Any]:
    n = stats["n"]
    net_ci = _confidence_interval_95(stats.get("net_mean", 0.0), stats.get("net_std", 0.0), n)

    if n < MIN_N_FOR_ANY_VERDICT:
        return {**stats, "mde": None, "net_mean_ci_95": net_ci,
                "verdict": "INSUFFICIENT_POWER",
                "reason": f"n={n} < minimum {MIN_N_FOR_ANY_VERDICT} trades"}

    mde_gross = MDE_MULTIPLIER * stats["gross_std"] / (n ** 0.5)
    mde_net = MDE_MULTIPLIER * stats["net_std"] / (n ** 0.5)
    gross_detectable = abs(stats["gross_mean"]) >= mde_gross
    net_detectable = abs(stats["net_mean"]) >= mde_net

    if not net_detectable:
        verdict = "INSUFFICIENT_POWER"
        reason = f"|net_mean|={abs(stats['net_mean']):.0f} < MDE={mde_net:.0f} at n={n}"
    elif not gross_detectable or stats["gross_mean"] <= 0:
        verdict = "NO_EDGE"
        reason = "gross return itself is not detectably positive -- no signal to erode"
    elif stats["net_mean"] <= 0:
        verdict = "COST_ERODED"
        reason = (f"gross_mean={stats['gross_mean']:.0f} clears MDE and is positive, "
                   f"but net_mean={stats['net_mean']:.0f} does not survive costs")
    else:
        verdict = "EDGE_DETECTED"
        reason = "net_mean clears MDE and is positive -- not a power problem (still needs DSR/holdout/benchmark gates)"

    return {**stats, "mde_gross": round(mde_gross, 2), "mde_net": round(mde_net, 2),
            "net_mean_ci_95": net_ci, "verdict": verdict, "reason": reason}


def run() -> Dict[str, Any]:
    results = {}
    for name, (fn, params, split_date) in STRATEGIES.items():
        r = fn(**params, start_date=split_date, verbose=False)
        stats = _per_trade_stats(r.get("trades", []))
        results[name] = classify(stats)
    Path("minimum_detectable_edge_report.json").write_text(json.dumps(results, indent=2, default=str))
    return results


if __name__ == "__main__":
    rep = run()
    for name, r in rep.items():
        print(f"{name:26s} n={r['n']:4d}  verdict={r['verdict']:18s}  {r['reason']}")
