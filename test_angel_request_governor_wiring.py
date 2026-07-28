"""Regression for a 2026-07-28 audit finding: request_governor was only
wired to getCandleData -- placeOrder/modifyOrder/cancelOrder/GTT/searchScrip
could all still burst uncoordinated against Angel's account-wide rate limit.
These are low-frequency, order-lifecycle-event calls (not a per-symbol scan
loop), so pacing them is safe; ltpData/getProfile are deliberately excluded
(real per-symbol hot paths where blocking pacing would regress scan speed)."""
import threading

import angel
import request_governor


class _FakeObj:
    def __init__(self):
        self.calls = []

    def placeOrder(self, params):
        self.calls.append("placeOrder")
        return "ORDER123"

    def orderBook(self):
        return {"data": []}

    def modifyOrder(self, params):
        self.calls.append("modifyOrder")
        return {"status": True}

    def cancelOrder(self, order_id, variety):
        self.calls.append("cancelOrder")
        return {"status": True}

    def searchScrip(self, exchange, symbol):
        self.calls.append("searchScrip")
        return {"data": [{"symboltoken": "999"}]}


def _bare_angel(fake_obj):
    inst = object.__new__(angel.AngelOne)
    inst.paper_trade = False
    inst.block_real_orders = False
    inst.obj = fake_obj
    inst._obj = fake_obj
    inst._lock = threading.Lock()
    inst._rate_limited_until = 0.0
    inst._get_token_no_lock = lambda symbol, exchange: "12345"
    return inst


def test_place_order_paces_through_request_governor(monkeypatch):
    calls = []
    monkeypatch.setattr(
        request_governor, "acquire",
        lambda provider, interval: calls.append((provider, interval)) or 0.0,
    )
    inst = _bare_angel(_FakeObj())
    monkeypatch.setattr(inst, "_ensure_connected", lambda: True)
    inst.place_order("RELIANCE", 1, "BUY", price=100.0)
    assert ("angel_order_ops", angel.ORDER_API_MIN_INTERVAL_SEC) in calls


def test_search_scrip_paces_through_request_governor(monkeypatch):
    calls = []
    monkeypatch.setattr(
        request_governor, "acquire",
        lambda provider, interval: calls.append((provider, interval)) or 0.0,
    )
    inst = _bare_angel(_FakeObj())
    monkeypatch.setattr(inst, "_ensure_connected", lambda: True)
    inst._search_scrip_safe("RELIANCE", "NSE")
    assert ("angel_order_ops", angel.ORDER_API_MIN_INTERVAL_SEC) in calls


def test_modify_and_cancel_order_pace_through_request_governor(monkeypatch):
    calls = []
    monkeypatch.setattr(
        request_governor, "acquire",
        lambda provider, interval: calls.append((provider, interval)) or 0.0,
    )
    inst = _bare_angel(_FakeObj())
    inst.modify_order("ORDER123", new_sl=95.0)
    inst.cancel_order("ORDER123")
    assert calls.count(("angel_order_ops", angel.ORDER_API_MIN_INTERVAL_SEC)) == 2
