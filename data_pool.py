"""
data_pool.py — Multi-Source Data Pool

Instead of relying on a single data source, tries multiple in priority order.
Angel One is primary, then NSE direct, then yfinance, then Twelve Data (free tier).

Priority:
  1. Angel One SmartAPI  — real NSE data, free with account
  2. NSE Direct API      — nseindia.com, free, indices only
  3. yfinance            — 15-min delayed, free, all symbols
  4. Twelve Data         — free tier (800 calls/day), good quality
  5. Alpha Vantage       — free tier (25 calls/day), backup only

Add API keys to .env:
  TWELVE_DATA_KEY=your_key   (free at twelvedata.com)
  ALPHA_VANTAGE_KEY=your_key (free at alphavantage.co)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_TWELVE_KEY = os.getenv("TWELVE_DATA_KEY", "")
_AV_KEY     = os.getenv("ALPHA_VANTAGE_KEY", "")

# Cache to avoid hitting APIs too often
_cache: Dict[str, dict] = {}
_CACHE_TTL = 60  # 1 minute


def _cached(key: str) -> Optional[pd.DataFrame]:
    if key in _cache:
        if time.time() - _cache[key]["ts"] < _CACHE_TTL:
            return _cache[key]["df"]
    return None


def _store(key: str, df: pd.DataFrame) -> None:
    if df is not None and not df.empty:
        _cache[key] = {"df": df, "ts": time.time()}


def _safe_close_col(df: pd.DataFrame) -> pd.DataFrame:
    """Fix yfinance MultiIndex columns."""
    if df is None or df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df.columns = [str(c).lower() for c in df.columns]
    return df


# ── Source 1: Angel One ───────────────────────────────────────────────────────
def _from_angel(symbol: str, interval: str = "5m", days: int = 5,
                angel=None) -> Optional[pd.DataFrame]:
    if not angel:
        return None
    try:
        from data_fetcher import DataFetcher
        df = DataFetcher(angel=angel).get_market_data(symbol, interval, days)
        return df
    except Exception as e:
        logger.debug("Angel One data for %s: %s", symbol, e)
        return None


# ── Source 2: NSE Direct (indices only, free) ─────────────────────────────────
def _from_nse_direct(symbol: str) -> Optional[pd.DataFrame]:
    _NSE_SYMBOLS = {
        "NIFTY":      "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
        "BANKNIFTY":  "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20BANK",
        "FINNIFTY":   "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20FIN%20SERVICE",
        "MIDCPNIFTY": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20MID%20SELECT",
    }
    url = _NSE_SYMBOLS.get(symbol.upper())
    if not url:
        return None
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get(url, timeout=8)
        if r.status_code != 200:
            return None
        data  = r.json()
        price = float(data.get("data", [{}])[0].get("lastPrice", 0) or 0)
        if price <= 0:
            return None
        # Return a minimal 1-bar DataFrame
        now = datetime.now()
        return pd.DataFrame([{
            "open": price, "high": price, "low": price,
            "close": price, "volume": 0,
        }], index=[now])
    except Exception as e:
        logger.debug("NSE direct %s: %s", symbol, e)
        return None


# ── Source 3: yfinance (free, 15-min delayed) ─────────────────────────────────
def _from_yfinance(symbol: str, interval: str = "5m", days: int = 7) -> Optional[pd.DataFrame]:
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        _MAP = {
            "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK",
            "FINNIFTY": "NIFTY_FIN_SERVICE.NS", "MIDCPNIFTY": "MIDCPNIFTY.NS",
            "SENSEX": "^BSESN",
        }
        ticker  = _MAP.get(symbol.upper(), f"{symbol}.NS")
        period  = f"{max(days, 7)}d"
        df      = yf.download(ticker, period=period, interval=interval,
                               progress=False, auto_adjust=True)
        df = _safe_close_col(df)
        return df if df is not None and not df.empty else None
    except Exception as e:
        logger.debug("yfinance %s: %s", symbol, e)
        return None


# ── Source 4: Twelve Data (free tier: 800 req/day) ────────────────────────────
def _from_twelve_data(symbol: str, interval: str = "5min", days: int = 5) -> Optional[pd.DataFrame]:
    if not _TWELVE_KEY:
        return None
    try:
        import requests
        _MAP = {
            "NIFTY": "NIFTY50", "BANKNIFTY": "BANKNIFTY",
            "FINNIFTY": "FINNIFTY", "SENSEX": "SENSEX",
        }
        sym_td = _MAP.get(symbol.upper(), symbol.upper())
        # Twelve Data uses :NSE suffix for Indian stocks
        if sym_td not in _MAP.values():
            sym_td = f"{sym_td}:NSE"
        exchange = "NSE" if symbol.upper() not in {"SENSEX"} else "BSE"
        url = (f"https://api.twelvedata.com/time_series"
               f"?symbol={sym_td}&exchange={exchange}"
               f"&interval={interval}&outputsize={days*78}"
               f"&apikey={_TWELVE_KEY}&format=JSON")
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data   = r.json()
        values = data.get("values", [])
        if not values:
            return None
        df = pd.DataFrame(values)
        df.index = pd.to_datetime(df["datetime"])
        df = df.rename(columns={
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume"
        })[["open", "high", "low", "close", "volume"]]
        df = df.apply(pd.to_numeric, errors="coerce").sort_index()
        return df
    except Exception as e:
        logger.debug("Twelve Data %s: %s", symbol, e)
        return None


# ── Source 5: Alpha Vantage (free tier: 25 req/day) ──────────────────────────
def _from_alpha_vantage(symbol: str) -> Optional[pd.DataFrame]:
    if not _AV_KEY:
        return None
    try:
        import requests
        # AV supports NSE stocks via BSE extension
        sym = f"{symbol}.BSE"
        url = (f"https://www.alphavantage.co/query"
               f"?function=TIME_SERIES_INTRADAY&symbol={sym}"
               f"&interval=5min&outputsize=compact&apikey={_AV_KEY}")
        r   = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data  = r.json()
        ts    = data.get("Time Series (5min)", {})
        if not ts:
            return None
        rows = []
        for dt_str, vals in sorted(ts.items()):
            rows.append({
                "datetime": pd.to_datetime(dt_str),
                "open":     float(vals["1. open"]),
                "high":     float(vals["2. high"]),
                "low":      float(vals["3. low"]),
                "close":    float(vals["4. close"]),
                "volume":   float(vals["5. volume"]),
            })
        df = pd.DataFrame(rows).set_index("datetime").sort_index()
        return df
    except Exception as e:
        logger.debug("Alpha Vantage %s: %s", symbol, e)
        return None


# ── MAIN: Pool fetcher ────────────────────────────────────────────────────────
def get_data(
    symbol:   str,
    interval: str = "5m",
    days:     int = 7,
    angel     = None,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV data from multiple sources in priority order.
    Returns first successful result.

    Priority:
      1. Angel One (real NSE data)
      2. NSE Direct (indices only, instant)
      3. yfinance (15-min delayed, reliable)
      4. Twelve Data (free API key needed)
      5. Alpha Vantage (free API key needed, 25/day limit)
    """
    key = f"{symbol}_{interval}_{days}"
    cached = _cached(key)
    if cached is not None:
        return cached

    sources = [
        ("Angel One",    lambda: _from_angel(symbol, interval, days, angel)),
        ("NSE Direct",   lambda: _from_nse_direct(symbol)),
        ("yfinance",     lambda: _from_yfinance(symbol, interval, days)),
        ("Twelve Data",  lambda: _from_twelve_data(symbol, interval.replace("m","min"), days)),
        ("Alpha Vantage",lambda: _from_alpha_vantage(symbol)),
    ]

    for source_name, fetch_fn in sources:
        try:
            df = fetch_fn()
            if df is not None and not df.empty and len(df) >= 2:
                logger.debug("Data: %s → %s (%d bars)", symbol, source_name, len(df))
                _store(key, df)
                return df
        except Exception:
            continue

    logger.warning("All data sources failed for %s", symbol)
    return None


def get_source_status() -> Dict[str, str]:
    """Check which sources are available."""
    status = {}
    # Angel One
    try:
        import importlib
        importlib.import_module("smartapi")
        status["Angel One"] = "✅ Library installed"
    except Exception:
        status["Angel One"] = "❌ smartapi not installed"
    # yfinance
    try:
        status["yfinance"] = "✅ Available (15-min delay)"
    except Exception:
        status["yfinance"] = "❌ Not installed"
    # Twelve Data
    status["Twelve Data"] = "✅ Key set" if _TWELVE_KEY else "⚠️ No key (free at twelvedata.com)"
    # Alpha Vantage
    status["Alpha Vantage"] = "✅ Key set" if _AV_KEY else "⚠️ No key (25/day limit)"
    return status
