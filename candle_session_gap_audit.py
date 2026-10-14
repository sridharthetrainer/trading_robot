"""Detect internal candle gaps that a latest-bar freshness check cannot see."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

REPORT = Path("candle_session_gap_report.json")


def audit(*, db_path: str = "candle_cache.db", symbols: list[str] | None = None,
          intervals: tuple[str, ...] = ("1m", "5m", "15m", "1h"), days: int = 45,
          write: bool = True) -> dict:
    if symbols is None:
        from candle_coverage_backfill import _learning_symbols
        symbols = _learning_symbols(None)
    symbols = sorted({str(symbol).upper() for symbol in symbols})
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    placeholders = ",".join("?" for _ in symbols)
    with sqlite3.connect(db_path) as conn:
        sessions = [row[0] for row in conn.execute(
            f"""SELECT substr(timestamp,1,10) session
                  FROM candles WHERE interval='1d' AND symbol IN ({placeholders})
                   AND timestamp>=? GROUP BY session
                HAVING COUNT(DISTINCT symbol) >= ? ORDER BY session""",
            [*symbols, cutoff, max(1, len(symbols) // 2)],
        )]
        present = {
            (row[0], row[1], row[2])
            for row in conn.execute(
                f"""SELECT symbol, interval, substr(timestamp,1,10)
                      FROM candles WHERE symbol IN ({placeholders})
                       AND interval IN ({','.join('?' for _ in intervals)})
                       AND timestamp>=? GROUP BY symbol,interval,substr(timestamp,1,10)""",
                [*symbols, *intervals, cutoff],
            )
        }
    missing = [
        {"symbol": symbol, "interval": interval, "session": session}
        for session in sessions for symbol in symbols for interval in intervals
        if (symbol, interval, session) not in present
    ]
    missing_sessions = sorted({item["session"] for item in missing})
    result = {
        "cutoff": cutoff, "symbols": len(symbols), "intervals": list(intervals),
        "expected_sessions": sessions, "missing_pairs": len(missing),
        "missing_sessions": missing_sessions, "sample": missing[:100],
        "ok": not missing,
        "recommended_command": (
            f".venv/bin/python outage_candle_backfill.py --from {missing_sessions[0]} "
            f"--to {missing_sessions[-1]}" if missing_sessions else ""
        ),
    }
    if write:
        REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    report = audit()
    print(json.dumps({key: report[key] for key in (
        "ok", "symbols", "expected_sessions", "missing_pairs",
        "missing_sessions", "recommended_command",
    )}, indent=2))
