"""
model_quality.py — two refinements that improve trade quality (not raw profit):

1. Split-conformal classification (calibrated abstention): only ACT when the model
   is provably confident at level alpha; otherwise abstain. Turns a raw probability
   into a "trade / skip" decision with a coverage guarantee — fewer low-quality bets.

2. MDA (Mean-Decrease-Accuracy) permutation feature importance, evaluated under
   PurgedKFold so it doesn't leak. Surfaces which features actually carry signal so
   the many edgeless modifiers can be pruned ("reduce, don't add").

Pure numpy + sklearn; reuses purged_cv.
"""
from __future__ import annotations

import numpy as np


def conformal_qhat(cal_proba, cal_y, alpha: float = 0.1) -> float:
    """Nonconformity threshold from a calibration set (split conformal).
    cal_proba: (n, n_classes); cal_y: (n,) int labels. Returns qhat."""
    P = np.asarray(cal_proba, dtype=float)
    y = np.asarray(cal_y, dtype=int)
    n = len(y)
    if n == 0:
        return 1.0
    scores = 1.0 - P[np.arange(n), y]                 # nonconformity of true class
    level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))


def conformal_prediction_set(proba_row, qhat: float):
    """Classes whose nonconformity (1-p) is within qhat. A singleton = confident."""
    row = np.asarray(proba_row, dtype=float)
    return [int(c) for c in range(len(row)) if (1.0 - row[c]) <= qhat]


def conformal_accept(proba_row, qhat: float, act_class: int = 1) -> bool:
    """True only when the prediction set is exactly {act_class} (confident & actionable)."""
    return conformal_prediction_set(proba_row, qhat) == [int(act_class)]


def mda_importance(estimator, X, y, feature_names=None, *, n_splits: int = 5,
                   horizon: int = 12, embargo: int = 0, scoring: str = "roc_auc",
                   n_repeats: int = 3, random_state: int = 0) -> list:
    """Permutation importance under PurgedKFold. Returns a list of
    {feature, importance, std} sorted desc (importance = mean score drop when the
    feature is shuffled). Positive = the feature helps; ~0 / negative = noise."""
    from sklearn.base import clone
    from sklearn.metrics import get_scorer
    from purged_cv import PurgedKFold

    X = np.asarray(X, dtype=float)
    y = np.asarray(y)
    n_feat = X.shape[1]
    names = list(feature_names) if feature_names is not None else [f"f{i}" for i in range(n_feat)]
    scorer = get_scorer(scoring)
    rng = np.random.default_rng(random_state)
    cv = PurgedKFold(n_splits=n_splits, horizon=horizon, embargo=embargo)

    drops = {j: [] for j in range(n_feat)}
    for train, test in cv.split(X, y):
        if len(np.unique(y[train])) < 2 or len(np.unique(y[test])) < 2:
            continue
        est = clone(estimator).fit(X[train], y[train])
        try:
            base = float(scorer(est, X[test], y[test]))
        except Exception:
            continue
        for j in range(n_feat):
            for _ in range(n_repeats):
                Xp = X[test].copy()
                Xp[:, j] = rng.permutation(Xp[:, j])
                try:
                    drops[j].append(base - float(scorer(est, Xp, y[test])))
                except Exception:
                    pass
    out = []
    for j in range(n_feat):
        d = drops[j]
        out.append({"feature": names[j],
                    "importance": float(np.mean(d)) if d else 0.0,
                    "std": float(np.std(d)) if d else 0.0})
    return sorted(out, key=lambda r: r["importance"], reverse=True)
