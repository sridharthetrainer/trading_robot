#!/usr/bin/env python3
"""Audit local candle cache quality for EOD training readiness."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd

from trading_calendar import session_lag

logger = logging.getLogger(__name__)

REPORT_JSON = "data_quality_watchdog_report.json"


def quarantine_invalid_candles(db_path: str = "candle_cache.db") -> Dict[str, Any]:
    """Quarantine structurally impossible bars; never erase merely stale data."""
    if not Path(db_path).exists():
        return {"ok": False, "reason": "candle_cache_missing", "quarantined": 0}
    predicate = """
        open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
        OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
        OR high < open OR high < close OR high < low
        OR low > open OR low > close OR low > high
        OR volume IS NULL OR volume < 0
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candle_quarantine (
                symbol TEXT, interval TEXT, timestamp TEXT, open REAL, high REAL,
                low REAL, close REAL, volume INTEGER, reason TEXT,
                quarantined_at TEXT
            )
        """)
        count = int(conn.execute(f"SELECT COUNT(*) FROM candles WHERE {predicate}").fetchone()[0])
        if count:
            conn.execute(
                f"""INSERT INTO candle_quarantine
                    SELECT symbol, interval, timestamp, open, high, low, close,
                           volume, 'invalid_ohlcv', datetime('now')
                      FROM candles WHERE {predicate}"""
            )
            conn.execute(f"DELETE FROM candles WHERE {predicate}")
    return {"ok": True, "quarantined": count, "stale_rows_deleted": 0}


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


def _audit_candle_cache(
    db_path: str = "candle_cache.db",
    *,
    max_intraday_age_days: float | None = None,
    symbols: Iterable[str] | None = None,
    write: bool = True,
    _connection=None,
) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {"ok": False, "reason": "candle_cache_missing", "checks": []}
    if max_intraday_age_days is None:
        max_intraday_age_days = float(os.getenv("DATA_WATCHDOG_MAX_INTRADAY_AGE_DAYS", "1.5"))
    conn = _connection
    requested_symbols = (
        {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
        if symbols is not None else None
    )
    parameters = sorted(requested_symbols) if requested_symbols is not None else []
    where = ("WHERE symbol IN (" + ",".join("?" for _ in parameters) + ")") if parameters else ("WHERE 0" if requested_symbols is not None else "")
    groups = conn.execute(
        f"SELECT symbol, interval, COUNT(*), MIN(timestamp), MAX(timestamp) FROM candles {where} GROUP BY symbol, interval",
        parameters,
    ).fetchall()
    all_group_count = len(groups)
    missing_symbols = sorted((requested_symbols or set()) - {row[0] for row in groups})
    checks: List[Dict[str, Any]] = []
    for symbol, interval, count, first_ts, last_ts in groups:
        rows = conn.execute(
            "SELECT timestamp FROM candles WHERE symbol=? AND interval=? ORDER BY timestamp DESC LIMIT 2000",
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
        # Express the configured wall-clock allowance as missed exchange
        # sessions. This applies the limit without falsely ageing Friday data
        # over a weekend or an exchange holiday. Daily bars are not exempt.
        allowed_session_lag = max(0, int(float(max_intraday_age_days)))
        freshness_ok = bool(lag_sessions <= allowed_session_lag)
        checks.append({
            "symbol": symbol,
            "interval": interval,
            "bars": int(count or 0),
            "first": first_ts,
            "last": last_ts,
            "age_days": round(age_days, 3),
            "session_lag": int(lag_sessions),
            "allowed_session_lag": allowed_session_lag,
            "median_spacing_min": round(median, 3),
            "spacing_sample_bars": len(idx),
            "spacing_scope": "latest_2000_bars",
            "spacing_ok": spacing_ok,
            "freshness_ok": freshness_ok,
            "min_bars": _min_bars(interval),
            "ok": bool(int(count or 0) >= _min_bars(interval) and spacing_ok and freshness_ok),
        })
    bad = [c for c in checks if not c.get("ok")]
    stale = [c for c in checks if not c.get("freshness_ok")]
    zero_volume_by_interval = {}
    for interval, zero_rows, total_rows in conn.execute(
        f"SELECT interval, SUM(CASE WHEN volume=0 THEN 1 ELSE 0 END), COUNT(*) FROM candles {where} GROUP BY interval", parameters
    ):
        zero_volume_by_interval[str(interval)] = {
            "rows": int(zero_rows or 0),
            "total": int(total_rows or 0),
            "pct": round(100.0 * int(zero_rows or 0) / max(int(total_rows or 0), 1), 2),
        }
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        # Was `len(checks) > 0` -- true as soon as ANY group existed, regardless
        # of how many were bad/stale. A report with 1 group that failed every
        # check still said ok=true. Consumers (autonomous_learning_cycle's step
        # status) read this boolean directly, so it must reflect whether the
        # checks actually passed, not merely whether the audit ran.
        "ok": len(checks) > 0 and len(bad) == 0 and not missing_symbols,
        "missing_symbols": missing_symbols,
        "total_groups": len(checks),
        "all_cache_groups": all_group_count if requested_symbols is None else None,
        "ignored_out_of_scope_groups": 0 if requested_symbols is None else None,
        "scope": "selected_symbols" if requested_symbols is not None else "all_cache_symbols",
        "requested_symbols": len(requested_symbols) if requested_symbols is not None else None,
        "bad_groups": len(bad),
        "stale_groups": len(stale),
        "max_intraday_age_days": max_intraday_age_days,
        "total_bars": sum(int(c.get("bars", 0)) for c in checks),
        # Zero volume is valid for indices and occasional illiquid bars, so it
        # is audited rather than deleted. Consumers can now distinguish it from
        # structurally invalid OHLCV.
        "zero_volume_by_interval": zero_volume_by_interval,
        "checks": checks,
    }
    # 2026-07-14: this used to only get written by main()'s CLI path — the
    # nightly autonomous_learning_cycle step calls this function directly,
    # so REPORT_JSON sat 13 days stale despite the check itself running (and
    # recomputing correctly) every night. Every caller now gets a fresh file
    # by default; pass write=False (or --no-write on the CLI) to suppress.
    if write:
        try:
            Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.debug("data_quality_watchdog report write: %s", exc)
    return report


def audit_candle_cache(db_path: str = "candle_cache.db", *, max_intraday_age_days=None,
                       symbols=None, write: bool = True, query_budget: float | None = None) -> Dict[str, Any]:
    """Bounded read-only audit; incomplete scans cannot report ready data."""
    if not Path(db_path).exists():
        return {"ok": False, "reason": "candle_cache_missing", "checks": []}
    if query_budget is None:
        # The 9-million-row production cache takes over 40s to audit on this
        # host. The former 15s deadline prevented any completed quality report.
        query_budget = float(os.getenv("DATA_WATCHDOG_QUERY_BUDGET_SEC", "60"))
    from db_util import db_conn
    try:
        with db_conn(db_path, read_only=True, timeout=2, query_budget=query_budget) as conn:
            return _audit_candle_cache(db_path, max_intraday_age_days=max_intraday_age_days,
                                      symbols=symbols, write=write, _connection=conn)
    except sqlite3.DatabaseError as exc:
        report = {"ok": False, "reason": "incomplete_database_audit", "error": str(exc),
                  "checks": [], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if write:
            Path(REPORT_JSON).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--query-budget", type=float, default=None, help="SQLite audit budget in seconds (default: DATA_WATCHDOG_QUERY_BUDGET_SEC or 60)")
    parser.add_argument("--repair", action="store_true", help="quarantine invalid OHLCV rows before auditing")
    args = parser.parse_args(list(argv) if argv is not None else None)
    repair = quarantine_invalid_candles() if args.repair else None
    report = audit_candle_cache(write=not args.no_write, query_budget=args.query_budget)
    if repair is not None:
        report["repair"] = repair
        if not args.no_write:
            Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "ok": report.get("ok"),
        "total_groups": report.get("total_groups", 0),
        "bad_groups": report.get("bad_groups", 0),
        "stale_groups": report.get("stale_groups", 0),
        "total_bars": report.get("total_bars", 0),
    }, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
