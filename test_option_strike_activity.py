#!/usr/bin/env python3
"""Smoke tests for CE/PE strike activity reporting."""

import json
import sqlite3
import tempfile
import time
from pathlib import Path

from option_strike_activity import build_strike_activity_report


def _make_snapshot_db(path: Path) -> None:
    rows = [
        {
            "strikePrice": 24000,
            "CE_openInterest": 12000,
            "PE_openInterest": 85000,
            "CE_changeinOpenInterest": 2000,
            "PE_changeinOpenInterest": 18000,
            "CE_totalTradedVolume": 9000,
            "PE_totalTradedVolume": 36000,
            "CE_lastPrice": 180,
            "PE_lastPrice": 95,
            "CE_impliedVolatility": 12,
            "PE_impliedVolatility": 14,
        },
        {
            "strikePrice": 24200,
            "CE_openInterest": 76000,
            "PE_openInterest": 14000,
            "CE_changeinOpenInterest": 15000,
            "PE_changeinOpenInterest": 1000,
            "CE_totalTradedVolume": 34000,
            "PE_totalTradedVolume": 7000,
            "CE_lastPrice": 85,
            "PE_lastPrice": 170,
            "CE_impliedVolatility": 13,
            "PE_impliedVolatility": 15,
        },
    ]
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE option_chain_snapshots (
                ts REAL NOT NULL,
                snapshot_time TEXT NOT NULL,
                underlying TEXT NOT NULL,
                spot REAL DEFAULT 0,
                expiry TEXT DEFAULT '',
                atm_strike REAL DEFAULT 0,
                pcr_oi REAL DEFAULT 0,
                pcr_change_oi REAL DEFAULT 0,
                max_pain REAL DEFAULT 0,
                ok INTEGER DEFAULT 1,
                reason TEXT DEFAULT '',
                rows_json TEXT DEFAULT '[]',
                summary_json TEXT DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO option_chain_snapshots
            (ts, snapshot_time, underlying, spot, expiry, atm_strike, ok, rows_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                "2026-06-19T10:00:00+0530",
                "NIFTY",
                24100,
                "2026-06-25",
                24100,
                1,
                json.dumps(rows),
            ),
        )


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "option_chain_snapshots.db"
        _make_snapshot_db(db)
        result = build_strike_activity_report(
            underlying="NIFTY",
            prefer_live=False,
            db_path=str(db),
        )
        assert result.ok, result.reason
        assert "CE 24200" in result.text
        assert "PE 24000" in result.text
        assert "Support 24000" in result.text
        assert "Resistance 24200" in result.text
    print("PASS strike activity snapshot report")


if __name__ == "__main__":
    main()


def test_analysis_includes_direction_oi_and_volume(tmp_path):
    db = tmp_path / 'snapshots.db'
    _make_snapshot_db(db)
    result = build_strike_activity_report(prefer_live=False, db_path=str(db))
    assert result.ok
    assert 'Direction scores: Bullish' in result.text
    assert 'Bearish' in result.text
    assert 'CE OI 88.0K | PE OI 99.0K' in result.text
    assert 'CE volume 43.0K | PE volume 43.0K' in result.text
    assert 'Change OI: CE 17.0K | PE 19.0K' in result.text
    assert 'not probabilities' in result.text


def test_missing_change_oi_is_not_displayed_as_neutral_ratio(tmp_path):
    db = tmp_path / 'snapshots.db'
    _make_snapshot_db(db)
    with sqlite3.connect(db) as conn:
        rows = json.loads(conn.execute('SELECT rows_json FROM option_chain_snapshots').fetchone()[0])
        for row in rows:
            row.pop('CE_changeinOpenInterest')
            row.pop('PE_changeinOpenInterest')
        conn.execute('UPDATE option_chain_snapshots SET rows_json=?', (json.dumps(rows),))
    result = build_strike_activity_report(prefer_live=False, db_path=str(db))
    assert result.ok
    assert 'Chg N/A' in result.text
    assert 'no directional vote' in result.text
