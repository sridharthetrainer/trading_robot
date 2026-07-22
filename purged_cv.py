"""
purged_cv.py — Purged K-Fold cross-validation with embargo (López de Prado, AFML ch.7),
plus Combinatorial Purged CV (AFML ch.12).

WHY: plain K-Fold / TimeSeriesSplit leak in financial ML because a sample's label
spans a forward HORIZON (triple-barrier looks ahead up to `max_bars`). If a train
sample's label window overlaps the test fold, the model peeks at test-period
outcomes → inflated CV scores. This repo saw AUC swing 0.5↔0.74 just by changing
the splitter — that swing IS the leakage. PurgedKFold removes train samples whose
label window overlaps the test fold and embargoes a gap after each fold, giving a
CV number you can actually trust.

WHY CPCV TOO (2026-07-22, external-review follow-up): a single PurgedKFold config
(n_splits/horizon/embargo) is itself just one particular partition of the
timeline into folds — this repo saw the mean AUC swing by ~0.09 (0.59→0.68) just
from changing n_splits=5→3 and horizon/embargo, with no way to tell whether that
swing was a better/worse config or just which specific rows happened to land in
which fold. CombinatorialPurgedKFold fixes the block boundaries once (n_groups)
and then evaluates EVERY combination of holding out n_test_groups of them as the
test set, purging/embargoing around each held-out block — giving C(n_groups,
n_test_groups) independent train/test paths from the same partition instead of
just n_groups of them, so the reported spread reflects genuine path-to-path
variance rather than one arbitrary partition choice.

Pure (numpy only), sklearn-compatible (.split / .get_n_splits) so it drops into
cross_val_score.
"""
from __future__ import annotations

from itertools import combinations
from math import comb

import numpy as np


class PurgedKFold:
    """Contiguous (non-shuffled) K-Fold for fixed-horizon labels.

    Each row i is assumed to carry a label spanning rows [i, i+horizon]. For each
    contiguous test fold [a, b], training rows in [a-horizon, b+horizon+embargo]
    are PURGED (overlap) / EMBARGOED (serial correlation after the fold).

    Parameters
    ----------
    n_splits : number of folds (>=2)
    horizon  : forward label span in rows (e.g. triple-barrier max_bars)
    embargo  : extra rows dropped after each test fold (serial-correlation gap)
    """

    def __init__(self, n_splits: int = 5, horizon: int = 1, embargo: int = 0):
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        self.n_splits = int(n_splits)
        self.horizon = max(0, int(horizon))
        self.embargo = max(0, int(embargo))

    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        return self.n_splits

    def split(self, X, y=None, groups=None):
        n = len(X)
        if n < self.n_splits:
            raise ValueError("fewer samples than folds")
        idx = np.arange(n)
        for fold in np.array_split(idx, self.n_splits):
            a, b = int(fold[0]), int(fold[-1])
            test = idx[a:b + 1]
            lo = a - self.horizon                 # train labels ending before fold
            hi = b + self.horizon + self.embargo  # purge overlap + embargo after
            train = idx[(idx < lo) | (idx > hi)]
            if len(train) == 0 or len(test) == 0:
                continue
            yield train, test


def purged_cv_score(estimator, X, y, *, n_splits: int = 5, horizon: int = 12,
                    embargo: int = 0, scoring: str = "roc_auc") -> dict:
    """Cross-validate with PurgedKFold and return {mean, std, folds, n_splits_used}.
    Skips degenerate folds (single-class y) so AUC stays defined. numpy/sklearn only."""
    from sklearn.base import clone
    from sklearn.metrics import get_scorer

    X = np.asarray(X)
    y = np.asarray(y)
    scorer = get_scorer(scoring)
    cv = PurgedKFold(n_splits=n_splits, horizon=horizon, embargo=embargo)
    scores = []
    for train, test in cv.split(X, y):
        if len(np.unique(y[train])) < 2 or len(np.unique(y[test])) < 2:
            continue  # AUC undefined on a single-class fold
        est = clone(estimator)
        est.fit(X[train], y[train])
        try:
            scores.append(float(scorer(est, X[test], y[test])))
        except Exception:
            continue
    if not scores:
        return {"mean": float("nan"), "std": float("nan"), "folds": [], "n_splits_used": 0}
    return {
        "mean": float(np.mean(scores)),
        "std": float(np.std(scores)),
        "folds": [round(s, 4) for s in scores],
        "n_splits_used": len(scores),
    }


