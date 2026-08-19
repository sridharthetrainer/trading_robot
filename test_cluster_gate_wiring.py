"""Tests for LiveSignalEngine._cluster_gate_check() -- the live wiring of
cluster_risk_gate.py into the real entry path (2026-08-19 spec audit §4-6).

Same unbound-method + minimal-stub pattern as
test_live_signal_engine_risk_gate_shadow.py / test_ws_disconnect_halt.py.
"""
import types

import pytest

from live_signal_engine import LiveSignalEngine


class _FakeBroker:
    angel = types.SimpleNamespace(obj=None)


class _FakeBrokerManager:
    def get_execution_broker(self):
        return _FakeBroker()


def _make_stub(capital=1_000_000.0, open_positions=None):
    return types.SimpleNamespace(
        trade_manager=types.SimpleNamespace(
            capital=capital,
            get_open_positions=lambda: open_positions or [],
        ),
        broker_manager=_FakeBrokerManager(),
    )


def test_allows_and_returns_full_multiplier_with_no_open_positions():
    stub = _make_stub()
    allowed, mult, reason = LiveSignalEngine._cluster_gate_check(
        stub, symbol="RELIANCE", strategy="orb", side="BUY",
        entry_price=100.0, stop_loss=98.0, qty=100, regime_label="RANGE")
    assert allowed is True
    assert mult == 1.0


def test_blocks_when_cluster_disabled_for_regime():
    stub = _make_stub()
    # "trend" -> cluster E; event_day disables everything except F/G/H.
    allowed, mult, reason = LiveSignalEngine._cluster_gate_check(
        stub, symbol="NIFTY", strategy="trend", side="BUY",
        entry_price=100.0, stop_loss=98.0, qty=100, regime_label="EVENT_DAY_UNUSED")
    # regime_label doesn't map to event_day directly (that requires
    # is_event_day=True, not derivable from a regime label alone) -- this
    # confirms the fail-safe default (weak_trend_low_vol) is used instead,
    # where cluster E is simply not in the active list.
    assert allowed is False
    assert "CLUSTER" in reason


def test_zero_capital_fails_open():
    stub = _make_stub(capital=0.0)
    allowed, mult, reason = LiveSignalEngine._cluster_gate_check(
        stub, symbol="RELIANCE", strategy="orb", side="BUY",
        entry_price=100.0, stop_loss=98.0, qty=100, regime_label="")
    assert allowed is True
    assert mult == 1.0
    assert "capital" in reason


def test_exception_in_gate_fails_open(monkeypatch):
    stub = _make_stub()

    class _BoomGate:
        def can_enter(self, **kwargs):
            raise RuntimeError("boom")

    import cluster_risk_gate
    monkeypatch.setattr(cluster_risk_gate, "ClusterRiskGate", lambda: _BoomGate())

    allowed, mult, reason = LiveSignalEngine._cluster_gate_check(
        stub, symbol="RELIANCE", strategy="orb", side="BUY",
        entry_price=100.0, stop_loss=98.0, qty=100, regime_label="RANGE")
    assert allowed is True
    assert mult == 1.0
    assert "fail-open" in reason


def test_open_positions_risk_pct_computed_from_entry_stop_qty():
    """A same-cluster open position should trigger the intra-cluster size
    cap even though trade_manager.get_open_positions() carries no
    pre-computed risk_pct field (ManagedTrade has none) -- confirms the
    wiring computes it from entry/stop/qty/capital rather than defaulting
    to 0 (which would silently disable this whole check)."""
    capital = 1_000_000.0
    # Existing cluster-A position: |100-80| * 100 / 1,000,000 * 100 = 0.2% risk_pct.
    open_positions = [{
        "symbol": "TCS", "strategy": "orb", "side": "BUY",
        "entry_price": 100.0, "stop_loss": 80.0, "qty": 100,
    }]
    stub = _make_stub(capital=capital, open_positions=open_positions)

    # New candidate: |100-50| * 100 / 1,000,000 * 100 = 0.5% requested risk_pct
    # -- cluster A's "2nd position" tier caps this to 0.3%, giving an expected
    # multiplier of 0.3/0.5 = 0.6. This confirms the open position's risk_pct
    # was actually computed from entry/stop/qty (0.2%, correctly under
    # max_total=0.6 alongside the capped 0.3%) rather than silently reading 0
    # (which would have hidden the intra-cluster cap from ever engaging).
    allowed, mult, reason = LiveSignalEngine._cluster_gate_check(
        stub, symbol="RELIANCE", strategy="orb", side="BUY",
        entry_price=100.0, stop_loss=50.0, qty=100, regime_label="RANGE")

    assert allowed is True
    assert mult == pytest.approx(0.6)
