"""Regression tests for the 2026-09-21 GTT-replacement reconciliation fix
(_replace_sl / _replace_target / _reconcile_replacement in
manual_trade_tracker.py).

Production defect this locks in a fix for (found during a safety audit, not
evidence about whether the dynamic exit strategy improves expectancy --
see RESEARCH_QUEUE_2026-09-13.md): place_gtt_order() collapses a genuine
broker rejection and a client-side timeout into the same `None` return
(angel.py's broad `except Exception: return None` around gttCreateRule).
The old _replace_sl/_replace_target simply returned on `None`, assuming
"nothing happened" -- but if the broker actually created the new GTT and
only the response was lost, that GTT was never recorded locally and never
cancelled, leaving TWO live SL/target GTTs on one position with zero local
awareness of the second.

Each test captures the four states the audit asked for implicitly through
assertions: broker GTTs (_FakeAngel.gtts), broker calls made (placed/
cancelled lists), local trade state (trade.sl_gtt_id / sl_reconcile_target),
and the alerts sent (a proxy for "system's interpreted protection state").

Uses an isolated bare tracker instance and a fake Angel client -- no
network, no real broker calls, no real DB writes.
"""
import threading

import manual_trade_tracker as mtt


class _FakeAngel:
    """gtts: current live broker GTTs (list of dicts: id, tradingsymbol,
    status, triggerprice). place_gtt_order_returns: value to return from the
    NEXT place_gtt_order call -- None simulates the ambiguous case this fix
    is about."""

    def __init__(self, gtts, place_gtt_order_returns=None,
                 gtt_lists_raises: bool = False):
        self.obj = self
        self._lock = threading.Lock()
        self.gtts = list(gtts)
        self._next_return = place_gtt_order_returns
        self._gtt_lists_raises = gtt_lists_raises
        self.placed = []     # (symbol, qty, trigger, limit, kwargs)
        self.cancelled = []  # gtt ids

    def gttLists(self, status=None, page=1, count=50):
        if self._gtt_lists_raises:
            raise ConnectionError("simulated broker query failure")
        return {"data": self.gtts}

    def place_gtt_order(self, symbol, qty, trigger, limit,
                         transaction_type=None, exchange=None, producttype=None):
        self.placed.append((symbol, qty, trigger, limit,
                             {"transaction_type": transaction_type,
                              "exchange": exchange, "producttype": producttype}))
        return self._next_return

    def cancel_gtt_order(self, gid, symbol):
        self.cancelled.append(gid)
        return True


def _bare_tracker(angel):
    inst = object.__new__(mtt.ManualTradeTracker)
    inst._angel = angel
    inst._lock = threading.Lock()
    inst._active_trades = {}
    inst._protect_warned = set()
    inst._underlying = {}
    inst.alerts = []
    inst.saved = []
    inst._save_trade = lambda trade: inst.saved.append(trade)
    inst.send_channel = lambda msg: inst.alerts.append(msg)
    return inst


def _make_trade(sl_gtt_id="OLD-1"):
    t = mtt.ManualTrade(
        order_id="POS-TEST1", symbol="NIFTY18AUG2624200CE", exchange="NFO",
        side="BUY", qty=260, entry_price=65.00, product="CARRYFORWARD",
        order_time="2026-09-21T09:30:00",
    )
    t.sl_gtt_id = sl_gtt_id
    t.stop_loss = 60.0
    t.trailing_sl = 60.0
    return t


def _gtt(gid, trigger, symbol="NIFTY18AUG2624200CE", status="NEW"):
    return {"id": gid, "tradingsymbol": symbol, "status": status,
            "triggerprice": str(trigger)}


# -- 1. Confirmed success: unchanged happy path --------------------------

def test_confirmed_success_places_new_cancels_old_no_pending_state():
    old_gtt = _gtt("OLD-1", 60.0)
    angel = _FakeAngel(gtts=[old_gtt], place_gtt_order_returns="NEW-1")
    tr = _bare_tracker(angel)
    trade = _make_trade()

    tr._replace_sl(trade, 62.0)

    assert trade.sl_gtt_id == "NEW-1"
    assert trade.stop_loss == 62.0
    assert trade.sl_reconcile_target == 0.0
    assert angel.cancelled == ["OLD-1"]
    assert len(angel.placed) == 1


# -- 2. Genuine rejection / request never reached broker ------------------

