#!/usr/bin/env python3
"""
test_eod_signal_miner.py

Run:
    .venv/bin/python test_eod_signal_miner.py
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

from eod_signal_miner import build_report, mine_symbol, persist_candidates, render_markdown


def _synthetic_trend(rows: int = 140) -> pd.DataFrame:
    idx = pd.date_range("2026-06-17 09:15:00", periods=rows, freq="5min")
    close = []
    price = 100.0
    for i in range(rows):
        price += 0.03 if i < 50 else 0.18
        close.append(price)
    df = pd.DataFrame(index=idx)
    df["close"] = close
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df[["open", "close"]].max(axis=1) + 0.08
    df["low"] = df[["open", "close"]].min(axis=1) - 0.08
    df["volume"] = [1000 if i < 50 else 2200 for i in range(rows)]
    return df


def test_miner_finds_candidates():
    df = _synthetic_trend()
    result = mine_symbol("TEST", df, warmup=40, min_score=4)
    report = build_report([result])
    text = render_markdown(report)
    assert (
        result["ok"] is True
        and len(result["candidates"]) > 0
        and report["summary"]["n"] == len(result["candidates"])
        and report["by_setup"]
        and "EOD Signal Miner Report" in text
    )


def test_miner_handles_insufficient_data():
    result = mine_symbol("SHORT", _synthetic_trend(rows=20))
    report = build_report([result])
    assert result["ok"] is False and report["summary"]["n"] == 0


def test_persist_candidates_dedupes_across_runs():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eod_signal_miner_test.db")
        cands = [{
            "symbol": "TEST", "time": "2026-07-14 09:20:00+05:30", "side": "BUY",
            "setup": "mtf_momentum", "score": 5, "opposition": 2,
            "factors": ["above_vwap", "rsi_above_55"], "entry_price": 100.0,
            "label": 1, "return_pct": 1.5,
        }]
        first = persist_candidates(cands, db_path=db_path)
        second = persist_candidates(cands, db_path=db_path)  # same window re-mined
        with sqlite3.connect(db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM eod_mined_candidates").fetchone()[0]
        assert first["inserted"] == 1 and second["inserted"] == 0 and n == 1


def main() -> int:
    tests = [
        ("miner finds candidates", test_miner_finds_candidates),
        ("miner handles insufficient data", test_miner_handles_insufficient_data),
        ("persist_candidates dedupes across runs", test_persist_candidates_dedupes_across_runs),
    ]
    failed = 0
    for name, fn in tests:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        if ok is None:
            ok = True
        if ok:
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
