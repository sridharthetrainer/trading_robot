"""Regression for the 2026-08-11 incident: NIFTY11AUG2624400CE (an INTRADAY
long) had its fallback GTT stop placed as CARRYFORWARD -- angel.py's
place_gtt_order() hardcoded that product type and manual_trade_tracker.py's
four call sites had no way to override it. When premium crashed from ~108 to
71.2, the GTT never executed the close (broker can't net a CARRYFORWARD
square-off against an INTRADAY holding -- the exact mismatch already
documented at manual_trade_tracker.py:2139 for the direct-order path, which
was fixed there but not here). The position ran unprotected for 87 minutes
and was only closed by a manual opposite-side order, at -Rs6,362.85 instead
of the near-breakeven the SL trigger should have capped it at.

Fix: place_gtt_order() takes a producttype param (default CARRYFORWARD,
unchanged for the swing-position caller in trade_manager.py which is
genuinely CARRYFORWARD); manual_trade_tracker.py's four GTT call sites now
pass trade.product through, same pattern already used by _square_off and
_book_partial_profit."""
import threading

import angel
import request_governor


class _FakeObj:
    def __init__(self):
        self.gtt_calls = []

    def gttCreateRule(self, params):
        self.gtt_calls.append(dict(params))
        return {"status": True, "data": {"id": "GTT1"}}


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


def test_place_gtt_order_defaults_to_carryforward(monkeypatch):
    """Unchanged default -- the swing-position caller in trade_manager.py
    relies on this and must keep working without passing producttype."""
    monkeypatch.setattr(request_governor, "acquire", lambda *a, **k: 0.0)
    fake = _FakeObj()
    inst = _bare_angel(fake)
    monkeypatch.setattr(inst, "_ensure_connected", lambda: True)
    inst.place_gtt_order("NIFTY11AUG2624400CE", 195, 106.81, 105.7)
    assert fake.gtt_calls[0]["producttype"] == "CARRYFORWARD"


def test_place_gtt_order_honours_intraday_producttype(monkeypatch):
    """The fix: an INTRADAY position's GTT must be placed as INTRADAY so the
    broker can actually net the close against the held position."""
    monkeypatch.setattr(request_governor, "acquire", lambda *a, **k: 0.0)
    fake = _FakeObj()
    inst = _bare_angel(fake)
    monkeypatch.setattr(inst, "_ensure_connected", lambda: True)
    inst.place_gtt_order(
        "NIFTY11AUG2624400CE", 195, 106.81, 105.7,
        producttype="INTRADAY",
    )
    assert fake.gtt_calls[0]["producttype"] == "INTRADAY"
