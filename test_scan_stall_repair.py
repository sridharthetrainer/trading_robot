"""Scan-stall auto-repair: watchdog restarts an alive-but-not-scanning bot.
Covers the runtime_telemetry accessor + the finite/inf repair decision rule."""
import importlib
import runtime_telemetry


def test_seconds_since_last_scan_inf_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_telemetry, "DB_PATH", tmp_path / "rt.db")
    monkeypatch.setattr(runtime_telemetry, "_SCHEMA_READY_FOR", "")
    assert runtime_telemetry.seconds_since_last_scan() == float("inf")


def test_seconds_since_last_scan_finite_after_a_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_telemetry, "DB_PATH", tmp_path / "rt.db")
    monkeypatch.setattr(runtime_telemetry, "_SCHEMA_READY_FOR", "")
    runtime_telemetry.begin_scan(10)
    age = runtime_telemetry.seconds_since_last_scan()
    assert age != float("inf") and age < 5     # just happened


def _should_repair(scan_age, threshold, phase, hb_age, positions):
    """Mirror of the watchdog's scan-stall gate (finite stale + LIVE + alive +
    flat). Kept here so the decision rule is regression-tested."""
    return (phase == "LIVE" and 0 <= hb_age < 300
            and scan_age != float("inf") and scan_age > threshold
            and positions == 0)


def test_repair_rule():
    T = 1500
    assert _should_repair(2000, T, "LIVE", 30, 0) is True      # stalled → repair
    assert _should_repair(float("inf"), T, "LIVE", 30, 0) is False  # cold start → no
    assert _should_repair(2000, T, "LIVE", 30, 2) is False     # open positions → no
    assert _should_repair(2000, T, "AFTER_HOURS", 30, 0) is False  # not market hrs
    assert _should_repair(2000, T, "LIVE", 900, 0) is False    # heartbeat stale → other path
    assert _should_repair(600, T, "LIVE", 30, 0) is False      # recent scan → no
