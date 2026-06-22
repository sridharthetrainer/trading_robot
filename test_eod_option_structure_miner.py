#!/usr/bin/env python3
"""
test_eod_option_structure_miner.py

Run:
    python test_eod_option_structure_miner.py
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

from eod_option_structure_miner import _leg_rows, run_structure_miner


def _synthetic_5m() -> pd.DataFrame:
    idx = pd.date_range("2026-06-19 09:15", periods=42, freq="5min")
    closes = [
        100, 99.4, 98.8, 99.2, 100.2, 101.1, 102.4, 101.8, 102.8, 104.0,
        103.5, 104.7, 105.8, 105.2, 106.5, 107.2, 106.4, 105.3, 104.2, 103.1,
        104.0, 103.0, 101.8, 100.9, 99.8, 98.7, 99.3, 98.1, 97.2, 96.4,
        97.1, 96.0, 95.4, 96.2, 97.0, 98.3, 99.1, 98.4, 99.5, 100.7,
        101.5, 102.2,
    ]
    df = pd.DataFrame(index=idx)
    df["close"] = closes
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df[["open", "close"]].max(axis=1) + 0.25
    df["low"] = df[["open", "close"]].min(axis=1) - 0.25
    df["volume"] = [1000 + (i % 7) * 120 for i in range(len(df))]
    return df


def test_leg_rows_find_bull_and_bear_legs():
    legs = _leg_rows("NIFTY", _synthetic_5m(), top_n=10)
    directions = {leg["direction"] for leg in legs}
    assert (
        "BUY" in directions
        and "SELL" in directions
        and any(leg["max_profit_pct"] > 1.0 for leg in legs)
        and all("features_json" in leg for leg in legs)
    )


def test_run_structure_miner_persists_rows_with_mock_loader():
    import eod_option_structure_miner as miner

    original_loader = miner._load_structure_candles
    miner._load_structure_candles = lambda symbol, days, allow_fetch=False: (_synthetic_5m(), "test")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "structure.db")
            report_path = str(Path(tmp) / "report.json")
            edges_path = str(Path(tmp) / "edges.json")
            report = run_structure_miner(
                symbols=["NIFTY"],
                days=1,
                top_n=6,
                persist=True,
                db_path=db_path,
                report_file=report_path,
                edges_file=edges_path,
            )
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT COUNT(*) FROM option_structure_legs").fetchone()[0]
            assert (
                report["symbols_ok"] == 1
                and report["legs"] > 0
                and rows == report["stored"]
                and Path(report_path).exists()
                and Path(edges_path).exists()
            )
    finally:
        miner._load_structure_candles = original_loader


def main() -> int:
    tests = [
        ("finds bull and bear legs", test_leg_rows_find_bull_and_bear_legs),
        ("persists mined structure rows", test_run_structure_miner_persists_rows_with_mock_loader),
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
