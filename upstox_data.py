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

# 2026-08-18: _get_instrument_key()'s ISIN-lookup path was silently broken
# for EVERY stock, not just the ones a data-quality audit happened to catch.
# It searched scrip_master.json / angel_scrip_master.json (neither exists on
# this machine) then OpenAPIScripMaster.json, which DOES exist but has no
# "isin" field at all (confirmed: it's actually an NSE bond/debenture master,
# not an equity one -- its symbols look like "722HPCL29-N0", a debenture, not
# the HPCL stock). So `isin` was always "", the `if ... and isin ...` guard
# was always false, and _get_instrument_key() returned None for every single
# stock -- meaning this whole Upstox fallback path has been a silent no-op
# this entire time, invisible whenever the primary source (Angel) covers a
# symbol, but a real gap whenever it doesn't. Root-caused via 5 real symbols
# (HPCL, HEROMOTOCO, ICICIPRULI, OIL, SHREECEM) stuck at their last-Angel-
# covered date for 10-37+ sessions with zero working fallback.
#
# Fix: use Upstox's own complete, public, no-auth instrument master (this
# endpoint, no API key needed) instead of trying to derive an ISIN from
# Angel's files. Maps trading_symbol -> instrument_key directly -- no ISIN
# cross-referencing needed at all.
_UPSTOX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
_UPSTOX_MASTER_CACHE_FILE = "upstox_instrument_master_cache.json"
_UPSTOX_MASTER_CACHE_TTL_SEC = 24 * 3600   # refresh once/day -- this list changes rarely
_UPSTOX_MASTER_LOADED = False

# Known NSE ticker renames where our symbol lists still use the old name but
# Upstox's master (and the exchange) use the new one. Same pattern already
# used for this exact symbol in angel.py's _load_nse_eq_tokens().
_UPSTOX_SYMBOL_ALIASES = {"HPCL": "HINDPETRO"}


def _load_upstox_instrument_master() -> dict:
    """trading_symbol -> instrument_key, for NSE_EQ and BSE_EQ. Cached to
    disk with a 1-day TTL so this isn't a ~3.4MB download on every call."""
    global _UPSTOX_MASTER_LOADED
    if _UPSTOX_MASTER_LOADED and _ISIN_CACHE:
        return _ISIN_CACHE

    import json
    from pathlib import Path

    cache_path = Path(_UPSTOX_MASTER_CACHE_FILE)
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < _UPSTOX_MASTER_CACHE_TTL_SEC:
            try:
                _ISIN_CACHE.update(json.loads(cache_path.read_text()))
                _UPSTOX_MASTER_LOADED = True
                return _ISIN_CACHE
            except Exception as e:
                logger.debug("Upstox master cache read failed: %s", e)

    try:
        import gzip
        r = requests.get(_UPSTOX_MASTER_URL, timeout=30)
        r.raise_for_status()
        data = json.loads(gzip.decompress(r.content))
        # Two passes so NSE_EQ always wins over BSE_EQ for the same symbol,
        # matching this system's documented NSE-primary architecture --
        # relying on the raw file's row order to do that (as a single-pass
        # "first one wins" loop would) is not something the source format
        # guarantees, and verified it does NOT hold for several real symbols
        # (RELIANCE, OIL, SHREECEM all resolved to BSE_EQ under a single
        # first-wins pass).
        mapping = {}
        for preferred_segment in ("NSE_EQ", "BSE_EQ"):
            for item in data:
                if not isinstance(item, dict) or item.get("segment") != preferred_segment:
                    continue
                ts = str(item.get("trading_symbol", "")).upper()
                key = item.get("instrument_key", "")
                if ts and key and ts not in mapping:
                    mapping[ts] = key
        _ISIN_CACHE.update(mapping)
        try:
            cache_path.write_text(json.dumps(mapping))
        except Exception as e:
            logger.debug("Upstox master cache write failed: %s", e)
        logger.info("Upstox instrument master loaded: %d NSE/BSE equity symbols", len(mapping))
        _UPSTOX_MASTER_LOADED = True
    except Exception as e:
        logger.warning("Upstox instrument master download failed: %s", e)
        # Leave _UPSTOX_MASTER_LOADED False so a later call can retry rather
        # than permanently giving up for the rest of this process's life.
    return _ISIN_CACHE

# ── Interval mapping ─────────────────────────────────────────────────────
_INTERVAL_MAP_V2 = {
    "1m":  "1minute",
    "5m":  "1minute",    # derive exact bars from a supported base interval
    "15m": "1minute",
    "30m": "30minute",
    "1h":  "30minute",
    "1d":  "day",
    "day": "day",
}


