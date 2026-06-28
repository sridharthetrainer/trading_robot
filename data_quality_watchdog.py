#!/usr/bin/env python3
"""Audit local candle cache quality for EOD training readiness."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd

from trading_calendar import session_lag


REPORT_JSON = "data_quality_watchdog_report.json"


def _expected(interval: str) -> float:
    return {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}.get(interval, 0)


def _min_bars(interval: str) -> int:
    return {"1d": 3, "1h": 5, "15m": 5, "5m": 5, "1m": 5}.get(interval, 5)


def _age_days(ts: str) -> float:
    try:
        last = pd.Timestamp(ts)
        if last.tzinfo is not None:
            last = last.tz_convert("Asia/Kolkata").tz_localize(None)
        return max(0.0, (pd.Timestamp.now() - last).total_seconds() / 86400.0)
    except Exception:
        return 9999.0


def audit_candle_cache(
    db_path: str = "candle_cache.db",
    *,
    max_intraday_age_days: float | None = None,
) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {"ok": False, "reason": "candle_cache_missing", "checks": []}
    if max_intraday_age_days is None:
        max_intraday_age_days = float(os.getenv("DATA_WATCHDOG_MAX_INTRADAY_AGE_DAYS", "1.5"))
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
        age_days = _age_days(last_ts)
        lag_sessions = session_lag(last_ts)
        # Wall-clock age incorrectly marks every Friday snapshot stale on Sunday
        # and every pre-holiday snapshot stale during a long exchange holiday.
        freshness_ok = bool(interval == "1d" or lag_sessions == 0)
        checks.append({
            "symbol": symbol,
            "interval": interval,
            "bars": int(count or 0),
            "first": first_ts,
            "last": last_ts,
            "age_days": round(age_days, 3),
            "session_lag": int(lag_sessions),
            "median_spacing_min": round(median, 3),
            "spacing_ok": spacing_ok,
            "freshness_ok": freshness_ok,
            "min_bars": _min_bars(interval),
            "ok": bool(int(count or 0) >= _min_bars(interval) and spacing_ok and freshness_ok),
        })
    conn.close()
    bad = [c for c in checks if not c.get("ok")]
    stale = [c for c in checks if not c.get("freshness_ok")]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": len(checks) > 0,
        "total_groups": len(checks),
        "bad_groups": len(bad),
        "stale_groups": len(stale),
        "max_intraday_age_days": max_intraday_age_days,
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
        "stale_groups": report.get("stale_groups", 0),
        "total_bars": report.get("total_bars", 0),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
