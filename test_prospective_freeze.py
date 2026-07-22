"""Contract test: get_prospective_rows() must never return a
historical_backfill row, regardless of what else is in the table -- this is
the one thing an external review flagged as a real bug (backfilled rows were
mechanically indistinguishable from genuine prospective evidence) and it
must stay fixed."""
import sqlite3

import prospective_freeze as pf
from signal_log import SignalLogger


def _seed_row(conn, signal_date, prediction_origin, frozen_regime_pwin=0.6):
    conn.execute(
        "INSERT INTO signal_log (signal_date, prediction_origin, frozen_regime_pwin) "
        "VALUES (?,?,?)", (signal_date, prediction_origin, frozen_regime_pwin))


def test_get_prospective_rows_excludes_historical_backfill(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_log_test.db")
    SignalLogger(db_path=db_path)  # creates schema incl. prediction_origin
    conn = sqlite3.connect(db_path)
    try:
        _seed_row(conn, "2026-07-20", "historical_backfill")
        _seed_row(conn, "2026-07-23", "live_prospective")
        _seed_row(conn, "2026-07-24", "live_prospective")
        conn.commit()
    finally:
        conn.close()

    df = pf.get_prospective_rows(db_path=db_path)
    assert len(df) == 2
    assert set(df["prediction_origin"]) == {"live_prospective"}
    assert "2026-07-20" not in set(df["signal_date"])


def test_get_prospective_rows_excludes_unscored_rows(tmp_path):
    db_path = str(tmp_path / "signal_log_test2.db")
    SignalLogger(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO signal_log (signal_date, prediction_origin) VALUES (?,?)",
            ("2026-07-23", "live_prospective"))
        conn.commit()
    finally:
        conn.close()

    df = pf.get_prospective_rows(db_path=db_path)
    assert len(df) == 0  # frozen_regime_pwin IS NULL -- not yet scored
