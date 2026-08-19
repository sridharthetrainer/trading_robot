"""Tests for LiveSignalEngine._check_ws_disconnect_halt() -- the WS-outage
gap found in the 2026-08-19 spec audit: nothing previously halted NEW
entries when the WebSocket had been down a while (existing positions were
already protected via REST fallback monitoring; new entries were not).

Uses the same unbound-method + minimal-stub pattern as
test_live_signal_engine_risk_gate_shadow.py -- LiveSignalEngine.__init__ has
too large a dependency surface to construct directly in a unit test.
"""
import types

from live_signal_engine import LiveSignalEngine


class _FakeAlerts:
    def __init__(self):
        self.warnings = []
        self.criticals = []

    def warning(self, msg):
        self.warnings.append(msg)

    def critical(self, msg):
        self.criticals.append(msg)


def _make_engine_stub(alerts=None):
    return types.SimpleNamespace(
        trade_manager=types.SimpleNamespace(alerts=alerts),
    )


def test_connected_never_halts():
    stub = _make_engine_stub()
    halt = LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=True, now_ts=1000.0)
    assert halt is False


def test_disconnect_under_30s_does_not_halt():
    stub = _make_engine_stub()
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1000.0)  # outage starts
    halt = LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1015.0)  # +15s
    assert halt is False


def test_disconnect_30s_or_more_halts_and_alerts_once():
    alerts = _FakeAlerts()
    stub = _make_engine_stub(alerts)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1000.0)
    halt = LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1031.0)  # +31s

    assert halt is True
    assert len(alerts.criticals) == 1
    assert "31" in alerts.criticals[0] or "disconnected" in alerts.criticals[0]


def test_alert_not_repeated_every_cycle_during_a_long_outage():
    alerts = _FakeAlerts()
    stub = _make_engine_stub(alerts)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1000.0)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1031.0)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1060.0)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1120.0)

    assert len(alerts.criticals) == 1, "must alert once per outage, not every cycle"


def test_reconnect_resets_state_and_sends_a_recovery_alert():
    alerts = _FakeAlerts()
    stub = _make_engine_stub(alerts)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1000.0)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1031.0)  # halts + alerts

    halt = LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=True, now_ts=1040.0)  # reconnected

    assert halt is False
    assert len(alerts.warnings) == 1
    assert stub._ws_disconnected_since is None


def test_a_second_outage_after_recovery_alerts_again():
    alerts = _FakeAlerts()
    stub = _make_engine_stub(alerts)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1000.0)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1031.0)  # 1st outage alert
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=True, now_ts=1040.0)   # recovers

    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=2000.0)  # 2nd outage starts
    halt = LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=2031.0)

    assert halt is True
    assert len(alerts.criticals) == 2, "a fresh outage after recovery must alert again"


def test_no_crash_when_alerts_unavailable():
    stub = _make_engine_stub(alerts=None)
    LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1000.0)
    halt = LiveSignalEngine._check_ws_disconnect_halt(stub, ws_ok=False, now_ts=1031.0)
    assert halt is True  # still halts even if there's nowhere to alert to
