from datetime import datetime, timedelta
import sqlite3

import pandas as pd

from signal_log import SignalLogger
from triple_barrier import label_triple_barrier


def test_custom_barrier_overrides_generic_target():
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 102.0],
            "low": [100.0, 99.5, 98.0],
            "close": [100.0, 101.0, 98.5],
        }
    )

    generic = label_triple_barrier(df, 0, 100.0, target_pct=0.015, stop_pct=0.01, max_bars=3, side="BUY")
    custom = label_triple_barrier(
        df,
        0,
        100.0,
        target_pct=0.015,
        stop_pct=0.01,
        max_bars=3,
        side="BUY",
        target_price=103.0,
        stop_price=99.0,
    )

    assert generic == 1
    assert custom == -1


def test_signal_logger_stores_candidate_risk_levels(tmp_path):
    db_path = tmp_path / "signal_log.db"
    sl = SignalLogger(db_path=str(db_path))

    row_id = sl.log_candidate(
        {
            "symbol": "NIFTY",
            "side": "BUY",
            "strategy": "RISK_TEST",
            "entry_price": 100.0,
            "stop_loss": 98.0,
            "target_price": 106.0,
            "score": 80,
        }
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT stop_loss, target, rr FROM signal_log WHERE id=?",
            (row_id,),
        ).fetchone()

    assert row == (98.0, 106.0, 3.0)


def test_mark_executed_can_update_risk_levels(tmp_path):
    db_path = tmp_path / "signal_log.db"
    sl = SignalLogger(db_path=str(db_path))
    row_id = sl.log_candidate(
        {
            "symbol": "NIFTY",
            "side": "BUY",
            "strategy": "RISK_LINK",
            "entry_price": 100.0,
            "score": 80,
        }
    )

    assert sl.mark_executed(
        "NIFTY",
        trade_id="T_RISK",
        strategy="RISK_LINK",
        option_metadata={"stop_loss": 97.0, "target": 106.0},
        require_trade_row=False,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT executed, trade_id, stop_loss, target, rr FROM signal_log WHERE id=?",
            (row_id,),
        ).fetchone()

    assert row == (1, "T_RISK", 97.0, 106.0, 2.0)


def test_eod_labeller_uses_stored_signal_barriers(tmp_path, monkeypatch):
    import trading_calendar
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda *_args, **_kwargs: True)
    db_path = tmp_path / "signal_log.db"
    sl = SignalLogger(db_path=str(db_path))
    row_id = sl.log_candidate(
        {
            "symbol": "NIFTY",
            "side": "BUY",
            "strategy": "RISK_TB",
            "entry_price": 100.0,
            "stop_loss": 99.0,
            "target_price": 103.0,
            "score": 80,
        }
    )

    now = datetime.now().replace(second=0, microsecond=0)
    idx = [now + timedelta(minutes=5 * i) for i in range(4)]
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 102.0, 102.0],
            "low": [100.0, 99.5, 98.5, 98.0],
            "close": [100.0, 101.0, 98.8, 98.0],
        },
        index=pd.DatetimeIndex(idx),
    )

    assert sl.apply_triple_barrier_labels({"NIFTY": df}) == 1

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT tb_label, tb_target, tb_stop, tb_rr, tb_used_custom_barrier, tb_r_multiple "
            "FROM signal_log WHERE id=?",
            (row_id,),
        ).fetchone()

    assert row[0] == -1
    assert row[1] == 103.0
    assert row[2] == 99.0
    assert row[3] == 3.0
    assert row[4] == 1
    assert row[5] < 0
