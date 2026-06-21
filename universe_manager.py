"""
universe_manager.py

Tiered symbol universe for faster learning without broad live exposure.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, Set


INDEX_UNIVERSE = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "SENSEX"]

# Curated liquid F&O-style stock universe. This is intentionally conservative:
# enough names to create more paper/shadow samples, not so many that scanning
# turns into a rate-limit machine.
LIQUID_FNO_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "LT", "SBIN",
    "AXISBANK", "KOTAKBANK", "BHARTIARTL", "ITC", "HINDUNILVR", "BAJFINANCE",
    "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO",
    "HCLTECH", "TECHM", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "HINDALCO",
    "ADANIENT", "ADANIPORTS", "ONGC", "COALINDIA", "NTPC", "POWERGRID",
    "M&M", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO", "CIPLA", "DRREDDY",
    "DIVISLAB", "APOLLOHOSP", "BRITANNIA", "NESTLEIND", "GRASIM",
    "INDUSINDBK", "BANKBARODA", "CANBK", "PNB", "AUBANK", "FEDERALBNK",
    "IDFCFIRSTB", "CHOLAFIN", "SHRIRAMFIN", "SBILIFE", "HDFCLIFE",
    "ICICIPRULI", "BAJAJFINSV", "DLF", "GODREJPROP", "LODHA", "IRCTC",
    "INDIGO", "HAL", "BEL", "BHEL", "SIEMENS", "ABB", "PIDILITIND",
    "TATACONSUM", "TRENT", "DMART", "ZOMATO", "PAYTM", "NYKAA",
]


def _cfg(name: str, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _dedupe(symbols: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for symbol in symbols:
        sym = str(symbol or "").upper().strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _csv_universe(path: str = "nifty200.csv") -> List[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        with p.open(newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if "Symbol" not in (reader.fieldnames or []):
                return []
            return _dedupe(row.get("Symbol", "") for row in reader)
    except Exception:
        return []


def build_learning_universe(csv_symbols: Iterable[str] | None = None) -> List[str]:
    mode = str(_cfg("LEARNING_UNIVERSE_MODE", "liquid_fno") or "liquid_fno").lower()
    max_symbols = max(1, int(_cfg("LEARNING_UNIVERSE_MAX_SYMBOLS", 100) or 100))
    extras = list(_cfg("LEARNING_UNIVERSE_EXTRA_SYMBOLS", []) or [])
    csv_list = _dedupe(csv_symbols or []) or (_csv_universe() if mode in {"all", "nifty200", "nifty100"} else [])

    if mode in {"all", "nifty200"}:
        base = csv_list or LIQUID_FNO_UNIVERSE
    elif mode == "nifty100":
        base = (csv_list or LIQUID_FNO_UNIVERSE)[:100]
    elif mode in {"liquid", "liquid_fno", "fno"}:
        csv_set = set(csv_list)
        base = [s for s in LIQUID_FNO_UNIVERSE if not csv_set or s in csv_set]
        if not base:
            base = LIQUID_FNO_UNIVERSE
    elif mode in {"trade_symbols", "small"}:
        base = list(_cfg("TRADE_SYMBOLS", INDEX_UNIVERSE) or INDEX_UNIVERSE)
    else:
        base = LIQUID_FNO_UNIVERSE

    return _dedupe(list(INDEX_UNIVERSE) + list(extras) + list(base))[:max_symbols]


def probation_universe() -> List[str]:
    return _dedupe(_cfg("PROBATION_UNIVERSE", ["NIFTY", "BANKNIFTY", "SENSEX"]) or [])


def live_universe() -> List[str]:
    return _dedupe(_cfg("LIVE_UNIVERSE", []) or [])


def _underlying_from_option_symbol(symbol: str) -> str:
    sym = str(symbol or "").upper().strip()
    for suffix in ("CE", "PE"):
        if sym.endswith(suffix):
            head = sym[:-2]
            while head and head[-1].isdigit():
                head = head[:-1]
            return head
    return sym


def is_probation_symbol_allowed(symbol: str, source_symbol: str = "") -> bool:
    allowed = set(probation_universe())
    if not allowed:
        return False
    candidates = {
        str(symbol or "").upper().strip(),
        str(source_symbol or "").upper().strip(),
        _underlying_from_option_symbol(symbol),
    }
    return bool(allowed & {c for c in candidates if c})


def is_live_symbol_allowed(symbol: str, source_symbol: str = "") -> bool:
    allowed = set(live_universe())
    if not allowed:
        return True
    candidates = {
        str(symbol or "").upper().strip(),
        str(source_symbol or "").upper().strip(),
        _underlying_from_option_symbol(symbol),
    }
    return bool(allowed & {c for c in candidates if c})


def describe_universe(csv_symbols: Iterable[str] | None = None) -> dict:
    learning = build_learning_universe(csv_symbols)
    return {
        "learning_mode": str(_cfg("LEARNING_UNIVERSE_MODE", "liquid_fno")),
        "learning_count": len(learning),
        "learning_preview": learning[:20],
        "probation_universe": probation_universe(),
        "live_universe": live_universe(),
    }
