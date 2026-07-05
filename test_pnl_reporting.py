import json
import sqlite3
from datetime import datetime

from pnl_reporting import format_today_pnl, is_option_trade, today_pnl_breakdown


def _db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE trades (
          symbol TEXT,strategy TEXT,metadata TEXT,signal_metadata TEXT,
          gross_pnl REAL,total_charges REAL,realized_pnl REAL,status TEXT,
          entry_time REAL,exit_time REAL)""")


def test_option_classification_from_symbol_and_metadata():
    assert is_option_trade({"symbol": "NIFTY30JUL26000CE"})
    assert is_option_trade({"symbol": "NIFTY", "metadata": json.dumps({"option_type": "PE"})})
    assert not is_option_trade({"symbol": "RELIANCE", "metadata": "{}"})


def test_today_breakdown_separates_options_and_normal(tmp_path):
    db = tmp_path / "trades.db"
    _db(db)
    now = datetime.now().timestamp()
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("NIFTY30JUL26000CE", "OPTION_SCALP", "{}", "{}", 120, 20, 100, "CLOSED", now, now),
                ("RELIANCE", "TREND", "{}", "{}", -40, 10, -50, "CLOSED", now, now),
            ],
        )
    report = today_pnl_breakdown(str(db))
    assert report["options"]["net"] == 100
    assert report["normal"]["net"] == -50
    assert report["total"]["net"] == 50
    assert "Options" in format_today_pnl(str(db))
