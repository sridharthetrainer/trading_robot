#!/usr/bin/env python3
"""Audit local candle cache quality for EOD training readiness."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd


REPORT_JSON = "data_quality_watchdog_report.json"


def _expected(interval: str) -> float:
    return {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}.get(interval, 0)


def audit_candle_cache(db_path: str = "candle_cache.db") -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {"ok": False, "reason": "candle_cache_missing", "checks": []}
    conn = sqlite3.connect(db_path)
    groups = conn.execute(
        "SELECT symbol, interval, COUNT(*), MIN(timestamp), MAX(timestamp) "
        "FROM candles GROUP BY symbol, interval"
    ).fetchall()
    checks: List[Dict[str, Any]] = []
    for symbol, interval, count, first_ts, last_ts in groups:
        rows = conn.execute(
            "SELECT timestamp FROM candles WHERE symbol=? AND interval=? ORDER BY timestamp",
            (symbol, interval),
        ).fetchall()
        idx = pd.to_datetime([r[0] for r in rows], errors="coerce")
        idx = idx[~pd.isna(idx)]
        median = 0.0
        spacing_ok = False
        if len(idx) >= 3:
            diffs = pd.Series(idx).sort_values().diff().dropna().dt.total_seconds() / 60.0
            diffs = diffs[diffs > 0]
            if len(diffs):
                median = float(diffs.median())
                exp = _expected(interval)
                spacing_ok = bool(exp and ((median <= exp * 3) if interval != "1d" else median >= 60))
        checks.append({
            "symbol": symbol,
            "interval": interval,
            "bars": int(count or 0),
            "first": first_ts,
            "last": last_ts,
            "median_spacing_min": round(median, 3),
            "spacing_ok": spacing_ok,
            "ok": bool(int(count or 0) >= 5 and spacing_ok),
        })
    conn.close()
    bad = [c for c in checks if not c.get("ok")]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": len(checks) > 0,
        "total_groups": len(checks),
        "bad_groups": len(bad),
        "total_bars": sum(int(c.get("bars", 0)) for c in checks),
        "checks": checks,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = audit_candle_cache()
    if not args.no_write:
        Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "ok": report.get("ok"),
        "total_groups": report.get("total_groups", 0),
        "bad_groups": report.get("bad_groups", 0),
        "total_bars": report.get("total_bars", 0),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
