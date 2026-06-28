def test_training_contract_audit_passes_with_absent_or_quarantined_models(tmp_path, monkeypatch):
    import eod_weight_engine
    import learned_filters
    import ml_trainer
    import signal_calibrator
    import training_contract_audit

    monkeypatch.setattr(training_contract_audit, "_signal_counts", lambda: {
        "legacy_labelled": 20, "clean_labelled": 0, "clean_days": 0,
    })
    monkeypatch.setattr(ml_trainer, "_load_model", lambda _label: None)
    monkeypatch.setattr(learned_filters, "FILTERS_FILE", tmp_path / "missing_filters.json")
    monkeypatch.setattr(eod_weight_engine, "WEIGHTS_FILE", tmp_path / "missing_weights.json")
    monkeypatch.setattr(signal_calibrator, "_MODEL_FILE", tmp_path / "missing_model.pkl")
    signal_calibrator._calibrator = None
    learned_filters._CACHE.update(mtime=0.0, data={})
    eod_weight_engine._CACHE.update(mtime=0.0, weights={})

    report = training_contract_audit.build_training_contract_audit(write=False)
    assert report["ok"] is True
    assert report["forbidden_selected"] == []
