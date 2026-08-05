#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from derive_daily_candles import derive_daily_candles


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE candles (
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
            """
        )
        conn.execute(
            """
            CREATE TABLE cache_meta (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                last_update TEXT,
                bar_count INTEGER,
                UNIQUE(symbol, interval)
            )
            """
        )
        rows = [
            ("TEST", "1m", "2026-06-18 09:15:00+05:30", 100, 102, 99, 101, 10),
            ("TEST", "1m", "2026-06-18 15:29:00+05:30", 101, 105, 100, 104, 20),
            ("TEST", "1m", "2026-06-19 09:15:00+05:30", 104, 106, 103, 105, 30),
            ("TEST", "1m", "2026-06-19 15:29:00+05:30", 105, 108, 104, 107, 40),
        ]
        conn.executemany(
            "INSERT INTO candles(symbol, interval, timestamp, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def test_derive_daily_candles_from_intraday_cache() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "candles.db"
        _make_db(db)
        report = derive_daily_candles(db_path=str(db), source_interval="1m", write=False)
        assert report["ok"]
        assert report["symbols_ok"] == 1
        assert report["inserted_rows"] == 2
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT timestamp, open, high, low, close, volume FROM candles WHERE interval='1d' ORDER BY timestamp"
            ).fetchall()
        assert len(rows) == 2
        assert rows[0][1:] == (100.0, 105.0, 99.0, 104.0, 30)
        assert rows[1][1:] == (104.0, 108.0, 103.0, 107.0, 70)


def test_derive_daily_removes_contaminating_intraday_rows() -> None:
    """A prior bad 1d cache row cannot survive a later derived refresh."""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "candles.db"
        _make_db(db)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
                ("TEST", "1d", "2026-06-18 09:15:00+05:30", 1, 1, 1, 1, 1),
            )
            conn.execute(
                "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
                ("TEST", "1d", "2026-06-01 00:00:00+05:30", 90, 91, 89, 90, 1),
            )
        report = derive_daily_candles(db_path=str(db), source_interval="1m", write=False)
        assert report["removed_non_daily_rows"] == 1
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT timestamp FROM candles WHERE interval='1d' ORDER BY timestamp"
            ).fetchall()
        assert "2026-06-18 09:15:00+05:30" not in {row[0] for row in rows}
        assert "2026-06-01 00:00:00+05:30" in {row[0] for row in rows}


def main() -> int:
    try:
        test_derive_daily_candles_from_intraday_cache()
        test_derive_daily_removes_contaminating_intraday_rows()
    except Exception as exc:
        print(f"FAIL derive daily candles: {exc}")
        return 1
    print("PASS derive daily candles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
