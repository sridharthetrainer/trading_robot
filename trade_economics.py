"""Pure, testable after-cost trade admission checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite

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
    slippage_pct_per_leg: float = 0.0,
) -> TradeEconomics:
    """Evaluate after-fee, after-slippage expectancy.

    Slippage is a fraction per leg (0.005 means 0.5%), applied adversely
    to entry and both exits. Probability must be supplied on a 0–1 scale.
    """
    def rejected(reason: str) -> TradeEconomics:
        return TradeEconomics(False, reason, 0.0, 1.0,
                              0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    try:
        entry, target, stop = map(float, (entry_price, target_price, stop_loss))
        quantity_value = float(qty)
        probability = float(win_probability)
        brokerage = float(brokerage_per_leg)
        hurdle = float(cost_hurdle_multiplier)
        minimum = float(min_expected_net)
        margin = float(min_probability_margin)
        slip = float(slippage_pct_per_leg)
    except (TypeError, ValueError, OverflowError):
        return rejected("invalid_economic_inputs")
    if not all(isfinite(value) for value in (
        entry, target, stop, quantity_value, probability, brokerage,
        hurdle, minimum, margin, slip,
    )):
        return rejected("invalid_economic_inputs")
    if not 0 <= probability <= 1:
        return rejected("invalid_win_probability")
    if (brokerage < 0 or hurdle < 0 or minimum < 0 or not 0 <= margin <= 1
            or not 0 <= slip < 1):
        return rejected("invalid_economic_inputs")
    quantity = int(quantity_value)
    if quantity_value != quantity:
        return rejected("invalid_risk_levels")
    direction = str(side or "").strip().upper()

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

    sign = 1 if direction == "BUY" else -1
    filled_entry = entry * (1 + sign * slip)
    filled_target = target * (1 - sign * slip)
    filled_stop = stop * (1 - sign * slip)
    filled_flat = entry * (1 - sign * slip)
    target_gross = sign * (target - entry) * quantity
    _filled_gross, target_net, target_costs = calculate_net_pnl(
        filled_entry, filled_target, quantity, direction, brokerage,
        is_options, product_type,
    )
    _stop_gross, stop_net, stop_costs = calculate_net_pnl(
        filled_entry, filled_stop, quantity, direction, brokerage,
        is_options, product_type,
    )
    _flat_gross, _flat_net, flat_costs = calculate_net_pnl(
        filled_entry, filled_flat, quantity, direction, brokerage,
        is_options, product_type,
    )

    win_value = max(0.0, float(target_net))
    loss_value = abs(min(0.0, float(stop_net)))
    denominator = win_value + loss_value
    breakeven = loss_value / denominator if denominator > 0 else 1.0
    expected = probability * float(target_net) + (1.0 - probability) * float(stop_net)
    target_drag = float(target_costs.total) + (entry + target) * slip * quantity
    stop_drag = float(stop_costs.total) + (entry + stop) * slip * quantity
    flat_drag = float(flat_costs.total) + 2 * entry * slip * quantity
    estimated_cost = max(target_drag, stop_drag, flat_drag)
    required_gross = hurdle * estimated_cost

    reason = ""
    if target_gross < required_gross:
        reason = "target_does_not_cover_cost_hurdle"
    elif target_net <= 0:
        reason = "target_net_not_positive"
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
        estimated_cost=round(estimated_cost, 2),
        required_target_gross=round(required_gross, 2),
    )
