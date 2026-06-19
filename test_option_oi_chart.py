#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from option_oi_chart import generate_option_oi_chart


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE option_chain_snapshots (
            ts REAL,
            snapshot_time TEXT,
            underlying TEXT,
            spot REAL,
            expiry TEXT,
            atm_strike REAL,
            pcr_oi REAL,
            pcr_change_oi REAL,
            max_pain REAL,
            ok INTEGER,
            reason TEXT,
            rows_json TEXT,
            summary_json TEXT
        )
    """)
    rows_a = [
        {
            "strikePrice": 23500,
            "CE_openInterest": 1000,
            "PE_openInterest": 1500,
            "CE_changeinOpenInterest": 100,
            "PE_changeinOpenInterest": 250,
            "CE_totalTradedVolume": 100,
            "PE_totalTradedVolume": 300,
        },
        {
            "strikePrice": 23550,
            "CE_openInterest": 2200,
            "PE_openInterest": 1200,
            "CE_changeinOpenInterest": 380,
            "PE_changeinOpenInterest": 120,
            "CE_totalTradedVolume": 400,
            "PE_totalTradedVolume": 120,
        },
        {
            "strikePrice": 23600,
            "CE_openInterest": 700,
            "PE_openInterest": 900,
            "CE_changeinOpenInterest": 60,
            "PE_changeinOpenInterest": 90,
            "CE_totalTradedVolume": 70,
            "PE_totalTradedVolume": 80,
        },
    ]
    rows_b = [
        {
            "strikePrice": 23500,
            "CE_openInterest": 1200,
            "PE_openInterest": 1800,
            "CE_changeinOpenInterest": 180,
            "PE_changeinOpenInterest": 320,
            "CE_totalTradedVolume": 180,
            "PE_totalTradedVolume": 450,
        },
        {
            "strikePrice": 23550,
            "CE_openInterest": 2600,
            "PE_openInterest": 1100,
            "CE_changeinOpenInterest": 460,
            "PE_changeinOpenInterest": 50,
            "CE_totalTradedVolume": 520,
            "PE_totalTradedVolume": 90,
        },
        {
            "strikePrice": 23600,
            "CE_openInterest": 900,
            "PE_openInterest": 950,
            "CE_changeinOpenInterest": 90,
            "PE_changeinOpenInterest": 110,
            "CE_totalTradedVolume": 90,
            "PE_totalTradedVolume": 100,
        },
    ]
    for ts, hhmm, spot, rows in [
        (1.0, "09:20:00", 23520, rows_a),
        (2.0, "09:25:00", 23540, rows_b),
    ]:
        conn.execute(
            """
            INSERT INTO option_chain_snapshots
            (ts, snapshot_time, underlying, spot, expiry, atm_strike, pcr_oi,
             pcr_change_oi, max_pain, ok, reason, rows_json, summary_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                f"2026-06-18T{hhmm}+0530",
                "NIFTY",
                spot,
                "2026-06-23",
                23500,
                1.2,
                1.4,
                23500,
                1,
                "",
                json.dumps(rows),
                "{}",
            ),
        )
    conn.commit()
    conn.close()


def test_aggregate_chart() -> bool:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "snapshots.db"
        _make_db(db)
        result = generate_option_oi_chart(
            underlying="NIFTY",
            day="2026-06-18",
            db_path=str(db),
            output_dir=td,
        )
        return result.ok and Path(result.path).exists() and result.points == 2


def test_strike_chart() -> bool:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "snapshots.db"
        _make_db(db)
        result = generate_option_oi_chart(
            underlying="NIFTY",
            day="2026-06-18",
            strike=23500,
            db_path=str(db),
            output_dir=td,
        )
        return result.ok and Path(result.path).exists() and "Strike 23500" in result.caption


def test_top_multi_strike_chart() -> bool:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "snapshots.db"
        _make_db(db)
        result = generate_option_oi_chart(
            underlying="NIFTY",
            day="2026-06-18",
            compare_top=3,
            db_path=str(db),
            output_dir=td,
        )
        return (
            result.ok
            and Path(result.path).exists()
            and "multi-strike" in result.caption
            and "Key support" in result.caption
            and "Key resistance" in result.caption
        )


def main() -> int:
    tests = [
        ("aggregate OI chart", test_aggregate_chart),
        ("single strike OI chart", test_strike_chart),
        ("top multi-strike OI chart", test_top_multi_strike_chart),
    ]
    failed = 0
    for name, fn in tests:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        if ok:
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
