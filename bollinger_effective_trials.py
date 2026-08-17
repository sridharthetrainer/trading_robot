"""
bollinger_effective_trials.py — effective number of independent trials for
the bollinger_otm_reversal 9-point parameter grid, replacing the raw grid
size (N=9) fed into deflated_sharpe_ratio() in seminar_param_search.py.

Per external review: 9 correlated parameter variants (period in {12,14,16},
std_mult in {1.4,1.5,1.7}) are not 9 independent bets -- neighboring points
trade the same underlying signal on largely the same days. The principled
fix (Li & Ji 2005 / Nyholt-style effective-number-of-tests, built from the
eigenvalues of the trials' return-correlation matrix) is used here rather
than guessing an integer or leaving N=9 unadjusted.

Method:
  1. Run each of the 9 grid points on the SAME dev period seminar_param_
     search.py used (end_date=SPLIT_DATE), get its trades.
  2. Aggregate each to a DAILY P&L series over the dev period's trading days
     (0 on days that combo didn't trade) -- a common time index is required
     to correlate trials fairly against each other.
  3. Build the 9x9 correlation matrix across those daily series, eigen-
     decompose it.
  4. N_eff = N^2 / sum(eigenvalues^2) -- since eigenvalues of a correlation
     matrix always sum to N (trace = N, diagonal is all 1s), this reduces to
     the participation-ratio formula: fully correlated trials collapse
     toward N_eff=1, fully independent trials stay near N_eff=N.
  5. Recompute DSR with N_eff instead of the raw grid size 9, report both.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_bollinger_otm_reversal import backtest_bollinger_otm_reversal
from validation_harness import deflated_sharpe_ratio

SPLIT_DATE = "2026-05-19"
PERIOD_GRID = [12, 14, 16]
STD_GRID = [1.4, 1.5, 1.7]
LOTS = 10


def _daily_pnl_series(trades: list) -> pd.Series:
    if not trades:
        return pd.Series(dtype=float)
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    return df.groupby("entry_date")["pnl"].sum()


def run() -> dict:
    daily_series = {}
    dev_stats = {}
    for period in PERIOD_GRID:
        for std in STD_GRID:
            key = f"{period}_{std}"
            r = backtest_bollinger_otm_reversal(
                period=period, std_mult=std, lots=LOTS, end_date=SPLIT_DATE, verbose=False)
            daily_series[key] = _daily_pnl_series(r.get("trades", []))
            dev_stats[key] = {"num_trades": r.get("num_trades"), "sharpe": r.get("sharpe")}

    # Common daily index across all 9 trials (union of all trading days any
    # combo traded on), 0-filled where a given combo didn't trade that day.
    all_days = sorted(set().union(*[s.index for s in daily_series.values()]))
    matrix = pd.DataFrame(0.0, index=all_days, columns=list(daily_series.keys()))
    for key, s in daily_series.items():
        matrix.loc[s.index, key] = s.values

    corr = matrix.corr().values
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)
    eigenvalues = np.linalg.eigvalsh(corr)
    eigenvalues = np.clip(eigenvalues, 0, None)  # numerical noise can give tiny negatives

    n_raw = len(daily_series)
    n_eff = float((eigenvalues.sum() ** 2) / (eigenvalues ** 2).sum())

    # Recompute DSR for the actual grid-search winner (period=14, std=1.5)
    # using n_eff instead of the raw grid size.
    winner_key = "14_1.5"
    winner_stats = dev_stats[winner_key]
    dsr_raw_n = deflated_sharpe_ratio(
        sr=winner_stats["sharpe"], n_trades=winner_stats["num_trades"], n_trials=n_raw)
    dsr_eff_n = deflated_sharpe_ratio(
        sr=winner_stats["sharpe"], n_trades=winner_stats["num_trades"], n_trials=round(n_eff))

    report = {
        "n_raw_trials": n_raw,
        "n_effective_trials": round(n_eff, 2),
        "eigenvalues": [round(float(e), 4) for e in sorted(eigenvalues, reverse=True)],
        "mean_pairwise_correlation": round(float((corr.sum() - n_raw) / (n_raw * (n_raw - 1))), 4),
        "winner": winner_key,
        "winner_sharpe": winner_stats["sharpe"],
        "winner_n_trades": winner_stats["num_trades"],
        "dsr_with_raw_n9": round(dsr_raw_n, 4),
        "dsr_with_effective_n": round(dsr_eff_n, 4),
        "dev_stats": dev_stats,
    }
    Path("bollinger_effective_trials_report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    rep = run()
    print(f"N_raw={rep['n_raw_trials']}  N_effective={rep['n_effective_trials']}  "
          f"mean_pairwise_corr={rep['mean_pairwise_correlation']}")
    print(f"DSR (winner={rep['winner']}, sharpe={rep['winner_sharpe']}, n={rep['winner_n_trades']}):")
    print(f"  with raw N=9        : {rep['dsr_with_raw_n9']}")
    print(f"  with effective N={rep['n_effective_trials']:.1f}: {rep['dsr_with_effective_n']}")
