"""
bollinger_reversal_robustness_check.py — two more robustness checks on the
bollinger_otm_reversal near-miss (period=14, std_mult=1.5: holdout +Rs88,609,
Sharpe 3.44, DSR=0.77), both recommended independently by two external
reviewers of this session's work:

1. PARAMETER PLATEAU — is 14/1.5 an isolated spike, or do neighboring grid
   points (12-16 period, 1.4-1.7 std) also perform well? A broad profitable
   region is real evidence; an isolated optimum surrounded by losers is a
   classic overfitting signature. Reuses the same 9-point grid from
   seminar_param_search.py, but evaluates ALL 9 on both dev and holdout
   (the original search only ever touched holdout with the single best-dev
   point, by design -- this is deliberately looking at the rest now).

2. TEMPORAL STABILITY WITHIN HOLDOUT — is the holdout's positive P&L spread
   across the period, or concentrated in one lucky stretch? Splits the 50
   holdout trades into 3 chronological thirds and reports each segment's P&L
   independently. "All three positive" is much stronger evidence than "the
   total is positive."

Both are diagnostic only -- this does not change the strategy's PASS/FAIL
verdict (still FAIL per seminar_param_search.py's DSR gate), just
characterizes HOW close/far it is from being real.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backtest_bollinger_otm_reversal import backtest_bollinger_otm_reversal

SPLIT_DATE = "2026-05-19"
PERIOD_GRID = [12, 14, 16]
STD_GRID = [1.4, 1.5, 1.7]
BEST = {"period": 14, "std_mult": 1.5}


def parameter_plateau() -> list:
    rows = []
    for period in PERIOD_GRID:
        for std in STD_GRID:
            dev = backtest_bollinger_otm_reversal(
                period=period, std_mult=std, lots=10, end_date=SPLIT_DATE, verbose=False)
            hold = backtest_bollinger_otm_reversal(
                period=period, std_mult=std, lots=10, start_date=SPLIT_DATE, verbose=False)
            rows.append({
                "period": period, "std_mult": std,
                "dev_trades": dev.get("num_trades"), "dev_sharpe": dev.get("sharpe"),
                "dev_pnl": dev.get("total_pnl"),
                "holdout_trades": hold.get("num_trades"), "holdout_sharpe": hold.get("sharpe"),
                "holdout_pnl": hold.get("total_pnl"),
                "holdout_positive": bool(hold.get("total_pnl", 0) > 0),
                "is_grid_search_winner": (period == BEST["period"] and std == BEST["std_mult"]),
            })
    return rows


def temporal_stability(n_segments: int = 3) -> list:
    hold = backtest_bollinger_otm_reversal(
        **BEST, lots=10, start_date=SPLIT_DATE, verbose=False)
    trades = sorted(hold.get("trades", []), key=lambda t: t["entry_date"])
    n = len(trades)
    seg_size = max(1, n // n_segments)
    segments = []
    for i in range(n_segments):
        start = i * seg_size
        end = n if i == n_segments - 1 else (i + 1) * seg_size
        seg = trades[start:end]
        if not seg:
            continue
        pnls = np.array([t["pnl"] for t in seg])
        segments.append({
            "segment": i + 1, "n_trades": len(seg),
            "date_range": f"{seg[0]['entry_date']} to {seg[-1]['entry_date']}",
            "total_pnl": round(float(pnls.sum()), 2),
            "win_rate": round(float((pnls > 0).mean()), 4),
            "positive": bool(pnls.sum() > 0),
        })
    return segments


if __name__ == "__main__":
    print("=== Parameter plateau (9-point grid, dev + holdout) ===")
    plateau = parameter_plateau()
    n_holdout_positive = sum(1 for r in plateau if r["holdout_positive"])
    for r in plateau:
        flag = "★" if r["is_grid_search_winner"] else (" " if r["holdout_positive"] else "❌")
        print(f"{flag} period={r['period']:3d} std={r['std_mult']:.1f}  "
              f"dev_sharpe={r['dev_sharpe']:>6.2f}  "
              f"holdout_pnl={r['holdout_pnl']:>10,.0f}  holdout_sharpe={r['holdout_sharpe']:>6.2f}")
    print(f"\n{n_holdout_positive}/9 grid points are holdout-positive")

    print("\n=== Temporal stability (holdout split into 3 chronological thirds) ===")
    segs = temporal_stability()
    for s in segs:
        flag = "  " if s["positive"] else "❌"
        print(f"{flag} segment {s['segment']} ({s['date_range']}, n={s['n_trades']}): "
              f"pnl={s['total_pnl']:>10,.0f}  win_rate={s['win_rate']:.0%}")
    n_seg_positive = sum(1 for s in segs if s["positive"])
    print(f"\n{n_seg_positive}/{len(segs)} segments positive")

    Path("bollinger_reversal_robustness_report.json").write_text(
        json.dumps({"parameter_plateau": plateau, "temporal_stability": segs}, indent=2, default=str))
