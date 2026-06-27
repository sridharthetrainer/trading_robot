from capital_compounder import calculate_full_costs
from trade_economics import evaluate_trade_economics
from trade_manager import ManagedTrade, TradeManager


def test_2026_option_and_cash_cost_rates_are_segment_specific():
    option = calculate_full_costs(200.0, 210.0, 50, is_options=True)
    intraday = calculate_full_costs(
        1000.0, 1010.0, 10, is_options=False, product_type="INTRADAY"
    )
    delivery = calculate_full_costs(
        1000.0, 1010.0, 10, is_options=False, product_type="DELIVERY"
    )

    assert round(option.exchange_charge, 4) == round((200 + 210) * 50 * 0.0003553, 4)
    assert intraday.exchange_charge < 1.0
    assert delivery.stt > intraday.stt


def test_trade_economics_requires_positive_after_cost_expectancy():
    result = evaluate_trade_economics(
        entry_price=200,
        target_price=210,
        stop_loss=195,
        qty=50,
        side="BUY",
        win_probability=0.60,
        is_options=True,
        min_expected_net=25,
    )

    assert result.allowed is True
    assert result.expected_net >= 25
    assert result.win_probability > result.breakeven_probability


def test_trade_economics_blocks_low_probability_or_tiny_target():
    low_probability = evaluate_trade_economics(
        entry_price=200,
        target_price=210,
        stop_loss=195,
        qty=50,
        side="BUY",
        win_probability=0.40,
        is_options=True,
        min_expected_net=25,
    )
    tiny_target = evaluate_trade_economics(
        entry_price=200,
        target_price=201,
        stop_loss=195,
        qty=50,
        side="BUY",
        win_probability=0.80,
        is_options=True,
        min_expected_net=0,
    )

    assert low_probability.allowed is False
    assert low_probability.reason == "win_probability_below_after_cost_breakeven"
    assert tiny_target.allowed is False
    assert tiny_target.reason == "target_does_not_cover_cost_hurdle"


def test_eod_close_defers_when_quote_is_missing():
    manager = TradeManager.__new__(TradeManager)
    trade = ManagedTrade(
        trade_id="T1",
        symbol="RELIANCE",
        side="BUY",
        qty=1,
        strategy="TREND",
        broker_name="PAPER",
        order_id="PAPER-1",
        entry_price=100.0,
        entry_time=1.0,
        metadata={"asset_type": "CASH", "style": "intraday"},
    )
    manager.open_trades = {trade.trade_id: trade}
    manager._trade_exchange = lambda _trade: "NSE"
    manager._is_swing_trade = lambda _trade: False
    manager._is_option_trade = lambda _trade: False
    manager._close_trade_internal = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("must not close without a trustworthy quote")
    )

    closed = manager.close_positions_at_eod(ltp_getter=lambda *_args: None)

    assert closed == 0
    assert "T1" in manager.open_trades
