import pytest

from capital_compounder import calculate_net_pnl
from trade_economics import evaluate_trade_economics


def evaluate(**overrides):
    inputs = dict(entry_price=200, target_price=210, stop_loss=195,
                  qty=50, side="BUY", win_probability=0.60)
    inputs.update(overrides)
    return evaluate_trade_economics(**inputs)


@pytest.mark.parametrize("probability", [float("nan"), float("inf"), -0.1, 1.5, None, "bad"])
def test_invalid_probability_cannot_admit_a_trade(probability):
    assert not evaluate(win_probability=probability).allowed


@pytest.mark.parametrize("field,value", [
    ("entry_price", float("inf")), ("qty", 50.5),
    ("qty", float("nan")), ("brokerage_per_leg", -20),
    ("min_expected_net", float("nan")), ("cost_hurdle_multiplier", -1),
    ("min_probability_margin", -0.1), ("slippage_pct_per_leg", -0.01),
    ("slippage_pct_per_leg", 1), ("side", None),
])
def test_invalid_economic_inputs_fail_closed(field, value):
    assert not evaluate(**{field: value}).allowed


@pytest.mark.parametrize("side,target,stop", [("BUY", 210, 195), ("SELL", 190, 205)])
def test_slippage_matches_adverse_fills_on_both_sides(side, target, stop):
    slip = 0.005
    sign = 1 if side == "BUY" else -1
    result = evaluate(side=side, target_price=target, stop_loss=stop,
                      slippage_pct_per_leg=slip)
    _, target_net, costs = calculate_net_pnl(
        200 * (1 + sign * slip), target * (1 - sign * slip), 50, side)
    _, stop_net, _ = calculate_net_pnl(
        200 * (1 + sign * slip), stop * (1 - sign * slip), 50, side)
    assert result.target_net == target_net
    assert result.stop_net == stop_net
    assert result.expected_net == round(0.6 * target_net + 0.4 * stop_net, 2)
    assert result.estimated_cost >= round(costs.total + (200 + target) * slip * 50, 2)
    assert result.breakeven_probability > evaluate(
        side=side, target_price=target, stop_loss=stop).breakeven_probability


def test_slippage_rejects_fee_only_positive_expectancy():
    assert evaluate(win_probability=0.55).allowed
    stressed = evaluate(win_probability=0.55, slippage_pct_per_leg=0.005)
    assert not stressed.allowed
    assert stressed.expected_net < 0


def test_nonpositive_target_is_rejected_even_with_zero_hurdles():
    result = evaluate(target_price=200.1, win_probability=1,
                      cost_hurdle_multiplier=0, min_probability_margin=0)
    assert not result.allowed
    assert result.reason == "target_net_not_positive"
