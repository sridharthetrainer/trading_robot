"""Pure, testable after-cost trade admission checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from capital_compounder import calculate_net_pnl


@dataclass(frozen=True)
class TradeEconomics:
    allowed: bool
    reason: str
    win_probability: float
    breakeven_probability: float
    target_gross: float
    target_net: float
    stop_net: float
    expected_net: float
    estimated_cost: float
    required_target_gross: float

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_trade_economics(
    *,
    entry_price: float,
    target_price: float,
    stop_loss: float,
    qty: int,
    side: str,
    win_probability: float,
    brokerage_per_leg: float = 20.0,
    is_options: bool = True,
    product_type: str = "INTRADAY",
    cost_hurdle_multiplier: float = 2.5,
    min_expected_net: float = 0.0,
    min_probability_margin: float = 0.02,
) -> TradeEconomics:
    """Evaluate a setup using its target, stop, costs, and win probability."""
    entry = float(entry_price or 0.0)
    target = float(target_price or 0.0)
    stop = float(stop_loss or 0.0)
    quantity = int(qty or 0)
    direction = str(side or "BUY").upper()
    probability = max(0.0, min(1.0, float(win_probability or 0.0)))

    valid = entry > 0 and target > 0 and stop > 0 and quantity > 0
    valid = valid and direction in {"BUY", "SELL"}
    if direction == "BUY":
        valid = valid and target > entry > stop
    else:
        valid = valid and target < entry < stop
    if not valid:
        return TradeEconomics(
            False, "invalid_risk_levels", probability, 1.0,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        )

    target_gross, target_net, target_costs = calculate_net_pnl(
        entry, target, quantity, direction, brokerage_per_leg,
        is_options, product_type,
    )
    _stop_gross, stop_net, stop_costs = calculate_net_pnl(
        entry, stop, quantity, direction, brokerage_per_leg,
        is_options, product_type,
    )
    _flat_gross, _flat_net, flat_costs = calculate_net_pnl(
        entry, entry, quantity, direction, brokerage_per_leg,
        is_options, product_type,
    )

    win_value = max(0.0, float(target_net))
    loss_value = abs(min(0.0, float(stop_net)))
    denominator = win_value + loss_value
    breakeven = loss_value / denominator if denominator > 0 else 1.0
    expected = probability * float(target_net) + (1.0 - probability) * float(stop_net)
    required_gross = max(0.0, float(cost_hurdle_multiplier)) * float(flat_costs.total)

    reason = ""
    if target_gross < required_gross:
        reason = "target_does_not_cover_cost_hurdle"
    elif probability < breakeven + max(0.0, float(min_probability_margin)):
        reason = "win_probability_below_after_cost_breakeven"
    elif expected < float(min_expected_net):
        reason = "expected_net_below_minimum"

    return TradeEconomics(
        allowed=not reason,
        reason=reason or "economic_edge_pass",
        win_probability=round(probability, 6),
        breakeven_probability=round(breakeven, 6),
        target_gross=round(float(target_gross), 2),
        target_net=round(float(target_net), 2),
        stop_net=round(float(stop_net), 2),
        expected_net=round(float(expected), 2),
        estimated_cost=round(
            max(float(target_costs.total), float(stop_costs.total), float(flat_costs.total)), 2
        ),
        required_target_gross=round(required_gross, 2),
    )