def _resample_v2(df: pd.DataFrame, requested: str, source_interval: str) -> Optional[pd.DataFrame]:
    """Convert a V2 base interval to the exact requested OHLCV interval."""
    rules = {"5m": "5min", "15m": "15min", "1h": "60min"}
    rule = rules.get(requested)
    if not rule or requested in {"1m", "30m", "1d", "day"}:
        return df
    try:
        out = df.resample(rule, origin="start_day", offset="15min").agg({
            "open": "first", "high": "max", "low": "min", "close": "last",
            "volume": "sum",
        }).dropna(subset=["open", "high", "low", "close"])
        return out if len(out) >= 2 else None
    except Exception:
        logger.debug("Upstox V2 resample failed %s from %s", requested, source_interval,
                     exc_info=True)
        return None

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

    lookup_sym = _UPSTOX_SYMBOL_ALIASES.get(sym, sym)
    master = _load_upstox_instrument_master()
    if lookup_sym in master:
        return master[lookup_sym]

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
    hist_df = None
    try:
        v2_interval = _INTERVAL_MAP_V2.get(interval, "day")
        url = f"{_BASE_V2}/{encoded_key}/{v2_interval}/{end_date}/{start_date}"
        r = requests.get(url, headers=_HEADERS, timeout=10)
        if r.status_code == 200:
            df = _parse_candles(r.json())
            df = _resample_v2(df, interval, v2_interval) if df is not None else None
            if df is not None and len(df) >= 2:
                hist_df = df
            else:
                logger.debug("Upstox V2 %s: empty response", symbol)
        else:
            logger.debug("Upstox V2 %s: HTTP %d", symbol, r.status_code)
    except Exception as e:
        logger.debug("Upstox V2 %s: %s", symbol, e)

    # 2026-07-21: Upstox's historical-candle endpoint NEVER includes today's
    # in-progress session by design (that is what the dedicated /intraday/
    # endpoint below is for) -- but a multi-day `days=` request against a
    # reliable instrument (every NSE/BSE index) always has plenty of PAST
    # days to satisfy `len(df) >= 2`, so the code below was unreachable in
    # practice: the historical call "succeeded" every single day and the
    # function returned before ever trying /intraday/. Confirmed live: 6
    # major indices were frozen at the prior day's close across every
    # intraday interval for 2+ consecutive trading days, while individual
    # equities (whose historical call more often genuinely fails/empties,
    # e.g. an ISIN resolution miss) fell through to Angel and stayed fresh.
    # Fix: if the historical response's last bar isn't from today, ALSO
    # fetch /intraday/ and merge today's forming bars in -- rather than
    # trusting "historical succeeded" as proof today's data was included.
    # NOTE: "1h" belongs in this set too (it resamples from a 30minute base
    # fetch via _INTERVAL_MAP_V2, same granularity the intraday endpoint
    # already accepts for "30m") -- the original code's pre-fix intraday
    # check excluded it as well, carried the same gap forward the first
    # time round and confirmed live: 1h stayed stuck a day behind even
    # after 1m/5m/15m were fixed, for exactly this reason.
    need_intraday = interval in ("1m", "5m", "15m", "30m", "1h") and (
        hist_df is None or hist_df.index[-1].date() < datetime.now().date()
    )
    intra_df = None
    if need_intraday:
        try:
            v2_intra = _INTERVAL_MAP_V2.get(interval, "1minute")
            url = f"{_BASE_V2}/intraday/{encoded_key}/{v2_intra}"
            r = requests.get(url, headers=_HEADERS, timeout=10)
            if r.status_code == 200:
                df = _parse_candles(r.json())
                intra_df = _resample_v2(df, interval, v2_intra) if df is not None else None
        except Exception as e:
            logger.debug("Upstox intraday %s: %s", symbol, e)

    if hist_df is not None and intra_df is not None and len(intra_df):
        merged = pd.concat([hist_df, intra_df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        logger.info("Upstox V2+intraday ✅ %s %s: %d bars (merged, last=%s)",
                    symbol, interval, len(merged), merged.index[-1])
        return merged
    if intra_df is not None and len(intra_df) >= 2:
        logger.info("Upstox intraday ✅ %s %s: %d bars", symbol, interval, len(intra_df))
        return intra_df
    if hist_df is not None:
        logger.info("Upstox V2 ✅ %s %s: %d bars (no fresher intraday available)",
                    symbol, interval, len(hist_df))
        return hist_df

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
