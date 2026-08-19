"""Tests for margin_circuit_breaker.py -- the auto-liquidation gap found in
the 2026-08-19 spec audit (MarginFeed existed with zero callers; nothing
closed existing positions on a margin trigger).

Uses fake Angel/TradeManager doubles -- no network, no real broker calls.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import margin_circuit_breaker as mcb


# ── Fakes ────────────────────────────────────────────────────────────────

class _FakeAngelObj:
    def __init__(self, rms_data):
        self._rms_data = rms_data

    def rmsLimit(self):
        if self._rms_data is None:
            raise RuntimeError("simulated broker error")
        return {"data": self._rms_data}


class _FakeAngel:
    def __init__(self, rms_data):
        self.obj = _FakeAngelObj(rms_data)
        self._lock = _NullLock()


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@dataclass
class _FakeTrade:
    trade_id: str
    symbol: str
    entry_price: float
    is_option: bool = False
    is_swing: bool = False
    side: str = "BUY"
    qty: int = 1


class _FakeTradeManager:
    """Minimal stand-in exposing exactly the surface margin_circuit_breaker
    uses: open_trades, _is_option_trade, _is_swing_trade, _trade_exchange,
    _calculate_pnl, _close_trade_internal -- same private-attribute pattern
    gap_risk_manager.py and live_signal_engine.py already use on the real
    TradeManager."""

    def __init__(self, trades):
        self.open_trades: Dict[str, _FakeTrade] = {t.trade_id: t for t in trades}
        self.closed: list = []
        self.fail_close_for: Optional[str] = None

    def _is_option_trade(self, trade):
        return trade.is_option

    def _is_swing_trade(self, trade):
        return trade.is_swing

    def _trade_exchange(self, trade):
        return "NFO" if trade.is_option else "NSE"

    def _calculate_pnl(self, trade, mark, is_options=True):
        sign = 1 if trade.side == "BUY" else -1
        return sign * (mark - trade.entry_price) * trade.qty

    def _close_trade_internal(self, trade_id, exit_price, reason, exchange):
        if trade_id == self.fail_close_for:
            return False
        trade = self.open_trades.pop(trade_id, None)
        if trade is None:
            return False
        self.closed.append((trade_id, exit_price, reason))
        return True


def _ltp_map(prices):
    def _getter(symbol, exchange):
        return prices.get(symbol)
    return _getter


# ── compute_margin_utilization ──────────────────────────────────────────

def test_parses_standard_rms_response():
    angel = _FakeAngel({"availablecash": "100000", "utiliseddebits": "400000"})
    margin = mcb.compute_margin_utilization(angel)
    assert margin is not None
    assert margin["ratio"] == 0.8


def test_returns_none_when_keys_missing_never_guesses():
    angel = _FakeAngel({"someOtherField": "123"})
    assert mcb.compute_margin_utilization(angel) is None


def test_returns_none_on_broker_error():
    angel = _FakeAngel(None)
    assert mcb.compute_margin_utilization(angel) is None


def test_returns_none_when_angel_not_connected():
    class _Disconnected:
        obj = None
    assert mcb.compute_margin_utilization(_Disconnected()) is None


# ── run_margin_circuit_breaker: no-op paths ─────────────────────────────

def test_noop_when_margin_unavailable():
    angel = _FakeAngel(None)
    tm = _FakeTradeManager([_FakeTrade("T1", "NIFTY", 100, is_option=True)])
    result = mcb.run_margin_circuit_breaker(angel, tm)
    assert result["checked"] is False
    assert result["triggered"] is False
    assert tm.open_trades  # nothing touched


def test_noop_when_under_trigger_ratio():
    angel = _FakeAngel({"availablecash": "300000", "utiliseddebits": "700000"})  # 70%
    tm = _FakeTradeManager([_FakeTrade("T1", "NIFTY", 100, is_option=True)])
    result = mcb.run_margin_circuit_breaker(angel, tm)
    assert result["triggered"] is False
    assert tm.open_trades


# ── priority ordering: MIS -> options -> futures/swing ──────────────────

def test_closes_mis_before_options_before_swing_futures():
    """Same scenario each round: ratio stays above target until all three
    close, forcing the breaker through the full priority order."""
    ratios = iter([
        {"availablecash": "100000", "utiliseddebits": "500000"},  # 83% trigger
        {"availablecash": "150000", "utiliseddebits": "450000"},  # 75% after MIS close, still >60
        {"availablecash": "200000", "utiliseddebits": "400000"},  # 66.7% after options close, still >60
        {"availablecash": "300000", "utiliseddebits": "300000"},  # 50% after futures close -> stop
    ])

    class _SequencedAngel(_FakeAngel):
        def __init__(self):
            self.obj = self
            self._lock = _NullLock()

        def rmsLimit(self):
            return {"data": next(ratios)}

    tm = _FakeTradeManager([
        _FakeTrade("FUT1", "NIFTYFUT", 100, is_option=False, is_swing=True),
        _FakeTrade("OPT1", "NIFTY24000CE", 100, is_option=True),
        _FakeTrade("MIS1", "RELIANCE", 100, is_option=False, is_swing=False),
    ])
    result = mcb.run_margin_circuit_breaker(_SequencedAngel(), tm, ltp_getter=_ltp_map({}))

    assert result["triggered"] is True
    order = [c["trade_id"] for c in result["closed"]]
    assert order == ["MIS1", "OPT1", "FUT1"], f"wrong priority order: {order}"


def test_lowest_unrealized_pnl_closed_first_within_same_priority_bucket():
    angel = _FakeAngel({"availablecash": "100000", "utiliseddebits": "500000"})  # 83.3%, above trigger
    tm = _FakeTradeManager([
        _FakeTrade("MIS_WINNER", "TCS", entry_price=100, side="BUY"),
        _FakeTrade("MIS_LOSER", "INFY", entry_price=100, side="BUY"),
    ])
    # After the loser closes, ratio still shows 80% (fake angel is static) so
    # the loop would try to close the winner too -- cap MAX_CLOSES via a
    # target that's reached isn't testable with a static angel, so instead
    # verify ordering by checking which one is closed FIRST.
    prices = {"TCS": 150, "INFY": 50}  # winner up 50, loser down 50
    result = mcb.run_margin_circuit_breaker(angel, tm, ltp_getter=_ltp_map(prices))

    assert result["closed"][0]["trade_id"] == "MIS_LOSER", (
        "the most-losing position within the same priority bucket must close first"
    )


def test_stops_once_target_ratio_reached():
    ratios = iter([
        {"availablecash": "100000", "utiliseddebits": "500000"},  # 83.3% trigger
        {"availablecash": "500000", "utiliseddebits": "500000"},  # 50% after first close -> stop
    ])

    class _SequencedAngel(_FakeAngel):
        def __init__(self):
            self.obj = self
            self._lock = _NullLock()

        def rmsLimit(self):
            return {"data": next(ratios)}

    tm = _FakeTradeManager([
        _FakeTrade("MIS1", "RELIANCE", 100),
        _FakeTrade("MIS2", "TCS", 100),
    ])
    result = mcb.run_margin_circuit_breaker(_SequencedAngel(), tm, ltp_getter=_ltp_map({}))

    assert len(result["closed"]) == 1, "must stop as soon as the ratio is back under target"
    assert len(tm.open_trades) == 1


def test_stops_if_a_close_fails_instead_of_retry_looping():
    angel = _FakeAngel({"availablecash": "100000", "utiliseddebits": "500000"})  # 83%
    tm = _FakeTradeManager([_FakeTrade("MIS1", "RELIANCE", 100)])
    tm.fail_close_for = "MIS1"

    result = mcb.run_margin_circuit_breaker(angel, tm)

    assert result["closed"] == []
    assert "MIS1" in tm.open_trades, "a failed close must leave the position open, not vanish"


def test_stops_if_no_candidates_left_even_above_trigger():
    angel = _FakeAngel({"availablecash": "100000", "utiliseddebits": "500000"})  # 83%
    tm = _FakeTradeManager([])  # nothing open, yet margin is somehow over trigger

    result = mcb.run_margin_circuit_breaker(angel, tm)

    assert result["triggered"] is True
    assert result["closed"] == []
