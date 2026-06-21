"""
options_bhavcopy_backfill.py — backfill REAL NIFTY option EOD prices.

WHY
  The existing condor backtest (backtest_iron_condor.py) is synthetic: it invents
  the credit (0.4% of spot) and the loss-on-breach (50% of max), so its rupee P&L
  is fabricated. A real condor backtest needs REAL per-strike option premia. NSE
  archives the F&O bhavcopy daily, with per-strike CE/PE close + settlement price,
  downloadable for past dates — so the whole NIFTY option surface is backfillable.

  Two archive layouts (this module normalises both):
    UDiFF  (2024-07-ish → now):
      https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
    OLD    (… → 2024):
      https://archives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip

  Additive + isolated: imports no live code, places no orders, writes only to its
  own DB (options_nifty.db). Stores NIFTY index options only (keeps the DB small).

SCHEMA  options_eod(date, expiry, strike, opt_type, close, settle, oi, underlying)

USAGE
  python options_bhavcopy_backfill.py --start 2020-01-01 --end 2026-06-08
  python options_bhavcopy_backfill.py --days 60
"""
from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests

logger = logging.getLogger("options_bhavcopy_backfill")

_DB_PATH = Path("options_nifty.db")
_REQUEST_GAP_SEC = 0.3
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                      "Referer": "https://www.nseindia.com/", "Accept": "*/*"})
    try:
        from nse_proxy import apply as _apply; _apply(s)
    except Exception:
        pass
    try:
        s.get("https://www.nseindia.com/", timeout=8)
    except Exception:
        pass
    return s


def _udiff_url(d: date) -> str:
    return ("https://nsearchives.nseindia.com/content/fo/"
            f"BhavCopy_NSE_FO_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip")


def _old_url(d: date) -> str:
    return ("https://archives.nseindia.com/content/historical/DERIVATIVES/"
            f"{d.year}/{_MONTHS[d.month - 1]}/"
            f"fo{d.strftime('%d')}{_MONTHS[d.month - 1]}{d.year}bhav.csv.zip")


def _read_zip_csv(content: bytes) -> Optional[pd.DataFrame]:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        return pd.read_csv(z.open(name))
    except Exception as e:
        logger.debug("zip parse: %s", e)
        return None


def _norm_udiff(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    df = df[(df["TckrSymb"] == "NIFTY") & (df["OptnTp"].isin(["CE", "PE"]))].copy()
    if df.empty:
        return None
    out = pd.DataFrame({
        "expiry": pd.to_datetime(df["XpryDt"]).dt.strftime("%Y-%m-%d"),
        "strike": df["StrkPric"].astype(float),
        "opt_type": df["OptnTp"],
        "close": df["ClsPric"].astype(float),
        "settle": df["SttlmPric"].astype(float),
        "oi": df["OpnIntrst"].fillna(0).astype(float),
        "underlying": df["UndrlygPric"].astype(float),
    })
    return out


def _norm_old(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    df = df[(df["SYMBOL"] == "NIFTY") & (df["INSTRUMENT"] == "OPTIDX")].copy()
    if df.empty:
        return None
    out = pd.DataFrame({
        "expiry": pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y").dt.strftime("%Y-%m-%d"),
        "strike": df["STRIKE_PR"].astype(float),
        "opt_type": df["OPTION_TYP"],
        "close": df["CLOSE"].astype(float),
        "settle": df["SETTLE_PR"].astype(float),
        "oi": df["OPEN_INT"].fillna(0).astype(float),
        "underlying": float("nan"),          # not in old option rows; fill from index later
    })
    return out


def fetch_day(d: date, session: requests.Session) -> Optional[pd.DataFrame]:
    if d.weekday() >= 5:
        return None
    # UDiFF first (current), then OLD (historical)
    for url, norm in ((_udiff_url(d), _norm_udiff), (_old_url(d), _norm_old)):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000:
                raw = _read_zip_csv(r.content)
                if raw is not None:
                    out = norm(raw)
                    if out is not None and len(out):
                        return out
        except Exception as e:
            logger.debug("fetch %s: %s", d, e)
    return None


def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS options_eod (
            date TEXT, expiry TEXT, strike REAL, opt_type TEXT,
            close REAL, settle REAL, oi REAL, underlying REAL,
            PRIMARY KEY (date, expiry, strike, opt_type)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_date ON options_eod(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_exp ON options_eod(expiry)")
    conn.commit()
    return conn


def _have_date(conn: sqlite3.Connection, d: date) -> bool:
    return conn.execute("SELECT 1 FROM options_eod WHERE date=? LIMIT 1",
                        (d.isoformat(),)).fetchone() is not None


def store_day(conn: sqlite3.Connection, d: date, df: pd.DataFrame) -> int:
    df = df.copy()
    df.insert(0, "date", d.isoformat())
    df.to_sql("_stage", conn, if_exists="replace", index=False)
    conn.execute("""INSERT OR REPLACE INTO options_eod
        (date,expiry,strike,opt_type,close,settle,oi,underlying)
        SELECT date,expiry,strike,opt_type,close,settle,oi,underlying FROM _stage""")
    conn.execute("DROP TABLE _stage")
    conn.commit()
    return len(df)


def backfill(start: date, end: date, force: bool = False) -> dict:
    conn = _init_db()
    session = _session()
    stored = skipped = missing = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            if not force and _have_date(conn, d):
                skipped += 1
            else:
                df = fetch_day(d, session)
                if df is not None and store_day(conn, d, df):
                    stored += 1
                    if stored % 50 == 0:
                        logger.info("stored %d days (…%s)", stored, d)
                else:
                    missing += 1
                time.sleep(_REQUEST_GAP_SEC)
        d += timedelta(days=1)
    conn.close()
    return {"stored": stored, "already_had": skipped, "no_data": missing}


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Backfill real NIFTY option EOD prices.")
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--days", type=int); p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today()
    if a.start:
        start = datetime.strptime(a.start, "%Y-%m-%d").date()
    elif a.days:
        start = end - timedelta(days=a.days)
    else:
        start = end - timedelta(days=30)
    print(f"Backfilling NIFTY options {start} → {end}")
    print(f"Done: {backfill(start, end, a.force)}")
    print(f"DB: {_DB_PATH.resolve()}")


if __name__ == "__main__":
    main()
