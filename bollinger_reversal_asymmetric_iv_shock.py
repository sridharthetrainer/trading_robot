"""
bollinger_reversal_asymmetric_iv_shock.py — extends bollinger_reversal_
sensitivity.py's symmetric IV-shock test with an asymmetric one, per an
external review's critique: real IV mis-calibration isn't a uniform ±shock,
it's directionally skewed (the "leverage effect" -- IV rises more after a
down-move than an up-move).

The Bollinger reversal strategy's own entry logic gives a clean, non-
arbitrary way to apply this asymmetry: it buys CE when price just broke
BELOW the lower band (triggered by a down-move -- exactly where the leverage
effect predicts the prior day's IV most understates the true intraday IV)
and buys PE when price broke ABOVE the upper band (an up-move, where IV
typically doesn't spike as much, sometimes contracts). So a larger positive
shock on the CE side and a smaller/zero shock on the PE side is the
market-microstructure-motivated stress test, not a symmetric guess.

Same locked holdout, same best-found parameters (period=14, std_mult=1.5,
lots=10) as the rest of this session's Bollinger reversal work.
"""
from __future__ import annotations

import json
from pathlib import Path

from backtest_bollinger_otm_reversal import backtest_bollinger_otm_reversal

SPLIT_DATE = "2026-05-19"
BEST_PARAMS = {"period": 14, "std_mult": 1.5, "lots": 10}

# (CE shock, PE shock) pairs -- CE side gets the larger shock throughout,
# modeling "IV understated more after down-moves than up-moves"
ASYMMETRIC_SCENARIOS = [
    (0.0, 0.0),     # baseline, no shock
    (0.02, 0.0),    # mild skew
    (0.05, 0.0),    # moderate skew, zero on the up-move side
    (0.08, 0.02),   # moderate skew, small residual on the up-move side
    (0.10, 0.0),    # extreme skew, zero on the up-move side
    (0.10, -0.03),  # extreme skew, up-move side IV actually contracts
    (0.15, 0.0),    # beyond what the symmetric test covered at all
]


def run() -> dict:
    rows = []
    for ce_shock, pe_shock in ASYMMETRIC_SCENARIOS:
        r = backtest_bollinger_otm_reversal(
            **BEST_PARAMS, start_date=SPLIT_DATE,
            sigma_shock={"CE": ce_shock, "PE": pe_shock}, verbose=False,
        )
        rows.append({
            "ce_shock": ce_shock, "pe_shock": pe_shock,
            "num_trades": r.get("num_trades", 0),
            "total_pnl": r.get("total_pnl"),
            "sharpe": r.get("sharpe"),
            "positive": bool(r.get("total_pnl", 0) > 0),
        })

    n_positive = sum(1 for r in rows if r["positive"])
    report = {
        "strategy": "bollinger_otm_reversal", "params": BEST_PARAMS, "holdout_start": SPLIT_DATE,
        "scenarios": rows, "n_scenarios": len(rows), "n_positive": n_positive,
    }
    Path("bollinger_reversal_asymmetric_iv_shock_report.json").write_text(
        json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    rep = run()
    print(f"{rep['n_positive']}/{rep['n_scenarios']} asymmetric scenarios stay net-positive\n")
    print(f"{'CE shock':>10s} {'PE shock':>10s} {'trades':>7s} {'pnl':>12s} {'sharpe':>8s}")
    for r in rep["scenarios"]:
        flag = "  " if r["positive"] else "❌"
        print(f"{flag}{r['ce_shock']:>8.2f} {r['pe_shock']:>10.2f} "
              f"{r['num_trades']:>7d} {r['total_pnl']:>12,.0f} {r['sharpe']:>8.2f}")
