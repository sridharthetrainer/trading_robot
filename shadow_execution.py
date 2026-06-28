"""Conservative after-cost option execution model for research outcomes."""

from __future__ import annotations

import os
from typing import Any, Dict

from capital_compounder import calculate_net_pnl


def simulate_option_round_trip(
    entry_price: float,
    exit_price: float,
    *,
    qty: int | None = None,
    side: str = "BUY",
    slippage_pct_per_leg: float | None = None,
) -> Dict[str, Any]:
    entry = float(entry_price or 0)
    exit_value = float(exit_price or 0)
    quantity = max(1, int(qty or os.getenv("OPTION_LOT_SIZE", "65")))
    slip = max(0.0, float(
        slippage_pct_per_leg
        if slippage_pct_per_leg is not None
        else os.getenv("SHADOW_OPTION_SLIPPAGE_PCT", "0.005")
    ))
    direction = str(side or "BUY").upper()
    if direction == "BUY":
        filled_entry, filled_exit = entry * (1 + slip), exit_value * (1 - slip)
    else:
        filled_entry, filled_exit = entry * (1 - slip), exit_value * (1 + slip)
    gross, net, costs = calculate_net_pnl(
        filled_entry, filled_exit, quantity, direction,
        float(os.getenv("BROKERAGE_PER_ORDER", "20")), True, "INTRADAY",
    )
    return {
        "qty": quantity,
        "raw_entry_price": round(entry, 4), "raw_exit_price": round(exit_value, 4),
        "fill_entry_price": round(filled_entry, 4), "fill_exit_price": round(filled_exit, 4),
        "slippage_pct_per_leg": slip, "gross_pnl": round(float(gross), 2),
        "estimated_costs": round(float(costs.total), 2), "net_pnl": round(float(net), 2),
        "label": 1 if net > 0 else -1 if net < 0 else 0,
    }
