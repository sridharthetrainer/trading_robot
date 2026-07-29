"""
external_data_loader.py — ingest third-party historical index CSVs for
backtesting (2026-07-29, operator: "download all the available data, we
will use for backtest").

Source: a community-shared Google Drive folder (linked from
github.com/debaonline4u/NSE-Data, "2024 NSE Index Minute data"), NOT an NSE
or broker-official source -- the repo owner explicitly disclaims ownership
("I found this data in the drive... I don't own them"). Verified structurally
before trusting it (zero duplicate timestamps, zero OHLC-invalid rows, 2,240
distinct trading days over 2015-2024 matching NSE's real calendar, and the
8 exactly-60-bar days line up with real historical Diwali Muhurat sessions --
strong circumstantial evidence this is genuine collected data, not fabricated).

Despite that, this is UNVERIFIED THIRD-PARTY data with a different provenance
than this system's own broker-sourced candles. It is stored in its OWN
database (external_backtest_data.db), tagged with source='external_2015_2024',
and is NEVER written into candle_cache.db (the live engine's table) -- keeping
third-party historical data and live-collected data from ever being silently
conflated is the whole point of this module's existence.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = Path("external_backtest_data.db")
SOURCE_TAG = "external_2015_2024"


def _init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            interval TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (symbol, source, interval, timestamp)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candles_lookup "
        "ON candles(symbol, source, interval, timestamp)"
    )
    conn.commit()
    return conn


def validate_ohlc(df: pd.DataFrame) -> Dict[str, Any]:
    """Structural sanity checks. Returns a report; does not mutate df."""
    issues: Dict[str, Any] = {}
    issues["duplicate_timestamps"] = int(df.index.duplicated().sum())
    bad = df[
        (df["high"] < df["low"]) | (df["high"] < df["open"]) | (df["high"] < df["close"])
        | (df["low"] > df["open"]) | (df["low"] > df["close"])
        | (df["open"] <= 0) | (df["close"] <= 0)
    ]
    issues["bad_ohlc_rows"] = int(len(bad))
    issues["rows"] = int(len(df))
    issues["distinct_days"] = int(df.index.normalize().nunique())
    return issues


def load_csv(symbol: str, csv_path: str, interval: str = "1m",
             db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Validate and ingest one CSV (columns: date,open,high,low,close,volume)
    into external_backtest_data.db, tagged with SOURCE_TAG. Returns a
    validation + ingestion report. Rejects the file (inserts nothing) if
    structural validation fails."""
    df = pd.read_csv(csv_path, parse_dates=["date"])
    df = df.set_index("date").sort_index()

    report = validate_ohlc(df)
    if report["bad_ohlc_rows"] > 0 or report["duplicate_timestamps"] > 0:
        report["accepted"] = False
        report["reason"] = "failed structural validation -- not ingested"
        return report

    conn = _init_db(db_path)
    rows = [
        (symbol.upper(), SOURCE_TAG, interval, str(idx),
         float(row["open"]), float(row["high"]), float(row["low"]),
         float(row["close"]), float(row.get("volume", 0) or 0))
        for idx, row in df.iterrows()
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO candles "
        "(symbol, source, interval, timestamp, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    report["accepted"] = True
    report["rows_inserted"] = len(rows)
    return report


def resample_and_store(symbol: str, from_interval: str, to_interval: str,
                        db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Resample already-ingested candles (e.g. 1m -> 5m) via proper OHLCV
    aggregation (open=first, high=max, low=min, close=last, volume=sum) and
    store the result under the SAME symbol/source with the new interval."""
    conn = _init_db(db_path)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND source=? AND interval=? ORDER BY timestamp",
        conn, params=(symbol.upper(), SOURCE_TAG, from_interval),
    )
    if df.empty:
        conn.close()
        return {"error": f"no {from_interval} candles found for {symbol}"}

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    freq_map = {"5m": "5min", "15m": "15min", "1h": "1h", "1d": "1D"}
    freq = freq_map.get(to_interval)
    if freq is None:
        conn.close()
        return {"error": f"unsupported target interval {to_interval!r}"}

    resampled = df.resample(freq, origin="start_day").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])

    rows = [
        (symbol.upper(), SOURCE_TAG, to_interval, str(idx),
         float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
         float(r["volume"]))
        for idx, r in resampled.iterrows()
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO candles "
        "(symbol, source, interval, timestamp, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return {"symbol": symbol, "from_interval": from_interval,
            "to_interval": to_interval, "rows_stored": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--also-resample-to", default="5m")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    report = load_csv(args.symbol, args.csv, interval=args.interval)
    print(report)
    if report.get("accepted") and args.also_resample_to:
        resample_report = resample_and_store(args.symbol, args.interval, args.also_resample_to)
        print(resample_report)
    return 0 if report.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
