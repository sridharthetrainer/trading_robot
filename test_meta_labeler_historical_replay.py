import sqlite3

import pandas as pd

import meta_labeler_historical_replay as mlhr


def _seed_trending_symbol(conn, symbol, n_days=140):
    """A clean uptrend with periodic pullbacks -- enough breakout triggers
    and enough history for pivots/ATR/EMA warmup to all be well-defined."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS candles (symbol TEXT, interval TEXT, timestamp TEXT, "
        "open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
    price = 100.0
    ts0 = pd.Timestamp("2023-01-02")
    rows = []
    for i in range(n_days):
        ts = ts0 + pd.Timedelta(days=i)
        if ts.dayofweek >= 5:
            continue
        # Sharp breakout jump every ~12 sessions, small pullback drift
        # otherwise -- guarantees the 10-day-high/low breakout rule
        # actually triggers periodically, unlike a smooth steady drift
        # (which the daily high/low buffer would otherwise swamp).
        drift = 4.0 if i % 12 == 0 else -0.4
        o = price
        c = price + drift
        h, l = max(o, c) + 0.2, min(o, c) - 0.2
        vol = 100000 + (5000 if i % 10 == 0 else 0)
        rows.append((symbol, "1d", ts.isoformat(), o, h, l, c, vol))
        price = c
    conn.executemany(
        "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def test_deep_symbols_filters_by_bar_count(tmp_path, monkeypatch):
    db = tmp_path / "candles.db"
    conn = sqlite3.connect(db)
    _seed_trending_symbol(conn, "DEEP", n_days=600)
    _seed_trending_symbol(conn, "SHALLOW", n_days=50)
    conn.commit()

    deep = mlhr._deep_symbols(conn, min_bars=400)
    assert "DEEP" in deep
    assert "SHALLOW" not in deep
    conn.close()


def test_build_symbol_observations_produces_labelled_rows(tmp_path):
    db = tmp_path / "candles.db"
    conn = sqlite3.connect(db)
    _seed_trending_symbol(conn, "TEST", n_days=140)
    df = mlhr._load_daily(conn, "TEST")
    conn.close()

    obs = mlhr._build_symbol_observations("TEST", df)
    assert len(obs) > 0
    for o in obs:
        assert o["tb_label"] in (-1, 0, 1)
        assert o["meta_label"] in (0, 1)
        assert o["side_buy"] in (0, 1)
        assert o["symbol"] == "TEST"
        # no lookahead: pivot features must be finite, not NaN/inf from an
        # empty prior-week/month slice
        assert o["pct_from_weekly_r1"] == o["pct_from_weekly_r1"]  # not NaN


def test_run_end_to_end_on_synthetic_universe(tmp_path, monkeypatch):
    db = tmp_path / "candles.db"
    conn = sqlite3.connect(db)
    # >500 real trading days needed: _deep_symbols' min_bars default is
    # bound at import time, so monkeypatching MIN_DAILY_BARS afterward
    # would not reach it -- seed comfortably past the real default instead.
    for sym in ("AAA", "BBB", "CCC"):
        _seed_trending_symbol(conn, sym, n_days=950)
    conn.commit()
    conn.close()

    monkeypatch.setattr(mlhr, "CANDLE_DB", str(db))
    monkeypatch.setattr(mlhr, "REPORT_FILE", tmp_path / "report.json")

    report = mlhr.run()
    assert report.get("error") is None
    assert report["n_symbols"] == 3
    assert report["distinct_days_total"] > 100
    assert 0.0 <= report["auc"] <= 1.0
    assert mlhr.REPORT_FILE.exists()
