import json

import pandas as pd


def test_lstm_retrain_is_explicitly_skipped_when_disabled(tmp_path, monkeypatch):
    import config
    from off_hours_engine import OffHoursEngine

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "USE_LSTM", False)
    report = OffHoursEngine()._run_lstm_retrain()

    assert report["status"] == "SKIPPED"
    assert report["promoted"] is False
    assert report["research_only"] is True
    assert json.loads((tmp_path / "lstm_training_report.json").read_text())["status"] == "SKIPPED"


def test_lstm_retrain_uses_function_api_but_never_promotes(tmp_path, monkeypatch):
    import config
    import lstm_model
    from off_hours_engine import OffHoursEngine

    class Fetcher:
        def get_market_data(self, symbol, interval, days):
            assert interval == "5m"
            return pd.DataFrame({"close": range(600)})

    calls = []

    def fake_train(frame, symbol):
        calls.append((symbol, len(frame)))
        return {"trained": True, "val_accuracy": 0.75, "model_path": "research.pt"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "USE_LSTM", True)
    monkeypatch.setenv("LSTM_RESEARCH_SYMBOLS", "NIFTY,BANKNIFTY")
    monkeypatch.setattr(lstm_model, "_get_angel_data_fetcher", lambda: Fetcher())
    monkeypatch.setattr(lstm_model, "train_lstm", fake_train)

    report = OffHoursEngine()._run_lstm_retrain()
    assert calls == [("NIFTY", 600), ("BANKNIFTY", 600)]
    assert report["status"] == "TRAINED_RESEARCH_ONLY"
    assert report["promoted"] is False
    assert report["research_only"] is True
