"""
purged_cv.py — Purged K-Fold cross-validation with embargo (López de Prado, AFML ch.7).

WHY: plain K-Fold / TimeSeriesSplit leak in financial ML because a sample's label
spans a forward HORIZON (triple-barrier looks ahead up to `max_bars`). If a train
sample's label window overlaps the test fold, the model peeks at test-period
outcomes → inflated CV scores. This repo saw AUC swing 0.5↔0.74 just by changing
the splitter — that swing IS the leakage. PurgedKFold removes train samples whose
label window overlaps the test fold and embargoes a gap after each fold, giving a
CV number you can actually trust.

Pure (numpy only), sklearn-compatible (.split / .get_n_splits) so it drops into
cross_val_score.
"""
from __future__ import annotations

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
