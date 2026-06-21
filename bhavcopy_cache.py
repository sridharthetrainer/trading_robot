"""
bhavcopy_cache.py — NSE Bhavcopy EOD Data Cache

Downloads NSE official Bhavcopy (market summary) daily.
Stores OHLCV for all NSE stocks in local SQLite.
Backtest uses this — no Angel One session needed after market close.

Bhavcopy URL: https://archives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
Free, official, always available after 6 PM.
"""
from __future__ import annotations
import io, logging, sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_DB_PATH  = Path("nse_cache.db")
_BHV_URL  = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{}.csv"
_BHV_URL2 = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{}_F_0000.csv"


def _init_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol TEXT, date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL,
            PRIMARY KEY (symbol, date)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_date ON ohlcv(symbol, date)")
    conn.commit()
    return conn


def download_bhavcopy(for_date: date = None) -> int:
    """
    Download NSE Bhavcopy for a given date.
    Returns number of records stored.
    """
    if for_date is None:
        for_date = date.today()
    
    # Skip weekends
    if for_date.weekday() >= 5:
        logger.debug("Bhavcopy: skipping weekend %s", for_date)
        return 0

    date_str = for_date.strftime("%d%m%Y")
    date_str2 = for_date.strftime("%Y%m%d")
    
    urls = [
        _BHV_URL.format(date_str),
        _BHV_URL2.format(date_str2),
        f"https://archives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str2}_F_0000.csv",
    ]
    
    import requests
    df = None
    for url in urls:
        try:
            s = requests.Session()
            s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            r = s.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) > 1000:
                df = pd.read_csv(io.BytesIO(r.content))
                logger.info("Bhavcopy downloaded: %s (%d rows)", url.split("/")[-1], len(df))
                break
        except Exception as e:
            logger.debug("Bhavcopy URL failed %s: %s", url, e)
    
    if df is None or df.empty:
        logger.warning("Bhavcopy not available for %s", for_date)
        return 0
    
    # Normalize columns
    df.columns = [c.strip().upper() for c in df.columns]
    col_map = {
        "SYMBOL":     ["SYMBOL","SCRIP","ISIN_CODE"],
        "OPEN":       ["OPEN","OPEN_PRICE","OPENPRICE"],
        "HIGH":       ["HIGH","HIGH_PRICE","HIGHPRICE"],
        "LOW":        ["LOW","LOW_PRICE","LOWPRICE"],
        "CLOSE":      ["CLOSE","CLOSE_PRICE","CLOSEPRICE","LAST_PRICE","LAST"],
        "VOLUME":     ["TOTTRDQTY","VOLUME","TRADEDQTY","TTL_TRDQTY"],
    }
    rename = {}
    for target, candidates in col_map.items():
        for c in candidates:
            if c in df.columns:
                rename[c] = target
                break
    df = df.rename(columns=rename)
    
    required = {"SYMBOL","OPEN","HIGH","LOW","CLOSE"}
    if not required.issubset(df.columns):
        logger.warning("Bhavcopy columns missing: %s", required - set(df.columns))
        return 0
    
    # Filter EQ series only
    if "SERIES" in df.columns:
        df = df[df["SERIES"].str.strip() == "EQ"]
    
    date_iso = for_date.isoformat()
    conn = _init_db()
    rows = 0
    for _, row in df.iterrows():
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?)",
                (str(row["SYMBOL"]).strip().upper(), date_iso,
                 float(row["OPEN"]), float(row["HIGH"]),
                 float(row["LOW"]),  float(row["CLOSE"]),
                 float(row.get("VOLUME", 0) or 0))
            )
            rows += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    logger.info("Bhavcopy stored: %d records for %s", rows, date_iso)
    return rows


# Symbol aliases — nifty200 name → Bhavcopy name
_BHAV_ALIASES = {
    "UJJIVAN":    "UJJIVANSFB",
    "MINDTREE":   "LTIM",
    "MCDOWELL-N": "MCDOWELLS",
    "HDFC":       "HDFCAMC",
    "AMARAJABAT": "AMARA",
    "MINDAIND":   "MINDACORP",
    "HPCL":       "HINDPETRO",
    "NALCO":      "NATIONALUM",
    "LTIM":       "LTIMINDTEE",
    # Indices not in bhavcopy — handled separately
}


def get_ohlcv(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
    """
    Get OHLCV history for a symbol from local Bhavcopy cache.
    Tries aliases automatically for renamed symbols.
    """
    if not _DB_PATH.exists():
        return None
    sym = symbol.upper().strip()
    # Try aliases
    candidates = [sym, _BHAV_ALIASES.get(sym, sym)]
    # Also try with common suffixes stripped
    if sym.endswith("LTD"): candidates.append(sym[:-3].strip())
    
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(str(_DB_PATH))
        for candidate in candidates:
            df = pd.read_sql_query(
                "SELECT date, open, high, low, close, volume FROM ohlcv "
                "WHERE symbol=? AND date>=? ORDER BY date",
                conn, params=(candidate, cutoff))
            if not df.empty:
                conn.close()
                df["date"] = pd.to_datetime(df["date"])
                return df.set_index("date")
        conn.close()
        return None
    except Exception as e:
        logger.debug("get_ohlcv %s: %s", symbol, e)
        return None


def download_last_n_days(n: int = 30) -> int:
    """Download bhavcopy for last N trading days. Run once to seed cache."""
    total = 0
    d = date.today()
    days_done = 0
    while days_done < n:
        if d.weekday() < 5:  # weekday
            count = download_bhavcopy(d)
            if count > 0:
                total += count
                days_done += 1
        d -= timedelta(days=1)
        if (date.today() - d).days > 90:
            break
    return total


def cache_status() -> dict:
    """Return cache statistics."""
    if not _DB_PATH.exists():
        return {"status": "empty", "records": 0, "symbols": 0, "latest_date": None}
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MAX(date) FROM ohlcv")
        rows, syms, latest = c.fetchone()
        conn.close()
        return {"status": "ok", "records": rows, "symbols": syms, "latest_date": latest}
    except Exception:
        return {"status": "error"}


def _yf_fallback(symbol: str, days: int = 60):
    """Last-resort fallback: yfinance download."""
    try:
        import yf_compat as yf  # yfinance removed: Yahoo API broken
        sym = symbol.upper()
        ticker = sym if sym in {"^NSEI","^BSESN"} else f"{sym}.NS"
        df = yf.download(ticker, period=f"{days}d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) > 5:
            df.columns = [c.lower() if isinstance(c,str) else c[0].lower()
                          for c in df.columns]
            return df
    except Exception:
        pass
    return None


def get_history(symbol: str, days: int = 60, interval: str = "5m"):
    """
    Alias used by signal_engine and live_signal_engine.
    Returns OHLCV DataFrame from bhavcopy cache or fallback.
    """
    return get_ohlcv(symbol, days=days, interval=interval)


def get_daily_history(symbol: str, days: int = 252):
    """Daily OHLCV for regime detection, Weinstein stage, etc."""
    return get_ohlcv(symbol, days=days, interval="1d")
