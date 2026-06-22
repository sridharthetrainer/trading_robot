import pandas as pd
import sqlite3


def test_intraday_spacing_rejects_daily_shaped_data():
    import intraday_candle_recorder as rec

    idx = pd.date_range("2026-06-18", periods=5, freq="D")
    df = pd.DataFrame({
        "open": [1, 2, 3, 4, 5],
        "high": [2, 3, 4, 5, 6],
        "low": [0, 1, 2, 3, 4],
        "close": [1, 2, 3, 4, 5],
        "volume": [100] * 5,
    }, index=idx)

    ok, median = rec._interval_spacing_ok(df, "5m")
    assert ok is False
    assert median >= 1440


def test_resample_intraday_1m_to_5m_valid():
    import intraday_candle_recorder as rec

    idx = pd.date_range("2026-06-22 09:15", periods=20, freq="min")
    df = pd.DataFrame({
        "open": range(20),
        "high": [v + 1 for v in range(20)],
        "low": [v - 1 for v in range(20)],
        "close": range(20),
        "volume": [10] * 20,
    }, index=idx)

    out = rec._resample_intraday(df, "5m")
    ok, median = rec._interval_spacing_ok(out, "5m")
    assert len(out) == 4
    assert ok is True
    assert median == 5


def test_save_verified_candles_normalizes_columns(tmp_path, monkeypatch):
    import candle_cache
    import intraday_candle_recorder as rec

    monkeypatch.setattr(candle_cache, "_DB_PATH", tmp_path / "candles.db")
    monkeypatch.setattr(candle_cache, "_INIT_DONE", False)
    idx = pd.date_range("2026-06-22 09:15", periods=3, freq="min")
    df = pd.DataFrame({
        "Open": [100, 101, 102],
        "High": [101, 102, 103],
        "Low": [99, 100, 101],
        "Close": [100.5, 101.5, 102.5],
        "Volume": [10, 11, 12],
    }, index=idx)

    inserted = rec._save_verified_candles("NIFTY", "1m", df)
    cached = candle_cache.get_cached_candles("NIFTY", "1m", days=10)
    assert inserted == 3
    assert cached is not None
    assert len(cached) == 3


def test_require_today_rejects_stale_bar(monkeypatch):
    import intraday_candle_recorder as rec

    class FakeTimestamp(pd.Timestamp):
        @classmethod
        def now(cls, tz=None):
            return pd.Timestamp("2026-06-22 16:00", tz=tz)

    monkeypatch.setattr(rec.pd, "Timestamp", FakeTimestamp)
    idx = pd.date_range("2026-06-19 09:15", periods=10, freq="5min")
    df = pd.DataFrame({
        "open": range(10),
        "high": [v + 1 for v in range(10)],
        "low": [v - 1 for v in range(10)],
        "close": range(10),
        "volume": [10] * 10,
    }, index=idx)

    assert rec._has_fresh_trading_bar(df, require_today=True) is False
    assert rec._has_fresh_trading_bar(df, require_today=False) is True


def test_data_fetcher_rejects_daily_bars_for_intraday_interval():
    from data_fetcher import DataFetcher

    fetcher = DataFetcher.__new__(DataFetcher)
    idx = pd.date_range("2026-06-18", periods=5, freq="D")
    df = pd.DataFrame({
        "open": [1, 2, 3, 4, 5],
        "high": [2, 3, 4, 5, 6],
        "low": [0, 1, 2, 3, 4],
        "close": [1, 2, 3, 4, 5],
        "volume": [100] * 5,
    }, index=idx)

    assert fetcher._data_matches_interval("5m", df) is False
    assert fetcher._data_matches_interval("1d", df) is True


def test_data_fetcher_accepts_intraday_shaped_bars():
    from data_fetcher import DataFetcher

    fetcher = DataFetcher.__new__(DataFetcher)
    idx = pd.date_range("2026-06-22 09:15", periods=10, freq="5min")
    df = pd.DataFrame({
        "open": range(10),
        "high": [v + 1 for v in range(10)],
        "low": [v - 1 for v in range(10)],
        "close": range(10),
        "volume": [100] * 10,
    }, index=idx)

    assert fetcher._data_matches_interval("5m", df) is True


def test_data_quality_watchdog_flags_stale_intraday_cache(tmp_path):
    from data_quality_watchdog import audit_candle_cache

    db = tmp_path / "candles.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE candles (
            symbol TEXT,
            interval TEXT,
            timestamp TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER
        )
    """)
    idx = pd.date_range("2026-06-19 09:15", periods=8, freq="5min")
    for ts in idx:
        conn.execute(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)",
            ("NIFTY", "5m", str(ts), 100, 101, 99, 100, 10),
        )
    conn.commit()
    conn.close()

    report = audit_candle_cache(str(db), max_intraday_age_days=0.1)
    assert report["stale_groups"] == 1
    assert report["bad_groups"] == 1
    assert report["checks"][0]["freshness_ok"] is False
