"""
participant_oi_backfill.py — backfill NSE participant-wise OI history.

WHY THIS EXISTS
  The live `participant_oi.py` only ever holds a single snapshot and its history
  file (`participant_oi_history.json`) was all zeros, so there was nothing to
  backtest. But NSE archives the participant-wise OI as a dated CSV that is
  downloadable for PAST trading days:

      https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv

  This module pulls that archive across a date range into an accumulating SQLite
  table so an edge in FII/Pro/Client positioning can actually be MEASURED.

  Additive and read-only w.r.t. the rest of the system: it imports no live
  trading code, writes only to its own DB, and places no orders. Detecting
  positioning is not the same as having an edge — backtest before wiring.

SCHEMA (one row per date × client_type; client_type in {Client,DII,FII,Pro,TOTAL})
  the 14 raw contract-count columns NSE publishes, snake_cased.

USAGE
  python participant_oi_backfill.py --days 400          # last 400 calendar days
  python participant_oi_backfill.py --start 2024-01-01 --end 2026-06-06
  python participant_oi_backfill.py --days 30 --force   # re-download existing
"""
from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger("participant_oi_backfill")

_DB_PATH = Path("participant_oi.db")
_HOSTS = ("https://nsearchives.nseindia.com", "https://archives.nseindia.com")
_PATH = "/content/nsccl/fao_participant_oi_{}.csv"
_REQUEST_GAP_SEC = 0.4          # be polite to NSE
_MIN_CONTENT_BYTES = 300

# NSE CSV header (stripped) -> our column name
_COLMAP = {
    "Future Index Long": "future_index_long",
    "Future Index Short": "future_index_short",
    "Future Stock Long": "future_stock_long",
    "Future Stock Short": "future_stock_short",
    "Option Index Call Long": "opt_index_call_long",
    "Option Index Put Long": "opt_index_put_long",
    "Option Index Call Short": "opt_index_call_short",
    "Option Index Put Short": "opt_index_put_short",
    "Option Stock Call Long": "opt_stock_call_long",
    "Option Stock Put Long": "opt_stock_put_long",
    "Option Stock Call Short": "opt_stock_call_short",
    "Option Stock Put Short": "opt_stock_put_short",
    "Total Long Contracts": "total_long",
    "Total Short Contracts": "total_short",
}
_NUM_COLS = list(_COLMAP.values())


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.nseindia.com/",
        "Accept": "*/*",
    })
    try:                                            # route via proxy if configured
        from nse_proxy import apply as _apply
        _apply(s)
    except Exception:
        pass
    try:                                            # warm cookies (best effort)
        s.get("https://www.nseindia.com/", timeout=8)
    except Exception:
        pass
    return s


def fetch_day(d: date, session: Optional[requests.Session] = None) -> Optional[pd.DataFrame]:
    """Return the participant-OI frame for a trading day, or None if unavailable."""
    if d.weekday() >= 5:                            # weekend: no report
        return None
    s = session or _session()
    ds = d.strftime("%d%m%Y")
    for host in _HOSTS:
        url = host + _PATH.format(ds)
        try:
            r = s.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) > _MIN_CONTENT_BYTES:
                df = pd.read_csv(io.BytesIO(r.content), skiprows=1)
                df.columns = [str(c).strip() for c in df.columns]
                if "Client Type" in df.columns:
                    return df
        except Exception as e:
            logger.debug("fetch %s %s: %s", d, host, e)
    return None


# ── storage ─────────────────────────────────────────────────────────────────--

def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    cols = ", ".join(f"{c} INTEGER" for c in _NUM_COLS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS participant_oi (
            date TEXT, client_type TEXT, {cols},
            PRIMARY KEY (date, client_type)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_poi_date ON participant_oi(date)")
    conn.commit()
    return conn


def _have_date(conn: sqlite3.Connection, d: date) -> bool:
    row = conn.execute("SELECT 1 FROM participant_oi WHERE date=? LIMIT 1",
                       (d.isoformat(),)).fetchone()
    return row is not None


def store_day(conn: sqlite3.Connection, d: date, df: pd.DataFrame) -> int:
    rename = {k: v for k, v in _COLMAP.items() if k in df.columns}
    missing = [k for k in _COLMAP if k not in df.columns]
    if missing:
        logger.warning("%s: missing columns %s — skipping", d, missing)
        return 0
    df = df.rename(columns=rename)
    placeholders = ", ".join(["?"] * (2 + len(_NUM_COLS)))
    cols_sql = "date, client_type, " + ", ".join(_NUM_COLS)
    n = 0
    for _, row in df.iterrows():
        ctype = str(row["Client Type"]).strip()
        if ctype not in ("Client", "DII", "FII", "Pro", "TOTAL"):
            continue
        vals = [d.isoformat(), ctype]
        for c in _NUM_COLS:
            try:
                vals.append(int(float(str(row[c]).replace(",", "").strip() or 0)))
            except Exception:
                vals.append(0)
        conn.execute(f"INSERT OR REPLACE INTO participant_oi ({cols_sql}) "
                     f"VALUES ({placeholders})", vals)
        n += 1
    conn.commit()
    return n


# ── driver ──────────────────────────────────────────────────────────────────--

def backfill(start: date, end: date, force: bool = False) -> Dict[str, int]:
    conn = _init_db()
    session = _session()
    days = ok = skipped = missing = 0
    d = start
    while d <= end:
        if d.weekday() < 5:                         # weekdays only
            days += 1
            if not force and _have_date(conn, d):
                skipped += 1
            else:
                df = fetch_day(d, session)
                if df is not None and store_day(conn, d, df):
                    ok += 1
                    logger.info("stored %s", d)
                else:
                    missing += 1                    # holiday or not yet published
                time.sleep(_REQUEST_GAP_SEC)
        d += timedelta(days=1)
    conn.close()
    return {"weekdays": days, "stored": ok, "already_had": skipped, "no_data": missing}


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill NSE participant-wise OI history.")
    p.add_argument("--days", type=int, help="backfill the last N calendar days")
    p.add_argument("--start", help="start date YYYY-MM-DD")
    p.add_argument("--end", help="end date YYYY-MM-DD (default: today)")
    p.add_argument("--force", action="store_true", help="re-download days already stored")
    return p.parse_args(argv)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    elif args.days:
        start = end - timedelta(days=args.days)
    else:
        start = end - timedelta(days=30)
    print(f"Backfilling participant OI {start} → {end} (force={args.force})")
    stats = backfill(start, end, force=args.force)
    print(f"Done: {stats}")
    print(f"DB: {_DB_PATH.resolve()}")


if __name__ == "__main__":
    main()
