#!/usr/bin/env python3
"""Backfill market-profile snapshots from local candle_cache.db."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

import pandas as pd

from market_profile_context import DB_PATH, build_market_profile_context


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_profile_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            snapshot_time TEXT NOT NULL,
            ts REAL NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '5m',
            price REAL DEFAULT 0,
            poc REAL DEFAULT 0,
            vah REAL DEFAULT 0,
            val REAL DEFAULT 0,
            hvn_json TEXT DEFAULT '[]',
            lvn_json TEXT DEFAULT '[]',
            value_width_pct REAL DEFAULT 0,
            poc_distance_pct REAL DEFAULT 0,
            vah_distance_pct REAL DEFAULT 0,
            val_distance_pct REAL DEFAULT 0,
            profile_position TEXT DEFAULT '',
            profile_bias TEXT DEFAULT 'NEUTRAL',
            acceptance_state TEXT DEFAULT '',
            score_modifier REAL DEFAULT 0,
            quality REAL DEFAULT 0,
            created_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_symbol_ts "
        "ON market_profile_snapshots(symbol, ts)"
    )
    conn.commit()


def _symbols(candle_db: str, interval: str, limit: int) -> List[str]:
    with sqlite3.connect(candle_db) as conn:
        rows = conn.execute(
            """
            SELECT symbol, COUNT(*) AS n
              FROM candles
             WHERE interval = ?
             GROUP BY symbol
             ORDER BY n DESC
             LIMIT ?
            """,
            (interval, int(limit)),
        ).fetchall()
    return [str(r[0]).upper() for r in rows]


def _daily_symbols(daily_db: str, limit: int) -> List[str]:
    with sqlite3.connect(daily_db) as conn:
        rows = conn.execute(
            """
            SELECT symbol, COUNT(*) AS n
              FROM ohlcv
             WHERE close > 0
             GROUP BY symbol
             HAVING n >= 20
             ORDER BY n DESC, symbol
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [str(r[0]).upper() for r in rows]


def _load_candles(candle_db: str, symbol: str, interval: str, max_bars: int) -> pd.DataFrame:
    with sqlite3.connect(candle_db) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
              FROM candles
             WHERE symbol = ? AND interval = ?
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (symbol.upper(), interval, int(max_bars)),
        ).fetchall()
    data = []
    for r in reversed(rows):
        ts = pd.Timestamp(r[0])
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
        data.append({
            "date": ts,
            "open": float(r[1] or 0),
            "high": float(r[2] or 0),
            "low": float(r[3] or 0),
            "close": float(r[4] or 0),
            "volume": float(r[5] or 0),
        })
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data).set_index("date").sort_index()