class CombinatorialPurgedKFold:
    """Combinatorial Purged K-Fold (López de Prado, AFML ch.12).

    Splits the timeline into `n_groups` contiguous blocks ONCE, then evaluates
    every combination of choosing `n_test_groups` of them as the test set —
    C(n_groups, n_test_groups) paths total — purging/embargoing training rows
    around EACH held-out block (same rule as PurgedKFold, applied per-block).
    This answers "how much does the AUC estimate vary across which rows land
    in the test set", not just "what is the AUC for one particular partition".

    Parameters
    ----------
    n_groups      : number of contiguous blocks to divide the timeline into (>=3)
    n_test_groups : how many of those blocks form the test set each path (1..n_groups-1)
    horizon       : forward label span in rows (e.g. triple-barrier max_bars)
    embargo       : extra rows dropped after each held-out block
    """

    def __init__(self, n_groups: int = 6, n_test_groups: int = 2,
                 horizon: int = 1, embargo: int = 0):
        if n_groups < 3:
            raise ValueError("n_groups must be >= 3")
        if not (1 <= n_test_groups < n_groups):
            raise ValueError("n_test_groups must be between 1 and n_groups-1")
        self.n_groups = int(n_groups)
        self.n_test_groups = int(n_test_groups)
        self.horizon = max(0, int(horizon))
        self.embargo = max(0, int(embargo))

    def n_paths(self) -> int:
        return comb(self.n_groups, self.n_test_groups)

    def split(self, X, y=None, groups=None):
        n = len(X)
        if n < self.n_groups:
            raise ValueError("fewer samples than groups")
        idx = np.arange(n)
        blocks = np.array_split(idx, self.n_groups)
        bounds = [(int(b[0]), int(b[-1])) for b in blocks]
        for test_group_ids in combinations(range(self.n_groups), self.n_test_groups):
            test = np.concatenate([blocks[g] for g in test_group_ids])
            keep = np.ones(n, dtype=bool)
            for g in test_group_ids:
                a, b = bounds[g]
                lo, hi = a - self.horizon, b + self.horizon + self.embargo
                keep &= ~((idx >= lo) & (idx <= hi))
            train = idx[keep]
            if len(train) == 0 or len(test) == 0:
                continue
            yield train, test


def cpcv_score(estimator, X, y, *, n_groups: int = 6, n_test_groups: int = 2,
               horizon: int = 12, embargo: int = 0,
               scoring: str = "roc_auc") -> dict:
    """Cross-validate with CombinatorialPurgedKFold over ALL C(n_groups,
    n_test_groups) paths and return the full distribution (mean/median/std/
    min/max/iqr), not just a single point estimate. Skips degenerate
    (single-class) paths. Same skip/clone/score pattern as purged_cv_score."""
    from sklearn.base import clone
    from sklearn.metrics import get_scorer

    X = np.asarray(X)
    y = np.asarray(y)
    scorer = get_scorer(scoring)
    cv = CombinatorialPurgedKFold(n_groups=n_groups, n_test_groups=n_test_groups,
                                   horizon=horizon, embargo=embargo)
    scores = []
    for train, test in cv.split(X, y):
        if len(np.unique(y[train])) < 2 or len(np.unique(y[test])) < 2:
            continue
        est = clone(estimator)
        est.fit(X[train], y[train])
        try:
            scores.append(float(scorer(est, X[test], y[test])))
        except Exception:
            continue
    if not scores:
        return {"mean": float("nan"), "median": float("nan"), "std": float("nan"),
                "min": float("nan"), "max": float("nan"), "iqr": float("nan"),
                "paths": [], "n_paths_used": 0, "n_paths_total": cv.n_paths()}
    arr = np.array(scores)
    q75, q25 = np.percentile(arr, [75, 25])
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "iqr": float(q75 - q25),
        "paths": [round(s, 4) for s in scores],
        "n_paths_used": len(scores),
        "n_paths_total": cv.n_paths(),
    }
