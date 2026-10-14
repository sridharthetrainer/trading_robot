import sqlite3

from candle_session_gap_audit import audit


def test_internal_gap_is_detected_even_when_latest_session_exists(tmp_path):
    db = tmp_path / "candles.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table candles(symbol text, interval text, timestamp text)")
        for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
            conn.execute("insert into candles values('ABC','1d',?)", (day + " 00:00:00",))
        for day in ("2026-10-07", "2026-10-09"):
            conn.execute("insert into candles values('ABC','1m',?)", (day + " 09:15:00",))
    report = audit(db_path=str(db), symbols=["ABC"], intervals=("1m",), days=5000,
                   write=False)
    assert report["missing_sessions"] == ["2026-10-08"]
    assert report["missing_pairs"] == 1
