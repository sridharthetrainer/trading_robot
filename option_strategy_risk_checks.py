"""
option_strategy_risk_checks.py — pure, read-only risk-taxonomy checks for
the 2026-07-16 option strategy catalog.

Every function here is a pure calculation with no side effects.
can_enter_trade is used by the catalog before shadow evaluation; it does
not place orders or authorize live trading.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _cfg(name: str, default: float) -> float:
    try:
        import config as cfg
        return float(getattr(cfg, name, default))
    except Exception as exc:
        logger.debug("_cfg(%s): %s", name, exc)
        return float(default)


def mtm_stop_level(allocated_capital: float, pct: Optional[float] = None) -> float:
    """Per-trade MTM stop in rupees: pct of allocated capital for that
    trade. Spec default 1.5% -- unvalidated, carried over as given."""
    p = pct if pct is not None else _cfg("OPTION_STRATEGY_MTM_STOP_PCT", 0.015)
    return round(float(allocated_capital) * p, 2)


def risk_profile_for(strategy_id: str) -> str:
    """Thin lookup, kept separate from the full registry so a test of this
    one mapping doesn't need to import the whole 42-entry catalog."""
    from option_strategy_registry import get_strategy
    try:
        return get_strategy(strategy_id).risk_profile
    except KeyError:
        return "unknown"


def correlation_group_for(symbol: str) -> str:
    """NIFTY and BANKNIFTY short-vol positions count as one correlated
    position (rho~=0.85 per spec) -- FINNIFTY/MIDCPNIFTY are not NIFTY's
    direct pair per the spec's own correlation-cap rule, so they get their
    own group rather than being folded in silently."""
    s = str(symbol or "").upper()
    if s in ("NIFTY", "BANKNIFTY"):
        return "NIFTY_BANKNIFTY_CORRELATED"
    return s


def gap_and_adx_filter(gap_pct: float, adx: float,
                        gap_threshold: Optional[float] = None,
                        adx_ceiling: Optional[float] = None) -> Tuple[bool, str]:
    """Spec section 3.4: combined no-trade condition when a gap gets ignored
    because ADX looks tame -- 'gap>0.6% and ADX<20' is a specific and
    different risk than either check run alone. Returns (blocked, reason)."""
    gt = gap_threshold if gap_threshold is not None else _cfg("OPTION_STRATEGY_GAP_PCT_THRESHOLD", 0.006)
    ac = adx_ceiling if adx_ceiling is not None else 20.0
    if gap_pct > gt and adx < ac:
        return True, f"gap_{gap_pct:.4f}_gt_{gt}_with_adx_{adx:.1f}_lt_{ac}"
    return False, ""


def can_enter_trade(regime: Dict[str, Any], vix: float, api_latency_sec: float = 0.0,
                     open_undefined_risk_count: int = 0, adx: float = 20.0,
                     risk_profile: str = "undefined_risk",
                     **kw) -> Tuple[bool, str]:
    """The spec's can_enter_trade()/global-kill-switch aggregator (section
    3.4), expressed as a pure boolean+reason and applied to catalog shadow
    entries. Passing this check does not authorize execution."""
    vix_block_all = _cfg("OPTION_STRATEGY_VIX_BLOCK_ALL", 25.0)
    vix_block_nondirectional = _cfg("OPTION_STRATEGY_VIX_BLOCK_NONDIRECTIONAL", 30.0)
    latency_block = _cfg("OPTION_STRATEGY_API_LATENCY_BLOCK_SEC", 2.0)
    try:
        vix, api_latency_sec, adx = map(float, (vix, api_latency_sec, adx))
        gap_pct = float(regime.get("gap_pct", 0.0)) if isinstance(regime, dict) else 0.0
        count = float(open_undefined_risk_count)
    except (TypeError, ValueError, OverflowError):
        return False, "invalid_risk_inputs"
    if not all(math.isfinite(value) for value in (
        vix, api_latency_sec, adx, gap_pct, count,
        vix_block_all, vix_block_nondirectional, latency_block,
    )) or vix <= 0 or api_latency_sec < 0 or adx < 0 or count < 0 or count != int(count):
        return False, "invalid_risk_inputs"

    # Spec lists both "VIX>25 blocks all new entries" and, separately,
    # "VIX>30 skip all non-directional" -- since 30>25 the second condition
    # can never independently change the True/False outcome (anything past
    # 30 already tripped the first check), so the higher/more specific
    # threshold is checked first purely so its own reason string is
    # reachable and reported, not masked by the lower one.
    if vix > vix_block_nondirectional:
        return False, f"vix_{vix:.1f}_gt_{vix_block_nondirectional}_block_nondirectional"
    if vix > vix_block_all:
        return False, f"vix_{vix:.1f}_gt_{vix_block_all}_block_all"
    if api_latency_sec > latency_block:
        return False, f"api_latency_{api_latency_sec:.2f}s_gt_{latency_block}s"

    blocked, reason = gap_and_adx_filter(abs(gap_pct), adx)
    if blocked:
        return False, reason

    if count >= 1 and risk_profile == "undefined_risk":
        # Spec: at most 1 undefined-risk strategy concurrently. Enforcement
        # is moot until real positions exist, but the check is real now.
        return False, "max_1_undefined_risk_strategy_already_open"

    return True, ""


def portfolio_option_risk_gate(
    positions, *, max_abs_delta: float = 100.0, max_abs_gamma: float = 1.0,
    max_abs_theta: float = 5000.0, max_abs_vega: float = 5000.0,
    max_stress_loss: float = 3000.0,
) -> Dict[str, Any]:
    """Enforce portfolio Greeks and joint spot/IV stress limits."""
    from option_institutional_controls import portfolio_risk_gate
    return portfolio_risk_gate(
        list(positions or []),
        max_abs_delta=max_abs_delta,
        max_abs_gamma=max_abs_gamma,
        max_abs_theta=max_abs_theta,
        max_abs_vega=max_abs_vega,
        max_stress_loss=max_stress_loss,
    )
