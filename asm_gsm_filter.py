"""
asm_gsm_filter.py — SEBI ASM / GSM Surveillance Watch List Filter

Stocks on SEBI's Additional Surveillance Measure (ASM) or Graded Surveillance
Measure (GSM) lists suffer:
  - Market-maker withdrawal → 3-5× normal slippage
  - Abnormal institutional avoidance
  - Restricted trading conditions (higher margins, circuit limits)
  - Signals that look valid in backtest but are unexecutable live

This module fetches NSE's published ASM/GSM lists daily and provides a hard
filter identical in interface to fno_ban_list.py.

DATA SOURCE:
  ASM: https://www.nseindia.com/api/reportsmf?index=ASMReport
  GSM: https://www.nseindia.com/api/reportsmf?index=GSMReport
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Set

import requests

logger   = logging.getLogger(__name__)
_CACHE   = Path("asm_gsm_cache.json")
_TTL     = 3600 * 6   # refresh every 6 hours

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=6)
    except Exception:
        pass
    return s


def _fetch_list(url: str, session: requests.Session) -> Set[str]:
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return set()
        payload = r.json()
        # NSE returns {"data": [...]} or a bare list
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        symbols: Set[str] = set()
        for row in (rows or []):
            if isinstance(row, str):
                sym = row.strip().upper()
            elif isinstance(row, dict):
                sym = str(row.get("symbol", row.get("Symbol", ""))).strip().upper()
            else:
                continue
            if sym:
                symbols.add(sym.replace(".NS", "").replace("-EQ", ""))
        return symbols
    except Exception as e:
        logger.debug("ASM/GSM fetch %s: %s", url, e)
        return set()


def _fetch_asm_gsm() -> Set[str]:
    s = _nse_session()
    asm = _fetch_list("https://www.nseindia.com/api/reportsmf?index=ASMReport", s)
    gsm = _fetch_list("https://www.nseindia.com/api/reportsmf?index=GSMReport", s)
    combined = asm | gsm
    logger.info("ASM/GSM watch list: %d symbols (ASM=%d, GSM=%d)", len(combined), len(asm), len(gsm))
    return combined


def get_asm_gsm_list(force: bool = False) -> Set[str]:
    """Return combined ASM + GSM symbol set with file caching."""
    if not force:
        try:
            if _CACHE.exists():
                d = json.loads(_CACHE.read_text())
                if time.time() - d.get("ts", 0) < _TTL:
                    return set(d.get("symbols", []))
        except Exception:
            pass
    symbols = _fetch_asm_gsm()
    try:
        _CACHE.write_text(json.dumps({
            "ts":      time.time(),
            "date":    date.today().isoformat(),
            "symbols": list(symbols),
        }))
    except Exception:
        pass
    return symbols


def is_under_surveillance(symbol: str) -> bool:
    """Return True if symbol is on ASM or GSM watch list."""
    sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
    if sym in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}:
        return False
    return sym in get_asm_gsm_list()


def filter_surveillance(symbols: list) -> list:
    """Remove ASM/GSM symbols from a candidate list."""
    watch = get_asm_gsm_list()
    return [s for s in symbols
            if s.upper().replace(".NS", "").replace("-EQ", "") not in watch]


def get_surveillance_status_message() -> str:
    """Human-readable ASM/GSM count for morning brief."""
    watch = get_asm_gsm_list()
    if not watch:
        return "ASM/GSM: None flagged ✅"
    return f"ASM/GSM ({len(watch)}): {', '.join(sorted(watch)[:10])}{'...' if len(watch) > 10 else ''}"
