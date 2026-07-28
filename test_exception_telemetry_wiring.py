"""Regression for a 2026-07-28 audit finding: exception_telemetry.py had
exactly one call site in the whole repo. Extended to 5 bare-except locations
that were previously fully silent despite feeding pricing/capital-sizing/
order-state logic. Covers the pure recorder plus two representative wiring
points (one per touched module); the other three follow the identical
try/record_exception/except-pass shape."""
import json
import threading

import angel
import exception_telemetry
import trade_manager


def test_record_exception_appends_a_json_line(tmp_path, monkeypatch):
    path = tmp_path / "exception_telemetry.jsonl"
    monkeypatch.setattr(exception_telemetry, "_PATH", path)

    exception_telemetry.record_exception(
        "test_component", "test_op", ValueError("boom"), context={"symbol": "NIFTY"},
    )

    row = json.loads(path.read_text().strip())
    assert row["component"] == "test_component"
    assert row["operation"] == "test_op"
    assert row["error_type"] == "ValueError"
    assert row["context"] == {"symbol": "NIFTY"}


def test_record_exception_never_raises_on_write_failure(monkeypatch):
    monkeypatch.setattr(exception_telemetry, "_PATH", object())  # .open() will raise
    exception_telemetry.record_exception("x", "y", ValueError("boom"))  # must not raise


def test_get_real_ltp_records_telemetry_on_fetch_failure(tmp_path, monkeypatch):
    path = tmp_path / "exception_telemetry.jsonl"
    monkeypatch.setattr(exception_telemetry, "_PATH", path)

    class _FailingObj:
        def ltpData(self, *a, **kw):
            raise RuntimeError("angel ltp timeout")

    inst = object.__new__(angel.AngelOne)
    inst.obj = _FailingObj()
    monkeypatch.setattr(inst, "_ensure_connected", lambda: True)
    monkeypatch.setattr(inst, "_get_token_no_lock", lambda symbol, exchange: "12345")

    result = inst._get_real_ltp("RELIANCE")

    assert result is None
    row = json.loads(path.read_text().strip())
    assert row["component"] == "angel"
    assert row["operation"] == "get_real_ltp"
    assert row["context"]["symbol"] == "RELIANCE"


def test_cancel_stuck_orders_records_telemetry_on_broker_failure(tmp_path, monkeypatch):
    path = tmp_path / "exception_telemetry.jsonl"
    monkeypatch.setattr(exception_telemetry, "_PATH", path)

    class _FailingBrokerManager:
        def cancel_order(self, trade_id):
            raise RuntimeError("broker unreachable")

    manager = trade_manager.TradeManager(
        broker_manager=_FailingBrokerManager(), alert_manager=None,
        capital=100_000, db_path=str(tmp_path / "trades.db"), restore_state=False,
    )

    class _FakeConn:
        def execute(self, *a, **kw):
            return self
        def fetchall(self):
            return [("T000001", "RELIANCE", "BUY", 10, 1000.0, "TEST", "A123")]
        def commit(self): pass
        def close(self): pass

    monkeypatch.setattr(manager.store, "_connect", lambda: _FakeConn())
    manager.alerts = None

    manager._cancel_stuck_orders()

    row = json.loads(path.read_text().strip())
    assert row["component"] == "trade_manager"
    assert row["operation"] == "cancel_stuck_order"
    assert row["context"]["trade_id"] == "T000001"
    assert row["context"]["order_id"] == "A123"
