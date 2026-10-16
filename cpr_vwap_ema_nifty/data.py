"""
Data for CPR_VWAP_EMA_Nifty. Everything this strategy downloads goes into its
OWN cache (cpr_vwap_ema_nifty/data_cache.db). Existing databases are opened
read-only (sqlite mode=ro) and only as a fallback for days the own cache lacks.

Series:
  spot_1m  NIFTY 50 index 1m bars (Angel token 99926000). 5m/30m/daily are
           resampled from these so every timeframe comes from one source.
  fut_5m   NIFTY futures 5m bars WITH volume, for VWAP (index has no volume).

Fallback for spot_1m only (read-only):
  external_backtest_data.db  NIFTY 1m 2015-01..2024-01 (source external_2015_2024)
  candle_cache.db            NIFTY 1m 2026-05..
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd

PKG_DIR = Path(__file__).resolve().parent
REPO_DIR = PKG_DIR.parent
CACHE_DB = PKG_DIR / "data_cache.db"
logger = logging.getLogger("cpr_vwap_ema_nifty.data")

_CHUNK_DAYS = {"ONE_MINUTE": 28, "FIVE_MINUTE": 90}


def _conn(path: Path = CACHE_DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS bars (series TEXT, ts TEXT, open REAL, high REAL, low REAL, "
                "close REAL, volume REAL, PRIMARY KEY (series, ts))")
    con.execute("CREATE TABLE IF NOT EXISTS fetch_log (series TEXT, chunk_start TEXT, chunk_end TEXT, "
                "bars INTEGER, message TEXT, fetched_at TEXT, PRIMARY KEY (series, chunk_start))")
    return con


def _norm_ts(raw: str) -> str:
    """'2026-10-15T09:15:00+05:30' / '2026-10-15 09:15:00+05:30' -> '2026-10-15 09:15'."""
    return str(raw)[:16].replace("T", " ")


# ── Angel download ──────────────────────────────────────────────────────────

def _angel_client():
    from dotenv import load_dotenv
    load_dotenv(REPO_DIR / ".env")
    from angel import AngelOne  # read-only use of the existing broker wrapper
    a = AngelOne(api_key=os.getenv("API_KEY", ""), client_id=os.getenv("CLIENT_ID", ""),
                 password=os.getenv("PASSWORD", ""), totp_secret=os.getenv("TOTP_SECRET", ""))
    if not a._ensure_connected() or a.obj is None:
        raise RuntimeError("Angel login failed")
    return a.obj


def _get_candles(obj, token: str, exchange: str, interval: str, start: date, end: date,
                 min_interval: float) -> Tuple[list, str]:
    from request_governor import acquire  # shared, account-wide Angel pacing
    params = {"exchange": exchange, "symboltoken": token, "interval": interval,
              "fromdate": f"{start} 09:15", "todate": f"{end} 15:30"}
    msg = ""
    for attempt in range(4):
        acquire("angel_historical_candles", min_interval)
        try:
            r = obj.getCandleData(params) or {}
        except Exception as exc:  # network / rate-limit raise
            msg = str(exc)[:120]
            time.sleep(2 * (attempt + 1))
            continue
        msg = str(r.get("message") or "")
        if r.get("status") is False and "rate" in msg.lower():
            time.sleep(2 * (attempt + 1))
            continue
        return list(r.get("data") or []), msg
    return [], msg


def fetch(series: str, start: date, end: date, cfg: dict, force: bool = False) -> Dict[str, int]:
    """Download `series` (spot_1m | fut_5m) into the own cache, chunk by chunk.
    Chunks already logged are skipped unless force=True."""
    ang = cfg["angel"]
    if series == "spot_1m":
        token, exch, interval = ang["spot_token"], ang["spot_exchange"], "ONE_MINUTE"
    elif series == "fut_5m":
        token, exch, interval = ang["futures_token"], ang["futures_exchange"], "FIVE_MINUTE"
    else:
        raise ValueError(series)
    con = _conn()
    done = {r[0] for r in con.execute("SELECT chunk_start FROM fetch_log WHERE series=?", (series,))}
    obj = None
    stats = {"chunks": 0, "skipped": 0, "bars": 0, "empty": 0}
    step = _CHUNK_DAYS[interval]
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=step - 1))
        if not force and cur.isoformat() in done and stop < date.today() - timedelta(days=1):
            stats["skipped"] += 1
            cur = stop + timedelta(days=1)
            continue
        if obj is None:
            obj = _angel_client()
        rows, msg = _get_candles(obj, token, exch, interval, cur, stop, float(ang.get("min_interval_sec", 0.6)))
        con.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                        [(series, _norm_ts(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows])
        con.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                    (series, cur.isoformat(), stop.isoformat(), len(rows), msg, datetime.now().isoformat()))
        con.commit()
        stats["chunks"] += 1
        stats["bars"] += len(rows)
        stats["empty"] += int(not rows)
        logger.info("fetch %s %s..%s: %d bars (%s)", series, cur, stop, len(rows), msg)
        cur = stop + timedelta(days=1)
    con.close()
    return stats


# ── Loading ─────────────────────────────────────────────────────────────────

def _frame(rows, cols=("ts", "open", "high", "low", "close", "volume")) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(cols))
    if df.empty:
        return pd.DataFrame(columns=list(cols[1:]), index=pd.DatetimeIndex([], name="ts"))
    df["ts"] = pd.to_datetime(df["ts"].map(_norm_ts))
    return df.drop_duplicates("ts").set_index("ts").sort_index()


def load_series(series: str, start: str, end: str, db: Path = CACHE_DB) -> pd.DataFrame:
    if not Path(db).exists():
        return _frame([])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT ts, open, high, low, close, volume FROM bars WHERE series=? AND ts>=? AND ts<?",
                       (series, start, f"{end} 23:59")).fetchall()
    con.close()
    return _frame(rows)


def _fallback_spot_1m(start: str, end: str) -> pd.DataFrame:
    parts = []
    ext = REPO_DIR / "external_backtest_data.db"
    if ext.exists():
        con = sqlite3.connect(f"file:{ext}?mode=ro", uri=True)
        parts.append(_frame(con.execute(
            "SELECT timestamp, open, high, low, close, volume FROM candles WHERE symbol='NIFTY' AND interval='1m' "
            "AND timestamp>=? AND timestamp<?", (start, f"{end} 23:59")).fetchall()))
        con.close()
    cc = REPO_DIR / "candle_cache.db"
    if cc.exists():
        con = sqlite3.connect(f"file:{cc}?mode=ro", uri=True)
        rows = con.execute("SELECT timestamp, open, high, low, close, volume FROM candles WHERE symbol='NIFTY' "
                           "AND interval='1m' AND timestamp>=? AND timestamp<?", (start, f"{end}T23:59")).fetchall()
        parts.append(_frame(rows))
        con.close()
    parts = [p for p in parts if not p.empty]
    if not parts:
        return _frame([])
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="first")].sort_index()


def load_spot_1m(start: str, end: str, min_bars: int) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Own cache first; per day, fall back to the read-only stores when the
    own cache has fewer than `min_bars` session bars. Returns (bars, source per day)."""
    own = load_series("spot_1m", start, end).between_time("09:15", "15:29")
    fb = _fallback_spot_1m(start, end)
    fb = fb.between_time("09:15", "15:29") if not fb.empty else fb
    own_days = own.groupby(own.index.normalize()).size() if not own.empty else pd.Series(dtype=int)
    fb_days = fb.groupby(fb.index.normalize()).size() if not fb.empty else pd.Series(dtype=int)
    keep, source = [], {}
    for day in sorted(set(own_days.index) | set(fb_days.index)):
        key = day.strftime("%Y-%m-%d")
        if own_days.get(day, 0) >= min_bars:
            keep.append(own.loc[key]); source[key] = "angel"
        elif fb_days.get(day, 0) >= min_bars:
            keep.append(fb.loc[key]); source[key] = "fallback_readonly"
        else:
            source[key] = "insufficient"
    bars = pd.concat(keep).sort_index() if keep else _frame([])
    return bars[["open", "high", "low", "close"]].astype(float), source


def load_fut_5m(start: str, end: str) -> pd.DataFrame:
    f = load_series("fut_5m", start, end)
    return f.between_time("09:15", "15:29").astype(float) if not f.empty else f


def coverage(start: str, end: str) -> Dict[str, Optional[str]]:
    out = {}
    for s in ("spot_1m", "fut_5m"):
        f = load_series(s, start, end)
        out[s] = {"bars": int(len(f)), "first": str(f.index.min()) if len(f) else None,
                  "last": str(f.index.max()) if len(f) else None,
                  "days": int(f.index.normalize().nunique()) if len(f) else 0}
    return out