def test_genuine_rejection_leaves_old_gtt_intact_and_clears_pending():
    old_gtt = _gtt("OLD-1", 60.0)
    # Broker state after the "failed" attempt shows ONLY the old GTT --
    # nothing new was ever created.
    angel = _FakeAngel(gtts=[old_gtt], place_gtt_order_returns=None)
    tr = _bare_tracker(angel)
    trade = _make_trade()

    tr._replace_sl(trade, 62.0)

    # Old GTT must still be the one tracked and it must NOT be cancelled.
    assert trade.sl_gtt_id == "OLD-1"
    assert trade.stop_loss == 60.0  # unchanged -- replacement never happened
    assert trade.sl_reconcile_target == 0.0  # resolved: confirmed no-op
    assert angel.cancelled == []
    assert any("did not" in m or "remains active" in m for m in tr.alerts)


# -- 3. Timeout-but-created: adopted via reconciliation -------------------

def test_timeout_but_created_is_adopted_via_reconciliation():
    old_gtt = _gtt("OLD-1", 60.0)
    new_gtt = _gtt("NEW-GHOST-1", 62.0)  # broker actually created this
    angel = _FakeAngel(gtts=[old_gtt, new_gtt], place_gtt_order_returns=None)
    tr = _bare_tracker(angel)
    trade = _make_trade()

    tr._replace_sl(trade, 62.0)

    assert trade.sl_gtt_id == "NEW-GHOST-1"
    assert trade.stop_loss == 62.0
    assert trade.sl_reconcile_target == 0.0
    assert angel.cancelled == ["OLD-1"]
    assert any("confirmed via reconciliation" in m for m in tr.alerts)


# -- 4. Reconciliation itself fails: must not fall through to a 2nd order -

def test_reconciliation_failure_does_not_trigger_a_second_placement():
    old_gtt = _gtt("OLD-1", 60.0)
    angel = _FakeAngel(gtts=[old_gtt], place_gtt_order_returns=None,
                        gtt_lists_raises=True)
    tr = _bare_tracker(angel)
    trade = _make_trade()

    tr._replace_sl(trade, 62.0)
    assert trade.sl_reconcile_target == 62.0  # still pending -- unresolved
    assert len(angel.placed) == 1  # the one original attempt, nothing more

    # Simulate the next cycle calling _replace_sl again with a fresh
    # (even tighter) level while still pending. Must route straight to
    # reconciliation, never place another GTT.
    tr._replace_sl(trade, 63.0)
    assert len(angel.placed) == 1, "must not place a second GTT while ambiguous"
    assert trade.sl_reconcile_target == 62.0  # unchanged -- still the original target

    # Now the broker query recovers and confirms nothing new was created.
    angel._gtt_lists_raises = False
    tr._reconcile_replacement(trade, is_sl=True)
    assert trade.sl_reconcile_target == 0.0
    assert trade.sl_gtt_id == "OLD-1"
    assert len(angel.placed) == 1


# -- 5. Ambiguous (2+ matches): stays pending, never guesses --------------

def test_ambiguous_multiple_matches_stays_pending_no_retry_placement():
    old_gtt = _gtt("OLD-1", 60.0)
    ghost_a = _gtt("GHOST-A", 62.0)
    ghost_b = _gtt("GHOST-B", 62.05)  # within tolerance of the same target
    angel = _FakeAngel(gtts=[old_gtt, ghost_a, ghost_b],
                        place_gtt_order_returns=None)
    tr = _bare_tracker(angel)
    trade = _make_trade()

    tr._replace_sl(trade, 62.0)

    assert trade.sl_gtt_id == "OLD-1"       # unresolved -- old still tracked
    assert trade.sl_reconcile_target == 62.0  # stays pending
    assert angel.cancelled == []
    assert any("AMBIGUOUS" in m for m in tr.alerts)
    n_alerts = len(tr.alerts)

    # Calling again (next cycle) with a new candidate level must not place
    # another GTT, and must not spam a second identical alert.
    tr._replace_sl(trade, 64.0)
    assert len(angel.placed) == 1
    assert len(tr.alerts) == n_alerts, "ambiguous alert must be rate-limited, not repeated"


# -- 6. Restart-safety: a pending state loaded fresh from DB must also gate -

def test_pending_state_from_a_simulated_restart_still_blocks_new_placement():
    old_gtt = _gtt("OLD-1", 60.0)
    angel = _FakeAngel(gtts=[old_gtt], place_gtt_order_returns="SHOULD-NOT-BE-USED")
    tr = _bare_tracker(angel)
    trade = _make_trade()
    # Simulate what loading from DB after a restart would restore: a
    # pending reconciliation target from before the crash.
    trade.sl_reconcile_target = 61.5

    tr._replace_sl(trade, 63.0)

    assert len(angel.placed) == 0, "must reconcile, not place, when resuming a pending state"
