"""
margin_circuit_breaker.py -- auto-liquidation when margin utilization spikes.

Gap found in the 2026-08-19 spec audit (Strategy 34, "System Risk, Always
Active"): MarginFeed (market_data_feeds.py) fetches SPAN margins but has zero
callers anywhere in the codebase, and no module closes EXISTING live
positions on a margin trigger -- every other risk module (kill_switch.py,
daily_loss_limit.py) only blocks NEW entries.

Behaviour: when utilized/available margin crosses TRIGGER_RATIO (0.80),
liquidate open positions one at a time -- lowest priority bucket first
(MIS/intraday, then options, then NRML/swing futures), lowest unrealized P&L
within each bucket first -- re-checking the real ratio after every close,
until it's back under TARGET_RATIO (0.60) or there's nothing left to close.

Deliberately conservative: if the margin ratio can't be parsed with
confidence from rmsLimit(), the breaker does nothing and returns
checked=False -- it never guesses a number to decide whether to liquidate.
The spec named `/rest/secure/angelbroking/user/profile` as the polling
endpoint; that's the account-profile API, not a margin one -- this uses
rmsLimit(), the same endpoint angel.py's own get_balance() already relies on
for real capital data.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("margin_circuit_breaker")

TRIGGER_RATIO = 0.80
TARGET_RATIO = 0.60
MAX_CLOSES_PER_RUN = 20  # hard cap so a bad readout can't close everything


def compute_margin_utilization(angel) -> Optional[Dict[str, float]]:
    """Query Angel's RMS limit and compute the utilized/available ratio.
    Returns None -- never a guessed number -- if the response can't be
    parsed with confidence. Callers must treat None as 'skip the check'."""
    if not angel or not getattr(angel, "obj", None):
        return None
    try:
        lock = getattr(angel, "_lock", None)
        if lock is not None:
            with lock:
                resp = angel.obj.rmsLimit()
        else:
            resp = angel.obj.rmsLimit()
    except Exception as e:
        logger.debug("rmsLimit() failed: %s", e)
        return None
    if not resp or not isinstance(resp, dict):
        return None
    payload = resp.get("data", resp)
    if not isinstance(payload, dict):
        return None

    available = None
    for key in ("availablecash", "availableCash", "net", "netavailablecash",
                "NetAvailableCash", "availableBalance"):
        v = payload.get(key)
        if v is not None:
            try:
                available = float(v)
                break
            except Exception:
                continue

    utilized = None
    for key in ("utiliseddebits", "utilisedDebits", "utilisedMargin",
                "utilisedmargin", "utilised"):
        v = payload.get(key)
        if v is not None:
            try:
                utilized = float(v)
                break
            except Exception:
                continue

    if available is None or utilized is None:
        logger.debug("margin_utilization: could not find both available+utilized "
                      "keys in rmsLimit response: %s", list(payload.keys()))
        return None
    total = available + utilized
    if total <= 0:
        return None
    return {"available": available, "utilized": utilized, "total": total,
            "ratio": utilized / total}


def _priority(trade_manager, trade) -> int:
    """0 = close first (MIS/intraday), 1 = options, 2 = close last (NRML/swing futures)."""
    if trade_manager._is_option_trade(trade):
        return 1
    return 2 if trade_manager._is_swing_trade(trade) else 0


def _rank_candidates(trade_manager, ltp_getter) -> List[Tuple[int, float, str, Any]]:
    """(priority, unrealized_pnl, trade_id, trade), sorted so the
    highest-priority-to-close, lowest-P&L trade is first."""
    ranked = []
    for trade_id, trade in list(trade_manager.open_trades.items()):
        try:
            exchange = trade_manager._trade_exchange(trade)
            ltp = ltp_getter(trade.symbol, exchange) if ltp_getter else None
            mark = float(ltp) if ltp else float(trade.entry_price)
            unrealized = trade_manager._calculate_pnl(
                trade, mark, is_options=trade_manager._is_option_trade(trade))
        except Exception as e:
            logger.debug("margin_circuit_breaker: rank failed for %s: %s", trade_id, e)
            continue
        ranked.append((_priority(trade_manager, trade), unrealized, trade_id, trade))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return ranked


def run_margin_circuit_breaker(
    angel,
    trade_manager,
    *,
    ltp_getter: Optional[Callable[[str, str], Optional[float]]] = None,
    alerts=None,
) -> Dict[str, Any]:
    """Check margin utilization; liquidate toward TARGET_RATIO if TRIGGER_RATIO
    is breached. Safe to call repeatedly (e.g. every 30s) -- a no-op whenever
    the ratio is unavailable or under trigger."""
    result: Dict[str, Any] = {"checked": True, "triggered": False, "closed": []}

    margin = compute_margin_utilization(angel)
    if margin is None:
        result["checked"] = False
        return result
    result["ratio_before"] = margin["ratio"]

    if margin["ratio"] <= TRIGGER_RATIO:
        return result

    logger.warning(
        "MARGIN CIRCUIT BREAKER TRIGGERED: utilization=%.1f%% "
        "(available=Rs%.0f utilized=Rs%.0f) -- liquidating toward %.0f%%",
        margin["ratio"] * 100, margin["available"], margin["utilized"], TARGET_RATIO * 100,
    )
    result["triggered"] = True
    if alerts:
        try:
            alerts.warning(
                f"MARGIN CIRCUIT BREAKER: utilization {margin['ratio']*100:.0f}% "
                f"(available Rs{margin['available']:,.0f}, utilized Rs{margin['utilized']:,.0f}) "
                f"-- auto-liquidating lowest-P&L positions (MIS, then options, then futures) "
                f"toward {TARGET_RATIO*100:.0f}%.",
                dedup_key="margin_circuit_breaker",
            )
        except Exception:
            pass

    closes = 0
    while closes < MAX_CLOSES_PER_RUN:
        ranked = _rank_candidates(trade_manager, ltp_getter)
        if not ranked:
            logger.warning("Margin circuit breaker: no open positions left to close")
            break
        _, unrealized, trade_id, trade = ranked[0]
        exchange = trade_manager._trade_exchange(trade)
        exit_price = None
        if ltp_getter:
            try:
                exit_price = ltp_getter(trade.symbol, exchange)
            except Exception:
                exit_price = None
        exit_price = float(exit_price) if exit_price else float(trade.entry_price)

        ok = trade_manager._close_trade_internal(
            trade_id, exit_price, "margin_circuit_breaker", exchange)
        if ok:
            closes += 1
            result["closed"].append({
                "trade_id": trade_id, "symbol": trade.symbol, "unrealized_pnl": unrealized,
            })
            logger.warning("Margin circuit breaker closed %s (%s) unrealized_pnl=Rs%.2f",
                            trade.symbol, trade_id, unrealized)
        else:
            logger.error("Margin circuit breaker: close failed for %s, stopping "
                          "rather than retry-looping on the same position", trade_id)
            break

        margin = compute_margin_utilization(angel)
        if margin is None:
            logger.warning("Margin circuit breaker: lost margin readout mid-liquidation, "
                            "stopping after %d close(s)", closes)
            break
        result["ratio_after"] = margin["ratio"]
        if margin["ratio"] <= TARGET_RATIO:
            logger.warning("Margin circuit breaker: back under target (%.1f%%) after %d close(s)",
                            margin["ratio"] * 100, closes)
            break

    if alerts and closes:
        try:
            final_ratio = result.get("ratio_after", margin["ratio"] if margin else 0.0)
            alerts.warning(
                f"Margin circuit breaker closed {closes} position(s). "
                f"Utilization now {final_ratio*100:.0f}%.",
                dedup_key=None,
            )
        except Exception:
            pass

    return result
