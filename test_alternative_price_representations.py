import sqlite3

import numpy as np
import pandas as pd


def _sample(rows=140):
    rng = np.random.default_rng(12)
    close = 100 + np.linspace(0, 12, rows) + rng.normal(0, 0.35, rows)
    open_ = close + rng.normal(0, 0.2, rows)
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + 0.5,
        "low": np.minimum(open_, close) - 0.5,
        "close": close,
        "volume": rng.integers(1000, 8000, rows),
    }, index=pd.date_range("2026-06-01 09:15", periods=rows, freq="5min"))


def test_representation_features_are_finite_and_explicit_about_footprint_proxy():
    from alternative_price_representations import FEATURE_NAMES, build_representation_features

    features = build_representation_features(_sample())
    assert set(features) == set(FEATURE_NAMES)
    assert all(np.isfinite(value) for value in features.values())
    assert features["representation_coverage"] == 1.0
    assert features["footprint_available"] == 0.0


def test_representation_history_is_prefix_causal():
    from alternative_price_representations import (
        build_representation_features, representation_history,
    )

    frame = _sample(100)
    history = representation_history(frame)
    for cut in (60, 75, 99):
        direct = build_representation_features(frame.iloc[: cut + 1])
        for name, value in direct.items():
            assert abs(float(history.iloc[cut][name]) - float(value)) < 1e-9


def test_synthetic_representation_signal_uses_real_close_for_entry():
    import alternative_price_representations as alt

    frame = _sample()
    result = alt._signal(frame, "point_and_figure")
    assert result["representation_features"]["representation_coverage"] == 1.0
    if result.get("side"):
        assert result["entry_price"] == float(frame["close"].iloc[-1])
        assert result["execution_price_source"] == "real_ohlc_close"


def test_representation_features_persist_to_signal_log(tmp_path, monkeypatch):
    import trading_calendar
    from alternative_price_representations import build_representation_features
    from signal_log import SignalLogger

    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda *_a, **_k: True)
    features = build_representation_features(_sample())
    logger = SignalLogger(db_path=str(tmp_path / "signals.db"))
    row_id = logger.log_candidate({
        "symbol": "TEST", "side": "BUY", "entry_price": 100,
        "stop_loss": 99, "target": 102, "strategy": "three_line_break",
        "metadata": {"representation_features": features},
    })
    with sqlite3.connect(tmp_path / "signals.db") as conn:
        row = conn.execute(
            "SELECT representation_coverage,hollow_state,line_break_direction,"
            "footprint_available FROM signal_log WHERE id=?", (row_id,),
        ).fetchone()
    assert row[0] == 1.0
    assert row[1] == features["hollow_state"]
    assert row[2] == features["line_break_direction"]
    assert row[3] == 0.0


def test_alternative_strategies_are_one_decorrelated_price_factor():
    from strategy_clusters import effective_confluence, factor_of

    names = ["three_line_break", "kagi_reversal", "point_and_figure", "range_bar_momentum"]
    assert {factor_of(name) for name in names} == {"PRICE_TRANSFORM"}
    assert effective_confluence(names) == 1


def test_point_in_time_backfill_never_uses_future_candles(tmp_path, monkeypatch):
    import trading_calendar
    from alternative_representation_backfill import backfill_representation_features
    from signal_log import SignalLogger

    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda *_a, **_k: True)
    signal_db = tmp_path / "signals.db"
    candle_db = tmp_path / "candles.db"
    logger = SignalLogger(db_path=str(signal_db))
    row_id = logger.log_candidate({
        "symbol": "TEST", "side": "BUY", "entry_price": 105,
        "stop_loss": 104, "target": 107, "strategy": "stored",
    })
    with sqlite3.connect(signal_db) as conn:
        conn.execute(
            "UPDATE signal_log SET signal_date='2026-06-01',signal_time='14:15:00' WHERE id=?",
            (row_id,),
        )
    frame = _sample(80)
    with sqlite3.connect(candle_db) as conn:
        conn.execute("""CREATE TABLE candles (
            symbol TEXT, interval TEXT, timestamp TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL)
        """)
        for stamp, row in frame.iterrows():
            conn.execute(
                "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
                ("TEST", "5m", stamp.tz_localize("Asia/Kolkata").isoformat(),
                 row.open, row.high, row.low, row.close, row.volume),
            )
    report = backfill_representation_features(
        signal_db=str(signal_db), candle_db=str(candle_db),
        report_file=str(tmp_path / "report.json"),
    )
    assert report["updated"] == 1
    with sqlite3.connect(signal_db) as conn:
        coverage = conn.execute(
            "SELECT representation_coverage FROM signal_log WHERE id=?", (row_id,)
        ).fetchone()[0]
    assert coverage == 1.0
