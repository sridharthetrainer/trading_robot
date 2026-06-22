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
from typing import Any, Dict, Iterable, List, Tuple

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


def _index_ist_dates(df) -> Tuple[str, str]:
    if df is None or len(df) == 0:
        return "", ""
    idx = pd.to_datetime(df.index, errors="coerce")
    idx = idx[~idx.isna()]
    if len(idx) == 0:
        return "", ""
    try:
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
    except Exception:
        pass
    idx = idx.sort_values()
    return str(idx[0].date()), str(idx[-1].date())


def _has_fresh_trading_bar(df, *, require_today: bool) -> bool:
    if not require_today:
        return True
    _, last_date = _index_ist_dates(df)
    if not last_date:
        return False
    today = pd.Timestamp.now(tz="Asia/Kolkata").date().isoformat()
    return last_date >= today


def _normalize_ohlcv(df):
    if df is None or len(df) == 0:
        return None
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(set(out.columns)):
        return None
    if "volume" not in out.columns:
        out["volume"] = 0
    out = out[["open", "high", "low", "close", "volume"]]
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    return out


def _resample_intraday(df, interval: str):
    base = _normalize_ohlcv(df)
    if base is None or len(base) < 5:
        return None
    rule = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min"}.get(interval)
    if not rule:
        return None
    try:
        resampled = base.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna(subset=["open", "high", "low", "close"])
        return resampled if len(resampled) >= 3 else None
    except Exception:
        return None


def _save_verified_candles(symbol: str, interval: str, df) -> int:
    try:
        from candle_cache import save_candles

        return int(save_candles(symbol, interval, _normalize_ohlcv(df)) or 0)
    except Exception:
        return 0


def _default_symbols() -> List[str]:
    try:
        from data_fetcher import DataFetcher

        fetcher = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
        symbols = fetcher.get_ordered_symbols(include_full_universe=True)
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
    require_today: bool = True,
) -> Dict[str, Any]:
    from data_fetcher import DataFetcher

    symbols = [str(s).strip().upper() for s in (symbols or _default_symbols()) if str(s).strip()]
    intervals = [str(i).strip().lower() for i in (intervals or ["1m", "5m", "15m"]) if str(i).strip()]
    if max_symbols is None:
        try:
            import config as cfg
            default_max = int(getattr(cfg, "FULL_UNIVERSE_SCAN_MAX_SYMBOLS", 220) or 220)
        except Exception:
            default_max = 220
        max_symbols = int(os.getenv("INTRADAY_RECORDER_MAX_SYMBOLS", str(default_max)))
    symbols = symbols[: max(int(max_symbols or 0), 0)]

    fetcher = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
    results = []
    one_minute_cache: Dict[str, Any] = {}

    def fetch_verified(symbol: str, interval: str, requested_days: int) -> tuple[Any, Dict[str, Any]]:
        tries = []
        day_attempts = []
        if str(interval).lower() != "1d":
            day_attempts.extend([1, 2])
        day_attempts.append(int(requested_days))
        day_attempts = list(dict.fromkeys(max(1, int(d)) for d in day_attempts))

        for attempt_days in day_attempts:
            df = fetcher.get_market_data(symbol, interval=interval, days=attempt_days)
            spacing_ok, median_minutes = _interval_spacing_ok(df, interval)
            fresh_ok = _has_fresh_trading_bar(df, require_today=require_today)
            tries.append({
                "source": "direct",
                "days": attempt_days,
                "bars": int(len(df) if df is not None else 0),
                "spacing_ok": bool(spacing_ok),
                "fresh_ok": bool(fresh_ok),
                "median_spacing_min": round(median_minutes, 3),
            })
            normalized = _normalize_ohlcv(df)
            if normalized is not None and len(normalized) >= 5 and spacing_ok and fresh_ok:
                return normalized, {"attempts": tries, "source": "direct", "days_used": attempt_days}

        if interval in {"5m", "15m", "30m", "1h"}:
            one_min = one_minute_cache.get(symbol)
            if one_min is None:
                one_min, one_meta = fetch_verified(symbol, "1m", requested_days)
                one_minute_cache[symbol] = one_min
                tries.append({"source": "1m_seed", **one_meta})
            derived = _resample_intraday(one_min, interval)
            spacing_ok, median_minutes = _interval_spacing_ok(derived, interval)
            fresh_ok = _has_fresh_trading_bar(derived, require_today=require_today)
            tries.append({
                "source": "resampled_1m",
                "bars": int(len(derived) if derived is not None else 0),
                "spacing_ok": bool(spacing_ok),
                "fresh_ok": bool(fresh_ok),
                "median_spacing_min": round(median_minutes, 3),
            })
            if derived is not None and len(derived) >= 5 and spacing_ok and fresh_ok:
                return derived, {"attempts": tries, "source": "resampled_1m"}

        return None, {"attempts": tries, "source": ""}

    for symbol in symbols:
        for interval in intervals:
            started = time.time()
            row: Dict[str, Any] = {"symbol": symbol, "interval": interval}
            try:
                df, meta = fetch_verified(symbol, interval, days)
                spacing_ok, median_minutes = _interval_spacing_ok(df, interval)
                fresh_ok = _has_fresh_trading_bar(df, require_today=require_today)
                ok = bool(df is not None and len(df) >= 5 and spacing_ok and fresh_ok)
                saved_rows = _save_verified_candles(symbol, interval, df) if ok else 0
                row.update({
                    "ok": ok,
                    "bars": int(len(df) if df is not None else 0),
                    "saved_rows": saved_rows,
                    "median_spacing_min": round(median_minutes, 3),
                    "fresh_ok": bool(fresh_ok),
                    "source": meta.get("source", ""),
                    "attempts": meta.get("attempts", []),
                    "duration_sec": round(time.time() - started, 3),
                })
                if df is not None and len(df) >= 5 and not spacing_ok:
                    row["reason"] = "interval_spacing_mismatch"
                elif df is not None and len(df) >= 5 and not fresh_ok:
                    row["reason"] = "stale_no_today_bar"
                elif not ok:
                    row["reason"] = "no_verified_intraday_data"
                try:
                    if df is not None and len(df) > 0:
                        row["first_bar"] = str(df.index[0])
                        row["last_bar"] = str(df.index[-1])
                        first_date, last_date = _index_ist_dates(df)
                        row["first_bar_date"] = first_date
                        row["last_bar_date"] = last_date
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
        "require_today": require_today,
        "requested": len(results),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "saved_rows": sum(int(r.get("saved_rows", 0) or 0) for r in results),
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
            f"saved={row.get('saved_rows', 0)} "
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
    parser.add_argument("--allow-stale", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    intervals = [i.strip().lower() for i in args.intervals.split(",") if i.strip()]
    report = record_intraday_candles(
        symbols=symbols,
        intervals=intervals,
        days=args.days,
        max_symbols=args.max_symbols,
        write=not args.no_write,
        require_today=not args.allow_stale,
    )
    print(render_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
