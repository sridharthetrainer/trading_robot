import pandas as pd

import cross_sectional_factor_test as csf


def test_52_week_high_score_uses_only_pre_rebalance_prices():
    dates = pd.date_range("2025-01-01", periods=253, freq="D")
    prices = pd.Series([100.0] * 252 + [1000.0], index=dates)

    scores = csf._factor_scores({"TEST": prices}, dates[-1], "52_week_high")

    assert scores["TEST"] == 1.0


def test_low_vol_score_uses_only_pre_rebalance_prices():
    dates = pd.date_range("2025-01-01", periods=253, freq="D")
    prices = pd.Series([100.0] * 252 + [1000.0], index=dates)

    scores = csf._factor_scores({"TEST": prices}, dates[-1], "low_vol")

    assert scores["TEST"] == 0.0


def test_factor_candidate_requires_positive_holdout_confidence_bound():
    train = {"n": 20, "mean_monthly_pct": 1.0, "p": 0.001}
    holdout = {"n": 10, "mean_monthly_pct": 0.2, "return_lcb95_pct": -0.1}

    assert csf._verdict(train, holdout, bonferroni=1) != "CANDIDATE"
