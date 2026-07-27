"""
risk_gate.py — shadow-mode consolidation of the equity risk layer.

2026-07-27, architecture-comparison follow-up: this system's risk checks are
scattered across 3+ independent call sites -- daily-loss is enforced
separately in DailyLossLimitManager (cycle-level gate), TradeManager (its own
lock check at open_trade time), and again inside PortfolioRiskManager's own
internal sub-check; VaR is checked both ad hoc in
live_signal_engine._execute_candidate (resize-only) and again inside
PortfolioRiskManager (config-gated, currently dormant since ENABLE_VAR
defaults false). Three independent sources of truth for the same facts is a
real drift risk (main_autonomous.py already has to manually re-sync two of
the daily-loss trackers after profit-lock updates).

SHADOW MODE ONLY. evaluate() is called ALONGSIDE the existing scattered
checks -- it never replaces them, and nothing here currently affects order
placement. Its verdict is compared to the real, live decision by
log_shadow_disagreement(), which only writes a record when they actually
disagree. That log is what should inform any future decision to cut over --
not this module's existence alone.

This file is purely additive: no existing module's behavior or signature
changes. PortfolioRiskManager.evaluate_new_trade()'s own internal daily-loss
sub-check is skipped simply by passing daily_loss_limit=None (an existing,
already-supported parameter) -- no new flag needed. Its internal VaR
sub-check is already dormant today since config.ENABLE_VAR defaults to
false; if that ever flips true, evaluate_new_trade would additionally run
its own VaR check via a different method (check_new_trade_var) than the one
this module calls (compute) -- harmless in shadow mode (just means the
shadow verdict's approved_quantity could reflect an extra internal resize),
but worth knowing if this module is ever extended.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from portfolio_risk import RiskDecision

logger = logging.getLogger(__name__)

SHADOW_LOG_FILE = Path("risk_gate_shadow_log.jsonl")
QTY_DISAGREEMENT_TOLERANCE = 0.01  # 1% relative tolerance before logging a qty disagreement


@dataclass
class RiskGateContext:
    """Everything evaluate() needs, sourced from the caller's already-live
    objects -- no new instances created, no new source of truth introduced."""
    daily_loss_manager: Any        # DailyLossLimitManager instance already on the engine
    portfolio_risk_manager: Any    # PortfolioRiskManager instance already on the engine
    capital: float


def evaluate(
    candidate: Dict[str, Any],
    execution_plan: Dict[str, Any],
    open_positions: List[Dict[str, Any]],
    ctx: RiskGateContext,
) -> RiskDecision:
    """Single, documented-order risk evaluation:
      1. Daily-loss lock -- DailyLossLimitManager.can_trade(), the same
         stateful lock the cycle-level gate already checks, instead of the
         fixed DEFAULT_MAX_DAILY_LOSS constant _execute_candidate passes
         into PortfolioRiskManager today (a real, pre-existing discrepancy
         this shadow comparison is specifically positioned to surface).
      2. Portfolio risk / exposure / correlation / premium-cap --
         PortfolioRiskManager.evaluate_new_trade() (its own internal
         daily-loss sub-check skipped via daily_loss_limit=None, so this
         function is the single place that check runs).
      3. Whole-portfolio VaR resize -- the same get_var_engine(...).compute()
         call _execute_candidate already makes ad hoc today.
    Returns portfolio_risk.RiskDecision -- the existing, already-used shape.
    """
    signal = candidate.get("signal", {}) or {}
    symbol = execution_plan.get("execution_symbol") or candidate.get("symbol", "")

    if not ctx.daily_loss_manager.can_trade():
        return RiskDecision(
            allowed=False, approved_quantity=0, approved_lots=0,
            reason="daily_loss_lock_active", estimated_trade_risk=0.0,
            resulting_total_exposure=0.0, resulting_symbol_exposure=0.0,
            resulting_portfolio_risk_pct=0.0,
            metadata={"source": "risk_gate.daily_loss"},
        )

    decision = ctx.portfolio_risk_manager.evaluate_new_trade(
        symbol=symbol,
        entry_price=execution_plan.get("entry_price"),
        stop_loss=execution_plan.get("stop_loss"),
        requested_quantity=execution_plan.get("requested_quantity"),
        open_positions=open_positions,
        correlation_group=execution_plan.get("correlation_group"),
        current_daily_pnl=0.0,   # already gated above via can_trade() -- avoid a second,
        daily_loss_limit=None,   # differently-sourced daily-loss re-check inside this call
        lot_size=execution_plan.get("lot_size", 1),
        is_options=execution_plan.get("asset_type") == "OPTION",
        spot_price=signal.get("price"),
    )
    if not decision.allowed:
        return decision

    try:
        from value_at_risk import get_var_engine
        rpt = get_var_engine(ctx.capital).compute(open_positions)
        var_frac = float(getattr(rpt, "var_pct", 0) or 0) / 100.0
        if var_frac and var_frac > 0.03 and decision.approved_quantity > 0:
            resized = max(1, int(decision.approved_quantity * 0.03 / var_frac))
            if resized < decision.approved_quantity:
                decision.approved_quantity = resized
                decision.approved_lots = max(
                    0, resized // max(1, int(execution_plan.get("lot_size", 1) or 1))
                )
                decision.reason = f"{decision.reason} (VaR-resized)"
    except Exception as exc:
        logger.debug("risk_gate: VaR check skipped: %s", exc)

    return decision


def log_shadow_disagreement(
    live_decision: Any, shadow_decision: Any, symbol: str, context: Dict[str, Any],
) -> None:
    """Append a record to SHADOW_LOG_FILE only when the shadow gate's verdict
    actually disagrees with the real, live decision (different allowed, or
    approved_quantity differing beyond QTY_DISAGREEMENT_TOLERANCE). Never
    raises -- shadow logging must never affect the caller."""
    try:
        allowed_differs = bool(live_decision.allowed) != bool(shadow_decision.allowed)
        live_qty = int(getattr(live_decision, "approved_quantity", 0) or 0)
        shadow_qty = int(getattr(shadow_decision, "approved_quantity", 0) or 0)
        qty_differs = False
        if live_qty or shadow_qty:
            denom = max(live_qty, shadow_qty, 1)
            qty_differs = abs(live_qty - shadow_qty) / denom > QTY_DISAGREEMENT_TOLERANCE
        if not allowed_differs and not qty_differs:
            return
        entry = {
            "symbol": symbol,
            "live":   {"allowed": bool(live_decision.allowed), "approved_quantity": live_qty,
                       "reason": getattr(live_decision, "reason", "")},
            "shadow": {"allowed": bool(shadow_decision.allowed), "approved_quantity": shadow_qty,
                       "reason": getattr(shadow_decision, "reason", "")},
            "context": context,
        }
        with open(SHADOW_LOG_FILE, "a") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:
        logger.debug("risk_gate: shadow disagreement logging failed: %s", exc)
