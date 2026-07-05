#!/usr/bin/env python3
"""
candle_coverage_backfill.py

Autonomous candle-cache coverage expansion.

The live scanner can work from 5m data, but ML/structure learning needs broader
1m and 1d coverage too. This module fetches the full learning universe by
interval with interval-specific lookbacks and stores results through the normal
DataFetcher/candle_cache path.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPORT_JSON = "candle_coverage_backfill_report.json"
COVERAGE_PLAN_JSON = "candle_coverage_plan.json"
DEFAULT_INTERVALS = ["1m", "5m", "15m", "1h", "1d"]
DEFAULT_PLAN_INTERVALS = ["1m", "1d"]
DEFAULT_DAYS = {
    "1m": 5,
    "5m": 15,
    "15m": 30,
    "1h": 60,
    "1d": 365,
}


def _intervals_from_env() -> List[str]:
    raw = os.getenv("CANDLE_COVERAGE_INTERVALS", ",".join(DEFAULT_INTERVALS))
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _days_for(interval: str) -> int:
    key = f"CANDLE_COVERAGE_DAYS_{interval.upper().replace('M', 'MIN').replace('H', 'HOUR').replace('D', 'DAY')}"
    if key in os.environ:
        return max(1, int(os.getenv(key, str(DEFAULT_DAYS.get(interval, 5))) or 1))
    generic = os.getenv(f"CANDLE_COVERAGE_DAYS_{interval.upper()}", "")
    if generic:
        return max(1, int(generic))
    return int(DEFAULT_DAYS.get(interval, 5))


def _learning_symbols(max_symbols: int | None = None) -> List[str]:
    from data_fetcher import DataFetcher

    fetcher = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
    symbols = fetcher.get_ordered_symbols(include_full_universe=True)
    limit = max_symbols
    if limit is None:
        try:
            import config as cfg

            limit = int(getattr(cfg, "CANDLE_COVERAGE_MAX_SYMBOLS", 0) or 0)
            if limit <= 0:
                limit = int(getattr(cfg, "FULL_UNIVERSE_SCAN_MAX_SYMBOLS", len(symbols)) or len(symbols))
        except Exception:
            limit = len(symbols)
    return symbols[: max(1, int(limit or len(symbols)))]


def _cache_snapshot(db_path: str = "candle_cache.db") -> Dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {"exists": False, "total": 0, "intervals": {}}
    try:
        with sqlite3.connect(path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
            rows = conn.execute(
                """
                SELECT interval, COUNT(*) rows, COUNT(DISTINCT symbol) symbols,
                       MIN(timestamp), MAX(timestamp)
                  FROM candles
                 GROUP BY interval
                """
            ).fetchall()
    except Exception as exc:
        return {"exists": True, "error": str(exc), "total": 0, "intervals": {}}
    return {
        "exists": True,
        "total": int(total or 0),
        "intervals": {
            str(r[0]): {
                "rows": int(r[1] or 0),
                "symbols": int(r[2] or 0),
                "first": r[3],
                "last": r[4],
            }
            for r in rows
        },
    }


def build_candle_coverage_plan(
    *,
    symbols: Iterable[str] | None = None,
    intervals: Iterable[str] | None = None,
    db_path: str = "candle_cache.db",
    batch_size: int | None = None,
    write: bool = True,
) -> Dict[str, Any]:
    """
    Build an offline plan for missing candle coverage.

    This is intentionally read-only: it does not call Angel/NSE. The daily
    pipeline can run it safely after market close to tell the next autonomous
    backfill cycle exactly which symbols and intervals need attention.
    """
    selected_symbols = [
        str(s).strip().upper()
        for s in (symbols or _learning_symbols(None))
        if str(s).strip()
    ]
    selected_intervals = [
        str(i).strip().lower()
        for i in (intervals or DEFAULT_PLAN_INTERVALS)
        if str(i).strip()
    ]
    if batch_size is None:
        batch_size = max(1, int(os.getenv("CANDLE_COVERAGE_PLAN_BATCH_SIZE", "25") or 25))

    from trading_calendar import latest_expected_session

    expected_session = latest_expected_session().isoformat()
    present_by_interval: Dict[str, set[str]] = {interval: set() for interval in selected_intervals}
    fresh_by_interval: Dict[str, set[str]] = {interval: set() for interval in selected_intervals}
    db_exists = Path(db_path).exists()
    if db_exists:
        try:
            placeholders = ",".join("?" for _ in selected_intervals)
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    f"""
                    SELECT interval, symbol, MAX(timestamp)
                      FROM candles
                     WHERE interval IN ({placeholders})
                       AND open > 0 AND high > 0 AND low > 0 AND close > 0
                     GROUP BY interval, symbol
                    """,
                    selected_intervals,
                ).fetchall()
            for interval, symbol, last_ts in rows:
                key = str(interval).lower()
                if key in present_by_interval:
                    present_by_interval[key].add(str(symbol).upper())
                    if str(last_ts or "")[:10] >= expected_session:
                        fresh_by_interval[key].add(str(symbol).upper())
        except Exception:
            present_by_interval = {interval: set() for interval in selected_intervals}

    interval_plans = []
    all_missing: Dict[str, List[str]] = {}
    for interval in selected_intervals:
        present = present_by_interval.get(interval, set())
        missing = [symbol for symbol in selected_symbols if symbol not in present]
        stale = [
            symbol for symbol in selected_symbols
            if symbol in present and symbol not in fresh_by_interval.get(interval, set())
        ]
        repair = missing + stale
        all_missing[interval] = missing
        interval_plans.append({
            "interval": interval,
            "target_symbols": len(selected_symbols),
            "present_symbols": len(present),
            "missing_symbols": len(missing),
            "stale_symbols": len(stale),
            "stale": stale,
            "coverage_pct": round(100.0 * (len(selected_symbols) - len(missing)) / max(len(selected_symbols), 1), 2),
            "priority_missing": missing[:batch_size],
            "recommended_command": (
                ".venv/bin/python3 candle_coverage_backfill.py "
                f"--intervals {interval} --symbols {','.join(repair[:batch_size])}"
                if repair else ""
            ),
        })

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_path": db_path,
        "db_exists": db_exists,
        "target_symbols": len(selected_symbols),
        "intervals": selected_intervals,
        "batch_size": int(batch_size),
        "latest_expected_session": expected_session,
        "interval_plans": interval_plans,
        "missing_by_interval": all_missing,
        "next_actions": [
            {
                "interval": row["interval"],
                "missing_symbols": row["missing_symbols"],
                "priority_missing": row["priority_missing"],
                "command": row["recommended_command"],
            }
            for row in interval_plans
            if row["missing_symbols"] > 0 or row["stale_symbols"] > 0
        ],
    }
    if write:
        Path(COVERAGE_PLAN_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def run_candle_coverage_backfill(
    *,
    symbols: Iterable[str] | None = None,
    intervals: Iterable[str] | None = None,
    max_symbols: int | None = None,
    write: bool = True,
) -> Dict[str, Any]:
    from intraday_candle_recorder import record_intraday_candles

    logging.getLogger("smartConnect").setLevel(logging.CRITICAL)
    logging.getLogger("SmartApi").setLevel(logging.CRITICAL)
    selected_symbols = [str(s).strip().upper() for s in (symbols or _learning_symbols(max_symbols)) if str(s).strip()]
    selected_intervals = [str(i).strip().lower() for i in (intervals or _intervals_from_env()) if str(i).strip()]
    before = _cache_snapshot()
    started = time.time()
    interval_reports = []
    for interval in selected_intervals:
        days = _days_for(interval)
        report = record_intraday_candles(
            symbols=selected_symbols,
            intervals=[interval],
            days=days,
            max_symbols=len(selected_symbols),
            write=False,
        )
        interval_reports.append({
            "interval": interval,
            "days": days,
            "requested": report.get("requested", 0),
            "ok_count": report.get("ok_count", 0),
            "sample_failures": [
                {
                    "symbol": row.get("symbol"),
                    "reason": row.get("reason", ""),
                    "bars": row.get("bars", 0),
                }
                for row in (report.get("results", []) or [])
                if not row.get("ok")
            ][:10],
        })
    after = _cache_snapshot()
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "symbols_requested": len(selected_symbols),
        "intervals": selected_intervals,
        "duration_sec": round(time.time() - started, 3),
        "before": before,
        "after": after,
        "interval_reports": interval_reports,
    }
    if write:
        Path(REPORT_JSON).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def render_summary(report: Dict[str, Any]) -> str:
    after = report.get("after", {}) if isinstance(report, dict) else {}
    intervals = after.get("intervals", {}) if isinstance(after.get("intervals"), dict) else {}
    lines = [
        "CANDLE COVERAGE BACKFILL",
        f"symbols={report.get('symbols_requested', 0)} intervals={','.join(report.get('intervals', []) or [])}",
    ]
    for interval in report.get("interval_reports", []) or []:
        stats = intervals.get(interval.get("interval"), {})
        lines.append(
            f"{interval.get('interval')} ok={interval.get('ok_count', 0)}/{interval.get('requested', 0)} "
            f"cache_symbols={stats.get('symbols', 0)} rows={stats.get('rows', 0)}"
        )
    return "\n".join(lines)


def render_plan_summary(report: Dict[str, Any]) -> str:
    lines = [
        "CANDLE COVERAGE PLAN",
        f"target_symbols={report.get('target_symbols', 0)} intervals={','.join(report.get('intervals', []) or [])}",
    ]
    for row in report.get("interval_plans", []) or []:
        lines.append(
            f"{row.get('interval')} coverage={row.get('coverage_pct', 0)}% "
            f"present={row.get('present_symbols', 0)} missing={row.get('missing_symbols', 0)}"
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="")
    parser.add_argument("--intervals", default="")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    intervals = [i.strip().lower() for i in args.intervals.split(",") if i.strip()] or None
    if args.plan_only:
        report = build_candle_coverage_plan(
            symbols=symbols,
            intervals=intervals or DEFAULT_PLAN_INTERVALS,
            write=not args.no_write,
        )
        print(render_plan_summary(report))
        return 0
    report = run_candle_coverage_backfill(
        symbols=symbols,
        intervals=intervals,
        max_symbols=args.max_symbols,
        write=not args.no_write,
    )
    print(render_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
