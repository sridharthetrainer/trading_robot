"""Purged champion-challenger comparison for tabular signal classifiers."""

from __future__ import annotations

from typing import Any, Dict, Optional

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
    net_returns: Optional[np.ndarray] = None,
    min_utility_coverage: float = 0.05,
    min_utility_samples: int = 20,
) -> Dict[str, Any]:
    """Compare candidates using out-of-fold probabilities from purged folds.

    AUC/Brier choose statistically clean probability models; when
    cost-adjusted R-multiples are supplied, also score each candidate by the
    best out-of-fold probability threshold's average net R. This prevents a
    "good classifier" from becoming champion if its high-probability slice
    still loses money after costs.
    """
    from sklearn.base import clone
    from sklearn.metrics import log_loss, roc_auc_score
    from purged_cv import PurgedKFold

    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    returns = None
    if net_returns is not None:
        returns = np.asarray(net_returns, dtype=float)
        if len(returns) != len(y):
            returns = None
    leaderboard = []
    estimators = candidate_estimators(len(y), X.shape[1])
    splitter = PurgedKFold(n_splits=n_splits, horizon=horizon, embargo=embargo)

    for name, estimator in estimators.items():
        observed: list[int] = []
        probabilities: list[float] = []
        baselines: list[float] = []
        scored_returns: list[float] = []
        folds = 0
        for train, test in splitter.split(X, y):
            if len(np.unique(y[train])) < 2 or len(np.unique(y[test])) < 2:
                continue
            fitted = clone(estimator).fit(X[train], y[train])
            probability = np.clip(fitted.predict_proba(X[test])[:, 1], 1e-6, 1 - 1e-6)
            observed.extend(y[test].tolist())
            probabilities.extend(probability.tolist())
            baselines.extend([float(np.mean(y[train]))] * len(test))
            if returns is not None:
                scored_returns.extend(returns[test].tolist())
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
        utility = {
            "available": False,
            "best_threshold": None,
            "best_avg_net_r": None,
            "best_sum_net_r": None,
            "best_coverage": 0.0,
            "best_selected": 0,
            "baseline_avg_net_r": None,
        }
        if returns is not None and len(scored_returns) == len(predicted):
            ret = np.asarray(scored_returns, dtype=float)
            baseline_avg = float(np.nanmean(ret)) if len(ret) else 0.0
            best = None
            for threshold in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75):
                sel = predicted >= threshold
                n_sel = int(sel.sum())
                coverage = n_sel / max(len(predicted), 1)
                if n_sel < min_utility_samples or coverage < min_utility_coverage:
                    continue
                avg_r = float(np.nanmean(ret[sel]))
                sum_r = float(np.nansum(ret[sel]))
                row = {
                    "threshold": threshold,
                    "avg_net_r": avg_r,
                    "sum_net_r": sum_r,
                    "coverage": coverage,
                    "selected": n_sel,
                }
                if best is None or (avg_r, sum_r, coverage) > (
                    best["avg_net_r"], best["sum_net_r"], best["coverage"]
                ):
                    best = row
            if best is not None:
                utility = {
                    "available": True,
                    "best_threshold": round(float(best["threshold"]), 4),
                    "best_avg_net_r": round(float(best["avg_net_r"]), 6),
                    "best_sum_net_r": round(float(best["sum_net_r"]), 6),
                    "best_coverage": round(float(best["coverage"]), 6),
                    "best_selected": int(best["selected"]),
                    "baseline_avg_net_r": round(baseline_avg, 6),
                }
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
            "utility": utility,
        })

    eligible = [row for row in leaderboard if row.get("eligible")]
    # If cost-adjusted R is available, choose a model whose OOF high-probability
    # slice has the best after-cost expectancy. AUC/Brier remain tie-breakers.
    if any((row.get("utility") or {}).get("available") for row in eligible):
        eligible.sort(key=lambda row: (
            -float((row.get("utility") or {}).get("best_avg_net_r") or -999.0),
            -float((row.get("utility") or {}).get("best_sum_net_r") or -999.0),
            -row["auc"],
            row["brier"],
            row["log_loss"],
        ))
    else:
        # AUC is primary discrimination; Brier and log loss break close ties.
        eligible.sort(key=lambda row: (-row["auc"], row["brier"], row["log_loss"]))
    champion = eligible[0]["name"] if eligible else ""
    return {
        "champion": champion,
        "candidate_count": len(estimators),
        "leaderboard": eligible + [row for row in leaderboard if not row.get("eligible")],
        "estimator": clone(estimators[champion]) if champion else None,
    }
