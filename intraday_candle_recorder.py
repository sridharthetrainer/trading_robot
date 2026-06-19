#!/usr/bin/env python3
"""
intraday_candle_recorder.py

Capture and persist intraday candles during the trading day so EOD learning does
not depend on late broker refetches.

Run:
    .venv/bin/python intraday_candle_recorder.py
    .venv/bin/python intraday_candle_recorder.py --symbols NIFTY,BANKNIFTY --intervals 1m,5m,15m
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd


REPORT_JSON = "intraday_candle_recorder_report.json"


def _interval_spacing_ok(df, interval: str) -> tuple[bool, float]:
    expected = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "1d": 1440,
    }.get(str(interval or "").lower())
    if expected is None or df is None or len(df) < 3:
        return False, 0.0
    try:
        idx = pd.to_datetime(df.index, errors="coerce")
        idx = idx[~idx.isna()]
        diffs = pd.Series(idx).sort_values().diff().dropna().dt.total_seconds() / 60.0
        diffs = diffs[diffs > 0]
        if len(diffs) == 0:
            return False, 0.0
        median = float(diffs.median())
        if str(interval).lower() == "1d":
            return median >= 60, median
        return median <= expected * 3, median
    except Exception:
        return False, 0.0


def _default_symbols() -> List[str]:
    try:
        from universe_manager import probation_universe

        symbols = probation_universe()
        if symbols:
            return symbols
    except Exception:
        pass
    return ["NIFTY", "BANKNIFTY", "SENSEX"]


def record_intraday_candles(
    *,
    symbols: List[str] | None = None,
    intervals: List[str] | None = None,
    days: int = 5,
    max_symbols: int | None = None,
    write: bool = True,
) -> Dict[str, Any]:
    from data_fetcher import DataFetcher

    symbols = [str(s).strip().upper() for s in (symbols or _default_symbols()) if str(s).strip()]
    intervals = [str(i).strip().lower() for i in (intervals or ["1m", "5m", "15m"]) if str(i).strip()]
    if max_symbols is None:
        max_symbols = int(os.getenv("INTRADAY_RECORDER_MAX_SYMBOLS", "6"))
    symbols = symbols[: max(int(max_symbols or 0), 0)]

    fetcher = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
    results = []
    for symbol in symbols:
        for interval in intervals:
            started = time.time()
            row: Dict[str, Any] = {"symbol": symbol, "interval": interval}
            try:
                df = fetcher.get_market_data(symbol, interval=interval, days=days)
                spacing_ok, median_minutes = _interval_spacing_ok(df, interval)
                row.update({
                    "ok": bool(df is not None and len(df) >= 5 and spacing_ok),
                    "bars": int(len(df) if df is not None else 0),
                    "median_spacing_min": round(median_minutes, 3),
                    "duration_sec": round(time.time() - started, 3),
                })
                if df is not None and len(df) >= 5 and not spacing_ok:
                    row["reason"] = "interval_spacing_mismatch"
                try:
                    if df is not None and len(df) > 0:
                        row["first_bar"] = str(df.index[0])
                        row["last_bar"] = str(df.index[-1])
                except Exception:
                    pass
            except Exception as exc:
                row.update({
                    "ok": False,
                    "bars": 0,
                    "reason": str(exc),
                    "duration_sec": round(time.time() - started, 3),
                })
            results.append(row)

    try:
        from candle_cache import get_cache_stats

        cache_stats = get_cache_stats()
    except Exception:
        cache_stats = {}

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "symbols": symbols,
        "intervals": intervals,
        "days": days,
        "requested": len(results),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "results": results,
        "cache_stats": cache_stats,
    }
    if write:
        Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def render_summary(report: Dict[str, Any]) -> str:
    lines = [
        "INTRADAY CANDLE RECORDER",
        f"requested={report.get('requested', 0)} ok={report.get('ok_count', 0)}",
    ]
    for row in report.get("results", [])[:30]:
        lines.append(
            f"{row.get('symbol')} {row.get('interval')} "
            f"ok={bool(row.get('ok'))} bars={row.get('bars', 0)} "
            f"spacing={row.get('median_spacing_min', 0)}m "
            f"reason={row.get('reason', '') or 'ok'} "
            f"last={row.get('last_bar', '')}"
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="")
    parser.add_argument("--intervals", default="1m,5m,15m")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    intervals = [i.strip().lower() for i in args.intervals.split(",") if i.strip()]
    report = record_intraday_candles(
        symbols=symbols,
        intervals=intervals,
        days=args.days,
        max_symbols=args.max_symbols,
        write=not args.no_write,
    )
    print(render_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
