"""Test for the 2026-08-19 fix to TradeManager._reconcile_open_trades_with_broker():
an orphaned trade found at startup (DB said OPEN, broker shows
REJECTED/CANCELLED) used to only logger.critical() -- nothing paged a
human unless they were tailing the log file at that exact moment. Now also
sends a real alert via self.alerts.

Uses the unbound-method + minimal-stub pattern established throughout this
session's live_signal_engine.py tests -- TradeManager's constructor has too
large a dependency surface (DB, position sizer, broker manager) to build
directly for a single-method test.
"""
import time
import types

from trade_manager import ManagedTrade, TradeManager


class _FakeAlerts:
    def __init__(self):
        self.criticals = []

    def critical(self, msg):
        self.criticals.append(msg)


class _FakeBrokerManager:
    def __init__(self, status_by_order_id):
        self._status = status_by_order_id

    def get_order_status(self, order_id, exchange="NFO"):
        return self._status.get(order_id)


def _make_trade(order_id, trade_id="T1"):
    return ManagedTrade(
        trade_id=trade_id, symbol="NIFTY24000CE", side="BUY", qty=65,
        strategy="orb", broker_name="ANGEL", order_id=order_id,
        entry_price=100.0, entry_time=time.time(),
    )


def _make_stub(open_trades, broker_manager, alerts):
    return types.SimpleNamespace(
        open_trades=open_trades,
        closed_trades=[],
        broker_manager=broker_manager,
        alerts=alerts,
        _persist_trade=lambda trade: None,
    )


def test_orphaned_trade_sends_a_real_alert_not_just_a_log_line():
    trade = _make_trade("REAL-ORDER-1")
    stub = _make_stub(
        open_trades={"T1": trade},
        broker_manager=_FakeBrokerManager({"REAL-ORDER-1": "REJECTED"}),
        alerts=_FakeAlerts(),
    )

    TradeManager._reconcile_open_trades_with_broker(stub)

    assert "T1" not in stub.open_trades
    assert trade.status == "ORPHANED"
    assert len(stub.alerts.criticals) == 1
    assert "NIFTY24000CE" in stub.alerts.criticals[0]
    assert "REJECTED" in stub.alerts.criticals[0]


def test_filled_order_stays_open_no_alert():
    trade = _make_trade("REAL-ORDER-2")
    stub = _make_stub(
        open_trades={"T1": trade},
        broker_manager=_FakeBrokerManager({"REAL-ORDER-2": "COMPLETE"}),
        alerts=_FakeAlerts(),
    )

    TradeManager._reconcile_open_trades_with_broker(stub)

    assert "T1" in stub.open_trades
    assert stub.alerts.criticals == []


def test_simulated_orders_skipped_no_alert():
    trade = _make_trade("PAPER-123")
    stub = _make_stub(
        open_trades={"T1": trade},
        broker_manager=_FakeBrokerManager({}),
        alerts=_FakeAlerts(),
    )

    TradeManager._reconcile_open_trades_with_broker(stub)

    assert "T1" in stub.open_trades
    assert stub.alerts.criticals == []


def test_no_alerts_configured_does_not_crash():
    trade = _make_trade("REAL-ORDER-3")
    stub = _make_stub(
        open_trades={"T1": trade},
        broker_manager=_FakeBrokerManager({"REAL-ORDER-3": "CANCELLED"}),
        alerts=None,
    )

    TradeManager._reconcile_open_trades_with_broker(stub)  # must not raise

    assert "T1" not in stub.open_trades
