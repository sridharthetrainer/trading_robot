import json

import pandas as pd


def test_outcome_fields_can_never_be_model_features():
    from ml_trainer import _feature_cols

    frame = pd.DataFrame({
        "score": [1.0], "volume_ratio": [2.0], "tb_outcome": [1],
        "tb_label": [1], "tb_r_multiple": [1.5], "tb_r_multiple_net": [1.2],
        "outcome_price": [110.0], "net_pnl": [50.0], "labelled_at": [123.0],
    })
    assert _feature_cols(frame) == ["score", "volume_ratio"]


def test_eod_context_is_shifted_to_strictly_prior_session():
    from ml_feature_builder import _strict_prior_context_map

    context = {
        "2026-06-24": {"hist_fii_net": -10.0},
        "2026-06-25": {"hist_fii_net": 20.0},
        "2026-06-27": {"hist_fii_net": 99.0},
    }
    mapped = _strict_prior_context_map(
        context, ["2026-06-24", "2026-06-25", "2026-06-26", "2026-06-28"]
    )
    assert mapped["2026-06-24"] == {}
    assert mapped["2026-06-25"]["hist_fii_net"] == -10.0
    assert mapped["2026-06-26"]["hist_fii_net"] == 20.0
    assert mapped["2026-06-28"]["hist_fii_net"] == 99.0


def test_legacy_calibrator_artifact_is_ignored(tmp_path, monkeypatch):
    import signal_calibrator

    model = tmp_path / "calibrator.pkl"
    meta = tmp_path / "calibrator.json"
    model.write_bytes(b"legacy")
    meta.write_text(json.dumps({"n_train": 9999, "promoted": True}))
    monkeypatch.setattr(signal_calibrator, "_MODEL_FILE", model)
    monkeypatch.setattr(signal_calibrator, "_META_FILE", meta)
    calibrator = signal_calibrator.SignalCalibrator()
    assert calibrator.is_trained() is False
    assert calibrator.score({})["log_reg_verdict"] == "NO_MODEL"


def test_prediction_uses_saved_training_column_order(monkeypatch):
    import ml_trainer

    class OrderedModel:
        def predict_proba(self, matrix):
            assert matrix.tolist() == [[10.0, 20.0]]
            return [[0.2, 0.8]]

    artifact = {
        "model": OrderedModel(),
        "training_contract": ml_trainer.TRAINING_CONTRACT,
        "promoted": True,
        "selected_features": ["first", "second"],
        "profit_utility": {"available": True, "best_avg_net_r": 0.1},
        # Deliberately reverse importance order; it must not control input order.
        "feature_importances": [("second", 0.9), ("first", 0.1)],
        "cv_auc_mean": 0.7,
    }
    monkeypatch.setattr(ml_trainer, "_load_model", lambda _label: artifact)
    result = ml_trainer.predict({"first": 10, "second": 20})
    assert result["available"] is True
    assert result["win_prob"] == 0.8


def test_prediction_rejects_promoted_artifact_without_profit_utility(monkeypatch):
    import ml_trainer

    artifact = {
        "model": object(),
        "training_contract": ml_trainer.TRAINING_CONTRACT,
        "promoted": True,
        "selected_features": ["score"],
        "cv_auc_mean": 0.7,
    }
    monkeypatch.setattr(ml_trainer, "_load_model", lambda _label: artifact)
    result = ml_trainer.predict({"score": 10})
    assert result["available"] is False
    assert result["reason"] == "missing_positive_profit_utility"


def test_legacy_or_in_sample_learned_filters_are_neutral(tmp_path, monkeypatch):
    import learned_filters

    path = tmp_path / "filters.json"
    path.write_text(json.dumps({
        "filters": [{
            "id": "legacy", "condition": {"feature": "score", "gt": 1}, "mult": 0.8,
        }],
    }))
    monkeypatch.setattr(learned_filters, "FILTERS_FILE", path)
    learned_filters._CACHE.update(mtime=0.0, data={})
    assert learned_filters.apply_learned_filters({"score": 9})["mult"] == 1.0

    path.write_text(json.dumps({
        "training_contract": learned_filters.FILTER_TRAINING_CONTRACT,
        "active": False, "rule_validation": "in_sample_discovery_only",
        "filters": [{
            "id": "candidate", "condition": {"feature": "score", "gt": 1}, "mult": 0.8,
        }],
    }))
    learned_filters._CACHE.update(mtime=0.0, data={})
    assert learned_filters.apply_learned_filters({"score": 9})["mult"] == 1.0


def test_legacy_eod_weights_are_neutral(tmp_path, monkeypatch):
    import eod_weight_engine

    path = tmp_path / "weights.json"
    path.write_text(json.dumps({"strategy_weights": {"trend": 1.5}}))
    monkeypatch.setattr(eod_weight_engine, "WEIGHTS_FILE", path)
    eod_weight_engine._CACHE.update(mtime=0.0, weights={})
    assert eod_weight_engine.get_strategy_weight("trend") == 1.0


def test_strategy_matrix_ignores_legacy_replay_sources(tmp_path):
    from strategy_performance_matrix import StrategyPerformanceMatrix

    matrix = StrategyPerformanceMatrix(matrix_file=str(tmp_path / "matrix.json"))
    for _ in range(40):
        matrix.record_trade(
            "trend", pnl=-1, day_type="NORMAL", time_bucket="OPEN",
            vix=15, regime="TREND", autosave=False, src="eod",
        )
    assert matrix.get_condition_multiplier(
        "trend", day_type="NORMAL", time_bucket="OPEN", vix=15, regime="TREND"
    ) == 1.0
