from option_execution_router import execute_option_entry
import pytest


class FakeBroker:
    def __init__(self, *, fills=None, ltp=100.0):
        self.fills = list(fills or [])
        self.ltp = ltp
        self.placed = []
        self.cancelled = []

    def get_name(self):
        return "FakeBroker"

    def get_ltp(self, symbol, exchange=None):
        return self.ltp

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        return f"O{len(self.placed)}"

    def poll_order_fill(self, order_id, timeout_sec=10.0):
        if self.fills:
            return self.fills.pop(0)
        return None

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True

    def get_order_status(self, order_id):
        return {"orderid": order_id, "status": "CANCELLED", "filledshares": 0}


def test_option_router_places_limit_and_records_fill():
    broker = FakeBroker(fills=[{
        "status": "COMPLETE",
        "averageprice": 100.5,
        "filledshares": 50,
    }])

    report = execute_option_entry(
        broker=broker,
        symbol="NIFTY26071625000CE",
        qty=50,
        decision_price=100.0,
        max_slippage_pct=0.0075,
        wait_sec=1,
        reprice_attempts=0,
    )

    assert report.ok is True
    assert report.order_id == "O1"
    assert report.fill_price == 100.5
    assert report.filled_qty == 50
    assert broker.placed[0]["order_type"] == "LIMIT"
    assert broker.placed[0]["price"] == 100.35


def test_option_router_cancels_and_reprices_once():
    broker = FakeBroker(fills=[
        None,
        {"status": "COMPLETE", "averageprice": 101.0, "filledshares": 50},
    ])

    report = execute_option_entry(
        broker=broker,
        symbol="NIFTY26071625000CE",
        qty=50,
        decision_price=100.0,
        max_slippage_pct=0.005,
        wait_sec=1,
        reprice_attempts=1,
    )

    assert report.ok is True
    assert report.attempts == 2
    assert report.cancelled_order_ids == ["O1"]
    assert broker.cancelled == ["O1"]
    assert broker.placed[0]["price"] == 100.25
    assert broker.placed[1]["price"] == 100.5


def test_option_router_blocks_stale_signal_before_order():
    broker = FakeBroker()

    report = execute_option_entry(
        broker=broker,
        symbol="NIFTY26071625000CE",
        qty=50,
        decision_price=100.0,
        signal_generated_at=1.0,
        max_signal_age_sec=1.0,
    )

    assert report.ok is False
    assert report.reason == "stale_signal_before_order"
    assert broker.placed == []


def test_option_router_blocks_price_chase_beyond_slippage_cap():
    broker = FakeBroker(ltp=102.0)

    report = execute_option_entry(
        broker=broker,
        symbol="NIFTY26071625000CE",
        qty=50,
        decision_price=100.0,
        max_slippage_pct=0.0075,
    )

    assert report.ok is False
    assert report.reason == "price_moved_beyond_slippage_cap"
    assert broker.placed == []


class FakeAngelObj:
    def __init__(self, rows):
        self._rows = rows

    def orderBook(self):
        return {"data": self._rows}


class FakeBrokerWithBook(FakeBroker):
    """Broker whose raw order book shows a fill the poll path never reports
    (the fill-after-cancel race: order completes between poll timeout and
    the cancel landing)."""

    def __init__(self, *, book_rows, **kw):
        super().__init__(**kw)
        self.paper_trade = False
        self.obj = FakeAngelObj(book_rows)


def test_option_router_honours_fill_after_cancel_race():
    broker = FakeBrokerWithBook(
        fills=[None],   # poll times out -> router cancels
        book_rows=[{
            "orderid": "O1", "status": "complete",
            "averageprice": 100.4, "filledshares": 50,
        }],
    )

    report = execute_option_entry(
        broker=broker,
        symbol="NIFTY26071625000CE",
        qty=50,
        decision_price=100.0,
        max_slippage_pct=0.0075,
        wait_sec=1,
        reprice_attempts=0,
    )

    assert report.ok is True
    assert report.reason == "filled_after_cancel_race"
    assert report.filled_qty == 50
    assert report.fill_price == 100.4
    assert broker.cancelled == ["O1"]


def test_option_router_counts_partial_fill_before_cancel():
    broker = FakeBrokerWithBook(
        fills=[None],
        book_rows=[{
            "orderid": "O1", "status": "cancelled",
            "averageprice": 100.3, "filledshares": 25,
        }],
    )

    report = execute_option_entry(
        broker=broker,
        symbol="NIFTY26071625000CE",
        qty=50,
        decision_price=100.0,
        max_slippage_pct=0.0075,
        wait_sec=1,
        reprice_attempts=0,
    )

    assert report.ok is True
    assert report.filled_qty == 25   # strictly filledshares, never order qty


def test_option_router_unfilled_cancel_stays_not_ok():
    broker = FakeBrokerWithBook(
        fills=[None],
        book_rows=[{
            "orderid": "O1", "status": "cancelled",
            "averageprice": 0, "filledshares": 0,
        }],
    )

    report = execute_option_entry(
        broker=broker,
        symbol="NIFTY26071625000CE",
        qty=50,
        decision_price=100.0,
        max_slippage_pct=0.0075,
        wait_sec=1,
        reprice_attempts=0,
    )

    assert report.ok is False
    assert report.reason == "not_filled_cancelled"
    assert report.filled_qty == 0


