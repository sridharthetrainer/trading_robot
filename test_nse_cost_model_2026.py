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
