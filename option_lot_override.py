"""
option_lot_override.py — runtime, Telegram-settable lot count for option trades.

Lets you cap option position size to a fixed number of lots (1 / 2 / 3 …) live
during the trading day via `/optlots N`, without a restart. The override is a
CEILING applied on top of the normal affordability + MAX_LOTS limits — i.e. the
engine trades min(override, affordable_lots, max_lots). It AUTO-EXPIRES at end of
day (date-stamped) so an aggressive setting never silently carries into tomorrow.

Single source of truth for both sizing paths (OptionSelector.compute_lots and
option_chain_engine._compute_lots). Pure stdlib, best-effort (never raises into
the trade path).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_FILE = Path("option_lot_override.json")
_HARD_MAX = 20   # absolute safety cap regardless of what's requested


def set_lots_override(lots: int) -> dict:
    """Set the option lot ceiling for TODAY. lots<=0 clears it (back to auto).
    Returns the new state dict."""
    n = int(lots)
    if n <= 0:
        clear_lots_override()
        return {"lots": None, "active": False, "date": date.today().isoformat()}
    n = max(1, min(n, _HARD_MAX))
    state = {"lots": n, "active": True, "date": date.today().isoformat()}
    try:
        _FILE.write_text(json.dumps(state))
    except Exception as exc:
        logger.warning("set_lots_override write failed: %s", exc)
    return state


def clear_lots_override() -> None:
    try:
        if _FILE.exists():
            _FILE.unlink()
    except Exception as exc:
        logger.warning("clear_lots_override failed: %s", exc)


def get_lots_override() -> Optional[int]:
    """Today's lot ceiling, or None if unset/expired/invalid. Never raises."""
    try:
        if not _FILE.exists():
            return None
        state = json.loads(_FILE.read_text())
        if str(state.get("date")) != date.today().isoformat():
            return None   # stale (set on a previous day) → ignore
        lots = int(state.get("lots") or 0)
        return lots if lots >= 1 else None
    except Exception:
        return None


def apply_override(computed_lots: int) -> int:
    """Apply the override as a ceiling to an already-computed lot count.
    Returns min(override, computed) when an override is active (but at least 1 if
    the setup is otherwise tradable), else the computed value unchanged."""
    override = get_lots_override()
    if override is None:
        return int(computed_lots)
    if computed_lots <= 0:
        return 0   # not affordable / not tradable — override doesn't force a trade
    return max(1, min(int(override), int(computed_lots)))


def status_text() -> str:
    """Human-readable status for Telegram."""
    o = get_lots_override()
    if o is None:
        return "🎚️ Option lots: <b>AUTO</b> (sized by capital/confidence, cap MAX_LOTS)"
    return f"🎚️ Option lots: <b>{o}</b> (manual ceiling, today only)"
