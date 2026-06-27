"""Contract tests for PurgedKFold: no train sample's label window leaks into test."""
import numpy as np
from purged_cv import PurgedKFold, purged_cv_score


def test_no_overlap_between_train_label_window_and_test():
    n, horizon, embargo = 100, 5, 3
    cv = PurgedKFold(n_splits=5, horizon=horizon, embargo=embargo)
    X = np.zeros((n, 2))
    for train, test in cv.split(X):
        a, b = test[0], test[-1]
        # every train index's label window [i, i+horizon] must NOT overlap [a,b],
        # and train after the fold must respect the embargo
        for i in train:
            assert not (i + horizon >= a and i <= b), f"purge leak: train {i} overlaps test [{a},{b}]"
            assert not (b < i <= b + horizon + embargo), f"embargo leak: train {i} too close after [{a},{b}]"
        assert set(train).isdisjoint(set(test))


def test_covers_all_test_indices_once():
    n = 60
    cv = PurgedKFold(n_splits=6, horizon=2)
    X = np.zeros((n, 1))
    seen = []
    for _, test in cv.split(X):
        seen.extend(test.tolist())
    assert sorted(seen) == list(range(n))   # every row tested exactly once


def test_purged_cv_score_runs_with_a_real_model():
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(0)
    n = 300
    X = rng.normal(size=(n, 4))
    y = (X[:, 0] + rng.normal(scale=0.5, size=n) > 0).astype(int)  # learnable signal
    out = purged_cv_score(LogisticRegression(max_iter=200), X, y,
                          n_splits=5, horizon=3, embargo=2, scoring="roc_auc")
    assert out["n_splits_used"] >= 3
    assert 0.5 < out["mean"] <= 1.0      # real signal → AUC clearly above chance
