"""
trade_autopsy.py

Lightweight post-trade classification. It adds explainable labels to closed
trades so learning jobs can distinguish bad direction, liquidity drag, late
exits, and execution-cost problems instead of seeing only raw P&L.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def classify_trade_autopsy(trade: Any) -> Dict[str, Any]:
    meta = getattr(trade, "metadata", None)
    meta = meta if isinstance(meta, dict) else {}
    entry = _f(getattr(trade, "entry_price", 0.0))
    exit_price = _f(getattr(trade, "exit_price", 0.0))
    stop = _f(getattr(trade, "stop_loss", 0.0))
    target = _f(getattr(trade, "target_price", 0.0))
    pnl = _f(getattr(trade, "realized_pnl", 0.0))
    qty = max(1.0, _f(getattr(trade, "qty", 1.0), 1.0))
    side = str(getattr(trade, "side", "") or "").upper()
    reason = str(getattr(trade, "exit_reason", "") or "").lower()

    risk_per_unit = abs(entry - stop) if entry > 0 and stop > 0 else 0.0
    reward_per_unit = abs(target - entry) if entry > 0 and target > 0 else 0.0
    r_multiple = pnl / max(risk_per_unit * qty, 1.0) if risk_per_unit > 0 else 0.0
    tags: List[str] = []

    if pnl > 0:
        tags.append("winner")
    elif pnl < 0:
        tags.append("loser")
    else:
        tags.append("flat")

    if "stop" in reason or "sl" in reason:
        tags.append("stop_exit")
        if abs(r_multiple) < 0.45:
            tags.append("tight_stop_or_noise")
    if "target" in reason or "t1" in reason:
        tags.append("target_exit")
    if "time" in reason or "hold" in reason or "eod" in reason:
        tags.append("time_exit")

    if pnl < 0 and entry > 0 and exit_price > 0:
        wrong_way = (side == "BUY" and exit_price < entry) or (side == "SELL" and exit_price > entry)
        if wrong_way:
            tags.append("direction_failed")

    costs = meta.get("costs", {}) if isinstance(meta.get("costs"), dict) else {}
    cost_total = _f(costs.get("total"), 0.0)
    gross = _f(meta.get("gross_pnl"), pnl)
    if cost_total > 0 and abs(gross) > 0 and cost_total / max(abs(gross), 1.0) > 0.35:
        tags.append("cost_drag_high")

    opt_quality = meta.get("option_execution_quality", {})
    if isinstance(opt_quality, dict):
        if opt_quality.get("warnings"):
            tags.append("option_quality_warning")
        if _f(opt_quality.get("score"), 100.0) < 75:
            tags.append("option_execution_quality_low")
        if "spread" in ",".join(str(x) for x in opt_quality.get("warnings", [])):
            tags.append("spread_risk")

    style = str(meta.get("style", "") or "").lower()
    if style:
        tags.append(f"style:{style}")

    likely_primary = "profit_protected" if pnl > 0 else "needs_review"
    for candidate in (
        "direction_failed",
        "option_execution_quality_low",
        "cost_drag_high",
        "tight_stop_or_noise",
        "time_exit",
        "stop_exit",
    ):
        if candidate in tags:
            likely_primary = candidate
            break

    return {
        "label": 1 if pnl > 0 else -1 if pnl < 0 else 0,
        "primary": likely_primary,
        "tags": sorted(set(tags)),
        "r_multiple": round(r_multiple, 3),
        "risk_per_unit": round(risk_per_unit, 4),
        "reward_per_unit": round(reward_per_unit, 4),
        "cost_total": round(cost_total, 2),
    }
