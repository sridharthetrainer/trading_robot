"""
pbo.py — Probability of Backtest Overfitting via CSCV (López de Prado, AFML ch.11).

WHY: with many strategy/parameter configs, the best in-sample one is often the
luckiest, not the best — and underperforms out-of-sample. PBO quantifies exactly
that: across all symmetric in-sample/out-of-sample splits, how often does the
config that looked best IS end up below the median OOS? PBO near 0 = robust
selection; PBO near 0.5+ = your selection process is overfit. Complements the
Deflated Sharpe Ratio with an independent overfitting estimate.

Pure numpy + itertools.
"""
from __future__ import annotations

import itertools
import numpy as np


def probability_of_backtest_overfitting(perf_matrix, n_splits: int = 10) -> dict:
    """
    perf_matrix : array (T observations × N configs) of per-period performance
                  (e.g. returns or per-bar R). Higher = better.
    n_splits    : even number of row-groups S for CSCV.

    Returns {pbo, n_combinations, median_logit, n_configs}.
    pbo = fraction of train/test splits where the best-IS config ranks at or below
    the OOS median (logit <= 0).
    """
    M = np.asarray(perf_matrix, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2:
        return {"pbo": float("nan"), "n_combinations": 0, "median_logit": float("nan"),
                "n_configs": int(M.shape[1] if M.ndim == 2 else 0)}
    S = int(n_splits)
    if S % 2:
        S += 1
    T, N = M.shape
    S = min(S, T)
    if S < 2:
        return {"pbo": float("nan"), "n_combinations": 0, "median_logit": float("nan"),
                "n_configs": N}

    groups = np.array_split(np.arange(T), S)
    logits = []
    for train_groups in itertools.combinations(range(S), S // 2):
        tr = np.concatenate([groups[g] for g in train_groups])
        te = np.concatenate([groups[g] for g in range(S) if g not in train_groups])
        is_perf = M[tr].mean(axis=0)        # per-config IS performance
        oos_perf = M[te].mean(axis=0)       # per-config OOS performance
        n_star = int(np.argmax(is_perf))    # best config in-sample
        # relative rank of n_star OOS, in (0,1)
        order = oos_perf.argsort()          # ascending
        rank = int(np.where(order == n_star)[0][0]) + 1
        w = rank / (N + 1.0)
        w = min(max(w, 1e-6), 1 - 1e-6)
        logits.append(np.log(w / (1.0 - w)))

    logits = np.array(logits, dtype=float)
    if logits.size == 0:
        return {"pbo": float("nan"), "n_combinations": 0, "median_logit": float("nan"),
                "n_configs": N}
    pbo = float(np.mean(logits <= 0.0))
    return {
        "pbo": pbo,
        "n_combinations": int(logits.size),
        "median_logit": float(np.median(logits)),
        "n_configs": N,
    }
