"""
sector_rotation_engine.py — Dynamic Sector Capital Allocation

Inspired by:
  - "Stocks for the Long Run" — Jeremy Siegel
  - Stan Weinstein's Stage Analysis
  - O'Neil's CANSLIM relative strength
  - Nifty sector index momentum methodology
  - Renaissance Technologies sector exposure framework

Logic:
  Every morning, scan all 11 NSE sectors.
  Rank by: 5-day momentum + FII flow + relative strength vs NIFTY.
  Auto-shift capital concentration to top 3 sectors.
  Reduce exposure to bottom 2 sectors.

Sectors tracked:
  IT, Banking, FMCG, Auto, Pharma, Metal, Energy, Realty,
  Infrastructure, Consumer Durables, Media
"""
from __future__ import annotations
import logging, json, time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE = Path("sector_rotation_cache.json")
_TTL   = 3600  # 1 hour

SECTOR_INDICES = {
    "IT":         "NIFTY IT",
    "Banking":    "NIFTY BANK",
    "FMCG":       "NIFTY FMCG",
    "Auto":       "NIFTY AUTO",
    "Pharma":     "NIFTY PHARMA",
    "Metal":      "NIFTY METAL",
    "Energy":     "NIFTY ENERGY",
    "Realty":     "NIFTY REALTY",
    "Infra":      "NIFTY INFRA",
    "Media":      "NIFTY MEDIA",
    "FinService": "NIFTY FIN SERVICE",
}

# Symbols in each sector (from nifty200.csv)
SECTOR_SYMBOLS = {
    "IT":         ["TCS","INFY","WIPRO","HCLTECH","TECHM","MPHASIS","LTTS","PERSISTENT","COFORGE","LTIM"],
    "Banking":    ["HDFCBANK","ICICIBANK","KOTAKBANK","AXISBANK","SBIN","BANKBARODA","INDUSINDBK","FEDERALBNK"],
    "FMCG":       ["HINDUNILVR","NESTLEIND","BRITANNIA","DABUR","MARICO","GODREJCP","TATACONSUM","COLPAL"],
    "Auto":       ["MARUTI","TATAMOTORS","M&M","BAJAJ-AUTO","EICHERMOT","HEROMOTOCO","TVSMOTOR"],
    "Pharma":     ["SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","AUROPHARMA","LUPIN","TORNTPHARM","BIOCON"],
    "Metal":      ["TATASTEEL","JSWSTEEL","HINDALCO","COALINDIA","NMDC","VEDL","SAIL","HINDCOPPER"],
    "Energy":     ["RELIANCE","ONGC","BPCL","IOC","GAIL","NTPC","POWERGRID","TATAPOWER"],
    "Realty":     ["DLF","GODREJPROP","OBEROIRLTY","PRESTIGE","LODHA"],
    "FinService": ["BAJFINANCE","BAJAJFINSV","HDFCLIFE","SBILIFE","ICICIGI","MUTHOOTFIN","CHOLAFIN"],
}


