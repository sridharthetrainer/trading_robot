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
    entry_spread_pct: float | None = None,
    exit_spread_pct: float | None = None,
    observed_volume: float | None = None,
    max_spread_pct: float = 0.20,
    fill_latency_sec: float = 1.0,
    lot_size: int | None = None,
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
    entry_spread = max(0.0, float(entry_spread_pct or 0.0))
    exit_spread = max(0.0, float(exit_spread_pct or entry_spread_pct or 0.0))
    volume = max(0.0, float(observed_volume or 0.0))
    contract_size = max(1, int(lot_size or quantity))
    requested_contracts = max(1, (quantity + contract_size - 1) // contract_size)
    spread_executable = entry_spread <= max_spread_pct and exit_spread <= max_spread_pct
    volume_executable = observed_volume is None or volume >= requested_contracts
    executable = entry > 0 and exit_value > 0 and spread_executable and volume_executable
    fill_probability = 0.0
    if executable:
        spread_factor = max(0.0, 1.0 - max(entry_spread, exit_spread) / max(max_spread_pct, 1e-9))
        volume_factor = 1.0 if observed_volume is None else min(1.0, volume / max(requested_contracts * 3.0, 1.0))
        fill_probability = max(0.05, min(1.0, 0.50 + 0.35 * spread_factor + 0.15 * volume_factor))

    # LTP is treated as the mid. A buyer crosses half the spread at entry and
    # again at exit, in addition to adverse slippage and statutory charges.
    if direction == "BUY":
        filled_entry = entry * (1 + entry_spread / 2.0) * (1 + slip)
        filled_exit = exit_value * (1 - exit_spread / 2.0) * (1 - slip)
    else:
        filled_entry = entry * (1 - entry_spread / 2.0) * (1 - slip)
        filled_exit = exit_value * (1 + exit_spread / 2.0) * (1 + slip)
    if not executable:
        reason = "invalid_price" if entry <= 0 or exit_value <= 0 else (
            "spread_too_wide" if not spread_executable else "insufficient_observed_volume"
        )
        return {
            "qty": quantity, "raw_entry_price": round(entry, 4),
            "raw_exit_price": round(exit_value, 4), "fill_entry_price": 0.0,
            "fill_exit_price": 0.0, "slippage_pct_per_leg": slip,
            "entry_spread_pct": entry_spread, "exit_spread_pct": exit_spread,
            "gross_pnl": 0.0, "estimated_costs": 0.0, "net_pnl": 0.0,
            "capital_at_risk": round(max(0.0, entry * quantity), 2), "net_r": 0.0,
            "label": -2, "execution_status": "REJECTED", "rejection_reason": reason,
            "fill_latency_sec": max(0.0, float(fill_latency_sec)),
            "fill_probability": 0.0,
        }
    gross, net, costs = calculate_net_pnl(
        filled_entry, filled_exit, quantity, direction,
        float(os.getenv("BROKERAGE_PER_ORDER", "20")), True, "INTRADAY",
    )
    capital_at_risk = max(0.0, filled_entry * quantity) if direction == "BUY" else max(0.0, entry * quantity)
    net_r = float(net) / capital_at_risk if capital_at_risk > 0 else 0.0
    return {
        "qty": quantity,
        "raw_entry_price": round(entry, 4), "raw_exit_price": round(exit_value, 4),
        "fill_entry_price": round(filled_entry, 4), "fill_exit_price": round(filled_exit, 4),
        "slippage_pct_per_leg": slip, "gross_pnl": round(float(gross), 2),
        "estimated_costs": round(float(costs.total), 2), "net_pnl": round(float(net), 2),
        "capital_at_risk": round(capital_at_risk, 2), "net_r": round(net_r, 6),
        "label": 1 if net > 0 else -1 if net < 0 else 0,
        "execution_status": "FILLED", "rejection_reason": "",
        "fill_latency_sec": max(0.0, float(fill_latency_sec)),
        "fill_probability": round(fill_probability, 4),
        "entry_spread_pct": entry_spread, "exit_spread_pct": exit_spread,
    }
