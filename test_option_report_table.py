import sqlite3

import option_bot_views
from option_multistrike_signals import ensure_multistrike_schema


def test_anytime_report_contains_levels_and_status(tmp_path, monkeypatch):
    db = tmp_path / "options.db"
    with sqlite3.connect(db) as conn:
        ensure_multistrike_schema(conn)
        conn.execute(
            """INSERT INTO option_strike_signals
               (ts,snapshot_time,underlying,expiry,strike,option_type,flow,direction,signal,tradable,
                price,score,entry_price,stop_loss,target_1,target_2,lifecycle_status)
               VALUES(1,'2026-07-02T10:00:00','NIFTY','2026-07-07',24000,'CE','CALL_BUYING','BULLISH','BUY_CE',1,
                      100,80,100,85,120,135,'TARGET1_HIT')"""
        )
    monkeypatch.setattr(option_bot_views, "DB_PATH", str(db))
    text = option_bot_views.anytime_report_table("2026-07-02")
    assert "ENTRY" in text and "SL" in text and "T1" in text and "T2" in text
    assert "NIFTY 24000CE" in text and "TARGET1_HIT" in text
