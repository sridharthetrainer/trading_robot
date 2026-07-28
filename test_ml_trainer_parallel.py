import numpy as np
import pandas as pd

import ml_trainer


def test_train_one_symbol_returns_none_on_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ValueError("synthetic failure")
    monkeypatch.setattr(ml_trainer, "_train_model", _raise)

    symbol, result = ml_trainer._train_one_symbol(
        "BADSYM", np.zeros((10, 3), dtype=np.float32), np.array([0, 1] * 5),
        ["f1", "f2", "f3"], sym_days=10, sym_n=10, fingerprint="abc123",
    )
    assert symbol == "BADSYM"
    assert result is None


def test_train_one_symbol_sets_promotion_fields_on_success(monkeypatch):
    fake_model_result = {
        "label": "NIFTY", "model": object(),
        "cv_method": "purged_kfold", "cv_auc_mean": 0.60,
        "purged_brier_skill": 0.05, "probability_calibration": "sigmoid_purged_cv",
        # Promotion is fail-closed on after-cost utility: it must be proven
        # available AND positive, not merely "not proven negative".
        "profit_utility": {"available": True, "best_avg_net_r": 0.10},
    }
    monkeypatch.setattr(ml_trainer, "_train_model", lambda *a, **kw: dict(fake_model_result))
    monkeypatch.setattr(ml_trainer, "MIN_PROMOTION_SAMPLES", 50)
    monkeypatch.setattr(ml_trainer, "MIN_PROMOTION_DAYS", 5)
    monkeypatch.setattr(ml_trainer, "MIN_PROMOTION_AUC", 0.55)

    symbol, result = ml_trainer._train_one_symbol(
        "NIFTY", np.zeros((60, 2), dtype=np.float32), np.array([0, 1] * 30),
        ["f1", "f2"], sym_days=6, sym_n=60, fingerprint="fp_nifty",
    )
    assert symbol == "NIFTY"
    assert result["promoted"] is True
    assert result["distinct_days"] == 6
    assert result["training_data_fingerprint"] == "fp_nifty"
    assert result["selected_features"] == ["f1", "f2"]


def test_train_one_symbol_not_promoted_when_utility_unavailable(monkeypatch):
    """Fail-closed regression: a model that clears every other promotion gate
    but has no proven after-cost utility (available=False, e.g. too few
    high-confidence samples) must NOT be promoted. Previously this case
    bypassed the check entirely."""
    fake_model_result = {
        "label": "NIFTY", "model": object(),
        "cv_method": "purged_kfold", "cv_auc_mean": 0.60,
        "purged_brier_skill": 0.05, "probability_calibration": "sigmoid_purged_cv",
        "profit_utility": {"available": False, "best_avg_net_r": None},
    }
    monkeypatch.setattr(ml_trainer, "_train_model", lambda *a, **kw: dict(fake_model_result))
    monkeypatch.setattr(ml_trainer, "MIN_PROMOTION_SAMPLES", 50)
    monkeypatch.setattr(ml_trainer, "MIN_PROMOTION_DAYS", 5)
    monkeypatch.setattr(ml_trainer, "MIN_PROMOTION_AUC", 0.55)

    _, result = ml_trainer._train_one_symbol(
        "NIFTY", np.zeros((60, 2), dtype=np.float32), np.array([0, 1] * 30),
        ["f1", "f2"], sym_days=6, sym_n=60, fingerprint="fp_nifty",
    )
    assert result["promoted"] is False


def test_train_one_symbol_not_promoted_below_threshold(monkeypatch):
    fake_model_result = {
        "label": "SMALLCAP", "model": object(),
        "cv_method": "purged_kfold", "cv_auc_mean": 0.60,
        "purged_brier_skill": 0.05, "probability_calibration": "sigmoid_purged_cv",
    }
    monkeypatch.setattr(ml_trainer, "_train_model", lambda *a, **kw: dict(fake_model_result))

    symbol, result = ml_trainer._train_one_symbol(
        "SMALLCAP", np.zeros((10, 2), dtype=np.float32), np.array([0, 1] * 5),
        ["f1", "f2"], sym_days=2, sym_n=10, fingerprint="fp_small",
    )
    assert result["promoted"] is False  # below MIN_PROMOTION_SAMPLES/DAYS


def _fast_fake_train_model(X, y, feature_names, label="cross_symbol", net_returns=None):
    """Stands in for the real _train_model (which runs a multi-candidate
    sklearn tournament + MDA importance -- correct but too slow for a unit
    test, and its own runtime isn't what this test is verifying). Returns
    the same shape of dict _train_model would, cheaply."""
    return {
        "label": label, "model": f"fake_model_{label}",
        "cv_method": "purged_kfold", "cv_auc_mean": 0.60,
        "purged_brier_skill": 0.05, "probability_calibration": "sigmoid_purged_cv",
        "profit_utility": {
            "available": net_returns is not None,
            "best_avg_net_r": 0.10 if net_returns is not None else None,
        },
        "n_samples": len(y), "feature_importances": [],
    }


