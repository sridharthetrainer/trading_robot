"""Regression tests for harmless systemd timer catch-up outcomes."""

import daily_pipeline
import learned_filters
import post_market_ml


def test_weekend_option_report_skip_is_benign():
    assert daily_pipeline._option_report_skip_is_benign(
        {"ok": False, "skipped": "not_post_market"}
    )
    assert not daily_pipeline._option_report_skip_is_benign(
        {"ok": False, "skipped": "send_failed"}
    )


def test_post_market_cli_treats_empty_matrix_as_benign(monkeypatch):
    monkeypatch.setattr(post_market_ml, "_acquire_lock", lambda: True)
    monkeypatch.setattr(post_market_ml, "_release_lock", lambda: None)
    monkeypatch.setattr(
        post_market_ml, "run_pipeline", lambda **_kwargs: {"error": "empty_feature_matrix"}
    )
    monkeypatch.setattr(post_market_ml, "_alert_pipeline_failure", lambda _message: None)
    monkeypatch.setattr("sys.argv", ["post_market_ml.py"])
    assert post_market_ml.main() == 0


def test_learned_filters_accepts_missing_auc(tmp_path, monkeypatch):
    monkeypatch.setattr(learned_filters, "FILTERS_FILE", tmp_path / "filters.json")
    monkeypatch.setattr(learned_filters, "MAX_FILTERS", 0)
    monkeypatch.setattr(learned_filters, "MAX_BOOSTS", 0)
    result = learned_filters.generate_and_save(
        {"__summary": {"overall_wr": 0.0, "n_total": 0}},
        {"cross_symbol": {"cv_auc_mean": None}},
    )
    assert result.exists()
