"""
market_shock_monitor.py -- market-shock emergency shutdown triggers.

Gap found in the 2026-08-19 spec audit: kill_switch.py triggers only from
system-health events (health_monitor.py) and loss-lock conditions
(daily_loss_limit.py) -- nothing watches for a fast adverse market move, a
VIX spike, a drawdown breach, or a margin breach and force-closes on it.

Implements 4 of the spec's 5 emergency-shutdown triggers with real,
verifiable data:
  - NIFTY drops > 2% within the last ~15 minutes (candle_cache 1m bars)
  - India VIX spikes > 25 within the last ~1 hour (VIXFeed's own history)
  - Account drawdown hits 10% off peak equity (capital_compounder's tracked peak)
  - Margin utilization > 85% (reuses margin_circuit_breaker's parser)

The 5th spec trigger, "NSE circuit breaker triggered," is deliberately NOT
implemented -- there is no reliable market-wide (not per-stock) circuit-halt
detector anywhere in this codebase, and guessing one from proxy signals
would risk exactly the false-positive/false-negative failure this module
exists to avoid. Each check below returns None (never a guessed answer) when
its underlying data isn't available, and the caller treats None as "skip",
never as "tripped" or "clear".

force_close on trigger closes ALL open positions (via KillSwitch.trigger),
not a market-neutral-only subset -- no strategy in this codebase currently
carries a machine-readable "market-neutral" tag to selectively spare, so the
spec's "keep only market-neutral positions" is not implementable without
that taxonomy existing first (see the cluster-matrix gap from the same audit).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("market_shock_monitor")

NIFTY_SHOCK_PCT = 0.02       # 2% in 15 minutes
NIFTY_SHOCK_WINDOW_MIN = 15
VIX_SPIKE_LEVEL = 25.0       # absolute VIX level
VIX_SPIKE_WINDOW_MIN = 60
DRAWDOWN_HALT_PCT = 0.10     # 10% off peak equity
MARGIN_HALT_RATIO = 0.85     # 85% utilized/available


def check_nifty_shock(symbol: str = "NIFTY") -> Optional[Dict[str, float]]:
    """Real move over the last NIFTY_SHOCK_WINDOW_MIN minutes using cached
    1-minute candles. Returns None if fewer than the required bars exist --
    never estimates from a partial window."""
    try:
        from candle_cache import get_cached_candles
        df = get_cached_candles(symbol, interval="1m", days=1)
    except Exception as e:
        logger.debug("check_nifty_shock: candle fetch failed: %s", e)
        return None
    if df is None or len(df) < NIFTY_SHOCK_WINDOW_MIN + 1:
        return None
    recent = df.tail(NIFTY_SHOCK_WINDOW_MIN + 1)
    start_price = float(recent["close"].iloc[0])
    now_price = float(recent["close"].iloc[-1])
    if start_price <= 0:
        return None
    move_pct = (now_price - start_price) / start_price
    return {"move_pct": move_pct, "start": start_price, "now": now_price}


def check_vix_spike(vix_feed) -> Optional[Dict[str, float]]:
    """Uses VIXFeed's own rolling history (5-min samples, ~2.5h retained).
    Returns None if there isn't enough history to cover the 1h window."""
    history = getattr(vix_feed, "_history", None)
    if not history:
        return None
    now = time.time()
    window_start = now - VIX_SPIKE_WINDOW_MIN * 60
    in_window = [h for h in history if h.get("ts", 0) >= window_start]
    if len(in_window) < 2:
        return None
    current = float(in_window[-1]["vix"])
    return {"current": current, "window_samples": len(in_window)}