def test_option_router_uses_quote_aware_smart_pricing_schedule():
    broker = FakeBroker(fills=[{
        "status": "COMPLETE", "averageprice": 100.0, "filledshares": 50,
    }], ltp=100.0)
    report = execute_option_entry(
        broker=broker, symbol="NIFTY26071625000CE", qty=50,
        decision_price=100.0, decision_bid=99.0, decision_ask=101.0,
        reprice_attempts=2, contract_hash="abc123",
    )
    assert report.ok is True
    assert report.contract_hash == "abc123"
    assert report.pricing_schedule[0] == 100.0
    assert report.pricing_schedule[-1] < 101.0
    assert broker.placed[0]["price"] == 100.0


def test_cancel_ack_without_terminal_state_cannot_duplicate_order():
    broker = FakeBrokerWithBook(book_rows=[{"orderid": "O1", "status": "OPEN", "filledshares": 0}])
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50,
                                  decision_price=100, reprice_attempts=1)
    assert len(broker.placed) == 1
    assert report.reconciliation_required
    assert report.reason == "order_state_unresolved_after_cancel"
    assert report.cancelled_order_ids == []


def test_unknown_order_state_cannot_reprice():
    broker = FakeBroker()
    broker.get_order_status = lambda order_id: None
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50,
                                  decision_price=100, reprice_attempts=1)
    assert len(broker.placed) == 1 and report.reconciliation_required


def test_partial_fill_with_open_remainder_requires_reconciliation():
    broker = FakeBrokerWithBook(book_rows=[{
        "orderid": "O1", "status": "OPEN", "filledshares": 25, "averageprice": 100,
    }])
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50,
                                  decision_price=100, reprice_attempts=1)
    assert report.filled_qty == 25 and report.reconciliation_required
    assert len(broker.placed) == 1


def test_smart_schedule_cannot_exceed_absolute_slippage_cap():
    broker = FakeBroker(fills=[{"status": "COMPLETE", "filledshares": 50, "averageprice": 100.1}])
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50, decision_price=100,
                                  decision_bid=100, decision_ask=110, max_slippage_pct=.001)
    assert report.ok
    assert broker.placed[0]["price"] <= 100.1


def test_lookup_cannot_age_signal_past_submission_deadline(monkeypatch):
    import option_execution_router as router
    clock = {"now": 100.0}
    monkeypatch.setattr(router.time, "time", lambda: clock["now"])
    broker = FakeBroker()
    def slow_ltp(*args, **kwargs):
        clock["now"] = 120.0
        return 100
    broker.get_ltp = slow_ltp
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50, decision_price=100,
                                  signal_generated_at=99, max_signal_age_sec=10)
    assert not broker.placed
    assert report.reason == "stale_signal_before_order"


@pytest.mark.parametrize("ltp", [0, -1, float("nan"), float("inf"), None])
def test_missing_or_invalid_quote_cannot_submit(ltp):
    broker = FakeBroker(ltp=ltp)
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50, decision_price=100)
    assert not broker.placed
    assert report.reason == "invalid_live_quote"


def test_submission_timeout_blocks_retry_and_requires_reconciliation():
    broker = FakeBroker()
    def uncertain_submission(**kwargs):
        broker.placed.append(kwargs)
        raise TimeoutError("acknowledgement lost")
    broker.place_order = uncertain_submission
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50, decision_price=100,
                                  reprice_attempts=1)
    assert len(broker.placed) == 1
    assert report.reconciliation_required
    assert report.reason == "broker_place_order_failed"


def test_router_records_monotonic_stage_timings(monkeypatch):
    import itertools
    import option_execution_router as router
    ticks = itertools.count(0, 1_000_000)
    monkeypatch.setattr(router.time, "perf_counter_ns", lambda: next(ticks))
    broker = FakeBroker(fills=[{"status": "COMPLETE", "filledshares": 50, "averageprice": 100}])
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50, decision_price=100)
    assert report.ok
    assert report.stage_latency_ms == {"quote_lookup": 1, "submit_to_ack": 1, "fill_wait": 1}


def test_complete_status_cannot_override_explicit_zero_fill_quantity():
    broker = FakeBroker(fills=[{"status": "COMPLETE", "filledshares": 0, "quantity": 50, "averageprice": 100}])
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50, decision_price=100)
    assert not report.ok and report.filled_qty == 0
    assert report.reconciliation_required


def test_limit_price_is_not_fabricated_into_missing_fill_price():
    broker = FakeBroker(fills=[{"status": "COMPLETE", "filledshares": 50}])
    report = execute_option_entry(broker=broker, symbol="TESTCE", qty=50, decision_price=100)
    assert report.filled_qty == 50
    assert report.fill_price == 0 and report.reconciliation_required


# Run in an empty working directory so this file never writes into the live bot's
# relative-path runtime state (2026-10-17 write audit; see conftest.isolated_cwd).
import pytest as _pytest_iso  # noqa: E402
pytestmark = _pytest_iso.mark.usefixtures("isolated_cwd")
