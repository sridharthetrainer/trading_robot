import time

import oi_tracker


def test_last_refresh_ts_round_trips_through_save_and_load(tmp_path, monkeypatch):
    state_file = tmp_path / "oi_tracker_state.json"
    monkeypatch.setattr(oi_tracker, "_STATE_FILE", state_file)

    tracker = oi_tracker.OITracker(alerts=None)
    now = time.time()
    tracker._last_direction["NIFTY"] = "BULLISH"
    tracker._last_refresh_ts["NIFTY"] = now
    tracker._save()

    reloaded = oi_tracker.OITracker(alerts=None)
    assert reloaded._last_direction.get("NIFTY") == "BULLISH"
    assert reloaded._last_refresh_ts.get("NIFTY") == now


def test_last_refresh_ts_not_restored_across_a_new_day(tmp_path, monkeypatch):
    state_file = tmp_path / "oi_tracker_state.json"
    state_file.write_text(
        '{"date": "2020-01-01", "snaps": {}, "directions": {}, '
        '"last_dir": {"NIFTY": "BEARISH"}, "last_refresh_ts": {"NIFTY": 123.0}}'
    )
    monkeypatch.setattr(oi_tracker, "_STATE_FILE", state_file)

    tracker = oi_tracker.OITracker(alerts=None)
    # yesterday's state must not silently carry into a new trading day
    assert tracker._last_refresh_ts == {}
    assert tracker._last_direction == {}
