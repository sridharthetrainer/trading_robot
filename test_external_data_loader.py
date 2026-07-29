import sqlite3
from pathlib import Path

import pandas as pd

import external_data_loader as edl


def _write_csv(path, rows):
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df.to_csv(path, index=False)


def test_load_csv_accepts_clean_data(tmp_path):
    db = tmp_path / "ext.db"
    csv = tmp_path / "nifty.csv"
    _write_csv(csv, [
        ["2020-01-01 09:15:00", 100, 101, 99, 100.5, 0],
        ["2020-01-01 09:16:00", 100.5, 102, 100, 101.5, 0],
        ["2020-01-01 09:17:00", 101.5, 103, 101, 102.5, 0],
    ])
    report = edl.load_csv("NIFTY", str(csv), db_path=db)
    assert report["accepted"] is True
    assert report["rows_inserted"] == 3
    assert report["duplicate_timestamps"] == 0
    assert report["bad_ohlc_rows"] == 0

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT symbol, source, interval FROM candles LIMIT 1"
    ).fetchone()
    assert rows == ("NIFTY", edl.SOURCE_TAG, "1m")


def test_load_csv_rejects_bad_ohlc(tmp_path):
    db = tmp_path / "ext.db"
    csv = tmp_path / "bad.csv"
    _write_csv(csv, [
        # high < low: structurally impossible
        ["2020-01-01 09:15:00", 100, 90, 99, 100.5, 0],
    ])
    report = edl.load_csv("NIFTY", str(csv), db_path=db)
    assert report["accepted"] is False
    assert report["bad_ohlc_rows"] == 1
    assert not db.exists() or sqlite3.connect(str(db)).execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='candles'"
    ).fetchone()[0] == 0


def test_load_csv_rejects_duplicate_timestamps(tmp_path):
    db = tmp_path / "ext.db"
    csv = tmp_path / "dup.csv"
    _write_csv(csv, [
        ["2020-01-01 09:15:00", 100, 101, 99, 100.5, 0],
        ["2020-01-01 09:15:00", 100, 101, 99, 100.5, 0],
    ])
    report = edl.load_csv("NIFTY", str(csv), db_path=db)
    assert report["accepted"] is False
    assert report["duplicate_timestamps"] == 1


def test_resample_1m_to_5m_uses_correct_ohlcv_aggregation(tmp_path):
    db = tmp_path / "ext.db"
    csv = tmp_path / "nifty.csv"
    rows = []
    # 10 one-minute bars starting 09:15 -> two clean 5-minute buckets
    prices = [100, 102, 99, 103, 101, 104, 98, 105, 100, 103]
    for i, p in enumerate(prices):
        minute = 15 + i
        rows.append([f"2020-01-01 09:{minute:02d}:00", p, p + 1, p - 1, p + 0.5, 10])
    _write_csv(csv, rows)
    edl.load_csv("NIFTY", str(csv), db_path=db)

    report = edl.resample_and_store("NIFTY", "1m", "5m", db_path=db)
    assert report["rows_stored"] == 2

    conn = sqlite3.connect(str(db))
    df = pd.read_sql_query(
        "SELECT * FROM candles WHERE interval='5m' ORDER BY timestamp", conn,
    )
    assert len(df) == 2
    first_bucket_prices = prices[0:5]
    assert df.iloc[0]["open"] == first_bucket_prices[0]           # first
    assert df.iloc[0]["close"] == first_bucket_prices[-1] + 0.5   # last close
    assert df.iloc[0]["high"] == max(first_bucket_prices) + 1     # max high
    assert df.iloc[0]["low"] == min(first_bucket_prices) - 1      # min low
    assert df.iloc[0]["volume"] == 50                             # sum of 5 * 10


def test_resample_missing_source_data_returns_error(tmp_path):
    db = tmp_path / "ext.db"
    report = edl.resample_and_store("NOPE", "1m", "5m", db_path=db)
    assert "error" in report


def test_ingested_data_is_isolated_by_source_tag(tmp_path):
    """The whole point of a separate source tag: never confusable with
    live-collected candle_cache.db data even if queried from the same table
    shape."""
    db = tmp_path / "ext.db"
    csv = tmp_path / "nifty.csv"
    _write_csv(csv, [["2020-01-01 09:15:00", 100, 101, 99, 100.5, 0]])
    edl.load_csv("NIFTY", str(csv), db_path=db)

    conn = sqlite3.connect(str(db))
    sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM candles")]
    assert sources == [edl.SOURCE_TAG]
    assert edl.SOURCE_TAG != "angel_live"
