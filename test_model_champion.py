import numpy as np


def test_champion_tournament_uses_purged_probabilistic_metrics():
    from model_champion import compare_candidates

    rng = np.random.default_rng(7)
    X = rng.normal(size=(360, 4))
    y = (1.4 * X[:, 0] - 0.6 * X[:, 1] + rng.normal(scale=0.8, size=360) > 0).astype(int)
    result = compare_candidates(X, y, n_splits=4, horizon=2, embargo=1)
    assert result["champion"] in {
        "logistic_l2", "gradient_boosting", "hist_gradient_boosting", "random_forest"
    }
    assert result["candidate_count"] == 4
    champion = result["leaderboard"][0]
    assert champion["folds"] >= 2
    assert champion["auc"] > 0.70
    assert champion["brier_skill"] > 0


def test_candidate_feature_selection_is_fold_local_pipeline_step():
    from model_champion import candidate_estimators

    candidates = candidate_estimators(500, 60)
    for estimator in candidates.values():
        assert "select" in estimator.named_steps
        assert estimator.named_steps["select"].k == 30


def test_champion_tournament_prefers_after_cost_utility_when_available():
    from model_champion import compare_candidates

    rng = np.random.default_rng(11)
    X = rng.normal(size=(420, 6))
    y = (X[:, 0] + rng.normal(scale=0.9, size=420) > 0).astype(int)
    # Synthetic after-cost R: positive labels usually pay, but not uniformly.
    # The assertion is not about a specific algorithm; it verifies the tournament
    # computes a usable OOF utility block and sorts by it when supplied.
    net_r = np.where(y == 1, 0.8 + 0.2 * X[:, 1], -0.6 - 0.1 * np.abs(X[:, 2]))
    result = compare_candidates(
        X, y, n_splits=4, horizon=2, embargo=1, net_returns=net_r,
        min_utility_samples=10,
    )
    champion = result["leaderboard"][0]
    utility = champion["utility"]
    assert utility["available"] is True
    assert utility["best_selected"] >= 10
    assert utility["best_avg_net_r"] > utility["baseline_avg_net_r"]
