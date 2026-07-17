import pytest

from nse_cost_model import NseCostModel


def test_april_2026_derivatives_rates_are_segment_specific():
    model = NseCostModel()
    futures = model.single_leg_cost(1_000_000, "FUT", "SELL", include_slippage=False)
    options = model.single_leg_cost(10_000, "OPT_BUY", "SELL", include_slippage=False)

    assert futures.stt == pytest.approx(500.0)
    assert futures.exchange_levy == pytest.approx(18.299)
    assert options.stt == pytest.approx(15.0)
    assert options.exchange_levy == pytest.approx(3.55299)


def test_angel_fno_brokerage_is_flat_per_executed_order():
    model = NseCostModel()
    assert model.brokerage_for(1_000, "FUT") == 20.0
    assert model.brokerage_for(1_000_000, "OPT_BUY") == 20.0


def test_short_option_round_trip_charges_stt_on_entry_premium():
    model = NseCostModel()
    cost = model.round_trip_cost(
        10_000,
        5_000,
        instrument="OPT_SELL",
        include_slippage=False,
    )
    entry = model.single_leg_cost(10_000, "OPT_SELL", "SELL", include_slippage=False)
    exit_ = model.single_leg_cost(5_000, "OPT_SELL", "BUY", include_slippage=False)
    assert cost == pytest.approx(round(entry.total + exit_.total, 2))
    assert entry.stt == pytest.approx(15.0)
    assert exit_.stt == 0.0


def test_capital_compounder_matches_side_aware_stt_convention():
    """Regression (2026-07-17): capital_compounder.calculate_full_costs — the
    model the shadow labeller actually uses via shadow_execution — charged
    STT on the exit leg unconditionally, flattering winning shorts (the
    sell-entry turnover is the LARGER leg exactly when a premium-seller
    wins). It must match NseCostModel's already-correct convention: STT on
    the sell leg, stamp duty on the buy leg, whichever end of the round
    trip those fall on."""
    from capital_compounder import STT_OPTIONS_SELL, STAMP_DUTY_RATE, calculate_full_costs

    qty = 65
    # Winning short: sell 100 -> buy back 20. Sell leg = ENTRY.
    short = calculate_full_costs(100.0, 20.0, qty, side="SELL")
    assert short.stt == pytest.approx(100.0 * qty * STT_OPTIONS_SELL)
    assert short.stamp_duty == pytest.approx(20.0 * qty * STAMP_DUTY_RATE)
    # Long control: buy 100 -> sell 120. Sell leg = EXIT (unchanged path).
    long_ = calculate_full_costs(100.0, 120.0, qty, side="BUY")
    assert long_.stt == pytest.approx(120.0 * qty * STT_OPTIONS_SELL)
    assert long_.stamp_duty == pytest.approx(100.0 * qty * STAMP_DUTY_RATE)
    # Default (no side arg) must remain byte-identical to the old behavior
    # for every existing long-side caller.
    default = calculate_full_costs(100.0, 120.0, qty)
    assert default.total == pytest.approx(long_.total)
