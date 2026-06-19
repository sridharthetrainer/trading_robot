"""
fno_ban_list.py  —  NSE F&O Ban List Checker

When a stock is in F&O ban (OI > 95% of MWPL):
  - No new positions allowed
  - Only position squaring permitted
  - Price discovery BREAKS DOWN
  - Your signals in these stocks are unreliable

This module blocks new entries in banned stocks automatically.

DATA SOURCE: https://www.nseindia.com/api/fo-ban (free, published daily)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Set

import requests

logger = logging.getLogger(__name__)
_CACHE = Path("fno_ban_cache.json")
_TTL   = 3600 * 4


def _fetch_ban_list() -> Set[str]:
    """Fetch current F&O ban list from NSE."""
    try:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer":    "https://www.nseindia.com/",
        })
        s.get("https://www.nseindia.com/", timeout=6)
        r = s.get("https://www.nseindia.com/api/fo-ban", timeout=10)
        if r.status_code == 200:
            data = r.json()
            banned = set()
            for row in (data.get("data", []) or data if isinstance(data, list) else []):
                sym = str(row.get("symbol", row if isinstance(row, str) else "")).strip().upper()
                if sym:
                    banned.add(sym)
            logger.info("F&O ban list: %d stocks banned", len(banned))
            return banned
    except Exception as e:
        logger.debug("Ban list fetch: %s", e)
    return set()


def get_ban_list(force: bool = False) -> Set[str]:
    """Get current F&O ban list with caching."""
    if not force:
        try:
            if _CACHE.exists():
                d = json.loads(_CACHE.read_text())
                if time.time() - d.get("ts", 0) < _TTL:
                    return set(d.get("symbols", []))
        except Exception:
            pass
    banned = _fetch_ban_list()
    try:
        _CACHE.write_text(json.dumps({
            "ts": time.time(),
            "date": date.today().isoformat(),
            "symbols": list(banned),
        }))
    except Exception:
        pass
    return banned


def is_banned(symbol: str) -> bool:
    """Return True if symbol is in F&O ban. Blocks new entries."""
    sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
    # Indices are never banned
    if sym in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}:
        return False
    banned = get_ban_list()
    return sym in banned


def filter_banned(symbols: list) -> list:
    """Filter out banned symbols from a list."""
    banned = get_ban_list()
    return [s for s in symbols
            if s.upper().replace(".NS","").replace("-EQ","") not in banned]


def get_ban_status_message() -> str:
    """Human-readable ban list for Telegram morning brief."""
    banned = get_ban_list()
    if not banned:
        return "F&O Ban: None today ✅"
    return f"F&O Ban ({len(banned)}): {', '.join(sorted(banned)[:10])}"
