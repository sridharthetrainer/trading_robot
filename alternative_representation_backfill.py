#!/usr/bin/env python3
"""Point-in-time backfill of representation features for stored signal rows."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from alternative_price_representations import FEATURE_NAMES, build_representation_features

REPORT_FILE = "alternative_representation_backfill.json"


def _signal_timestamp(date_value: Any, time_value: Any) -> pd.Timestamp | None:
    try:
        stamp = pd.Timestamp(f"{date_value} {time_value}")
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("Asia/Kolkata")
        return stamp.tz_convert("UTC")
    except Exception:
        return None


def _candles(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    interval_row = conn.execute(
        "SELECT interval,COUNT(*) AS n FROM candles WHERE symbol=? "
        "AND interval IN ('5m','1m','15m','1h') GROUP BY interval "
        "ORDER BY CASE interval WHEN '5m' THEN 1 WHEN '1m' THEN 2 "
        "WHEN '15m' THEN 3 ELSE 4 END LIMIT 1",
        (symbol,),
    ).fetchone()
    if not interval_row:
        return pd.DataFrame()
    interval = str(interval_row[0])
    rows = conn.execute(
        "SELECT timestamp,open,high,low,close,volume FROM candles "
        "WHERE symbol=? AND interval=? ORDER BY timestamp",
        (symbol, interval),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="mixed", errors="coerce", utc=True)
    return frame.dropna(subset=["timestamp"]).drop_duplicates("timestamp").sort_values("timestamp")


def backfill_representation_features(
    *,
    signal_db: str = "signal_log.db",
    candle_db: str = "candle_cache.db",
    limit: int = 0,
    report_file: str = REPORT_FILE,
) -> Dict[str, Any]:
    from signal_log import SignalLogger

    SignalLogger(db_path=signal_db)  # idempotent schema migration
    started = time.time()
    result: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seen": 0, "updated": 0, "missing_candles": 0,
        "insufficient_history": 0, "invalid_timestamp": 0,
        "preserved_training_classification": True,
    }
    if not Path(signal_db).exists() or not Path(candle_db).exists():
        return {**result, "ok": False, "reason": "database_missing"}

    with sqlite3.connect(signal_db, timeout=30) as signals, sqlite3.connect(candle_db, timeout=30) as candles:
        sql = (
            "SELECT id,symbol,signal_date,signal_time FROM signal_log "
            "WHERE COALESCE(representation_coverage,0)=0 ORDER BY id"
        )
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        pending = signals.execute(sql).fetchall()
        by_symbol: Dict[str, list] = {}
        for row in pending:
            by_symbol.setdefault(str(row[1] or "").upper(), []).append(row)
        assignments = ",".join(f"{name}=?" for name in FEATURE_NAMES)
        update_sql = f"UPDATE signal_log SET {assignments} WHERE id=?"
        for symbol, rows in by_symbol.items():
            frame = _candles(candles, symbol)
            if frame.empty:
                result["missing_candles"] += len(rows)
                result["seen"] += len(rows)
                continue
            timestamps = frame["timestamp"]
            for row_id, _symbol, signal_date, signal_time in rows:
                result["seen"] += 1
                stamp = _signal_timestamp(signal_date, signal_time)
                if stamp is None:
                    result["invalid_timestamp"] += 1
                    continue
                end = int(timestamps.searchsorted(stamp, side="right"))
                if end < 55:
                    result["insufficient_history"] += 1
                    continue
                features = build_representation_features(
                    frame.iloc[:end][["open", "high", "low", "close", "volume"]]
                )
                signals.execute(
                    update_sql,
                    [float(features[name]) for name in FEATURE_NAMES] + [int(row_id)],
                )
                result["updated"] += 1
        signals.commit()
    result["duration_sec"] = round(time.time() - started, 3)
    result["ok"] = True
    Path(report_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(backfill_representation_features(), indent=2))
