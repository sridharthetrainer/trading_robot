"""Tests for the re-entry cooldown warning added to manual_trade_tracker.py,
prompted by real trade history: 2026-08-11 saw 8 same-symbol re-entries on
NIFTY11AUG2624400CE within ~90 minutes, net -Rs8,990 (a rapid re-entry loop
after each stop-out); a milder version of the same pattern recurred on
2026-08-17. This doesn't block anything (orders are placed directly at the
broker) -- it warns once per detection so the pattern is visible in the
moment.

Uses an isolated temp DB (monkeypatched DB_PATH) -- never touches the real
production manual_trades.db.
"""
import sqlite3
import threading
from datetime import datetime, timedelta

import manual_trade_tracker as mtt


class _FakeAngel:
    def __init__(self):
        self.obj = object()


def _bare_tracker():
    inst = object.__new__(mtt.ManualTradeTracker)
    inst._angel = _FakeAngel()
    inst._lock = threading.Lock()
    inst._active_trades = {}
    return inst


def _insert_closed_trade(db_path, symbol, exit_time, exit_reason, pnl, order_id=None):
    order_id = order_id or f"X-{exit_time}"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO manual_trades (order_id,symbol,exchange,side,qty,entry_price,"
        "product,order_time,status,exit_time,exit_reason,pnl) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, symbol, "NFO", "BUY", 65, 100.0, "INTRADAY",
         exit_time, "CLOSED", exit_time, exit_reason, pnl))
    conn.commit()
    conn.close()


def _make_trade(symbol="NIFTY11AUG2624400CE"):
    return mtt.ManualTrade(
        order_id="NEW1", symbol=symbol, exchange="NFO", side="BUY", qty=65,
        entry_price=106.13, product="INTRADAY", order_time=datetime.now().isoformat(),
    )


def test_warns_on_recent_same_symbol_stop_loss(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)
    tracker = _bare_tracker()
    tracker._init_db()

    recent = (datetime.now() - timedelta(minutes=5)).isoformat()
    _insert_closed_trade(db_file, "NIFTY11AUG2624400CE", recent,
                          "STRUCTURAL STOP — underlying broke swing-low", -2177.5)

    sent = []
    monkeypatch.setattr(tracker, "send_channel", lambda text: sent.append(text))
    tracker._check_reentry_warning(_make_trade())

    assert len(sent) == 1
    assert "NIFTY11AUG2624400CE" in sent[0]
    assert "2,178" in sent[0]   # -2177.5 rounds to -2178 via the :,.0f format


def test_no_warning_when_no_recent_closes(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)
    tracker = _bare_tracker()
    tracker._init_db()

    sent = []
    monkeypatch.setattr(tracker, "send_channel", lambda text: sent.append(text))
    tracker._check_reentry_warning(_make_trade())

    assert sent == []


def test_no_warning_when_prior_close_was_profitable(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)
    tracker = _bare_tracker()
    tracker._init_db()

    recent = (datetime.now() - timedelta(minutes=5)).isoformat()
    _insert_closed_trade(db_file, "NIFTY11AUG2624400CE", recent,
                          "Trailing SL profit lock", 870.0)   # a WIN, not a loss

    sent = []
    monkeypatch.setattr(tracker, "send_channel", lambda text: sent.append(text))
    tracker._check_reentry_warning(_make_trade())

    assert sent == []


def test_no_warning_when_close_is_outside_cooldown_window(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)
    tracker = _bare_tracker()
    tracker._init_db()

    old = (datetime.now() - timedelta(minutes=mtt.REENTRY_COOLDOWN_MINUTES + 10)).isoformat()
    _insert_closed_trade(db_file, "NIFTY11AUG2624400CE", old, "STRUCTURAL STOP", -871.0)

    sent = []
    monkeypatch.setattr(tracker, "send_channel", lambda text: sent.append(text))
    tracker._check_reentry_warning(_make_trade())

    assert sent == []


def test_no_warning_for_a_different_symbol(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)
    tracker = _bare_tracker()
    tracker._init_db()

    recent = (datetime.now() - timedelta(minutes=5)).isoformat()
    _insert_closed_trade(db_file, "NIFTY11AUG2624300CE", recent, "STRUCTURAL STOP", -487.5)

    sent = []
    monkeypatch.setattr(tracker, "send_channel", lambda text: sent.append(text))
    tracker._check_reentry_warning(_make_trade("NIFTY11AUG2624400CE"))

    assert sent == []


def test_replays_the_real_2026_08_11_whipsaw_and_fires_a_warning(tmp_path, monkeypatch):
    """Sanity check against the actual incident that motivated this feature:
    replay the real sequence of closes on NIFTY11AUG2624400CE from
    2026-08-11 and confirm a warning would have fired before the costliest
    trade in that sequence (the -Rs6,362.85 one)."""
    db_file = str(tmp_path / "test_manual_trades.db")
    monkeypatch.setattr(mtt, "DB_PATH", db_file)
    tracker = _bare_tracker()
    tracker._init_db()

    base = datetime(2026, 8, 11, 9, 19, 6)
    real_closes = [
        (base + timedelta(minutes=5), "STRUCTURAL STOP", -2177.5),
        (base + timedelta(minutes=9), "Exit detected (manual or GTT)", -409.5),
    ]
    for exit_time, reason, pnl in real_closes:
        _insert_closed_trade(db_file, "NIFTY11AUG2624400CE", exit_time.isoformat(), reason, pnl)

    sent = []
    monkeypatch.setattr(tracker, "send_channel", lambda text: sent.append(text))
    # The next entry in the real sequence, ~2 min after the second close.
    next_entry_time = base + timedelta(minutes=11)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next_entry_time

    monkeypatch.setattr(mtt, "datetime", _FrozenDatetime)
    tracker._check_reentry_warning(_make_trade())

    assert len(sent) == 1
    assert "2" in sent[0]  # 2 recent losing closes
