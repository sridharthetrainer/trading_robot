"""
candle_cache.py — Local 5-minute candle cache in SQLite

Every time Angel/Upstox returns 5m data successfully, it's saved here.
Next time Angel fails, we serve from cache. Data survives restarts.

Usage:
    from candle_cache import save_candles, get_cached_candles
    
    # Save after successful fetch
    save_candles("NIFTY", "5m", df)
    
    # Read from cache when source fails
    df = get_cached_candles("NIFTY", "5m", days=5)
"""
from __future__ import annotations
import sqlite3
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path("candle_cache.db")
_INIT_DONE = False


def _get_conn():
    global _INIT_DONE
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=30)
    if not _INIT_DONE:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                UNIQUE(symbol, interval, timestamp)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_candles_sym_int_ts 
            ON candles(symbol, interval, timestamp)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_meta (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                last_update TEXT,
                bar_count INTEGER,
                UNIQUE(symbol, interval)
            )
        """)
        conn.commit()
        _INIT_DONE = True
    return conn


def save_candles(symbol: str, interval: str, df) -> int:
    """
    Save candle data to local cache.
    Returns number of new rows inserted.
    """
    if df is None or len(df) == 0:
        return 0
    
    try:
        conn = _get_conn()
        inserted = 0
        
        for idx, row in df.iterrows():
            ts = str(idx)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO candles "
                    "(symbol, interval, timestamp, open, high, low, close, volume) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (symbol.upper(), interval, ts,
                     float(row.get("open", 0)),
                     float(row.get("high", 0)),
                     float(row.get("low", 0)),
                     float(row.get("close", 0)),
                     int(row.get("volume", 0) or 0))
                )
                inserted += 1
            except Exception:
                pass
        
        # Update meta
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta "
            "(symbol, interval, last_update, bar_count) "
            "VALUES (?,?,?,?)",
            (symbol.upper(), interval, datetime.now().isoformat(), inserted)
        )
        conn.commit()
        conn.close()
        
        if inserted > 0:
            logger.debug("Cache saved: %s %s → %d bars", symbol, interval, inserted)
        return inserted
    except Exception as e:
        logger.debug("Cache save %s: %s", symbol, e)
        return 0


def get_cached_candles(
    symbol: str,
    interval: str = "5m",
    days: int = 5,
) -> Optional['pd.DataFrame']:
    """
    Get candles from local cache.
    Returns DataFrame or None if no cached data.
    """
    try:
        import pandas as pd
        conn = _get_conn()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candles "
            "WHERE symbol = ? AND interval = ? AND timestamp >= ? "
            "ORDER BY timestamp",
            (symbol.upper(), interval, cutoff)
        ).fetchall()
        conn.close()
        
        if not rows or len(rows) < 2:
            return None
        
        data = []
        for ts, o, h, l, c, v in rows:
            data.append({
                "date": pd.Timestamp(ts),
                "open": o, "high": h, "low": l,
                "close": c, "volume": v,
            })
        
        df = pd.DataFrame(data).set_index("date").sort_index()
        df = df[df["close"] > 0]
        
        if len(df) >= 2:
            logger.info("Cache hit: %s %s → %d bars", symbol, interval, len(df))
            return df
        return None
    except Exception as e:
        logger.debug("Cache read %s: %s", symbol, e)
        return None


def get_cache_stats() -> dict:
    """Get cache statistics for /health command."""
    try:
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM candles").fetchone()[0]
        meta = conn.execute(
            "SELECT symbol, interval, last_update, bar_count "
            "FROM cache_meta ORDER BY last_update DESC LIMIT 10"
        ).fetchall()
        conn.close()
        
        return {
            "total_bars": total,
            "symbols": symbols,
            "recent": [
                {"symbol": m[0], "interval": m[1],
                 "last_update": m[2], "bars": m[3]}
                for m in meta
            ],
        }
    except Exception:
        return {"total_bars": 0, "symbols": 0, "recent": []}


def cleanup_old_data(days: int = 30) -> int:
    """Remove cached data older than N days."""
    try:
        conn = _get_conn()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = conn.execute(
            "DELETE FROM candles WHERE timestamp < ?", (cutoff,)
        )
        deleted = cursor.rowcount
        conn.execute("PRAGMA optimize")
        conn.commit()
        conn.close()
        if deleted > 0:
            logger.info("Cache cleanup: removed %d bars older than %d days", deleted, days)
        return deleted
    except Exception:
        return 0
