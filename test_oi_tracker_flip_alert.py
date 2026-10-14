from oi_tracker import OITracker


class _FakeAlerts:
    def __init__(self, dedup_blocked=False, photo_ok=True):
        self._dedup_blocked = dedup_blocked
        self._photo_ok = photo_ok
        self.photo_calls = []
        self.text_calls = []
        self.marked_sent = []

    def _is_dedup_blocked(self, key, cooldown):
        return self._dedup_blocked

    def _mark_dedup_sent(self, key):
        self.marked_sent.append(key)

    def send_photo(self, path, caption=""):
        self.photo_calls.append((path, caption))
        return self._photo_ok

    def send(self, text, dedup_key=None, dedup_cooldown_override=None):
        self.text_calls.append((text, dedup_key, dedup_cooldown_override))
        return True


_DIRECTION = {"direction": "BULLISH", "conviction": "STRONG",
              "ce_delta": -4000.0, "pe_delta": 17000.0, "net_pcr": 1.08}


def test_flip_alert_sends_photo_and_marks_dedup_when_image_succeeds(monkeypatch):
    calls = {}

    def _fake_generate(events, history=None):
        calls["events"] = events
        calls["history"] = history
        class _R:
            ok = True
            path = "/tmp/fake_flip.png"
        return _R()

    monkeypatch.setattr("option_oi_chart.generate_oi_flip_alert_image", _fake_generate)

    alerts = _FakeAlerts()
    tracker = OITracker(alerts=alerts)
    tracker._alert_direction_flip("BANKNIFTY", "BEARISH", _DIRECTION, 56684.0, "15:01")

    assert len(alerts.photo_calls) == 1
    path, caption = alerts.photo_calls[0]
    assert path == "/tmp/fake_flip.png"
    assert "OI DIRECTION FLIP: BULLISH" in caption
    assert not alerts.text_calls  # no redundant plain-text send
    assert alerts.marked_sent == ["oi_flip:BANKNIFTY:BULLISH:15:01"]
    assert calls["events"][0]["symbol"] == "BANKNIFTY"
    assert calls["events"][0]["ce_delta"] == -4000.0


def test_flip_alert_falls_back_to_text_when_image_generation_raises(monkeypatch):
    def _raise(events, history=None):
        raise RuntimeError("matplotlib unavailable")

    monkeypatch.setattr("option_oi_chart.generate_oi_flip_alert_image", _raise)

    alerts = _FakeAlerts()
    tracker = OITracker(alerts=alerts)
    tracker._alert_direction_flip("NIFTY", "BULLISH", _DIRECTION, 23754.0, "15:06")

    assert not alerts.photo_calls
    assert len(alerts.text_calls) == 1
    text, dedup_key, cooldown = alerts.text_calls[0]
    assert "OI DIRECTION FLIP: BULLISH" in text
    assert dedup_key == "oi_flip:NIFTY:BULLISH:15:06"
    assert cooldown == 300


def test_flip_alert_falls_back_to_text_when_photo_send_fails(monkeypatch):
    def _fake_generate(events, history=None):
        class _R:
            ok = True
            path = "/tmp/fake_flip.png"
        return _R()

    monkeypatch.setattr("option_oi_chart.generate_oi_flip_alert_image", _fake_generate)

    alerts = _FakeAlerts(photo_ok=False)
    tracker = OITracker(alerts=alerts)
    tracker._alert_direction_flip("NIFTY", "BULLISH", _DIRECTION, 23754.0, "15:06")

    assert len(alerts.photo_calls) == 1
    assert len(alerts.text_calls) == 1  # fell back after failed send
    assert not alerts.marked_sent  # send_photo failed, no explicit mark (send() marks internally)


def test_flip_alert_skips_entirely_when_dedup_blocked(monkeypatch):
    called = {"n": 0}

    def _fake_generate(events, history=None):
        called["n"] += 1
        class _R:
            ok = True
            path = "/tmp/fake_flip.png"
        return _R()

    monkeypatch.setattr("option_oi_chart.generate_oi_flip_alert_image", _fake_generate)

    alerts = _FakeAlerts(dedup_blocked=True)
    tracker = OITracker(alerts=alerts)
    tracker._alert_direction_flip("BANKNIFTY", "BEARISH", _DIRECTION, 56684.0, "15:01")

    assert called["n"] == 0
    assert not alerts.photo_calls
    assert not alerts.text_calls


def test_flip_alert_noop_when_no_alerts_configured():
    tracker = OITracker(alerts=None)
    # must not raise
    tracker._alert_direction_flip("BANKNIFTY", "BEARISH", _DIRECTION, 56684.0, "15:01")


def test_new_day_does_not_diff_against_yesterdays_snapshot(monkeypatch, tmp_path):
    # Process left running overnight: yesterday's last snapshot is still in
    # memory. Today's first bar must start fresh (delta 0, NEUTRAL, no flip
    # alert), not report the whole overnight OI change as one 5-min delta.
    import oi_tracker
    monkeypatch.setattr(oi_tracker, "_STATE_FILE", tmp_path / "state.json")
    yesterday = {"22000CE": {"oi": 1_000_000.0, "ltp": 100.0},
                 "22000PE": {"oi": 1_000_000.0, "ltp": 100.0}}
    today = {"22000CE": {"oi": 3_000_000.0, "ltp": 120.0},
             "22000PE": {"oi": 1_000_000.0, "ltp": 80.0}}
    monkeypatch.setattr(oi_tracker, "_fetch_chain", lambda sym: {"raw": True})
    monkeypatch.setattr(oi_tracker, "_parse_chain", lambda raw: (22000.0, today))

    alerts = _FakeAlerts()
    tracker = OITracker(alerts=alerts)
    tracker._session_date = "2000-01-01"
    tracker._snaps["NIFTY"] = [("15:25", yesterday, 22000.0)]
    tracker._directions["NIFTY"] = [("15:25", {"direction": "NEUTRAL"}, 22000.0)]
    tracker._last_direction["NIFTY"] = "NEUTRAL"

    assert tracker._refresh("NIFTY")
    [(_, d, _)] = tracker._directions["NIFTY"]
    assert d["ce_delta"] == 0 and d["pe_delta"] == 0
    assert d["direction"] == "NEUTRAL"
    assert len(tracker._snaps["NIFTY"]) == 1
    assert not alerts.photo_calls and not alerts.text_calls


def test_same_day_still_diffs_against_previous_snapshot(monkeypatch, tmp_path):
    import oi_tracker
    monkeypatch.setattr(oi_tracker, "_STATE_FILE", tmp_path / "state.json")
    prev = {"22000CE": {"oi": 1_000_000.0, "ltp": 100.0},
            "22000PE": {"oi": 1_000_000.0, "ltp": 100.0}}
    now = {"22000CE": {"oi": 1_500_000.0, "ltp": 100.0},
           "22000PE": {"oi": 1_000_000.0, "ltp": 100.0}}
    monkeypatch.setattr(oi_tracker, "_fetch_chain", lambda sym: {"raw": True})
    monkeypatch.setattr(oi_tracker, "_parse_chain", lambda raw: (22000.0, now))

    tracker = OITracker(alerts=None)
    tracker._snaps["NIFTY"] = [("10:00", prev, 22000.0)]
    assert tracker._refresh("NIFTY")
    d = tracker._directions["NIFTY"][-1][1]
    assert d["ce_delta"] == 500_000.0 and d["direction"] == "BEARISH"
