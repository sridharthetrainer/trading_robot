"""Purged champion-challenger comparison for tabular signal classifiers."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np


def candidate_estimators(n_samples: int, n_features: int) -> Dict[str, Any]:
    """Small, diverse model set; breadth without an unbounded search space."""
    from sklearn.ensemble import (
        GradientBoostingClassifier,
        HistGradientBoostingClassifier,
        RandomForestClassifier,
    )
    from sklearn.linear_model import LogisticRegression
    from sklearn.feature_selection import SelectKBest, f_classif
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    min_leaf = max(20, int(n_samples * 0.01))
    keep = min(30, max(1, int(n_features)))
    selector = lambda: SelectKBest(score_func=f_classif, k=keep)
    return {
        "logistic_l2": Pipeline([
            ("select", selector()),
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                C=0.5, class_weight="balanced", max_iter=1000, random_state=42,
            )),
        ]),
        "gradient_boosting": Pipeline([
            ("select", selector()),
            ("clf", GradientBoostingClassifier(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                min_samples_leaf=min_leaf, subsample=0.8, random_state=42,
            )),
        ]),
        "hist_gradient_boosting": Pipeline([
            ("select", selector()),
            ("clf", HistGradientBoostingClassifier(
                max_iter=200, max_depth=4, learning_rate=0.05,
                min_samples_leaf=min_leaf, l2_regularization=1.0, random_state=42,
            )),
        ]),
        "random_forest": Pipeline([
            ("select", selector()),
            ("clf", RandomForestClassifier(
                n_estimators=300, max_depth=6, min_samples_leaf=min_leaf,
                max_features="sqrt", class_weight="balanced_subsample",
                random_state=42, n_jobs=-1,
            )),
        ]),
    }


def compare_candidates(
    X: np.ndarray,
    y: np.ndarray,
    *,
    n_splits: int = 5,
    horizon: int = 12,
    embargo: int = 3,
) -> Dict[str, Any]:
    """Compare candidates using out-of-fold probabilities from purged folds."""
    from sklearn.base import clone
    from sklearn.metrics import log_loss, roc_auc_score
    from purged_cv import PurgedKFold

    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    leaderboard = []
    estimators = candidate_estimators(len(y), X.shape[1])
    splitter = PurgedKFold(n_splits=n_splits, horizon=horizon, embargo=embargo)

    for name, estimator in estimators.items():
        observed: list[int] = []
        probabilities: list[float] = []
        baselines: list[float] = []
        folds = 0
        for train, test in splitter.split(X, y):
            if len(np.unique(y[train])) < 2 or len(np.unique(y[test])) < 2:
                continue
            fitted = clone(estimator).fit(X[train], y[train])
            probability = np.clip(fitted.predict_proba(X[test])[:, 1], 1e-6, 1 - 1e-6)
            observed.extend(y[test].tolist())
            probabilities.extend(probability.tolist())
            baselines.extend([float(np.mean(y[train]))] * len(test))
            folds += 1
        if folds < 2 or len(set(observed)) < 2:
            leaderboard.append({"name": name, "eligible": False, "reason": "insufficient_folds"})
            continue
        actual = np.asarray(observed, dtype=int)
        predicted = np.asarray(probabilities, dtype=float)
        baseline = np.asarray(baselines, dtype=float)
        brier = float(np.mean((predicted - actual) ** 2))
        baseline_brier = float(np.mean((baseline - actual) ** 2))
        auc = float(roc_auc_score(actual, predicted))
        leaderboard.append({
            "name": name,
            "eligible": True,
            "folds": folds,
            "samples_scored": len(actual),
            "auc": round(auc, 6),
            "brier": round(brier, 6),
            "baseline_brier": round(baseline_brier, 6),
            "brier_skill": round(1.0 - brier / baseline_brier, 6) if baseline_brier > 0 else -1.0,
            "log_loss": round(float(log_loss(actual, predicted, labels=[0, 1])), 6),
        })

    eligible = [row for row in leaderboard if row.get("eligible")]
    # AUC is primary discrimination; Brier and log loss break close ties.
    eligible.sort(key=lambda row: (-row["auc"], row["brier"], row["log_loss"]))
    champion = eligible[0]["name"] if eligible else ""
    return {
        "champion": champion,
        "candidate_count": len(estimators),
        "leaderboard": eligible + [row for row in leaderboard if not row.get("eligible")],
        "estimator": clone(estimators[champion]) if champion else None,
    }
