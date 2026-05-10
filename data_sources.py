"""
data_sources.py — Multi-Source Free Data Pool for NSE/Indian Markets

ALL SOURCES ARE FREE (no paid API key needed):

  1. Angel One SmartAPI    — Real-time NSE data (requires account, free)
  2. NSE Direct API        — nseindia.com, indices + stocks EOD
  3. NSE Equity API        — Per-stock OHLCV from NSE
  4. yfinance              — Yahoo Finance (15-min delayed, global)
  5. Stooq                 — Free historical EOD, very reliable
  6. Fyers API             — Free with Fyers account (alternative broker)
  7. Twelve Data           — 800 free calls/day (register at twelvedata.com)
  8. Alpha Vantage         — 25 free calls/day (global equities)
  9. Tiingo                — Free EOD + fundamentals (register at tiingo.com)
 10. FRED                  — Federal Reserve macro data (US rates, dollar index)

SETUP for API keys:
  Twelve Data:   https://twelvedata.com (free, 800/day) → TWELVE_DATA_KEY
  Alpha Vantage: https://alphavantage.co (free, 25/day)  → ALPHA_VANTAGE_KEY
  Tiingo:        ✅ ALREADY CONFIGURED (1000 calls/hour)
  Fyers:         Open account at fyers.in (free)         → FYERS_TOKEN

OFFLINE CACHING:
  All successful fetches are cached in data_cache/ folder.
  At 4 AM when internet is down, system uses yesterday's cached data.
  Cache is valid 24 hours for EOD, 5 min for intraday.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("data_cache")
_CACHE_DIR.mkdir(exist_ok=True)

# ── API Keys from .env ────────────────────────────────────────────────────────
_TWELVE_KEY   = os.getenv("TWELVE_DATA_KEY",  "")
_AV_KEY       = os.getenv("ALPHA_VANTAGE_KEY", "")
_TIINGO_KEY   = os.getenv("TIINGO_KEY", "")

# ── Symbol maps ───────────────────────────────────────────────────────────────
_YF_MAP = {
    "NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK","FINNIFTY":"NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY":"MIDCPNIFTY.NS","SENSEX":"^BSESN","NIFTYNEXT50":"^NSMIDCP",
}
_STOOQ_MAP = {"NIFTY":"^nsei","BANKNIFTY":"^nsebank","SENSEX":"^bsesn"}
_TWELVE_NSE_MAP = {
    "NIFTY":"NIFTY50","BANKNIFTY":"BANKNIFTY","SENSEX":"SENSEX",
}


def _safe_df(df) -> Optional[pd.DataFrame]:
    """Standardise OHLCV df."""
    if df is None or (hasattr(df,'empty') and df.empty): return None
    try:
        df = df.copy()
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
        df.columns = [c.lower() for c in df.columns]
        for col in ['open','high','low','close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        if 'volume' not in df.columns: df['volume'] = 0
        df = df.dropna(subset=['close'])
        return df if len(df)>=2 else None
    except Exception: return None


def _is_market_hours() -> bool:
    from datetime import time as _t
    n = datetime.now()
    if n.weekday()>=5: return False
    return _t(8,30) <= n.time() <= _t(20,0)


# ── CACHE: read/write ──────────────────────────────────────────────────────────
def _cache_key(symbol:str, interval:str) -> Path:
    return _CACHE_DIR / f"{symbol}_{interval}.pkl"

def _cache_write(symbol:str, interval:str, df:pd.DataFrame) -> None:
    try: df.to_pickle(str(_cache_key(symbol,interval)))
    except Exception: pass

def _cache_read(symbol:str, interval:str, max_age_hours:int=24) -> Optional[pd.DataFrame]:
    p = _cache_key(symbol, interval)
    if not p.exists(): return None
    age = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).total_seconds()/3600
    if age > max_age_hours: return None
    try: return pd.read_pickle(str(p))
    except Exception: return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 1: Angel One SmartAPI
# ─────────────────────────────────────────────────────────────────────────────
def _from_angel(symbol:str, interval:str, days:int, angel=None) -> Optional[pd.DataFrame]:
    if not angel: return None
    try:
        from data_fetcher import DataFetcher
        df = DataFetcher(angel=angel).get_market_data(symbol,interval,days)
        return _safe_df(df)
    except Exception as e:
        logger.debug("Angel: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 2: NSE Direct API (indices, free)
# ─────────────────────────────────────────────────────────────────────────────
def _from_nse_allindices(symbol:str) -> Optional[pd.DataFrame]:
    """Get current NIFTY price from NSE allIndices — works during market hours."""
    _IDX_MAP = {
        "NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK",
        "FINNIFTY":"NIFTY FIN SERVICE","MIDCPNIFTY":"NIFTY MIDCAP SELECT",
        "SENSEX":"S&P BSE SENSEX",
    }
    name = _IDX_MAP.get(symbol.upper())
    if not name: return None
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                iname = str(idx.get("index","")).upper()
                if name.upper() in iname or iname in name.upper():
                    price = float(idx.get("last", 0) or 0)
                    chg   = float(idx.get("percentChange", 0) or 0)
                    if price > 0:
                        # Return single-row df with today's price
                        import pandas as _pd
                        from datetime import datetime as _dt
                        df = _pd.DataFrame([{
                            "open":  float(idx.get("open", price)),
                            "high":  float(idx.get("high", price)),
                            "low":   float(idx.get("low",  price)),
                            "close": price,
                            "volume": 0,
                        }], index=[_dt.now()])
                        return _safe_df(df)
    except Exception as e: logger.debug("nse_allindices: %s", e)
    return None


def _from_nse_direct(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    # During market hours - use allIndices for live price
    from datetime import datetime as _dt, time as _dtime
    if _dtime(9,15) <= _dt.now().time() <= _dtime(15,35):
        df = _from_nse_allindices(symbol)
        if df is not None: return df

    if interval not in ('1d','daily'): return None
    _IDX = {"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK",
            "FINNIFTY":"NIFTY FIN SERVICE","MIDCPNIFTY":"NIFTY MIDCAP SELECT"}
    nse_sym = _IDX.get(symbol.upper())
    if not nse_sym: return None
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/",timeout=5)
        end   = datetime.now().strftime("%d-%m-%Y")
        start = (datetime.now()-timedelta(days=days+5)).strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/historical/indicesHistory?indexType="
            f"{nse_sym.replace(' ','%20')}&from={start}&to={end}", timeout=12)
        if r.status_code!=200: return None
        data = r.json().get("data",{}).get("indexCloseOnlineRecords",[])
        if not data: return None
        rows = [{"date":d["EOD_TIMESTAMP"],"open":d["EOD_OPEN_INDEX_VAL"],
                 "high":d["EOD_HIGH_INDEX_VAL"],"low":d["EOD_LOW_INDEX_VAL"],
                 "close":d["EOD_CLOSE_INDEX_VAL"],"volume":0} for d in data]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"],format="%d-%b-%Y")
        return _safe_df(df.set_index("date").sort_index())
    except Exception as e: logger.debug("NSE direct: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 3: NSE Equity API (per-stock OHLCV)
# ─────────────────────────────────────────────────────────────────────────────
def _from_nse_equity(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    if interval not in ('1d','daily'): return None
    if symbol.upper() in ('NIFTY','BANKNIFTY','SENSEX','FINNIFTY','MIDCPNIFTY'): return None
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/",timeout=5)
        end   = datetime.now().strftime("%d-%m-%Y")
        start = (datetime.now()-timedelta(days=days+5)).strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/historical/cm/equity?"
            f"symbol={symbol}&series=[%22EQ%22]&from={start}&to={end}", timeout=12)
        if r.status_code!=200: return None
        data = r.json().get("data",[])
        if not data: return None
        rows = [{"date":d["CH_TIMESTAMP"],"open":d["CH_OPENING_PRICE"],
                 "high":d["CH_TRADE_HIGH_PRICE"],"low":d["CH_TRADE_LOW_PRICE"],
                 "close":d["CH_CLOSING_PRICE"],"volume":d.get("CH_TOT_TRADED_QTY",0)}
                for d in data]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return _safe_df(df.set_index("date").sort_index())
    except Exception as e: logger.debug("NSE equity: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 4: yfinance (15-min delayed)
# ─────────────────────────────────────────────────────────────────────────────
def _from_yfinance(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    try:
        import yf_compat as yf, json as _json
        yf_sym = _YF_MAP.get(symbol.upper(), f"{symbol}.NS")
        period = f"{max(days,7)}d" if interval in ("1m","5m","15m","30m","1h") else f"{days}d"
        try:
            df = yf.download(yf_sym, period=period, interval=interval,
                             progress=False, auto_adjust=True)
        except (_json.JSONDecodeError, Exception): return None
        return _safe_df(df)
    except Exception as e: logger.debug("yfinance: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 5: Stooq (very reliable free EOD)
# ─────────────────────────────────────────────────────────────────────────────
def _from_stooq(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    if interval not in ('1d','daily'): return None
    try:
        import requests
        stooq_sym = _STOOQ_MAP.get(symbol.upper(), f"{symbol.lower()}.in")
        end   = datetime.now().strftime("%Y%m%d")
        start = (datetime.now()-timedelta(days=days+10)).strftime("%Y%m%d")
        r = requests.get(
            f"https://stooq.com/q/d/l/?s={stooq_sym}&d1={start}&d2={end}&i=d",
            timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code!=200 or "No data" in r.text: return None
        from io import StringIO
        df = pd.read_csv(StringIO(r.text),parse_dates=["Date"])
        df = df.rename(columns={"Date":"date","Open":"open","High":"high",
                                 "Low":"low","Close":"close","Volume":"volume"})
        return _safe_df(df.set_index("date").sort_index())
    except Exception as e: logger.debug("stooq: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 6: Twelve Data (FREE 800 calls/day — best intraday alternative)
# Register at twelvedata.com, add TWELVE_DATA_KEY to .env
# ─────────────────────────────────────────────────────────────────────────────
def _from_twelve_data(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    if not _TWELVE_KEY: return None
    try:
        import requests
        # Map intervals to Twelve Data format
        iv_map = {"1m":"1min","5m":"5min","15m":"15min","30m":"30min",
                  "1h":"1h","1d":"1day"}
        td_interval = iv_map.get(interval)
        if not td_interval: return None
        # Map NSE symbols to Twelve Data format
        td_sym = _TWELVE_NSE_MAP.get(symbol.upper(), f"{symbol}:NSE")
        output_size = min(days * 78, 5000)  # 78 bars per day for 5m
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol":td_sym,"interval":td_interval,"outputsize":output_size,
                    "apikey":_TWELVE_KEY,"exchange":"NSE"},
            timeout=12)
        if r.status_code!=200: return None
        data = r.json()
        if "values" not in data: return None
        rows = [{"date":d["datetime"],"open":float(d["open"]),"high":float(d["high"]),
                 "low":float(d["low"]),"close":float(d["close"]),
                 "volume":float(d.get("volume",0))} for d in data["values"]]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return _safe_df(df.set_index("date").sort_index())
    except Exception as e: logger.debug("twelve_data: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 7: Alpha Vantage (FREE 25 calls/day — good for EOD)
# Register at alphavantage.co, add ALPHA_VANTAGE_KEY to .env
# ─────────────────────────────────────────────────────────────────────────────
def _from_alpha_vantage(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    if not _AV_KEY: return None
    if interval not in ('1d','daily'): return None  # save calls for EOD only
    try:
        import requests
        # Alpha Vantage uses BSE:SYMBOL or NSE:SYMBOL format
        av_sym = f"NSE:{symbol}" if symbol.upper() not in ('NIFTY','SENSEX') else symbol
        r = requests.get("https://www.alphavantage.co/query", params={
            "function":"TIME_SERIES_DAILY","symbol":av_sym,
            "outputsize":"compact","apikey":_AV_KEY}, timeout=12)
        if r.status_code!=200: return None
        data = r.json().get("Time Series (Daily)",{})
        if not data: return None
        rows = [{"date":d,"open":float(v["1. open"]),"high":float(v["2. high"]),
                 "low":float(v["3. low"]),"close":float(v["4. close"]),
                 "volume":float(v["5. volume"])} for d,v in data.items()]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return _safe_df(df.set_index("date").sort_index().tail(days))
    except Exception as e: logger.debug("alpha_vantage: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 8: Tiingo (FREE 1000 calls/hour — EOD + intraday IEX feed)
# Key configured: 43f3cb0bc2a1ea5afd7d8b33c084d584e44ba65b
# ─────────────────────────────────────────────────────────────────────────────
_TIINGO_KEY   = os.getenv("TIINGO_KEY", "43f3cb0bc2a1ea5afd7d8b33c084d584e44ba65b")  # ✅ configured
_TWELVE_KEY   = os.getenv("TWELVE_DATA_KEY", "")  # 13757a73... already in .env
_AV_KEY       = os.getenv("ALPHA_VANTAGE_KEY", "")  # 6H691G... already in .env

# NSE symbol map for Tiingo (uses Yahoo-style tickers)
_TIINGO_SYM_MAP = {
    "NIFTY":      "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "SENSEX":     "^BSESN",
    "FINNIFTY":   "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "MIDCPNIFTY.NS",
}

def _from_tiingo(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    """
    Tiingo API — free tier: 1000 calls/hour, excellent reliability.
    EOD data for all global equities. Intraday via IEX (US stocks only).
    For NSE: use for EOD data — more reliable than yfinance at off-hours.
    Key: configured via TIINGO_KEY env var.
    """
    if not _TIINGO_KEY: return None
    if interval not in ('1d','daily'):
        return None  # Tiingo IEX = US stocks only; NSE intraday not available
    try:
        import requests
        # Map NSE symbols to Tiingo format
        tiingo_sym = _TIINGO_SYM_MAP.get(symbol.upper(), f"{symbol.lower()}")
        end   = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now()-timedelta(days=days+10)).strftime("%Y-%m-%d")
        
        headers = {"Content-Type":"application/json",
                   "Authorization":f"Token {_TIINGO_KEY}"}
        
        # Try EOD prices endpoint
        r = requests.get(
            f"https://api.tiingo.com/tiingo/daily/{tiingo_sym}/prices",
            params={"startDate":start,"endDate":end,"resampleFreq":"daily"},
            headers=headers, timeout=12)
        
        if r.status_code == 200:
            data = r.json()
            if data:
                rows = [{"date":d["date"],"open":d.get("open",d.get("adjOpen",0)),
                         "high":d.get("high",d.get("adjHigh",0)),
                         "low":d.get("low",d.get("adjLow",0)),
                         "close":d.get("close",d.get("adjClose",0)),
                         "volume":d.get("volume",0)} for d in data]
                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["date"])
                result = _safe_df(df.set_index("date").sort_index())
                if result is not None:
                    logger.debug("Tiingo: %s %d bars", symbol, len(result))
                    return result
        
        logger.debug("tiingo: HTTP %s for %s", r.status_code, symbol)
        return None
    except Exception as e: logger.debug("tiingo: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 9: Upstox API (free with Upstox account — alternative broker)
# ─────────────────────────────────────────────────────────────────────────────
def _from_upstox(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    """Upstox provides free historical data with account. UPSTOX_TOKEN in .env"""
    _token = os.getenv("UPSTOX_TOKEN","")
    if not _token: return None
    try:
        import requests
        iv_map = {"1d":"1D","1h":"60minute","30m":"30minute","5m":"5minute"}
        up_iv = iv_map.get(interval)
        if not up_iv: return None
        end   = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now()-timedelta(days=days+2)).strftime("%Y-%m-%d")
        instr = f"NSE_INDEX|{symbol}" if symbol in ('NIFTY','BANKNIFTY') else f"NSE_EQ|{symbol}"
        r = requests.get(
            "https://api.upstox.com/v2/historical-candle/"+instr+f"/{up_iv}/{end}/{start}",
            headers={"Authorization":f"Bearer {_token}","Accept":"application/json"},
            timeout=12)
        if r.status_code!=200: return None
        candles = r.json().get("data",{}).get("candles",[])
        if not candles: return None
        rows = [{"date":c[0],"open":c[1],"high":c[2],"low":c[3],"close":c[4],"volume":c[5]}
                for c in candles]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return _safe_df(df.set_index("date").sort_index())
    except Exception as e: logger.debug("upstox: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 10: Fyers API (free with Fyers account)
# ─────────────────────────────────────────────────────────────────────────────
def _from_fyers(symbol:str, interval:str, days:int) -> Optional[pd.DataFrame]:
    """Fyers provides free historical data with account. FYERS_TOKEN in .env"""
    _token = os.getenv("FYERS_TOKEN","")
    if not _token: return None
    try:
        import requests
        iv_map = {"1d":"D","1h":"60","30m":"30","15m":"15","5m":"5","1m":"1"}
        fy_iv = iv_map.get(interval)
        if not fy_iv: return None
        end_ts   = int(datetime.now().timestamp())
        start_ts = int((datetime.now()-timedelta(days=days+2)).timestamp())
        fy_sym = f"NSE:{symbol}-INDEX" if symbol in ('NIFTY','BANKNIFTY','FINNIFTY') else f"NSE:{symbol}-EQ"
        r = requests.get("https://api.fyers.in/data/v3/history", params={
            "symbol":fy_sym,"resolution":fy_iv,"date_format":"1",
            "range_from":str(start_ts),"range_to":str(end_ts),"cont_flag":"1"},
            headers={"Authorization":f"Bearer {_token}"},timeout=12)
        if r.status_code!=200: return None
        data = r.json().get("candles",[])
        if not data: return None
        rows = [{"date":datetime.fromtimestamp(c[0]),"open":c[1],"high":c[2],
                 "low":c[3],"close":c[4],"volume":c[5]} for c in data]
        df = pd.DataFrame(rows)
        return _safe_df(df.set_index("date").sort_index())
    except Exception as e: logger.debug("fyers: %s",e); return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN: get_market_data_from_pool
# ─────────────────────────────────────────────────────────────────────────────
def get_market_data_from_pool(
    symbol:str, interval:str="5m", days:int=5,
    angel=None, verbose:bool=False,
) -> Optional[pd.DataFrame]:
    """
    Try all data sources in priority order.
    Falls back to cache if all live sources fail (e.g. 4 AM network down).
    """
    sources = [
        ("Angel_One",    lambda: _from_angel(symbol, interval, days, angel)),
        ("NSE_Live",     lambda: _from_nse_allindices(symbol)),  # live during market hours
        ("NSE_Direct",   lambda: _from_nse_direct(symbol, interval, days)),
        ("NSE_Equity",   lambda: _from_nse_equity(symbol, interval, days)),
        ("yfinance",     lambda: _from_yfinance(symbol, interval, days)),
        ("Stooq",        lambda: _from_stooq(symbol, interval, days)),
        ("Twelve_Data",  lambda: _from_twelve_data(symbol, interval, days)),
        ("Alpha_Vantage",lambda: _from_alpha_vantage(symbol, interval, days)),
        ("Tiingo",       lambda: _from_tiingo(symbol, interval, days)),
        ("Fyers",        lambda: _from_fyers(symbol, interval, days)),
    ]

    for name, fn in sources:
        try:
            df = fn()
            if df is not None and len(df)>=2:
                if verbose: logger.info("Data: %s from %s (%d bars)",symbol,name,len(df))
                _cache_write(symbol, interval, df)  # cache successful fetch
                return df
        except Exception as e:
            logger.debug("Source %s failed for %s: %s",name,symbol,e)
        time.sleep(0.05)

    # All live sources failed — try cache (e.g. 4 AM network down)
    cached = _cache_read(symbol, interval, max_age_hours=26)
    if cached is not None:
        logger.info("Using cached data for %s (all live sources unavailable)",symbol)
        return cached

    logger.warning("All data sources failed for %s",symbol)
    return None


def source_health_check() -> dict:
    """Check each data source independently — accurate status."""
    import requests as _rq
    off = not _is_market_hours()
    results = {}

    # ── 1. NSE Live (allIndices) — most reliable during market hours ──
    try:
        _s = _rq.Session()
        _s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        _s.get("https://www.nseindia.com/", timeout=5)
        _r = _s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        _nifty = 0
        for _idx in _r.json().get("data",[]):
            if "NIFTY 50" in str(_idx.get("index","")):
                _nifty = float(_idx.get("last",0) or 0)
                break
        results["NSE Live"] = f"✅ NIFTY ₹{_nifty:,.0f}" if _nifty else "❌ No data"
    except Exception as _e:
        results["NSE Live"] = f"❌ {str(_e)[:30]}"

    # ── 2. Angel One ──────────────────────────────────────────────────
    try:
        import config as _cfg
        ak = getattr(_cfg,"API_KEY","") or ""
        ci = getattr(_cfg,"CLIENT_ID","") or ""
        if ak and ci:
            results["Angel One"] = "⚙️ Keys OK — connects at startup"
        else:
            results["Angel One"] = "❌ API_KEY or CLIENT_ID missing in .env"
    except Exception as _e:
        results["Angel One"] = f"❌ {str(_e)[:30]}"

    # ── 3. yfinance ───────────────────────────────────────────────────
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        _t = yf.Ticker("^NSEI")
        _df = _t.history(period="5d", interval="1d")
        if _df is not None and not _df.empty:
            _p = float(_df["Close"].iloc[-1])
            results["yfinance"] = f"✅ NIFTY ₹{_p:,.0f}" if _p else "❌ Empty"
        else:
            results["yfinance"] = "❌ Yahoo API broken (using NSE Live instead)"
    except Exception as _e:
        results["yfinance"] = "❌ Yahoo API broken (using NSE Live instead)"

    # ── 4. Stooq ──────────────────────────────────────────────────────
    try:
        from datetime import timedelta
        _end   = datetime.now().strftime("%Y%m%d")
        _start = (datetime.now()-timedelta(days=10)).strftime("%Y%m%d")
        _r2 = _rq.get(
            f"https://stooq.com/q/d/l/?s=^nsei&d1={_start}&d2={_end}&i=d",
            timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if _r2.status_code==200 and "Data" not in _r2.text[:20]:
            _lines = [l for l in _r2.text.strip().split("\n") if l]
            results["Stooq"] = f"✅ {len(_lines)-1} bars" if len(_lines)>1 else "❌ No data"
        else:
            results["Stooq"] = "❌ No data"
    except Exception as _e:
        results["Stooq"] = f"❌ {str(_e)[:30]}"

    # ── 5. Tiingo ─────────────────────────────────────────────────────
    if _TIINGO_KEY:
        try:
            _r3 = _rq.get(
                "https://api.tiingo.com/tiingo/daily/AAPL/prices",
                params={"token":_TIINGO_KEY},
                headers={"Authorization":f"Token {_TIINGO_KEY}"},
                timeout=8)
            results["Tiingo"] = f"✅ Connected" if _r3.status_code==200 else f"❌ HTTP {_r3.status_code}"
        except Exception as _e:
            results["Tiingo"] = f"❌ {str(_e)[:30]}"
    else:
        results["Tiingo"] = "⚙️ No key (add TIINGO_KEY to .env)"

    # ── 6. Twelve Data ────────────────────────────────────────────────
    if _TWELVE_KEY:
        try:
            _r4 = _rq.get(
                "https://api.twelvedata.com/price",
                params={"symbol":"AAPL","apikey":_TWELVE_KEY},
                timeout=8)
            results["Twelve Data"] = f"✅ Connected" if "price" in _r4.text else f"❌ {_r4.text[:40]}"
        except Exception as _e:
            results["Twelve Data"] = f"❌ {str(_e)[:30]}"
    else:
        results["Twelve Data"] = "⚙️ No key (add TWELVE_DATA_KEY)"

    # ── 7. Alpha Vantage ─────────────────────────────────────────────
    results["Alpha Vantage"] = f"⚙️ 25/day limit — reserved" if _AV_KEY else "⚙️ No key"

    # ── 8. Fyers ─────────────────────────────────────────────────────
    _fyers_tok = os.getenv("FYERS_TOKEN","")
    results["Fyers"] = "⚙️ Token set — connects at startup" if _fyers_tok else "⚙️ No token"

    # ── Cache ─────────────────────────────────────────────────────────
    results["_cache"] = f"📦 Cache: {len(list(_CACHE_DIR.glob('*.pkl')))} files"
    results["_note"]  = ""
    return results

