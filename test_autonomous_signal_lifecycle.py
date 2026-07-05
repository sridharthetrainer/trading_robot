import sqlite3
import pandas as pd

from autonomous_signal_lifecycle import active_generated_symbols, update_generated_signal_lifecycle


def _databases(tmp_path, *, high=104, low=99):
    signals = tmp_path / "signals.db"
    candles = tmp_path / "candles.db"
    with sqlite3.connect(signals) as conn:
        conn.execute("""CREATE TABLE signal_log (
            id INTEGER PRIMARY KEY,signal_date TEXT,signal_time TEXT,symbol TEXT,side TEXT,
            strategy TEXT,score REAL,entry_price REAL,stop_loss REAL,target REAL,
            executed INTEGER,rejection_reason TEXT,tb_label INTEGER)""")
        conn.execute("INSERT INTO signal_log VALUES (1,'2026-07-02','10:00:00','ABC','BUY','trend',10,100,98,103,0,'',-99)")
    with sqlite3.connect(candles) as conn:
        conn.execute("CREATE TABLE candles (symbol TEXT,interval TEXT,timestamp TEXT,high REAL,low REAL,close REAL)")
        conn.execute("INSERT INTO candles VALUES ('ABC','1m','2026-07-02T10:01:00+05:30',?,?,?)", (high, low, high))
    return signals, candles


def test_target_hit_is_persisted(tmp_path):
    signals, candles = _databases(tmp_path, high=104, low=99)
    result = update_generated_signal_lifecycle(signal_db=str(signals), candle_db=str(candles), session_date="2026-07-02")
    assert result["events"][0]["status"] == "TARGET_HIT"


def test_same_bar_stop_is_conservative(tmp_path):
    signals, candles = _databases(tmp_path, high=104, low=97)
    result = update_generated_signal_lifecycle(signal_db=str(signals), candle_db=str(candles), session_date="2026-07-02")
    assert result["events"][0]["status"] == "STOP_LOSS_HIT"


def test_live_in_memory_frame_detects_target_without_candle_db(tmp_path):
    signals, _ = _databases(tmp_path, high=100, low=99)
    frame = pd.DataFrame(
        {"high": [104], "low": [99], "close": [103.5]},
        index=pd.to_datetime(["2026-07-02T10:01:00+05:30"]),
    )
    result = update_generated_signal_lifecycle(
        signal_db=str(signals), candle_db=str(tmp_path / "missing.db"),
        session_date="2026-07-02", price_frames={"ABC": {"df": frame}},
    )
    assert result["events"][0]["status"] == "TARGET_HIT"


def test_active_symbols_excludes_rejected_and_closed(tmp_path):
    signals, _ = _databases(tmp_path)
    with sqlite3.connect(signals) as conn:
        conn.execute("INSERT INTO signal_log VALUES (2,'2026-07-02','10:00:00','BAD','BUY','trend',10,100,98,103,0,'filtered',-99)")
        conn.execute("INSERT INTO signal_log VALUES (3,'2026-07-02','10:00:00','DONE','BUY','trend',10,100,98,103,0,'',-99)")
        conn.execute("ALTER TABLE signal_log ADD COLUMN lifecycle_status TEXT DEFAULT 'OPEN'")
        conn.execute("UPDATE signal_log SET lifecycle_status='TARGET_HIT' WHERE symbol='DONE'")
    assert active_generated_symbols("2026-07-02", str(signals)) == {"ABC"}