def _make_synthetic_df(symbols_days: dict, n_features: int = 6, rows_per_day: int = 8) -> pd.DataFrame:
    rng = np.random.RandomState(42)
    rows = []
    feat_cols = [f"f{i}" for i in range(n_features)]
    for symbol, n_days in symbols_days.items():
        for day in range(n_days):
            for _ in range(rows_per_day):
                feats = rng.normal(size=n_features)
                # outcome weakly correlated with f0 so the classifier has
                # something non-degenerate to fit, without asserting on AUC.
                outcome = int(feats[0] + rng.normal(scale=1.5) > 0)
                row = dict(zip(feat_cols, feats))
                row.update({
                    "tb_outcome": outcome,
                    "__symbol": symbol,
                    "__signal_date": f"2026-01-{day + 1:02d}",
                    "__strategy": "synthetic", "__side": "BUY", "__log_time": 0.0,
                })
                rows.append(row)
    return pd.DataFrame(rows)


def test_train_all_parallel_matches_serial_semantics_end_to_end(monkeypatch):
    """End-to-end run with 2 symbols through the REAL ProcessPoolExecutor
    dispatch/collection path -- _train_model itself is faked (its own
    correctness/runtime is _train_model's concern, not this refactor's;
    the real multi-candidate sklearn tournament is too slow for a unit test
    and irrelevant to what changed here) so this verifies the parallelization
    plumbing: every eligible symbol's result comes back correctly keyed,
    with 'model' stripped and promotion fields set, same observable contract
    as the old serial loop."""
    monkeypatch.setattr(ml_trainer, "MIN_SYMBOL_SAMPLES", 50)
    monkeypatch.setattr(ml_trainer, "_train_model", _fast_fake_train_model)

    df = _make_synthetic_df({"NIFTY": 12, "BANKNIFTY": 10}, rows_per_day=6)
    result = ml_trainer.train_all(df)

    assert "error" not in result
    assert set(result["per_symbol"].keys()) == {"NIFTY", "BANKNIFTY"}
    for symbol, sym_result in result["per_symbol"].items():
        assert sym_result["label"] == symbol
        assert "model" not in sym_result  # stripped before storing in results
        assert "promoted" in sym_result
        assert sym_result["distinct_days"] > 0
        assert sym_result["training_data_fingerprint"]


def test_train_all_one_bad_symbol_does_not_lose_the_others(monkeypatch):
    """The 2026-07-11 resilience guarantee, re-verified under the
    parallelized implementation: a symbol whose training raises inside its
    OWN worker process must not prevent the other symbols' models (running
    in sibling worker processes) from completing and being collected."""
    monkeypatch.setattr(ml_trainer, "MIN_SYMBOL_SAMPLES", 50)

    def _flaky(X, y, feature_names, label="cross_symbol", net_returns=None):
        if label == "BANKNIFTY":
            raise ValueError("synthetic per-symbol failure")
        return _fast_fake_train_model(X, y, feature_names, label=label, net_returns=net_returns)

    monkeypatch.setattr(ml_trainer, "_train_model", _flaky)

    df = _make_synthetic_df({"NIFTY": 12, "BANKNIFTY": 10}, rows_per_day=6)
    result = ml_trainer.train_all(df)

    assert "error" not in result
    assert "NIFTY" in result["per_symbol"]
    assert "BANKNIFTY" not in result["per_symbol"]


def test_train_all_cross_symbol_failure_does_not_abort_pipeline(monkeypatch):
    """A cross-symbol training failure (e.g. <2 valid purged folds on the
    combined dataset) must not raise out of train_all() and abort the whole
    nightly pipeline -- same resilience guarantee _train_one_symbol already
    had, now applied to the cross-symbol call too. Per-symbol models (run
    with the same _train_model) must still complete."""
    monkeypatch.setattr(ml_trainer, "MIN_SYMBOL_SAMPLES", 50)

    def _cross_symbol_fails(X, y, feature_names, label="cross_symbol", net_returns=None):
        if label == "cross_symbol":
            raise ValueError("invalid_purged_cv: only 1 valid folds")
        return _fast_fake_train_model(X, y, feature_names, label=label, net_returns=net_returns)

    monkeypatch.setattr(ml_trainer, "_train_model", _cross_symbol_fails)

    df = _make_synthetic_df({"NIFTY": 12, "BANKNIFTY": 10}, rows_per_day=6)
    result = ml_trainer.train_all(df)

    assert "error" not in result
    assert result["cross_symbol"] == {"error": "cross_symbol_training_failed"}
    assert set(result["per_symbol"].keys()) == {"NIFTY", "BANKNIFTY"}
