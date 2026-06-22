#!/usr/bin/env python3
"""
test_signal_trade_reconciler.py

Run:
    python test_signal_trade_reconciler.py
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from signal_trade_reconciler import reconcile_signal_trades


def _make_dbs(tmp: str):
    trades = str(Path(tmp) / "trades.db")
    signals = str(Path(tmp) / "signal_log.db")
    tconn = sqlite3.connect(trades)
    tconn.execute(
        """
        CREATE TABLE trades (
            trade_id TEXT PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            strategy TEXT,
            entry_price REAL,
            entry_time REAL,
            created_at REAL,
            metadata TEXT
        )
        """
    )
    tconn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)",
        (
            "T001",
            "NIFTY20000CE",
            "BUY",
            "PIVOT_SCALPING",
            20.0,
            1000.0,
            1000.0,
            json.dumps({"asset_type": "OPTION", "strike": 20000, "option_type": "CE", "dte": 0, "style": "scalping"}),
        ),
    )
    tconn.commit()
    tconn.close()

    sconn = sqlite3.connect(signals)
    sconn.execute(
        """
        CREATE TABLE signal_log (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            strategy TEXT,
            log_time REAL,
            executed INTEGER DEFAULT 0,
            trade_id TEXT DEFAULT '',
            option_type TEXT DEFAULT '',
            option_strike INTEGER DEFAULT 0,
            option_expiry TEXT DEFAULT '',
            option_dte INTEGER DEFAULT 0,
            option_style TEXT DEFAULT '',
            option_premium REAL DEFAULT 0,
            option_symbol TEXT DEFAULT ''
        )
        """
    )
    sconn.execute(
        "INSERT INTO signal_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (11, "NIFTY20000CE", "BUY", "PIVOT_SCALPING", 1002.0, 0, "", "", 0, "", 0, "", 0.0, ""),
    )
    sconn.commit()
    sconn.close()
    return trades, signals


def test_reconcile_updates_matching_signal():
    with tempfile.TemporaryDirectory() as tmp:
        trades, signals = _make_dbs(tmp)
        result = reconcile_signal_trades(trades_db=trades, signal_db=signals, max_time_diff_sec=60)
        conn = sqlite3.connect(signals)
        row = conn.execute(
            "SELECT executed, trade_id, option_type, option_strike, option_dte, option_symbol FROM signal_log WHERE id=11"
        ).fetchone()
        conn.close()
        assert (
            result["updated"] == 1
            and row[0] == 1
            and row[1] == "T001"
            and row[2] == "CE"
            and row[3] == 20000
            and row[4] == 0
            and row[5] == "NIFTY20000CE"
        )


def test_reconcile_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        trades, signals = _make_dbs(tmp)
        first = reconcile_signal_trades(trades_db=trades, signal_db=signals, max_time_diff_sec=60)
        second = reconcile_signal_trades(trades_db=trades, signal_db=signals, max_time_diff_sec=60)
        assert first["updated"] == 1 and second["already_linked"] == 1 and second["updated"] == 0


def main() -> int:
    tests = [
        ("reconcile updates matching signal", test_reconcile_updates_matching_signal),
        ("reconcile is idempotent", test_reconcile_is_idempotent),
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
