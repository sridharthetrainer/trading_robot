"""
bollinger_reversal_sensitivity.py — stress-tests the one near-miss result from
this session's seminar-strategy search (bollinger_otm_reversal, period=14,
std_mult=1.5: holdout +Rs88,609, Sharpe 3.44, DSR=0.77, beat benchmark) against
the pricing model's own acknowledged weakness: option premiums are Black-
Scholes, anchored to the PREVIOUS day's real EOD settle, not real intraday
ticks (see option_intraday_pricer.py). A positive holdout result on a
synthetic option price series could be genuine signal or a pricing-model
artifact -- this can't fully resolve that (real intraday option data would),
but it answers a narrower, immediately checkable question: does the result
survive plausible IV mis-calibration and worse-than-modeled execution costs,
or does it evaporate the moment either is perturbed even slightly?

External-review recommendation this responds to: "perturb the modeled IV path
by plausible shocks (+-2, +-5, +-10 vol points) and vary slippage; if the
result disappears under modest perturbations, it is not robust enough to
promote." Same holdout period (locked, never touched by the grid search) and
same best-found parameters -- this only varies the pricing/cost assumptions,
not the strategy's signal logic.
"""
from __future__ import annotations

import json
from pathlib import Path

from backtest_bollinger_otm_reversal import backtest_bollinger_otm_reversal

SPLIT_DATE = "2026-05-19"   # from seminar_param_search_results.json, locked holdout start
BEST_PARAMS = {"period": 14, "std_mult": 1.5, "lots": 10}

SIGMA_SHOCKS = [-0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10]     # absolute vol-point shifts
EXTRA_COST_PCTS = [0.0, 0.0005, 0.001, 0.002]   # extra round-trip cost, fraction of notional


def run() -> dict:
    grid = []
    for shock in SIGMA_SHOCKS:
        for extra_cost in EXTRA_COST_PCTS:
            r = backtest_bollinger_otm_reversal(
                **BEST_PARAMS, start_date=SPLIT_DATE,
                sigma_shock=shock, extra_cost_pct=extra_cost, verbose=False,
            )
            grid.append({
                "sigma_shock": shock, "extra_cost_pct": extra_cost,
                "num_trades": r.get("num_trades", 0),
                "total_pnl": r.get("total_pnl"),
                "sharpe": r.get("sharpe"),
                "positive": bool(r.get("total_pnl", 0) > 0),
            })

    n_positive = sum(1 for g in grid if g["positive"])
    n_total = len(grid)
    base = next(g for g in grid if g["sigma_shock"] == 0.0 and g["extra_cost_pct"] == 0.0)

    report = {
        "strategy": "bollinger_otm_reversal", "params": BEST_PARAMS, "holdout_start": SPLIT_DATE,
        "baseline_holdout_pnl": base["total_pnl"], "baseline_holdout_sharpe": base["sharpe"],
        "grid": grid, "n_scenarios": n_total, "n_positive": n_positive,
        "fraction_positive": round(n_positive / n_total, 3) if n_total else 0.0,
    }
    Path("bollinger_reversal_sensitivity_report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    rep = run()
    print(f"baseline: pnl={rep['baseline_holdout_pnl']}  sharpe={rep['baseline_holdout_sharpe']}")
    print(f"{rep['n_positive']}/{rep['n_scenarios']} scenarios stay net-positive "
          f"({rep['fraction_positive']:.0%})")
    print()
    print(f"{'sigma_shock':>12s} {'extra_cost%':>12s} {'trades':>7s} {'pnl':>12s} {'sharpe':>8s}")
    for g in rep["grid"]:
        flag = "  " if g["positive"] else "❌"
        print(f"{flag}{g['sigma_shock']:>10.2f} {g['extra_cost_pct']*100:>11.2f}% "
              f"{g['num_trades']:>7d} {g['total_pnl']:>12,.0f} {g['sharpe']:>8.2f}")
