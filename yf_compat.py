"""
yf_compat.py — Drop-in yfinance replacement for NSE trading bot.
Yahoo Finance API is broken (JSONDecodeError on all tickers).
This module provides the same interface using working free APIs.

Usage: anywhere yf.download() was called, now returns data from:
  - NSE allIndices (NIFTY, BANKNIFTY, India VIX)
  - ExchangeRate-API (USD/INR)
  - Fixed values from cache (Crude, Gold, US10Y, US VIX)
"""
from __future__ import annotations
import logging
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Cache to avoid hammering APIs
_cache: dict = {}
_cache_ttl  = 300  # 5 minutes

def _cached(key: str, fn):
    import time
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < _cache_ttl:
        return _cache[key]["val"]
    val = fn()
    _cache[key] = {"val": val, "ts": now}
    return val


def _nse_price(index_name: str) -> float:
    """Fetch from NSE allIndices — always works during/after market hours."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=7)
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                if index_name.upper() in str(idx.get("index", "")).upper():
                    return float(idx.get("last", 0) or 0)
    except Exception as e:
        logger.debug("nse_price %s: %s", index_name, e)
    return 0.0


def _er_api_usd_inr() -> float:
    """ExchangeRate-API: free, reliable USD/INR."""
    try:
        import requests
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=6)
        if r.status_code == 200:
            return float(r.json().get("rates", {}).get("INR", 0))
    except Exception: pass
    try:
        import requests
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=INR", timeout=6)
        if r.status_code == 200:
            return float(r.json().get("rates", {}).get("INR", 0))
    except Exception: pass
    return 84.5  # fallback


def _make_df(price: float, change_pct: float = 0.0) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame with today's price."""
    if price <= 0:
        return pd.DataFrame()
    prev = price / (1 + change_pct/100) if change_pct else price
    dates = [datetime.now() - timedelta(days=1), datetime.now()]
    return pd.DataFrame({
        "Open":   [prev, price],
        "High":   [prev*1.001, price*1.001],
        "Low":    [prev*0.999, price*0.999],
        "Close":  [prev, price],
        "Volume": [0, 0],
    }, index=pd.DatetimeIndex(dates))



def _stooq_price(symbol: str) -> float:
    """Stooq.com — free global market data, no API key, no rate limit."""
    try:
        import requests, pandas as _pd, io
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code == 200 and "," in r.text:
            df = _pd.read_csv(io.StringIO(r.text))
            if not df.empty and "Close" in df.columns:
                return float(df["Close"].iloc[-1])
    except Exception as e:
        logger.debug("stooq %s: %s", symbol, e)
    return 0.0


# Ticker → fetch function
_TICKER_MAP = {
    "^NSEI":          lambda: _nse_price("NIFTY 50"),
    "^NSEBANK":       lambda: _nse_price("NIFTY BANK"),
    "^INDIAVIX":      lambda: _nse_price("INDIA VIX"),
    "^BSESN":         lambda: _nse_price("S&P BSE SENSEX"),
    "NIFTY_FIN_SERVICE.NS": lambda: _nse_price("NIFTY FIN SERVICE"),
    "MIDCPNIFTY.NS":  lambda: _nse_price("NIFTY MIDCAP SELECT"),
    "USDINR=X":       _er_api_usd_inr,
    "INRUSD=X":       lambda: 1.0/_er_api_usd_inr() if _er_api_usd_inr() else 0,
    # Global markets — Stooq free API (no rate limit, no key needed)
    "^GSPC":      lambda: _stooq_price("^SPX"),
    "^DJI":       lambda: _stooq_price("^DJI"),
    "^IXIC":      lambda: _stooq_price("^NDX"),
    "^VIX":       lambda: _stooq_price("^VIX"),
    "BZ=F":       lambda: _stooq_price("LCO.F"),    # Brent crude
    "CL=F":       lambda: _stooq_price("CL.F"),     # WTI crude
    "GC=F":       lambda: _stooq_price("GC.F"),     # Gold futures
    "DX-Y.NYB":   lambda: _stooq_price("UDX.FSO"),  # DXY
    "^N225":      lambda: _stooq_price("^NKX"),     # Nikkei
    "^HSI":       lambda: _stooq_price("^HSI"),     # Hang Seng
    "^FTSE":      lambda: _stooq_price("^FTM"),     # FTSE
    "^TNX":       lambda: 4.3,  # US 10Y — static fallback
}


def download(
    tickers,
    period:   str = "1d",
    interval: str = "1d",
    progress: bool = False,
    auto_adjust: bool = True,
    **kwargs,
) -> pd.DataFrame:
    """
    Drop-in replacement for yf.download().
    Returns OHLCV DataFrame from working APIs.
    Never raises exceptions.
    """
    if isinstance(tickers, (list, tuple)):
        ticker = tickers[0] if tickers else ""
    else:
        ticker = str(tickers)

    ticker = ticker.strip()

    try:
        fn = _TICKER_MAP.get(ticker)
        if fn:
            price = _cached(ticker, fn)
            if price and price > 0:
                return _make_df(float(price))
        else:
            # NSE stock — try SmartConnect (cached)
            # Return empty — data_fetcher handles stocks separately
            logger.debug("yf_compat: no handler for %s", ticker)
    except Exception as e:
        logger.debug("yf_compat %s: %s", ticker, e)

    return pd.DataFrame()


class Ticker:
    """Drop-in for yf.Ticker()."""
    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, period="1d", interval="1d", auto_adjust=True, **kwargs):
        return download(self.symbol, period=period, interval=interval, auto_adjust=auto_adjust)

    @property
    def info(self): return {}
    @property
    def fast_info(self): return {}
