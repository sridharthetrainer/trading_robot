#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from run_extended_validation import (
    _load_source,
    load_labeled_history,
    split_into_segments,
)


def _candle_row(ts: str, price: float = 100.0):
    return (ts, price, price + 1, price - 1, price, 1000)


def _make_candles_db(path: Path, symbol: str, interval: str, rows) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE candles (
            symbol TEXT NOT NULL, interval TEXT NOT NULL, timestamp TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL
        )
    """)
    conn.executemany(
        "INSERT INTO candles (symbol, interval, timestamp, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(symbol, interval, ts, o, h, l, c, v) for ts, o, h, l, c, v in rows],
    )
    conn.commit()
    conn.close()


# ── split_into_segments: the core correctness property this task is about ──

def test_split_never_bridges_a_large_time_gap_within_one_source() -> None:
    """A >10-day gap inside a SINGLE source's data must still split into two
    segments -- source tag alone isn't the only thing that matters, physical
    continuity does too (e.g. a broker outage inside otherwise-live data)."""
    idx = list(pd.date_range("2026-01-01", periods=5, freq="1D")) + \
          list(pd.date_range("2026-03-01", periods=5, freq="1D"))
    df = pd.DataFrame({
        "Open": range(10), "High": range(10), "Low": range(10), "Close": range(10),
        "Volume": [1] * 10,
        "_source": ["live_broker"] * 10,
    }, index=pd.DatetimeIndex(idx))
    segs = split_into_segments(df)
    assert len(segs) == 2
    assert segs[0]["bars"] == 5 and segs[1]["bars"] == 5
    assert segs[0]["source"] == segs[1]["source"] == "live_broker"


def test_split_separates_source_change_even_with_zero_time_gap() -> None:
    """A source change with NO time gap must still split -- provenance
    discontinuity matters independently of physical time continuity (this is
    what the old concatenate-everything approach missed: a train window
    ending in external data and a test window starting in live data, with no
    internal gap in either half, used to slip through undetected)."""
    idx = pd.date_range("2026-01-01", periods=10, freq="1D")
    df = pd.DataFrame({
        "Open": range(10), "High": range(10), "Low": range(10), "Close": range(10),
        "Volume": [1] * 10,
        "_source": ["external_2015_2024"] * 5 + ["live_broker"] * 5,
    }, index=idx)
    segs = split_into_segments(df)
    assert len(segs) == 2
    assert segs[0]["source"] == "external_2015_2024"
    assert segs[1]["source"] == "live_broker"
    assert segs[0]["bars"] == 5 and segs[1]["bars"] == 5


def test_split_keeps_normal_weekend_gaps_as_one_segment() -> None:
    """Weekends/holidays are normal and must NOT fragment a segment -- only
    the compound test_split_never_bridges test proves the >10-day threshold
    doesn't also trigger on a 2-3 day weekend gap."""
    # Fri, Mon, Tue -- a normal 3-day weekend gap, well under MAX_GAP_DAYS.
    idx = pd.DatetimeIndex(["2026-01-02", "2026-01-05", "2026-01-06"])
    df = pd.DataFrame({
        "Open": [1, 2, 3], "High": [1, 2, 3], "Low": [1, 2, 3], "Close": [1, 2, 3],
        "Volume": [1, 1, 1],
        "_source": ["live_broker"] * 3,
    }, index=idx)
    segs = split_into_segments(df)
    assert len(segs) == 1
    assert segs[0]["bars"] == 3


def test_split_returns_ohlcv_only_data_no_source_column() -> None:
    """Each segment's data must be ready to hand straight to a backtest_fn --
    the _source bookkeeping column must not leak into it."""
    idx = pd.date_range("2026-01-01", periods=3, freq="1D")
    df = pd.DataFrame({
        "Open": [1, 2, 3], "High": [1, 2, 3], "Low": [1, 2, 3], "Close": [1, 2, 3],
        "Volume": [1, 1, 1],
        "_source": ["live_broker"] * 3,
    }, index=idx)
    segs = split_into_segments(df)
    assert "_source" not in segs[0]["data"].columns
    assert list(segs[0]["data"].columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_split_empty_dataframe_returns_no_segments() -> None:
    assert split_into_segments(pd.DataFrame()) == []


# ── load_labeled_history: attribution must survive loading from real DBs ───

def test_load_source_tags_every_row_with_its_source() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "one_source.db"
        _make_candles_db(db, "NIFTY", "5m", [
            _candle_row("2026-01-01 09:15:00"),
            _candle_row("2026-01-01 09:20:00"),
        ])
        df = _load_source(str(db), "test_source", "NIFTY", "5m")
        assert len(df) == 2
        assert (df["_source"] == "test_source").all()


def test_load_labeled_history_combines_and_preserves_both_source_tags() -> None:
    """The end-to-end attribution property: after loading from two separate
    on-disk databases, every row's true origin must still be recoverable --
    this is what the original bug lost (source column selected out of the
    SQL query entirely)."""
    with tempfile.TemporaryDirectory() as td:
        external_db = Path(td) / "external_backtest_data.db"
        live_db = Path(td) / "candle_cache.db"
        _make_candles_db(external_db, "NIFTY", "5m", [
            _candle_row("2020-01-01 09:15:00"),
            _candle_row("2020-01-01 09:20:00"),
        ])
        _make_candles_db(live_db, "NIFTY", "5m", [
            _candle_row("2026-01-01 09:15:00"),
            _candle_row("2026-01-01 09:20:00"),
        ])
        import run_extended_validation as rev
        old_ext, old_live = rev.EXTERNAL_DB, rev.LIVE_DB
        rev.EXTERNAL_DB, rev.LIVE_DB = str(external_db), str(live_db)
        try:
            combined = pd.concat([
                rev._load_source(str(external_db), "external_2015_2024", "NIFTY", "5m"),
                rev._load_source(str(live_db), "live_broker", "NIFTY", "5m"),
            ]).sort_index()
        finally:
            rev.EXTERNAL_DB, rev.LIVE_DB = old_ext, old_live

        assert len(combined) == 4
        assert (combined.loc[combined.index < "2021-01-01", "_source"] == "external_2015_2024").all()
        assert (combined.loc[combined.index >= "2021-01-01", "_source"] == "live_broker").all()

        segs = split_into_segments(combined)
        assert len(segs) == 2, "one real gap between 2020 and 2026 must yield exactly two segments"


def test_load_labeled_history_missing_db_does_not_raise() -> None:
    """A symbol/db with no data at all must return empty, not raise -- e.g.
    BANKNIFTY before its live coverage window began."""
    import run_extended_validation as rev
    old_ext, old_live = rev.EXTERNAL_DB, rev.LIVE_DB
    rev.EXTERNAL_DB = "/nonexistent/external.db"
    rev.LIVE_DB = "/nonexistent/live.db"
    try:
        df = load_labeled_history("NOSUCHSYMBOL")
    finally:
        rev.EXTERNAL_DB, rev.LIVE_DB = old_ext, old_live
    assert df.empty
