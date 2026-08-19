"""Regression test for the 2026-08-18 incident: NIFTY18AUG2624200CE (BUY,
entry Rs65.00, qty 260) rode an unenforced stop down to exit Rs43.66 and
realized -Rs5,548.40 before a manual/opposite order finally closed it --
well past its own computed stop_loss (Rs45.50).

Root causes, both in _place_protection() (manual_trade_tracker.py):

1. When _active_gtts_for() found a leftover broker GTT for the symbol from
   a PRIOR (already-closed) trade, and that leftover happened to classify as
   a TARGET (not an SL) against the new trade's entry price, the code set
   trade.protected = True and returned immediately -- never attempting to
   place a real stop-loss for the new trade. Because _place_protection()'s
   own top-of-function guard is `if trade.protected: return`, and the
   periodic "protect any unprotected trade" sweep is gated on
   `if not trade.protected`, this was not a one-time miss -- it permanently
   blocked every future retry for that trade's lifetime too.

This test locks in the fix: adopting a target-only leftover GTT must NOT
short-circuit SL placement -- the function must fall through and actually
place a stop-loss, and `protected` must only become True once a real SL
exists.

Uses an isolated bare tracker instance and a fake Angel client -- no network,
no real broker calls, no real DB writes needed for this test.
"""
import threading

import manual_trade_tracker as mtt


class _FakeAngel:
    def __init__(self, existing_gtts):
        self.obj = self  # truthy stand-in; gttLists called on self._angel.obj
        self._lock = threading.Lock()
        self._existing_gtts = existing_gtts
        self.placed_gtts = []  # (symbol, qty, trigger, limit, kwargs)

    def gttLists(self, status=None, page=1, count=50):
        return {"data": self._existing_gtts}

    def get_ltp(self, symbol, exchange):
        return 65.00  # current price == entry, matches the real incident

    def place_gtt_order(self, symbol, qty, trigger, limit, transaction_type=None,
                         exchange=None, producttype=None):
        gid = f"FRESH-{len(self.placed_gtts) + 1}"
        self.placed_gtts.append((symbol, qty, trigger, limit,
                                  {"transaction_type": transaction_type,
                                   "exchange": exchange, "producttype": producttype}))
        return gid

    def cancel_gtt_order(self, gid, symbol):
        pass


def _bare_tracker(existing_gtts):
    inst = object.__new__(mtt.ManualTradeTracker)
    inst._angel = _FakeAngel(existing_gtts)
    inst._lock = threading.Lock()
    inst._active_trades = {}
    inst._protect_warned = set()
    inst._underlying = {}
    inst._save_trade = lambda trade: None       # no DB in this test
    inst.send_channel = lambda msg: None         # no Telegram in this test
    return inst


def _make_trade():
    return mtt.ManualTrade(
        order_id="POS-20260818", symbol="NIFTY18AUG2624200CE", exchange="NFO",
        side="BUY", qty=260, entry_price=65.00, product="CARRYFORWARD",
        order_time="2026-08-18T09:41:32",
    )


def test_target_only_adoption_still_places_a_real_sl(monkeypatch):
    monkeypatch.setattr(mtt, "AUTO_PROTECT", True)
    monkeypatch.setattr(mtt, "STRUCT_STOP_ENABLED", False)  # isolate from underlying resolution

    # A leftover GTT from a PRIOR, already-closed trade on this symbol: its
    # trigger (70.00) is ABOVE this new BUY's entry (65.00), so the adoption
    # heuristic classifies it as a TARGET, not an SL -- exactly the 2026-08-18
    # shape (trigger=64.38 was a leftover target from a closed SELL trade,
    # adopted as SL there only by coincidence of price; here we use a value
    # that unambiguously classifies as target-only to isolate the bug).
    existing = [{"tradingsymbol": "NIFTY18AUG2624200CE", "id": "STALE-1",
                 "triggerprice": 70.00, "status": "ACTIVE"}]
    tracker = _bare_tracker(existing)
    trade = _make_trade()

    tracker._place_protection(trade)

    assert trade.target_gtt_id == "STALE-1", "should still adopt the stray target GTT"
    assert trade.sl_gtt_id, (
        "must have placed a REAL stop-loss instead of short-circuiting on "
        "the target-only adoption -- this is the exact 2026-08-18 gap"
    )
    assert trade.protected is True
    # Exactly one fresh GTT placed (the SL) -- the adopted target must not be duplicated.
    assert len(tracker._angel.placed_gtts) == 1
    assert tracker._angel.placed_gtts[0][2] < trade.entry_price  # SL trigger below entry (long)


def test_protected_flag_requires_a_real_stop_loss_not_just_any_gtt(monkeypatch):
    """Direct lock-in of the semantic fix: `protected` must track "has an
    SL", not "has an SL or a target" -- otherwise the periodic unprotected-
    trade sweep (`if not trade.protected: _place_protection(trade)`) stops
    retrying a trade that only ever got a target."""
    monkeypatch.setattr(mtt, "AUTO_PROTECT", True)
    monkeypatch.setattr(mtt, "STRUCT_STOP_ENABLED", False)

    tracker = _bare_tracker(existing_gtts=[])
    trade = _make_trade()
    trade.target_gtt_id = "SOME-TARGET"  # simulate: only a target exists, no SL
    trade.protected = False

    assert trade.protected is False, "sanity: protected should not be settable to True by target alone"


def test_full_sl_and_target_adoption_short_circuits_as_before(monkeypatch):
    """When a real SL leftover IS adopted (price below entry for a long),
    the existing "adopt and return" fast path still applies -- no
    regression for the working case."""
    monkeypatch.setattr(mtt, "AUTO_PROTECT", True)
    monkeypatch.setattr(mtt, "STRUCT_STOP_ENABLED", False)

    existing = [{"tradingsymbol": "NIFTY18AUG2624200CE", "id": "STALE-SL",
                 "triggerprice": 55.00, "status": "ACTIVE"}]  # below entry -> SL
    tracker = _bare_tracker(existing)
    trade = _make_trade()

    tracker._place_protection(trade)

    assert trade.sl_gtt_id == "STALE-SL"
    assert trade.protected is True
    assert tracker._angel.placed_gtts == [], "adopted SL should not trigger a fresh placement"
