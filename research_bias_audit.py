#!/usr/bin/env python3
"""Detect indicator lookahead by comparing full-history and truncated results."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

REPORT_FILE = "research_bias_audit.json"


def _load_sample(db_path: str = "candle_cache.db", rows: int = 320) -> pd.DataFrame:
    if Path(db_path).exists():
        with sqlite3.connect(db_path) as conn:
            found = conn.execute(
                "SELECT symbol,interval FROM candles WHERE interval IN ('5m','15m','1h') "
                "GROUP BY symbol,interval HAVING COUNT(*)>=? AND SUM(COALESCE(volume,0))>0 "
                "ORDER BY COUNT(*) DESC LIMIT 1",
                (rows,),
            ).fetchone()
            if found:
                frame = pd.read_sql_query(
                    "SELECT timestamp,open,high,low,close,volume FROM candles "
                    "WHERE symbol=? AND interval=? ORDER BY timestamp DESC LIMIT ?",
                    conn, params=(found[0], found[1], rows),
                ).iloc[::-1]
                frame.index = pd.to_datetime(frame.pop("timestamp"), errors="coerce")
                frame.attrs.update(symbol=found[0], interval=found[1], source="candle_cache")
                return frame
    rng = np.random.default_rng(42)
    close = 100 + rng.normal(0, 0.5, rows).cumsum()
    frame = pd.DataFrame({
        "open": close + rng.normal(0, 0.1, rows),
        "high": close + rng.uniform(0.1, 0.8, rows),
        "low": close - rng.uniform(0.1, 0.8, rows),
        "close": close, "volume": rng.integers(1000, 10000, rows),
    }, index=pd.date_range("2026-01-01 09:15", periods=rows, freq="5min"))
    frame.attrs.update(symbol="SYNTHETIC", interval="5m", source="deterministic_synthetic")
    return frame


def _series_list(value: Any) -> list[pd.Series]:
    values = value if isinstance(value, tuple) else (value,)
    return [item for item in values if isinstance(item, pd.Series)]


def _audit_one(name: str, fn: Callable[[pd.DataFrame], Any], frame: pd.DataFrame) -> dict:
    full_outputs = _series_list(fn(frame.copy()))
    mismatches = 0
    comparisons = 0
    max_abs_error = 0.0
    for cut in (80, 120, 180, 240, len(frame) - 1):
        if cut >= len(frame):
            continue
        prefix = frame.iloc[: cut + 1].copy()
        prefix_outputs = _series_list(fn(prefix))
        for full, short in zip(full_outputs, prefix_outputs):
            if short.empty:
                continue
            left = pd.to_numeric(full.iloc[: cut + 1], errors="coerce").to_numpy(dtype=float)
            right = pd.to_numeric(short, errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(left) & np.isfinite(right)
            if not valid.any():
                continue
            error = float(np.max(np.abs(left[valid] - right[valid])))
            scale = max(1.0, float(np.max(np.abs(right[valid]))))
            comparisons += 1
            max_abs_error = max(max_abs_error, error)
            if error > 1e-9 * scale:
                mismatches += 1
    return {
        "name": name, "ok": comparisons > 0 and mismatches == 0, "comparisons": comparisons,
        "mismatches": mismatches, "max_abs_error": max_abs_error,
    }


def run_bias_audit(*, report_file: str = REPORT_FILE, write: bool = True) -> dict:
    import indicators as ind

    frame = _load_sample()
    checks = [
        ("sma", lambda df: ind.calculate_sma(df, 20)),
        ("ema", lambda df: ind.calculate_ema(df, 20)),
        ("rsi", lambda df: ind.calculate_rsi(df, 14)),
        ("atr", lambda df: ind.calculate_atr(df, 14)),
        ("adx", lambda df: ind.calculate_adx(df, 14, return_di=True)),
        ("supertrend", lambda df: ind.calculate_supertrend(df, 10, 3.0)),
        ("vwap", lambda df: ind.calculate_vwap(df)),
        ("bollinger", lambda df: ind.calculate_bollinger_bands(df, 20, 2.0)),
        ("macd", lambda df: ind.calculate_macd(df)),
        ("volume_ratio", lambda df: ind.calculate_volume_ratio(df, 20)),
        ("obv", lambda df: ind.calculate_obv(df)),
        ("mfi", lambda df: ind.calculate_mfi(df, 14)),
    ]
    results = []
    for name, fn in checks:
        try:
            results.append(_audit_one(name, fn, frame))
        except Exception as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "method": "full_history_vs_truncated_prefix",
        "sample": {key: frame.attrs.get(key) for key in ("source", "symbol", "interval")},
        "rows": len(frame), "checks": results,
        "ok": bool(results) and all(row.get("ok") for row in results),
        "failed": [row["name"] for row in results if not row.get("ok")],
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run_bias_audit(), indent=2))
