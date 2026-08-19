"""Test for the 2026-08-19 fix to TelegramCommands._cmd_kill(): it used to
close ALL open positions on a single /kill message with zero confirmation --
one fat-fingered or misdirected message instantly liquidated everything.
Now requires a second explicit "/kill CONFIRM" within 60 seconds.

Uses the unbound-method + minimal-stub pattern established throughout this
session -- TelegramCommands' constructor has too large a dependency surface
(bot API polling, menu state, etc) for a single-method test.
"""
import time
import types

from telegram_commands import TelegramCommandHandler as TelegramCommands


class _FakeTradeManager:
    def __init__(self, open_trades, closed_count=0):
        self.open_trades = open_trades
        self._closed_count = closed_count

    def close_all_trades(self, reason=""):
        n = self._closed_count or len(self.open_trades)
        self.open_trades = {}
        return n


def _make_stub(open_trades):
    tm = _FakeTradeManager(open_trades)
    bot = types.SimpleNamespace(live_engine=types.SimpleNamespace(trade_manager=tm))
    return types.SimpleNamespace(bot_ref=bot), tm


def test_first_call_asks_for_confirmation_does_not_close_anything():
    stub, tm = _make_stub({"T1": object(), "T2": object()})

    response = TelegramCommands._cmd_kill(stub, "/kill")

    assert "CONFIRM" in response
    assert "2" in response  # open position count
    assert tm.open_trades  # nothing closed yet


def test_confirm_within_window_closes_all():
    stub, tm = _make_stub({"T1": object()})
    TelegramCommands._cmd_kill(stub, "/kill")

    response = TelegramCommands._cmd_kill(stub, "/kill CONFIRM")

    assert "Closed" in response
    assert tm.open_trades == {}


def test_confirm_without_prior_request_is_rejected():
    stub, tm = _make_stub({"T1": object()})

    response = TelegramCommands._cmd_kill(stub, "/kill CONFIRM")

    assert "expired" in response.lower() or "not requested" in response.lower()
    assert tm.open_trades  # nothing closed


def test_confirm_after_60s_window_expires():
    stub, tm = _make_stub({"T1": object()})
    TelegramCommands._cmd_kill(stub, "/kill")
    stub._kill_pending_at = time.time() - 61  # simulate an expired window

    response = TelegramCommands._cmd_kill(stub, "/kill CONFIRM")

    assert "expired" in response.lower()
    assert tm.open_trades


def test_confirm_is_single_use():
    """A second CONFIRM after a successful kill must not close anything
    again (no positions left, and the pending flag was consumed)."""
    stub, tm = _make_stub({"T1": object()})
    TelegramCommands._cmd_kill(stub, "/kill")
    TelegramCommands._cmd_kill(stub, "/kill CONFIRM")

    response = TelegramCommands._cmd_kill(stub, "/kill CONFIRM")

    assert "expired" in response.lower() or "not requested" in response.lower()
