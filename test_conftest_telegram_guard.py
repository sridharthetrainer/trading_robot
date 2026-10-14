import requests


def test_telegram_calls_are_intercepted_in_tests():
    # Uses a bogus token: if the guard were missing this would hit the
    # network and fail with 404, not return ok.
    resp = requests.post("https://api.telegram.org/botBOGUS/sendMessage",
                         data={"chat_id": "0", "text": "x"}, timeout=5)
    assert resp.status_code == 200 and resp.json()["ok"] is True


def test_option_selection_alert_never_reaches_telegram(monkeypatch, tmp_path):
    import option_decision_journal as odj
    sent = []
    real = requests.sessions.Session.request

    def spy(self, method, url, *a, **kw):
        sent.append(str(url))
        return real(self, method, url, *a, **kw)

    monkeypatch.setattr(requests.sessions.Session, "request", spy)
    monkeypatch.setenv("OPTION_BOT_TOKEN", "BOGUS")
    monkeypatch.setenv("OPTION_CHAT_ID", "0")
    monkeypatch.setattr(odj, "_OPTION_ALERTS_READY", False)
    monkeypatch.setattr(odj, "_OPTION_ALERTS", None)
    monkeypatch.chdir(tmp_path)
    odj.record_option_decision(strategy="hero_zero", symbol="NIFTY", decision="selected",
                               reason="alert_only", side="BUY", spot=20000.0,
                               selected={"symbol": "NIFTY20300CE", "strike": 20300, "option_type": "CE"},
                               path=str(tmp_path / "j.jsonl"))
    # the alert path may run; whatever it sends must hit the guard's fake, and nothing may spool
    assert not list(tmp_path.glob("telegram_spool*")) and not list(tmp_path.glob(".telegram_media_spool*/*"))
