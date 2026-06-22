#!/usr/bin/env python3
"""Remove invalid OHLC placeholder rows from candle_cache.db."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable


DB_PATH = "candle_cache.db"
REPORT_FILE = "prune_invalid_candles_report.json"

INVALID_WHERE = """
open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
OR high < open OR high < close OR low > open OR low > close OR high < low
"""


def prune_invalid_candles(
    *,
    db_path: str = DB_PATH,
    report_file: str = REPORT_FILE,
    dry_run: bool = False,
    write: bool = True,
) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {"ok": False, "reason": "candle_cache_missing", "db_path": db_path}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        before_total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        invalid_by_interval = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT interval, COUNT(*) AS invalid_rows, COUNT(DISTINCT symbol) AS symbols
                  FROM candles
                 WHERE {INVALID_WHERE}
                 GROUP BY interval
                 ORDER BY interval
                """
            ).fetchall()
        ]
        invalid_total = int(sum(int(r["invalid_rows"] or 0) for r in invalid_by_interval))
        deleted = 0
        if not dry_run and invalid_total:
            cur = conn.execute(f"DELETE FROM candles WHERE {INVALID_WHERE}")
            deleted = int(cur.rowcount or 0)
            conn.execute(
                """
                UPDATE cache_meta
                   SET bar_count = (
                       SELECT COUNT(*)
                         FROM candles c
                        WHERE c.symbol = cache_meta.symbol
                          AND c.interval = cache_meta.interval
                   ),
                       last_update = ?
                """,
                (time.strftime("%Y-%m-%dT%H:%M:%S%z"),),
            )
            conn.execute("DELETE FROM cache_meta WHERE bar_count <= 0")
            conn.execute("PRAGMA optimize")
            conn.commit()
        after_total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        valid_symbols = [
            dict(r)
            for r in conn.execute(
                """
                SELECT interval, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols
                  FROM candles
                 GROUP BY interval
                 ORDER BY interval
                """
            ).fetchall()
        ]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": True,
        "db_path": db_path,
        "dry_run": bool(dry_run),
        "before_total": int(before_total or 0),
        "invalid_total": invalid_total,
        "deleted": deleted,
        "after_total": int(after_total or 0),
        "invalid_by_interval": invalid_by_interval,
        "valid_by_interval": valid_symbols,
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--report", default=REPORT_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = prune_invalid_candles(
        db_path=args.db,
        report_file=args.report,
        dry_run=args.dry_run,
    )
    print(json.dumps({
        "ok": report.get("ok"),
        "dry_run": report.get("dry_run"),
        "invalid_total": report.get("invalid_total", 0),
        "deleted": report.get("deleted", 0),
        "after_total": report.get("after_total", 0),
    }, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