def _load_daily(daily_db: str, symbol: str, max_bars: int) -> pd.DataFrame:
    with sqlite3.connect(daily_db) as conn:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume
              FROM ohlcv
             WHERE symbol = ? AND close > 0
             ORDER BY date DESC
             LIMIT ?
            """,
            (symbol.upper(), int(max_bars)),
        ).fetchall()
    data = [
        {
            "date": pd.Timestamp(r[0]),
            "open": float(r[1] or 0),
            "high": float(r[2] or 0),
            "low": float(r[3] or 0),
            "close": float(r[4] or 0),
            "volume": float(r[5] or 0),
        }
        for r in reversed(rows)
    ]
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data).set_index("date").sort_index()


def _existing_keys(conn: sqlite3.Connection, symbol: str, timeframe: str) -> set[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT symbol, CAST(ts AS INTEGER)
          FROM market_profile_snapshots
         WHERE symbol = ? AND timeframe = ?
        """,
        (symbol.upper(), timeframe),
    ).fetchall()
    return {(str(r[0]).upper(), int(r[1])) for r in rows}


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    timeframe: str,
    ts: pd.Timestamp,
    ctx: dict,
) -> None:
    ts_naive = ts.tz_convert(None) if ts.tzinfo is not None else ts
    epoch = float(ts.timestamp())
    conn.execute(
        """
        INSERT INTO market_profile_snapshots
        (snapshot_date, snapshot_time, ts, symbol, timeframe, price, poc, vah, val,
         hvn_json, lvn_json, value_width_pct, poc_distance_pct, vah_distance_pct,
         val_distance_pct, profile_position, profile_bias, acceptance_state,
         score_modifier, quality, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ts_naive.strftime("%Y-%m-%d"),
            ts_naive.strftime("%H:%M:%S"),
            epoch,
            symbol.upper(),
            timeframe,
            float(ctx.get("price", 0) or 0),
            float(ctx.get("poc", 0) or 0),
            float(ctx.get("vah", 0) or 0),
            float(ctx.get("val", 0) or 0),
            json.dumps(ctx.get("hvn") or []),
            json.dumps(ctx.get("lvn") or []),
            float(ctx.get("value_width_pct", 0) or 0),
            float(ctx.get("poc_distance_pct", 0) or 0),
            float(ctx.get("vah_distance_pct", 0) or 0),
            float(ctx.get("val_distance_pct", 0) or 0),
            str(ctx.get("profile_position", "")),
            str(ctx.get("profile_bias", "NEUTRAL")),
            str(ctx.get("acceptance_state", "")),
            float(ctx.get("score_modifier", 0) or 0),
            float(ctx.get("quality", 0) or 0),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def backfill(
    *,
    candle_db: str = "candle_cache.db",
    profile_db: str = DB_PATH,
    interval: str = "5m",
    daily_db: str = "nse_cache.db",
    source: str = "auto",
    symbol_limit: int = 80,
    max_bars_per_symbol: int = 1200,
    window: int = 80,
    step: int = 20,
    max_insert: int = 1500,
) -> dict:
    use_daily = source == "daily" or (
        source == "auto"
        and Path(daily_db).exists()
        and _daily_symbols(daily_db, 1)
    )
    if use_daily:
        symbols = _daily_symbols(daily_db, symbol_limit)
        source_label = "daily_nse_cache"
        timeframe = "1d"
    else:
        if not Path(candle_db).exists():
            return {"ok": False, "reason": "candle_cache_missing", "inserted": 0}
        symbols = _symbols(candle_db, interval, symbol_limit)
        source_label = "candle_cache"
        timeframe = interval
    inserted = 0
    seen_symbols = 0
    started = time.time()
    with sqlite3.connect(profile_db, timeout=30) as conn:
        _ensure_table(conn)
        for symbol in symbols:
            if use_daily:
                df = _load_daily(daily_db, symbol, max_bars_per_symbol)
            else:
                df = _load_candles(candle_db, symbol, interval, max_bars_per_symbol)
            if len(df) < window:
                continue
            seen_symbols += 1
            existing = _existing_keys(conn, symbol, timeframe)
            for end in range(window, len(df) + 1, step):
                ts = pd.Timestamp(df.index[end - 1])
                key = (symbol.upper(), int(ts.timestamp()))
                if key in existing:
                    continue
                window_df = df.iloc[end - window:end]
                ctx = build_market_profile_context(
                    window_df,
                    symbol=symbol,
                    side="",
                    timeframe=timeframe,
                    persist=False,
                )
                if not ctx.get("available"):
                    continue
                _insert_snapshot(conn, symbol=symbol, timeframe=timeframe, ts=ts, ctx=ctx)
                inserted += 1
                if inserted % 100 == 0:
                    conn.commit()
                if inserted >= max_insert:
                    break
            if inserted >= max_insert:
                break
        conn.commit()
    return {
        "ok": True,
        "symbols_seen": seen_symbols,
        "inserted": inserted,
        "duration_sec": round(time.time() - started, 2),
        "profile_db": profile_db,
        "source": source_label,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol-limit", type=int, default=80)
    parser.add_argument("--source", choices=["auto", "daily", "candle"], default="auto")
    parser.add_argument("--max-bars", type=int, default=1200)
    parser.add_argument("--window", type=int, default=80)
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument("--max-insert", type=int, default=1500)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = backfill(
        symbol_limit=args.symbol_limit,
        source=args.source,
        max_bars_per_symbol=args.max_bars,
        window=args.window,
        step=args.step,
        max_insert=args.max_insert,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
