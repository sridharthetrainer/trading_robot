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


class _FakeSmartConnect:
    """Stands in for SmartApi.SmartConnect -- just enough surface for
    AngelOne.connect() to run without a real network call."""
    def __init__(self, api_key=None):
        pass

    def generateSession(self, clientCode, password, totp):
        return {"data": {"jwtToken": "t", "refreshToken": "r"}}

    def getfeedToken(self):
        return "f"


def test_connect_paces_login_through_request_governor(monkeypatch):
    """Regression for a 2026-08-06 finding: RECONNECT_MIN_INTERVAL was a
    per-instance (== per-process) guard, so main_autonomous.py,
    manual_trade_tracker.py, trade_guardian_bot.py, and
    option_chain_recorder.py -- 4 separate processes sharing one Angel
    account -- each independently allowed themselves one login attempt per
    interval, colliding on Angel's actual account-wide login rate limit
    ("Angel login throttled" appeared 58+ times in one day across 3
    services, blocking live option-chain fetches). The guard only fires
    when self.obj is already set too -- a fresh/lost connection (the common
    trigger) always retried immediately with zero cross-process awareness."""
    calls = []
    monkeypatch.setattr(
        request_governor, "acquire",
        lambda provider, interval: calls.append((provider, interval)) or 0.0,
    )
    monkeypatch.setattr(angel, "SmartConnect", _FakeSmartConnect)
    monkeypatch.setattr(angel.pyotp, "TOTP", lambda secret: type("_T", (), {"now": lambda self: "000000"})())

    inst = object.__new__(angel.AngelOne)
    inst.paper_trade = False
    inst.obj = None
    inst._lock = threading.Lock()
    inst._rate_limited_until = 0.0
    inst._last_connect_ts = 0.0
    inst.api_key = "k"
    inst.client_id = "c"
    inst.password = "p"
    inst.totp_secret = "s"

    assert inst.connect() is True
    assert ("angel_login", angel.RECONNECT_MIN_INTERVAL) in calls
