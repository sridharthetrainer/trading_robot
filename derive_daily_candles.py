#!/usr/bin/env python3
"""Derive daily candles from intraday candle_cache rows.

Broker daily backfills can fail or be rate-limited, but the system already
stores broad 5m/15m history. This script converts valid intraday OHLCV into
`1d` candles inside candle_cache.db so daily/weekly/monthly context is available
for scanners, ML features, and EOD training.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd


DB_PATH = "candle_cache.db"
REPORT_FILE = "derived_daily_candles_report.json"


def _valid_ohlc(row: pd.Series) -> bool:
    try:
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    except Exception:
        return False
    return min(o, h, l, c) > 0 and h >= max(o, c) and l <= min(o, c) and h >= l


def _load_symbols(conn: sqlite3.Connection, source_interval: str) -> List[str]:
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM candles WHERE interval=? ORDER BY symbol",
        (source_interval,),
    ).fetchall()
    return [str(r[0]).upper() for r in rows]


def _load_intraday(conn: sqlite3.Connection, symbol: str, source_interval: str) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT timestamp, open, high, low, close, volume
          FROM candles
         WHERE symbol=? AND interval=?
           AND open > 0 AND high > 0 AND low > 0 AND close > 0
         ORDER BY timestamp
        """,
        (symbol.upper(), source_interval),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df[df.apply(_valid_ohlc, axis=1)]


def _derive_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    daily = (
        df.resample("1D")
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        .dropna(subset=["open", "high", "low", "close"])
    )
    daily = daily[daily.apply(_valid_ohlc, axis=1)]
    return daily


def derive_daily_candles(
    *,
    db_path: str = DB_PATH,
    source_interval: str = "5m",
    symbols: Iterable[str] | None = None,
    min_days: int = 2,
    write: bool = True,
    report_file: str = REPORT_FILE,
) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {"ok": False, "reason": "candle_cache_missing", "db_path": db_path}
    conn = sqlite3.connect(db_path)
    try:
        selected = [str(s).strip().upper() for s in symbols or _load_symbols(conn, source_interval) if str(s).strip()]
        per_symbol = []
        inserted_total = 0
        skipped = 0
        for symbol in selected:
            intraday = _load_intraday(conn, symbol, source_interval)
            daily = _derive_daily(intraday)
            if len(daily) < int(min_days):
                skipped += 1
                per_symbol.append({"symbol": symbol, "ok": False, "days": len(daily), "reason": "too_few_days"})
                continue
            inserted = 0
            for ts, row in daily.iterrows():
                day_ts = pd.Timestamp(ts).normalize()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO candles
                    (symbol, interval, timestamp, open, high, low, close, volume)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        symbol,
                        "1d",
                        str(day_ts),
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        int(row.get("volume", 0) or 0),
                    ),
                )
                inserted += 1
            conn.execute(
                """
                INSERT OR REPLACE INTO cache_meta
                (symbol, interval, last_update, bar_count)
                VALUES (?,?,?,?)
                """,
                (symbol, "1d", time.strftime("%Y-%m-%dT%H:%M:%S%z"), inserted),
            )
            inserted_total += inserted
            per_symbol.append({"symbol": symbol, "ok": True, "days": inserted})
        conn.commit()
    finally:
        conn.close()
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": True,
        "db_path": db_path,
        "source_interval": source_interval,
        "symbols_requested": len(selected),
        "symbols_ok": sum(1 for row in per_symbol if row.get("ok")),
        "symbols_skipped": skipped,
        "inserted_rows": inserted_total,
        "per_symbol": per_symbol,
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--source-interval", default="5m")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--min-days", type=int, default=2)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    report = derive_daily_candles(
        db_path=args.db,
        source_interval=args.source_interval,
        symbols=symbols,
        min_days=args.min_days,
        write=not args.no_write,
    )
    print(json.dumps({
        "ok": report.get("ok"),
        "source_interval": report.get("source_interval"),
        "symbols_ok": report.get("symbols_ok", 0),
        "symbols_skipped": report.get("symbols_skipped", 0),
        "inserted_rows": report.get("inserted_rows", 0),
    }, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
