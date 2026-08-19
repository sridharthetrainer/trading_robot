"""Tests for the position-reconciliation gap fixed 2026-08-19: angel.py's
reconcile_positions() used to fetch Angel's live positions but never
actually compare them against local tracked state; its only caller
(off_hours_engine._run_recon) was itself never called anywhere. This tests
the fixed two-directional comparison in angel.py plus the new periodic
wiring in position_reconciliation.py.

Same object.__new__(angel.AngelOne) bare-instance pattern already
established in test_gtt_producttype_matches_position.py.
"""
import threading

import angel
import position_reconciliation as pr


class _FakeAngelObj:
    def __init__(self, positions):
        self._positions = positions

    def position(self):
        return {"data": self._positions}


def _bare_angel(positions):
    inst = object.__new__(angel.AngelOne)
    inst.obj = _FakeAngelObj(positions)
    inst._lock = threading.Lock()
    inst._ensure_connected = lambda: True
    return inst


def _angel_pos(symbol, netqty, avg=100.0):
    return {"tradingsymbol": symbol, "netqty": netqty, "averageprice": avg}


# ── angel.reconcile_positions() two-directional comparison ──────────────

def test_matched_position():
    a = _bare_angel([_angel_pos("NIFTY24000CE", 65)])
    result = a.reconcile_positions(local_positions={"NIFTY24000CE": 65})
    assert result["matched"] == ["NIFTY24000CE"]
    assert result["mismatched"] == []


def test_quantity_mismatch_detected():
    a = _bare_angel([_angel_pos("NIFTY24000CE", 50)])  # broker says 50
    result = a.reconcile_positions(local_positions={"NIFTY24000CE": 65})  # local thinks 65
    assert result["mismatched"] == [{"symbol": "NIFTY24000CE", "local_qty": 65, "angel_qty": 50}]


def test_missing_angel_detected():
    """Local thinks a position is open but the broker has nothing --
    e.g. a broker-side square-off the tracker never saw."""
    a = _bare_angel([])
    result = a.reconcile_positions(local_positions={"NIFTY24000CE": 65})
    assert result["missing_angel"] == [{"symbol": "NIFTY24000CE", "local_qty": 65}]


def test_missing_local_detected():
    """The dangerous case: broker has a live position the local tracker
    doesn't know about at all -- unmonitored, unprotected."""
    a = _bare_angel([_angel_pos("NIFTY24000CE", 65)])
    result = a.reconcile_positions(local_positions={})
    assert result["missing_local"] == [{"symbol": "NIFTY24000CE", "angel_qty": 65}]


def test_direction_mismatch_caught_via_signed_qty():
    """Local thinks it's short (-65) but broker shows long (+65) --
    same magnitude, opposite direction. Must NOT be reported as matched."""
    a = _bare_angel([_angel_pos("NIFTY24000CE", 65)])
    result = a.reconcile_positions(local_positions={"NIFTY24000CE": -65})
    assert result["matched"] == []
    assert result["mismatched"] == [{"symbol": "NIFTY24000CE", "local_qty": -65, "angel_qty": 65}]


def test_no_local_positions_arg_preserves_old_behavior():
    """Backward compatible with the existing off_hours_engine.py caller,
    which calls with no local_positions."""
    a = _bare_angel([_angel_pos("NIFTY24000CE", 65)])
    result = a.reconcile_positions()
    assert result["angel_positions"] == {"NIFTY24000CE": {"qty": 65, "avg_price": 100.0}}
    assert result["matched"] == []
    assert result["mismatched"] == []


def test_disconnected_returns_empty_result():
    a = object.__new__(angel.AngelOne)
    a.obj = None
    a._ensure_connected = lambda: False
    result = a.reconcile_positions(local_positions={"X": 1})
    assert result == {"matched": [], "missing_local": [], "missing_angel": [],
                       "mismatched": [], "angel_positions": {}}


# ── position_reconciliation.run_reconciliation() ────────────────────────

class _FakeAlerts:
    def __init__(self):
        self.criticals = []

    def critical(self, msg):
        self.criticals.append(msg)


class _FakeTradeManager:
    def __init__(self, positions):
        self._positions = positions

    def get_open_positions(self):
        return self._positions


def test_run_reconciliation_no_mismatch(monkeypatch):
    a = _bare_angel([_angel_pos("NIFTY24000CE", 65)])
    tm = _FakeTradeManager([{"symbol": "NIFTY24000CE", "qty": 65, "side": "BUY"}])
    alerts = _FakeAlerts()

    result = pr.run_reconciliation(a, tm, alerts=alerts)

    assert result["checked"] is True
    assert result["mismatch"] is False
    assert alerts.criticals == []


def test_run_reconciliation_flags_mismatch_and_alerts():
    a = _bare_angel([_angel_pos("NIFTY24000CE", 50)])  # broker: 50
    tm = _FakeTradeManager([{"symbol": "NIFTY24000CE", "qty": 65, "side": "BUY"}])  # local: 65
    alerts = _FakeAlerts()

    result = pr.run_reconciliation(a, tm, alerts=alerts)

    assert result["mismatch"] is True
    assert "NIFTY24000CE" in result["reason"]
    assert len(alerts.criticals) == 1


def test_run_reconciliation_nets_multiple_local_trades_same_symbol():
    """Two local trades on the same symbol (e.g. partial scale-in) must net
    to the combined signed quantity before comparing against Angel's single
    netqty figure, not be compared trade-by-trade."""
    a = _bare_angel([_angel_pos("NIFTY24000CE", 130)])
    tm = _FakeTradeManager([
        {"symbol": "NIFTY24000CE", "qty": 65, "side": "BUY"},
        {"symbol": "NIFTY24000CE", "qty": 65, "side": "BUY"},
    ])
    result = pr.run_reconciliation(a, tm)
    assert result["mismatch"] is False
    assert result["matched"] == ["NIFTY24000CE"]


def test_run_reconciliation_short_side_nets_negative():
    a = _bare_angel([_angel_pos("NIFTY24000CE", -65)])
    tm = _FakeTradeManager([{"symbol": "NIFTY24000CE", "qty": 65, "side": "SELL"}])
    result = pr.run_reconciliation(a, tm)
    assert result["mismatch"] is False


def test_run_reconciliation_fetch_failure_fails_open():
    class _BoomAngel:
        def reconcile_positions(self, local_positions=None):
            raise RuntimeError("broker API down")

    tm = _FakeTradeManager([])
    result = pr.run_reconciliation(_BoomAngel(), tm)
    assert result["checked"] is False
    assert result["mismatch"] is False  # a fetch failure is not evidence of drift
