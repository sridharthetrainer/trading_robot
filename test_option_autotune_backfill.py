#!/usr/bin/env python3
"""
test_option_autotune_backfill.py

Run:
    python test_option_autotune_backfill.py
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from option_autotune_backfill import backfill_from_signal_log, backfill_from_trades_db
from option_decision_journal import load_recent_option_decisions


def _make_trades_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE trades (
            trade_id TEXT PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            strategy TEXT,
            entry_price REAL,
            exit_price REAL,
            qty INTEGER,
            realized_pnl REAL,
            status TEXT,
            exit_reason TEXT,
            score REAL,
            metadata TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "T100",
            "NIFTY20000CE",
            "BUY",
            "PIVOT_SCALPING",
            50.0,
            60.0,
            65,
            600.0,
            "CLOSED",
            "target_hit",
            7.5,
            json.dumps({"asset_type": "OPTION", "source_symbol": "NIFTY", "style": "scalping"}),
        ),
    )
    conn.commit()
    conn.close()


def _make_signal_log_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE signal_log (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            strategy TEXT,
            score REAL,
            executed INTEGER,
            trade_id TEXT,
            option_type TEXT,
            option_strike INTEGER,
            option_expiry TEXT,
            option_dte INTEGER,
            option_style TEXT,
            option_premium REAL,
            option_symbol TEXT,
            tb_label INTEGER,
            outcome_price REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO signal_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            7,
            "NIFTY",
            "BUY",
            "PIVOT_SCALPING",
            8.0,
            1,
            "",
            "CE",
            20100,
            "2026-06-17",
            0,
            "scalping",
            20.0,
            "NIFTY20100CE",
            1,
            26.0,
        ),
    )
    conn.commit()
    conn.close()


def test_backfill_trades_db_idempotent() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "trades.db")
        journal = str(Path(tmp) / "journal.jsonl")
        _make_trades_db(db)
        first = backfill_from_trades_db(db_path=db, journal_file=journal)
        second = backfill_from_trades_db(db_path=db, journal_file=journal)
        rows = load_recent_option_decisions(path=journal, limit=10)
        return (
            first == 1
            and second == 0
            and len(rows) == 1
            and rows[0]["trade_id"] == "T100"
            and rows[0]["outcome_label"] == 1
            and rows[0]["selected"]["strike"] == 20000
        )


def test_backfill_signal_log_idempotent() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "signal_log.db")
        journal = str(Path(tmp) / "journal.jsonl")
        _make_signal_log_db(db)
        first = backfill_from_signal_log(db_path=db, journal_file=journal)
        second = backfill_from_signal_log(db_path=db, journal_file=journal)
        rows = load_recent_option_decisions(path=journal, limit=10)
        return (
            first == 1
            and second == 0
            and len(rows) == 1
            and rows[0]["source_id"] == "signal_log:7"
            and rows[0]["selected"]["premium"] == 20.0
            and rows[0]["pnl"] == 6.0
        )


def main() -> int:
    tests = [
        ("backfill trades db idempotent", test_backfill_trades_db_idempotent),
        ("backfill signal log idempotent", test_backfill_signal_log_idempotent),
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
