import json


class _FakeAlerts:
    def __init__(self):
        self.sent = []

    def send(self, text, **kw):
        self.sent.append(text)
        return True

    def send_photo(self, path, caption=""):
        self.sent.append(caption)
        return True


def _record(odj, tmp_path, strategy):
    return odj.record_option_decision(
        strategy=strategy, symbol="FINNIFTY", decision="selected", reason="test", side="SELL",
        spot=24946.0, selected={"symbol": "FINNIFTY24950PE", "strike": 24950, "option_type": "PE"},
        path=str(tmp_path / "journal.jsonl"))


def test_scalp_is_journaled_but_not_alerted_by_default(tmp_path, monkeypatch):
    import option_decision_journal as odj
    fake = _FakeAlerts()
    monkeypatch.setattr(odj, "_option_alerts", lambda: fake)
    monkeypatch.delenv("OPTION_SCALP_ALERTS", raising=False)
    _record(odj, tmp_path, "OPTION_SCALP")
    rows = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert rows and rows[-1]["strategy"] == "OPTION_SCALP"       # still journaled
    assert fake.sent == []                                        # no Telegram alert
    assert odj.alert_option_scalp_report({"strategy": "OPTION_SCALP"}, None) is False


def test_scalp_alerts_can_be_switched_back_on(tmp_path, monkeypatch):
    import option_decision_journal as odj
    monkeypatch.setenv("OPTION_SCALP_ALERTS", "true")
    assert odj._scalp_alerts_enabled("OPTION_SCALP") is True


def test_other_strategies_still_alert(monkeypatch):
    import option_decision_journal as odj
    monkeypatch.delenv("OPTION_SCALP_ALERTS", raising=False)
    assert odj._scalp_alerts_enabled("hero_zero") is True
    assert odj._scalp_alerts_enabled("pivot_scalping") is True
