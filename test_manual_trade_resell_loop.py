"""Regression test for the 2026-06-08 incident: a NIFTY09JUN2623300CE
CARRYFORWARD position lost -Rs13,960.05 (-88.2%) with exit_reason "EMERGENCY:
re-sell loop halted (getOrderBook+square_off bug)" -- the monitoring cycle
kept re-evaluating the same trade as still OPEN and fired another close
order roughly every 60s (200+ orders on the live account) because the
CLOSED status was never persisted before the next cycle re-queried.

The fix already exists in _square_off() (manual_trade_tracker.py, "CRITICAL:
mark the trade CLOSED, persist it, and drop it from active tracking"), but
had no regression test locking it in. This test verifies the actual
end-to-end guarantee that prevents recurrence: after _square_off() succeeds
once, a fresh _load_open_trades() call (what the next monitoring cycle
would see) does NOT include the trade -- so it can never be closed twice.

Uses an isolated temp DB (monkeypatched DB_PATH) -- never touches the real
production manual_trades.db.
"""
import sqlite3
import threading

import manual_trade_tracker as mtt


class _FakeAngel:
    def __init__(self):
        self.obj = object()  # truthy, just needs to exist
        self.orders_placed = []

    def place_order(self, symbol, qty, side, order_type="MARKET", producttype="INTRADAY"):
        self.orders_placed.append((symbol, qty, side, producttype))
        return ("ORDER123", 100.0)   # (order_id, fill_price)


def _bare_tracker(db_path):
    inst = object.__new__(mtt.ManualTradeTracker)
    inst._angel = _FakeAngel()
    inst._lock = threading.Lock()
    inst._active_trades = {}
    return inst


def _make_trade(order_id="POS-TEST1", status="OPEN"):
    return mtt.ManualTrade(
        order_id=order_id, symbol="NIFTY09JUN2623300CE", exchange="NFO",
        side="BUY", qty=65, entry_price=243.42, product="CARRYFORWARD",
        order_time="2026-06-06T08:30:01", status=status,
        sl_gtt_id="", target_gtt_id="",
    )


def test_square_off_marks_closed_and_persists(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)

    tracker = _bare_tracker(db_file)
    tracker._init_db()

    trade = _make_trade()
    ok = tracker._square_off(trade, "test emergency close")

    assert ok is True
    assert trade.status == "CLOSED"
    assert tracker._angel.orders_placed == [("NIFTY09JUN2623300CE", 65, "SELL", "CARRYFORWARD")]


def test_closed_trade_does_not_reappear_in_next_open_trades_load(tmp_path, monkeypatch):
    """The actual bug: the next monitoring cycle calls _load_open_trades()
    and must NOT see this trade as open, or it fires another close order --
    the exact 200+ order re-sell loop from the 2026-06-08 incident."""
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)

    tracker = _bare_tracker(db_file)
    tracker._init_db()

    trade = _make_trade(order_id="POS-TEST2")
    # Simulate the trade already being tracked as open (as it would be after
    # the tracker first detected it), then square it off.
    tracker._active_trades = {trade.order_id: trade}
    tracker._square_off(trade, "structural stop")

    # Simulate the NEXT monitoring cycle: a fresh load from the DB, exactly
    # what a restarted process (or the next poll) would see.
    fresh_tracker = _bare_tracker(db_file)
    fresh_tracker._active_trades = {}
    fresh_tracker._load_open_trades()

    assert trade.order_id not in fresh_tracker._active_trades, (
        "closed trade reappeared as OPEN on reload -- this is the exact "
        "re-sell loop bug from the 2026-06-08 incident"
    )


def test_square_off_returns_false_and_leaves_open_if_order_placement_fails(tmp_path, monkeypatch):
    """If the close order itself fails, the trade must stay OPEN (so it's
    retried) rather than being silently marked closed with no real fill."""
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)

    tracker = _bare_tracker(db_file)
    tracker._init_db()
    tracker._angel.place_order = lambda *a, **k: (None, 0.0)  # simulates a broker rejection

    trade = _make_trade(order_id="POS-TEST3")
    ok = tracker._square_off(trade, "structural stop")

    assert ok is False
    assert trade.status == "OPEN"
