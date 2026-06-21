"""
corporate_actions.py  —  Corporate Action Calendar

Tracks: Ex-dividend dates, stock splits, bonus issues, rights issues.
BLOCKS trading on ex-date: price gap is NOT a signal, it's a corporate action.
BLOCKS trading day before rights/bonus record date.

DATA SOURCE: NSE corporate actions API (free)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)
_CACHE = Path("corporate_actions_cache.json")
_TTL   = 3600 * 12  # 12 hours


def _fetch_corporate_actions() -> list:
    """Fetch upcoming corporate actions from NSE."""
    actions = []
    try:
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0",
                           "Referer":"https://www.nseindia.com/"})
        s.get("https://www.nseindia.com/", timeout=6)
        end   = (date.today() + timedelta(days=30)).strftime("%d-%m-%Y")
        start = date.today().strftime("%d-%m-%Y")
        url   = (f"https://www.nseindia.com/api/corporates-corporateActions"
                 f"?index=equities&from_date={start}&to_date={end}")
        r = s.get(url, timeout=12)
        if r.status_code == 200:
            raw = r.json()
            for row in (raw if isinstance(raw, list) else raw.get("data", [])):
                ex_date_str = row.get("exDate", "") or row.get("ex_date","")
                sym = str(row.get("symbol","")).upper()
                purpose = str(row.get("purpose","") or row.get("subject","")).upper()
                if sym and ex_date_str:
                    try:
                        # parse DD-MMM-YYYY or YYYY-MM-DD
                        for fmt in ("%d-%b-%Y","%Y-%m-%d","%d-%m-%Y"):
                            try:
                                d = datetime.strptime(ex_date_str.strip(), fmt).date()
                                break
                            except ValueError:
                                d = None
                        if d:
                            actions.append({
                                "symbol":  sym,
                                "ex_date": str(d),
                                "purpose": purpose,
                            })
                    except Exception:
                        pass
    except Exception as e:
        logger.debug("Corp actions fetch: %s", e)
    return actions


def get_corporate_actions(force: bool = False) -> list:
    if not force:
        try:
            if _CACHE.exists():
                d = json.loads(_CACHE.read_text())
                if time.time() - d.get("ts", 0) < _TTL:
                    return d.get("actions", [])
        except Exception:
            pass
    actions = _fetch_corporate_actions()
    try:
        _CACHE.write_text(json.dumps({"ts": time.time(), "actions": actions}))
    except Exception:
        pass
    return actions


def has_corporate_action_today(symbol: str) -> bool:
    """
    Returns True if symbol has a corporate action today or tomorrow.
    Blocks trading on these days to avoid false signals from price gaps.
    """
    sym     = symbol.upper().replace(".NS","")
    today   = str(date.today())
    tomorrow= str(date.today() + timedelta(days=1))
    actions = get_corporate_actions()
    for a in actions:
        if a.get("symbol") == sym and a.get("ex_date") in (today, tomorrow):
            purpose = a.get("purpose","")
            logger.info("Corporate action for %s: %s on %s",
                        sym, purpose, a.get("ex_date"))
            return True
    return False


def get_action_summary() -> str:
    """Summary for morning Telegram brief."""
    today   = str(date.today())
    actions = get_corporate_actions()
    today_actions = [a for a in actions if a.get("ex_date") == today]
    if not today_actions:
        return "Corp actions today: None"
    syms = [f"{a['symbol']} ({a['purpose'][:15]})" for a in today_actions[:5]]
    return f"⚠️ Corp actions today: {', '.join(syms)}"

# Backward-compat alias
refresh_corporate_actions = get_corporate_actions
