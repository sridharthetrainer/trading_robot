from option_execution_router import execute_option_entry


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
