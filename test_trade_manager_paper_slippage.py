"""Regression tests for a 2026-07-28 audit finding: the primary paper-fill
path in trade_manager.py recorded entry/exit prices as exact, instant LTP
with zero slippage -- estimate_slippage() existed but was never called.
Both open_trade() and _close_single_trade_by_id() now apply it to the pure
PAPER path (no real broker fill available)."""
import pytest

from trade_manager import TradeManager, estimate_slippage, simulate_paper_fill


def _manager(tmp_path, **kwargs):
    return TradeManager(
        broker_manager=None,
        alert_manager=None,
        capital=100_000,
        max_open_positions=5,
        db_path=str(tmp_path / "trades.db"),
        restore_state=False,
        **kwargs,
    )


def test_estimate_slippage_is_adverse_to_the_order_side():
    buy_slip = estimate_slippage("RELIANCE", 1000.0, "BUY")
    sell_slip = estimate_slippage("RELIANCE", 1000.0, "SELL")
    assert buy_slip > 0     # buyer pays MORE than quoted
    assert sell_slip < 0    # seller receives LESS than quoted
    assert buy_slip == -sell_slip


def test_estimate_slippage_scales_with_liquidity_tier():
    index_slip = estimate_slippage("NIFTY", 20000.0, "BUY")
    largecap_slip = estimate_slippage("RELIANCE", 20000.0, "BUY")
    smallcap_slip = estimate_slippage("SOMEOBSCURESTOCK", 20000.0, "BUY")
    assert index_slip < largecap_slip < smallcap_slip


def test_quote_driven_paper_fill_uses_ask_and_available_quantity():
    fill = simulate_paper_fill(
        "RELIANCE", 100.0, "BUY", 10,
        {"bid": 99.0, "ask": 101.0, "ask_qty": 4},
        latency_ms=500,
    )
    assert fill["status"] == "PARTIAL"
    assert fill["fill_qty"] == 4
    assert fill["fill_price"] > 101.0
    assert fill["quote_driven"] is True


def test_quote_driven_paper_fill_refuses_zero_liquidity():
    fill = simulate_paper_fill(
        "RELIANCE", 100.0, "BUY", 10,
        {"bid": 99.0, "ask": 101.0, "ask_qty": 0},
    )
    assert fill["status"] == "UNFILLED"
    assert fill["fill_qty"] == 0


def test_open_trade_paper_fill_applies_slippage_to_entry_price(tmp_path):
    manager = _manager(tmp_path)
    trade_id = manager.open_trade(
        symbol="RELIANCE", side="BUY", strategy="TEST",
        entry_price=1000.0, stop_loss=990.0, target_price=1030.0,
        score=8.0, regime="TREND", atr=5.0, qty_override=10,
    )
    assert trade_id is not None
    trade = manager.open_trades[trade_id]
    expected = 1000.0 + estimate_slippage("RELIANCE", 1000.0, "BUY")
    assert trade.entry_price == pytest.approx(expected)
    assert trade.entry_price > 1000.0  # buying: realistic fill is worse (higher)


def test_close_single_trade_paper_fill_applies_slippage_to_exit_price(tmp_path):
    manager = _manager(tmp_path)
    trade_id = manager.open_trade(
        symbol="RELIANCE", side="BUY", strategy="TEST",
        entry_price=1000.0, stop_loss=990.0, target_price=1030.0,
        score=8.0, regime="TREND", atr=5.0, qty_override=10,
    )
    assert trade_id is not None

    manager._close_single_trade_by_id(trade_id, exit_price=1020.0, exit_reason="target_hit")
    closed = manager.closed_trades[-1]
    expected = 1020.0 + estimate_slippage("RELIANCE", 1020.0, "SELL")
    assert closed.exit_price == pytest.approx(expected)
    assert closed.exit_price < 1020.0  # selling to close: realistic fill is worse (lower)