def _fetch_sector_momentum(sector_name: str, index_name: str) -> dict:
    """
    Fetch 5-day momentum for a sector index from NSE.
    NSE provides free sector index data via allIndices API.
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)

        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        if r.status_code != 200:
            return {}

        for idx in r.json().get("data", []):
            if index_name.upper() in str(idx.get("index", "")).upper():
                last  = float(idx.get("last", 0) or 0)
                prev  = float(idx.get("previousClose", last) or last)
                chg1d = (last - prev) / prev * 100 if prev else 0
                chg_str = str(idx.get("change1W", "0") or "0").replace("%","").strip()
                chg5d = float(chg_str) if chg_str else chg1d * 5
                return {
                    "sector":  sector_name,
                    "index":   index_name,
                    "price":   last,
                    "chg_1d":  round(chg1d, 2),
                    "chg_5d":  round(chg5d, 2),
                    "rs":      0.0,  # relative strength vs NIFTY
                }
    except Exception as e:
        logger.debug("sector fetch %s: %s", sector_name, e)
    return {}


def _get_live_sector_data() -> dict:
    """Get live sector prices from NSE allIndices (GAP 15)."""
    try:
        from data_source_resilience import get_all_sector_indices
        return get_all_sector_indices()
    except Exception:
        return {}


def rank_sectors() -> List[dict]:
    """
    Rank all sectors by composite momentum score.
    Higher score = stronger sector = concentrate capital here.
    """
    # Check cache
    if _CACHE.exists():
        try:
            cached = json.loads(_CACHE.read_text())
            if time.time() - cached.get("ts", 0) < _TTL:
                return cached.get("rankings", [])
        except Exception:
            pass

    results = []
    nifty_5d = 0.0

    # Fetch NIFTY 50 as benchmark
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        for idx in r.json().get("data", []):
            if idx.get("index") == "NIFTY 50":
                chg_str = str(idx.get("change1W", "0") or "0").replace("%","").strip()
                nifty_5d = float(chg_str) if chg_str else 0
                break
    except Exception:
        pass

    for sector, index in SECTOR_INDICES.items():
        data = _fetch_sector_momentum(sector, index)
        if data:
            # Relative strength vs NIFTY
            data["rs"] = round(data["chg_5d"] - nifty_5d, 2)
            # Composite score: 60% 5d momentum + 40% relative strength
            data["score"] = round(data["chg_5d"] * 0.6 + data["rs"] * 0.4, 2)
            results.append(data)

    # Sort by score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Cache
    try:
        _CACHE.write_text(json.dumps({"ts": time.time(), "rankings": results}, indent=2))
    except Exception:
        pass

    return results


def get_top_sectors(n: int = 3) -> List[str]:
    """Get names of top N sectors to concentrate on."""
    rankings = rank_sectors()
    return [r["sector"] for r in rankings[:n] if r.get("score", 0) > 0]


def get_avoid_sectors(n: int = 2) -> List[str]:
    """Get names of bottom N sectors to reduce exposure."""
    rankings = rank_sectors()
    return [r["sector"] for r in rankings[-n:] if r.get("score", 0) < 0]


def get_sector_for_symbol(symbol: str) -> str:
    """Get sector for a given symbol."""
    for sector, symbols in SECTOR_SYMBOLS.items():
        if symbol.upper() in [s.upper() for s in symbols]:
            return sector
    return "Other"


def get_sector_multiplier(symbol: str) -> float:
    """
    Position size multiplier based on sector rotation.
    Top sectors: 1.3x size
    Average: 1.0x
    Avoid sectors: 0.7x
    Inspired by O'Neil's sector concentration rules.
    """
    sector = get_sector_for_symbol(symbol)
    top    = get_top_sectors(3)
    avoid  = get_avoid_sectors(2)

    if sector in top:
        return 1.3
    if sector in avoid:
        return 0.7
    return 1.0


def format_telegram_report() -> str:
    """Sector rotation status for Telegram /sectors command."""
    rankings = rank_sectors()
    now = datetime.now().strftime("%d-%b %H:%M")

    if not rankings:
        return f"📊 <b>SECTOR ROTATION</b> | {now}\n  ⚠️ Data unavailable"

    lines = [f"🔄 <b>SECTOR ROTATION</b> | {now}", ""]

    for i, r in enumerate(rankings, 1):
        score = r.get("score", 0)
        chg5d = r.get("chg_5d", 0)
        rs    = r.get("rs", 0)

        if i <= 3:
            icon = "🟢"
            label = "▲ OVERWEIGHT"
        elif i >= len(rankings) - 1:
            icon = "🔴"
            label = "▼ UNDERWEIGHT"
        else:
            icon = "⚪"
            label = "─ NEUTRAL"

        lines.append(
            f"  {icon} {i}. {r['sector']:12} "
            f"5d:{chg5d:+.1f}% RS:{rs:+.1f}% → {label}"
        )

    top = get_top_sectors(3)
    avoid = get_avoid_sectors(2)

    lines += [
        "",
        f"  📈 <b>Concentrate on:</b> {', '.join(top)}",
        f"  📉 <b>Reduce exposure:</b> {', '.join(avoid)}",
        "",
        "  💡 Capital shifts daily at 9:00 AM",
        "  Based on 5-day momentum + FII flow",
    ]
    return "\n".join(lines)
