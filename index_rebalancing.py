"""
index_rebalancing.py  —  Nifty/MSCI Index Rebalancing Signal

When NSE announces Nifty50/Nifty200 index changes:
  Stock being ADDED   → Index funds MUST buy → bullish 5-15% over 2-3 weeks
  Stock being DELETED → Index funds MUST sell → bearish 5-10% over 2-3 weeks

NSE announces rebalancing quarterly (Jan/Apr/Jul/Oct effective dates).
We watch for announcements and create a time-bounded signal.

DATA: Loaded from index_rebalancing.json (manually updated or scraped)
      NSE announces on their website 4-6 weeks before effective date.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)
_DATA_FILE = Path("index_rebalancing.json")


def _default_data() -> dict:
    """Template for manually-maintained rebalancing data."""
    return {
        "additions": {},    # symbol → {"effective_date": "2026-01-01", "index": "NIFTY50"}
        "deletions": {},    # symbol → {"effective_date": "2026-01-01", "index": "NIFTY50"}
        "last_updated": str(date.today()),
    }


def load_rebalancing_data() -> dict:
    try:
        if _DATA_FILE.exists():
            return json.loads(_DATA_FILE.read_text())
    except Exception:
        pass
    data = _default_data()
    try:
        _DATA_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass
    return data


def get_rebalancing_signal(symbol: str, direction: str) -> float:
    """
    Returns score modifier if symbol has upcoming index rebalancing.
    Adds score if aligned with expected institutional flow.
    """
    try:
        data    = load_rebalancing_data()
        today   = date.today()
        sym     = symbol.upper()
        window  = 21   # signal active for 21 trading days from announcement

        # Check if being added
        if sym in data.get("additions", {}):
            info = data["additions"][sym]
            eff  = date.fromisoformat(info.get("effective_date", "2099-01-01"))
            days = (eff - today).days
            if -5 <= days <= window:
                # In the buying window
                mod = 1.5 if direction == "BUY" else -0.8
                logger.debug("%s index addition signal: mod=%.1f days=%d", sym, mod, days)
                return round(mod, 2)

        # Check if being deleted
        if sym in data.get("deletions", {}):
            info = data["deletions"][sym]
            eff  = date.fromisoformat(info.get("effective_date", "2099-01-01"))
            days = (eff - today).days
            if -5 <= days <= window:
                mod = 1.5 if direction == "SELL" else -0.8
                logger.debug("%s index deletion signal: mod=%.1f days=%d", sym, mod, days)
                return round(mod, 2)

    except Exception as e:
        logger.debug("Rebalancing signal: %s", e)
    return 0.0


def add_rebalancing_event(symbol: str, action: str,
                           effective_date: str, index: str = "NIFTY50") -> None:
    """
    Add a rebalancing event manually.
    action: 'addition' or 'deletion'
    effective_date: 'YYYY-MM-DD'

    Usage from terminal:
        from index_rebalancing import add_rebalancing_event
        add_rebalancing_event("DMART", "addition", "2026-04-01", "NIFTY50")
    """
    data = load_rebalancing_data()
    key  = "additions" if action == "addition" else "deletions"
    data[key][symbol.upper()] = {
        "effective_date": effective_date,
        "index":          index,
        "added_on":       str(date.today()),
    }
    data["last_updated"] = str(date.today())
    try:
        _DATA_FILE.write_text(json.dumps(data, indent=2))
        logger.info("Rebalancing event added: %s %s %s", symbol, action, effective_date)
    except Exception as e:
        logger.error("Could not save rebalancing data: %s", e)
