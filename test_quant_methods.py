"""Contract tests for the LdP / quant-method additions (#2-#6)."""
import numpy as np


# ── #2 sample-uniqueness weighting ────────────────────────────────────────────
def test_sample_weights_downweight_overlapping_labels():
    from sample_weights import sample_weights, average_uniqueness
    # 5 heavily-overlapping labels at the start (horizon 4) + 5 spread-out → the
    # overlapping ones should be LESS unique (lower weight) than isolated ones.
    starts = [0, 1, 2, 3, 4, 40, 50, 60, 70, 80]
    w = sample_weights(starts, horizon=4, n_bars=100)
    assert abs(w.mean() - 1.0) < 1e-9            # normalised to mean 1
    assert np.mean(w[:5]) < np.mean(w[5:])       # clustered labels down-weighted
    u = average_uniqueness(starts, horizon=4, n_bars=100)
    assert (u > 0).all() and (u <= 1.0 + 1e-9).all()


# ── #3 PBO via CSCV ───────────────────────────────────────────────────────────
def test_pbo_low_for_genuinely_best_config():
    from pbo import probability_of_backtest_overfitting
    rng = np.random.default_rng(0)
    T, N = 200, 10
    M = rng.normal(scale=0.01, size=(T, N))
    M[:, 0] += 0.02                               # config 0 truly best everywhere
    out = probability_of_backtest_overfitting(M, n_splits=8)
    assert out["n_combinations"] > 0
    assert out["pbo"] < 0.2                       # robust → low PBO

def test_pbo_high_for_pure_noise():
    from pbo import probability_of_backtest_overfitting
    rng = np.random.default_rng(1)
    M = rng.normal(size=(200, 12))                # no real winner → overfit selection
    out = probability_of_backtest_overfitting(M, n_splits=8)
    assert out["pbo"] > 0.3


# ── #4 bet sizing from probability ────────────────────────────────────────────
def test_bet_size_monotonic_and_zero_at_chance():
    from bet_sizing import bet_size_from_prob, qty_from_prob
    assert abs(bet_size_from_prob(0.5)) < 1e-9            # chance → no bet
    assert bet_size_from_prob(0.9) > bet_size_from_prob(0.6) > 0
    assert bet_size_from_prob(0.9, pred=-1) < 0           # short side negative
    assert qty_from_prob(0.5, max_qty=10) == 0
    assert 0 < qty_from_prob(0.7, max_qty=10) <= 10


# ── #5 conformal + MDA ────────────────────────────────────────────────────────
def test_conformal_abstains_when_unsure():
    from model_quality import conformal_qhat, conformal_accept
    rng = np.random.default_rng(0)
    n = 200
    # under-confident model: true-class prob ~0.25..0.55 → high nonconformity →
    # high qhat → wide prediction sets → abstain on a marginal 0.55 call.
    true_p = rng.uniform(0.25, 0.55, n)
    cal_y = rng.integers(0, 2, n)
    cal_proba = np.zeros((n, 2))
    cal_proba[np.arange(n), cal_y] = true_p
    cal_proba[np.arange(n), 1 - cal_y] = 1 - true_p
    qhat = conformal_qhat(cal_proba, cal_y, alpha=0.1)
    assert qhat > 0.55
    assert not conformal_accept([0.45, 0.55], qhat, act_class=1)   # both classes in set → abstain
    # well-calibrated, high-confidence calibration → low qhat → a 0.98 call is accepted
    conf_p = rng.uniform(0.90, 0.99, n)
    cp = np.column_stack([1 - conf_p, conf_p])
    qhat2 = conformal_qhat(cp, np.ones(n, int), alpha=0.1)
    assert conformal_accept([0.02, 0.98], qhat2, act_class=1)

def test_mda_ranks_the_informative_feature_top():
    from model_quality import mda_importance
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(0)
    n = 400
    X = rng.normal(size=(n, 3))
    y = (X[:, 0] + rng.normal(scale=0.3, size=n) > 0).astype(int)  # only f0 matters
    imp = mda_importance(LogisticRegression(max_iter=200), X, y,
                         ["f0", "f1", "f2"], n_splits=4, horizon=2, n_repeats=2)
    assert imp[0]["feature"] == "f0"              # informative feature ranks first
    assert imp[0]["importance"] > 0


# ── #6 cross-sectional ranking ────────────────────────────────────────────────
def test_cross_sectional_rank_and_longshort():
    from cross_sectional import cross_sectional_rank, long_short_candidates, momentum_scores
    vals = {"A": 0.05, "B": -0.02, "C": 0.01, "D": -0.10, "E": 0.08}
    r = cross_sectional_rank(vals)
    assert r["E"] == 1.0 and r["D"] == 0.0        # strongest=1, weakest=0
    ls = long_short_candidates(vals, top_frac=0.2)
    assert ls["longs"][0] == "E" and ls["shorts"][0] == "D"
    mom = momentum_scores({"X": [100, 101, 102, 110]}, lookback=3)
    assert abs(mom["X"] - 0.10) < 1e-9
