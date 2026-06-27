"""
bhav_copy.py  —  NSE BhavCopy delivery % and OI 4-quadrant analysis.

FREE DATA SOURCES:
  BhavCopy:   NSE publishes daily at ~6 PM → delivery % per stock
  OI Change:  From NSE F&O BhavCopy → OI delta + price delta

SIGNALS:
  Delivery % > 80% + price rising  → institutional accumulation → +score
  Delivery % > 80% + price falling → institutional distribution → -score
  OI up + price up   → fresh longs  (strong bullish)
  OI up + price down → fresh shorts (strong bearish)
  OI down + price up → short cover  (weaker — temporary)
  OI down + price down → long unwind (weaker — temporary)
"""
from __future__ import annotations
import json, logging, time
from datetime import date
from pathlib import Path
from typing import Optional
import requests

logger = logging.getLogger(__name__)
_CACHE = Path("bhav_cache.json")
_TTL   = 3600 * 4   # refresh every 4 hours


def _fetch_bhav(trade_date: Optional[date] = None) -> dict:
    """Fetch BhavCopy from NSE. Returns {symbol: {delivery_pct, close, prev_close}}."""
    if trade_date is None:
        trade_date = date.today()
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://www.nseindia.com/"})
        session.get("https://www.nseindia.com/", timeout=5)
        url = (f"https://www.nseindia.com/api/equity-stockIndices"
               f"?index=NIFTY%20200")
        r = session.get(url, timeout=10)
        data = r.json()
        result = {}
        for row in data.get("data", []):
            sym = row.get("symbol", "")
            if not sym:
                continue
            result[sym] = {
                "close":        float(row.get("lastPrice", 0) or 0),
                "prev_close":   float(row.get("previousClose", 0) or 0),
                "change_pct":   float(row.get("pChange", 0) or 0),
                "delivery_pct": float(row.get("deliveryToTradedQuantity", 0) or 0),
                "volume":       float(row.get("totalTradedVolume", 0) or 0),
            }
        return result
    except Exception as e:
        logger.debug("BhavCopy fetch error: %s", e)
        return {}


def _load_cache() -> dict:
    try:
        if _CACHE.exists():
            d = json.loads(_CACHE.read_text())
            if time.time() - d.get("ts", 0) < _TTL:
                return d.get("data", {})
    except Exception:
        pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        _CACHE.write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception:
        pass


def get_bhav_data(force_refresh: bool = False) -> dict:
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached
    data = _fetch_bhav()
    if data:
        _save_cache(data)
    return data


def delivery_pct_score(symbol: str, direction: str) -> float:
    """
    Returns score modifier based on delivery %.
    High delivery + aligned direction = institutional conviction.
    """
    data = get_bhav_data()
    row  = data.get(symbol.upper(), {})
    if not row:
        return 0.0
    dpct = float(row.get("delivery_pct", 0))
    chg  = float(row.get("change_pct", 0))
    if dpct <= 0:
        return 0.0
    # Institutional accumulation: high delivery + price up + BUY signal
    if dpct > 80 and chg > 0.3 and direction == "BUY":
        return round(min((dpct - 80) / 20, 1.5), 2)   # +0 to +1.5
    # Institutional distribution: high delivery + price down + SELL signal
    if dpct > 80 and chg < -0.3 and direction == "SELL":
        return round(min((dpct - 80) / 20, 1.5), 2)
    # Fighting institutional flow
    if dpct > 70 and chg > 1.0 and direction == "SELL":
        return -1.0
    if dpct > 70 and chg < -1.0 and direction == "BUY":
        return -1.0
    return 0.0


# ── OI 4-quadrant analysis ────────────────────────────────────────────────────
def oi_quadrant_score(symbol: str, direction: str,
                      oi_change: float, price_change: float) -> tuple[str, float]:
    """
    Returns (quadrant_label, score_modifier).

    Quadrants:
      Q1: OI up + price up   = fresh longs  → bullish (+1.5)
      Q2: OI up + price down = fresh shorts → bearish (+1.5 for SELL)
      Q3: OI down + price up = short cover  → weak bullish (+0.5)
      Q4: OI down + price dn = long unwind  → weak bearish (+0.5 for SELL)
    """
    if oi_change > 0 and price_change > 0:
        label = "FRESH_LONGS"
        mod   = +1.5 if direction == "BUY"  else -1.0
    elif oi_change > 0 and price_change < 0:
        label = "FRESH_SHORTS"
        mod   = +1.5 if direction == "SELL" else -1.0
    elif oi_change < 0 and price_change > 0:
        label = "SHORT_COVER"
        mod   = +0.5 if direction == "BUY"  else -0.3
    elif oi_change < 0 and price_change < 0:
        label = "LONG_UNWIND"
        mod   = +0.5 if direction == "SELL" else -0.3
    else:
        label = "NEUTRAL"
        mod   = 0.0
    return label, round(mod, 2)

# Backward-compat alias
fetch_bhav_copy = get_bhav_data
