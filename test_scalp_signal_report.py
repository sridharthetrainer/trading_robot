from pathlib import Path

import pandas as pd

import option_scalper
from scalp_signal_report import render_scalp_signal_report


def _breakout_frame():
    index = pd.date_range("2026-07-28 09:15", periods=30, freq="5min")
    close = [100.0] * 29 + [104.0]
    return pd.DataFrame({
        "open": close,
        "high": [101.0] * 29 + [104.6],
        "low": [99.0] * 29 + [103.0],
        "close": close,
        "volume": [1000.0] * 29 + [3200.0],
    }, index=index)


def test_scalp_signal_contains_auditable_underlying_levels():
    signal = option_scalper.scalp_signal(_breakout_frame())
    assert signal is not None
    assert signal["side"] == "BUY"
    assert signal["stop"] < signal["last_close"] < signal["target_1"]
    assert signal["target_2"] > signal["target_1"]
    assert signal["vwap"] > 0
    assert signal["range_expansion"] > 0
    assert signal["volume_ratio"] > 1


def test_scalp_evidence_report_renders(tmp_path):
    frame = _breakout_frame()
    signal = option_scalper.scalp_signal(frame)
    payload = {
        "strategy": "OPTION_SCALP",
        "symbol": "NIFTY",
        "side": signal["side"],
        "spot": signal["last_close"],
        "setup_score": signal["score"],
        "source_id": "scalp_NIFTY_test",
        "selected": {
            "symbol": "NIFTY100CE", "strike": 100, "option_type": "CE",
        },
        "metadata": {"scalp_evidence": signal, "interval": "5m"},
    }

    path = Path(render_scalp_signal_report(frame, payload, str(tmp_path)))

    assert path.exists()
    assert path.stat().st_size > 10_000


def test_scan_sends_text_then_image_report(monkeypatch):
    frame = _breakout_frame()
    events = []

    class Fetcher:
        def get_market_data(self, *_args, **_kwargs):
            return frame

    def record(**kwargs):
        events.append("text")
        return {
            **kwargs,
            "metadata": kwargs.get("metadata", {}),
        }

    def image(payload, df):
        assert events == ["text"]
        assert payload["strategy"] == "OPTION_SCALP"
        assert df is frame
        events.append("image")
        return True

    import option_decision_journal
    monkeypatch.setattr(option_scalper, "_get_fetcher", lambda: Fetcher())
    monkeypatch.setattr(option_decision_journal, "record_option_decision", record)
    monkeypatch.setattr(option_decision_journal, "alert_option_scalp_report", image)
    monkeypatch.setattr(option_scalper, "_cfg", lambda name, default: default)

    emitted = option_scalper.scan_and_journal(["NIFTY"])

    assert emitted == 1
    assert events == ["text", "image"]
