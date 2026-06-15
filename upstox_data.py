"""
upstox_data.py — Upstox Historical Candle API (NO auth for historical data)

FREE, no daily login, no token needed for historical endpoints.
Provides 1m/5m/30m/daily candles for ALL NSE+BSE stocks and indices.

Usage:
    from upstox_data import get_candles, get_intraday_candles
    
    # Historical (no auth)
    df = get_candles("NSE_EQ|INE002A01018", interval="5m", days=5)
    
    # Index
    df = get_candles("NSE_INDEX|Nifty 50", interval="5m", days=5)
"""
from __future__ import annotations
import logging
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Symbol mapping: our symbols → Upstox instrument keys ─────────────────
_INDEX_MAP = {
    "NIFTY":        "NSE_INDEX|Nifty 50",
    "NIFTY 50":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY":    "NSE_INDEX|Nifty Bank",
    "NIFTY BANK":   "NSE_INDEX|Nifty Bank",
    "FINNIFTY":     "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY":   "NSE_INDEX|NIFTY MID SELECT",
    "SENSEX":       "BSE_INDEX|SENSEX",
    "NIFTYNEXT50":  "NSE_INDEX|Nifty Next 50",
    "NIFTYIT":      "NSE_INDEX|Nifty IT",
}

# NSE ISIN cache for stocks (loaded from scrip master or hardcoded top 50)
_ISIN_CACHE: dict = {}

# ── Interval mapping ─────────────────────────────────────────────────────
_INTERVAL_MAP_V2 = {
    "1m":  "1minute",
    "5m":  "30minute",   # V2 only has 1minute and 30minute
    "15m": "30minute",
    "30m": "30minute",
    "1d":  "day",
    "day": "day",
}

_INTERVAL_MAP_V3 = {
    "1m":  ("minutes", "1"),
    "5m":  ("minutes", "5"),
    "15m": ("minutes", "15"),
    "30m": ("minutes", "30"),
    "1h":  ("hours", "1"),
    "1d":  ("days", "1"),
    "day": ("days", "1"),
}

_BASE_V2 = "https://api.upstox.com/v2/historical-candle"
_BASE_V3 = "https://api.upstox.com/v3/historical-candle"
_HEADERS = {"Accept": "application/json"}

# Rate limiting
_last_call_time = 0.0
_MIN_INTERVAL = 0.35  # 350ms between calls


def _rate_limit():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_call_time = time.time()


def _get_instrument_key(symbol: str) -> Optional[str]:
    """Convert our symbol name to Upstox instrument key."""
    sym = symbol.strip().upper()
    
    # Check index map
    if sym in _INDEX_MAP:
        return _INDEX_MAP[sym]
    
    # Check ISIN cache
    if sym in _ISIN_CACHE:
        return _ISIN_CACHE[sym]
    
    # Try loading from Angel scrip master
    try:
        import json
        from pathlib import Path
        master_files = [
            Path("scrip_master.json"),
            Path("angel_scrip_master.json"),
            Path("OpenAPIScripMaster.json"),
        ]
        for mf in master_files:
            if mf.exists():
                data = json.loads(mf.read_text())
                for item in data:
                    if isinstance(item, dict):
                        s = item.get("symbol", "").upper()
                        isin = item.get("isin", "")
                        exch = item.get("exch_seg", "NSE")
                        if s == sym and isin and exch in ("NSE", "BSE"):
                            key = f"{exch}_EQ|{isin}"
                            _ISIN_CACHE[sym] = key
                            return key
                break
    except Exception:
        pass
    
    # Fallback: try common format NSE_EQ|{symbol}
    # This won't work directly but we try the v2 endpoint anyway
    return None


def get_candles(
    symbol: str,
    interval: str = "5m",
    days: int = 5,
    analytics_token: str = "",
) -> Optional[pd.DataFrame]:
    """
    Fetch historical candles from Upstox.
    
    V2 historical endpoint: NO auth needed (free for everyone)
    V3 with Analytics Token: more intervals, longer history
    
    Returns DataFrame with columns: open, high, low, close, volume
    """
    instrument_key = _get_instrument_key(symbol)
    if not instrument_key:
        logger.debug("Upstox: no instrument key for %s", symbol)
        return None
    
    _rate_limit()
    
    # Encode instrument key for URL (pipe needs encoding)
    import urllib.parse
    encoded_key = urllib.parse.quote(instrument_key, safe='')
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    # Try V3 first (more intervals) if analytics token available
    if analytics_token and interval in _INTERVAL_MAP_V3:
        try:
            unit, value = _INTERVAL_MAP_V3[interval]
            url = f"{_BASE_V3}/{encoded_key}/{unit}/{value}/{end_date}/{start_date}"
            headers = {**_HEADERS, "Authorization": f"Bearer {analytics_token}"}
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                df = _parse_candles(r.json())
                if df is not None and len(df) >= 2:
                    logger.info("Upstox V3 ✅ %s %s: %d bars", symbol, interval, len(df))
                    return df
        except Exception as e:
            logger.debug("Upstox V3 %s: %s", symbol, e)
    
    # V2 historical (NO auth needed)
    try:
        v2_interval = _INTERVAL_MAP_V2.get(interval, "day")
        url = f"{_BASE_V2}/{encoded_key}/{v2_interval}/{end_date}/{start_date}"
        r = requests.get(url, headers=_HEADERS, timeout=10)
        if r.status_code == 200:
            df = _parse_candles(r.json())
            if df is not None and len(df) >= 2:
                logger.info("Upstox V2 ✅ %s %s: %d bars", symbol, interval, len(df))
                return df
            else:
                logger.debug("Upstox V2 %s: empty response", symbol)
        else:
            logger.debug("Upstox V2 %s: HTTP %d", symbol, r.status_code)
    except Exception as e:
        logger.debug("Upstox V2 %s: %s", symbol, e)
    
    # V2 intraday (today only, NO auth)
    if interval in ("1m", "5m", "15m", "30m"):
        try:
            v2_intra = "1minute" if interval == "1m" else "30minute"
            url = f"{_BASE_V2}/intraday/{encoded_key}/{v2_intra}"
            r = requests.get(url, headers=_HEADERS, timeout=10)
            if r.status_code == 200:
                df = _parse_candles(r.json())
                if df is not None and len(df) >= 2:
                    logger.info("Upstox intraday ✅ %s: %d bars", symbol, len(df))
                    return df
        except Exception as e:
            logger.debug("Upstox intraday %s: %s", symbol, e)
    
    return None


def get_intraday_candles(
    symbol: str,
    interval: str = "5m",
) -> Optional[pd.DataFrame]:
    """Get today's intraday candles (no auth needed)."""
    return get_candles(symbol, interval=interval, days=1)


def _parse_candles(data: dict) -> Optional[pd.DataFrame]:
    """Parse Upstox candle response into DataFrame."""
    try:
        candles = data.get("data", {}).get("candles", [])
        if not candles:
            return None
        
        rows = []
        for c in candles:
            # Format: [timestamp, open, high, low, close, volume, oi]
            if len(c) >= 6:
                rows.append({
                    "date": pd.Timestamp(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": int(c[5]),
                })
        
        if not rows:
            return None
        
        df = pd.DataFrame(rows).set_index("date").sort_index()
        # Remove rows with zero close
        df = df[df["close"] > 0]
        return df if len(df) >= 2 else None
    except Exception:
        return None
