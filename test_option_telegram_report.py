import json
import sqlite3
from datetime import datetime

import option_telegram_report as report


def test_collect_report_data_filters_after_hours_spot_and_dedupes(tmp_path, monkeypatch):
    journal = tmp_path / "journal.jsonl"
    rows = [
        {"time": "2026-06-29T10:00:00+05:30", "symbol": "NIFTY", "spot": 24000,
         "decision": "chain_snapshot_evidence"},
        {"time": "2026-06-29T10:05:00+05:30", "symbol": "NIFTY", "spot": 24025,
         "decision": "selected", "side": "BUY", "strategy": "live",
         "selected": {"strike": 24000, "option_type": "CE"}},
        # Same selection key must not inflate the report.
        {"time": "2026-06-29T10:05:20+05:30", "symbol": "NIFTY", "spot": 24026,
         "decision": "selected", "side": "BUY", "strategy": "live",
         "selected": {"strike": 24000, "option_type": "CE"}},
        {"time": "2026-06-29T21:00:00+05:30", "symbol": "NIFTY", "spot": 25000,
         "decision": "chain_snapshot_evidence"},
    ]
    journal.write_text("\n".join(json.dumps(row) for row in rows))
    monkeypatch.setattr(report, "JOURNAL_FILE", journal)
    monkeypatch.setattr(report, "TRADES_DB", tmp_path / "missing.db")

    data = report.collect_report_data("2026-06-29")

    assert len(data["selections"]) == 1
    assert data["option_types"]["CE"] == 1
    assert [value for _, value in data["spots"]["NIFTY"]] == [24000, 24026]
    assert data["latest_spots"]["NIFTY"] == 25000


def test_option_trade_pnl_uses_closed_option_rows_only(tmp_path, monkeypatch):
    db = tmp_path / "trades.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (symbol TEXT, status TEXT, entry_time REAL, "
                "exit_time REAL, realized_pnl REAL, total_charges REAL)")
    ts = datetime(2026, 6, 29, 15, 0).timestamp()
    con.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?)", [
        ("NIFTY26JUN24000CE", "CLOSED", ts, ts, 500, 25),
        ("BANKNIFTY26JUN55000PE", "OPEN", ts, None, 999, 10),
        ("RELIANCE", "CLOSED", ts, ts, 700, 15),
    ])
    con.commit()
    con.close()
    monkeypatch.setattr(report, "TRADES_DB", db)
    monkeypatch.setattr(report, "JOURNAL_FILE", tmp_path / "missing.jsonl")

    data = report.collect_report_data("2026-06-29")

    assert len(data["trades"]) == 2
    assert data["realized_pnl"] == 500
    assert data["charges"] == 35
    assert data["wins"] == 1