def check_drawdown(capital_compounder) -> Optional[Dict[str, float]]:
    """Reuses capital_compounder's own tracked peak equity and rolling
    equity history (its own 15% size-halving breaker relies on the same
    _peak_equity; there's no separate 'current balance' attribute -- the
    latest entry of _equity_history, populated by update_equity() every
    live cycle, is the most recent known balance)."""
    if capital_compounder is None:
        return None
    peak = getattr(capital_compounder, "_peak_equity", None)
    history = getattr(capital_compounder, "_equity_history", None)
    if peak is None or not history or peak <= 0:
        return None
    bal = float(history[-1])
    drawdown_pct = (peak - bal) / peak
    return {"drawdown_pct": drawdown_pct, "peak": peak, "current": bal}


def check_margin_breach(angel) -> Optional[Dict[str, float]]:
    from margin_circuit_breaker import compute_margin_utilization
    return compute_margin_utilization(angel)


def evaluate(*, angel=None, vix_feed=None, capital_compounder=None,
             nifty_symbol: str = "NIFTY") -> Dict[str, Any]:
    """Runs all 4 checks and returns which (if any) tripped, with evidence.
    Never raises -- a check that errors is treated as unavailable (None),
    same as a check that returns no data."""
    tripped = []
    evidence: Dict[str, Any] = {}

    try:
        nifty = check_nifty_shock(nifty_symbol)
        evidence["nifty"] = nifty
        if nifty is not None and abs(nifty["move_pct"]) > NIFTY_SHOCK_PCT:
            tripped.append(("nifty_shock",
                             f"NIFTY moved {nifty['move_pct']*100:+.2f}% in "
                             f"{NIFTY_SHOCK_WINDOW_MIN}min ({nifty['start']:.1f}->{nifty['now']:.1f})"))
    except Exception as e:
        logger.debug("evaluate: nifty check failed: %s", e)

    try:
        vix = check_vix_spike(vix_feed) if vix_feed is not None else None
        evidence["vix"] = vix
        if vix is not None and vix["current"] > VIX_SPIKE_LEVEL:
            tripped.append(("vix_spike",
                             f"India VIX {vix['current']:.1f} > {VIX_SPIKE_LEVEL} "
                             f"within {VIX_SPIKE_WINDOW_MIN}min"))
    except Exception as e:
        logger.debug("evaluate: vix check failed: %s", e)

    try:
        dd = check_drawdown(capital_compounder)
        evidence["drawdown"] = dd
        if dd is not None and dd["drawdown_pct"] >= DRAWDOWN_HALT_PCT:
            tripped.append(("drawdown",
                             f"Drawdown {dd['drawdown_pct']*100:.1f}% off peak "
                             f"Rs{dd['peak']:,.0f} (now Rs{dd['current']:,.0f})"))
    except Exception as e:
        logger.debug("evaluate: drawdown check failed: %s", e)

    try:
        margin = check_margin_breach(angel) if angel is not None else None
        evidence["margin"] = margin
        if margin is not None and margin["ratio"] > MARGIN_HALT_RATIO:
            tripped.append(("margin_breach",
                             f"Margin utilization {margin['ratio']*100:.1f}% > "
                             f"{MARGIN_HALT_RATIO*100:.0f}%"))
    except Exception as e:
        logger.debug("evaluate: margin check failed: %s", e)

    return {"tripped": tripped, "evidence": evidence}


def run_market_shock_check(kill_switch, *, angel=None, vix_feed=None,
                            capital_compounder=None, nifty_symbol: str = "NIFTY") -> Dict[str, Any]:
    """Evaluate all triggers; if any tripped and the kill switch isn't
    already active, trip it with force_close=True. Safe to call repeatedly."""
    result = evaluate(angel=angel, vix_feed=vix_feed,
                       capital_compounder=capital_compounder, nifty_symbol=nifty_symbol)
    if not result["tripped"]:
        return result
    if kill_switch is None or kill_switch.is_active():
        return result

    reasons = "; ".join(msg for _, msg in result["tripped"])
    logger.critical("MARKET SHOCK EMERGENCY SHUTDOWN: %s", reasons)
    kill_switch.trigger(
        reason=f"market shock: {reasons}",
        source="market_shock_monitor",
        force_close=True,
    )
    result["kill_switch_triggered"] = True
    return result
