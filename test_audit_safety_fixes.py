import json
import sqlite3

import pandas as pd


def test_candle_cache_rejects_zero_ohlc(tmp_path, monkeypatch):
    import candle_cache

    db_path = tmp_path / "candle_cache.db"
    monkeypatch.setattr(candle_cache, "_DB_PATH", db_path)
    monkeypatch.setattr(candle_cache, "_INIT_DONE", False)

    # Use RECENT timestamps (relative to now) so the days=10 lookback always
    # includes them — hardcoded dates make this test go stale as wall-clock passes.
    _base = pd.Timestamp.now().normalize() + pd.Timedelta(hours=9, minutes=15)
    df = pd.DataFrame(
        [
            {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 100},
            {"open": 100, "high": 103, "low": 99, "close": 102, "volume": 200},
            {"open": 102, "high": 104, "low": 101, "close": 103, "volume": 250},
        ],
        index=[_base, _base + pd.Timedelta(minutes=5), _base + pd.Timedelta(minutes=10)],
    )

    assert candle_cache.save_candles("NIFTY", "5m", df) == 2
    cached = candle_cache.get_cached_candles("NIFTY", "5m", days=10)

    assert cached is not None
    assert len(cached) == 2
    assert (cached[["open", "high", "low", "close"]] > 0).all().all()


def test_candle_cache_read_ignores_existing_zero_ohlc(tmp_path, monkeypatch):
    import candle_cache

    db_path = tmp_path / "candle_cache.db"
    monkeypatch.setattr(candle_cache, "_DB_PATH", db_path)
    monkeypatch.setattr(candle_cache, "_INIT_DONE", False)

    # RECENT timestamps (relative to now) so the days=10 lookback always includes
    # them — hardcoded dates make this test go stale as wall-clock time passes.
    import pandas as pd
    base = (pd.Timestamp.now().normalize() + pd.Timedelta(hours=9, minutes=15))
    ts = [(base + pd.Timedelta(minutes=5 * k)).strftime("%Y-%m-%dT%H:%M:%S") for k in range(3)]

    conn = sqlite3.connect(db_path)
    candle_cache._get_conn().close()
    conn.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
                 ("NIFTY", "5m", ts[0], 0, 0, 0, 0, 100))
    conn.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
                 ("NIFTY", "5m", ts[1], 100, 102, 99, 101, 200))
    conn.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
                 ("NIFTY", "5m", ts[2], 101, 103, 100, 102, 250))
    conn.commit()
    conn.close()

    cached = candle_cache.get_cached_candles("NIFTY", "5m", days=10)

    assert cached is not None
    assert len(cached) == 2
    assert float(cached["close"].min()) > 0


def test_dashboard_validation_mentions_failed_holdout(tmp_path, monkeypatch):
    import daily_dashboard

    payload = {
        "last_run": "2026-06-20",
        "results": {
            "supertrend_mtf": {
                "verdict": "FAIL",
                "deflated_sharpe": 1.0,
                "holdout_pnl": None,
                "holdout_sharpe": None,
                "holdout_trades": None,
            }
        },
    }
    (tmp_path / "validation_results.json").write_text(json.dumps(payload))
    monkeypatch.chdir(tmp_path)

    lines = daily_dashboard._validation_section()
    text = "\n".join(lines)

    assert "verdict=FAIL" in text
    assert "no locked-holdout PASS" in text
    assert "live edge gate" in text


def test_live_payload_preserves_market_profile_levels():
    from live_signal_engine import LiveSignalEngine

    engine = LiveSignalEngine.__new__(LiveSignalEngine)
    profile = {
        "score_modifier": 0.25,
        "poc": 101.5,
        "vah": 103.0,
        "val": 99.0,
        "poc_distance_pct": 0.4,
        "value_width_pct": 3.9,
        "profile_bias": "VALUE_ACCEPTANCE",
        "profile_position": "UPPER_VALUE",
        "acceptance_state": "ACCEPTED_ABOVE_VALUE",
    }
    payload = engine._candidate_signal_log_payload({
        "symbol": "NIFTY",
        "score": 9.0,
        "signal": {
            "symbol": "NIFTY",
            "side": "BUY",
            "strategy": "profile_test",
            "price": 102.0,
            "score": 9.0,
            "market_profile": profile,
        },
    })

    assert payload["profile_poc"] == 101.5
    assert payload["profile_vah"] == 103.0
    assert payload["profile_val"] == 99.0
    assert payload["profile_value_width_pct"] == 3.9
    assert payload["profile_acceptance"] == "ACCEPTED_ABOVE_VALUE"
    assert payload["metadata"]["market_profile"]["poc"] == 101.5


def test_signal_logger_writes_market_profile_levels(tmp_path):
    from signal_log import SignalLogger

    db_path = tmp_path / "signal_log.db"
    logger = SignalLogger(db_path=str(db_path))
    profile = {
        "score_modifier": 0.25,
        "poc": 101.5,
        "vah": 103.0,
        "val": 99.0,
        "poc_distance_pct": 0.4,
        "value_width_pct": 3.9,
        "profile_bias": "VALUE_ACCEPTANCE",
        "profile_position": "UPPER_VALUE",
        "acceptance_state": "ACCEPTED_ABOVE_VALUE",
    }

    row_id = logger.log_candidate({
        "symbol": "NIFTY",
        "side": "BUY",
        "strategy": "profile_test",
        "score": 9.0,
        "price": 102.0,
        "metadata": {"market_profile": profile, "market_profile_mod": 0.25},
    })

    assert row_id is not None
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT profile_poc, profile_vah, profile_val, profile_value_width_pct, "
        "profile_bias, profile_position, profile_acceptance, market_profile_mod "
        "FROM signal_log WHERE id=?",
        (row_id,),
    ).fetchone()
    conn.close()

    assert row == (
        101.5,
        103.0,
        99.0,
        3.9,
        "VALUE_ACCEPTANCE",
        "UPPER_VALUE",
        "ACCEPTED_ABOVE_VALUE",
        0.25,
    )


def test_live_engine_logs_shadow_strategy_candidates(tmp_path, monkeypatch):
    import live_signal_engine
    from live_signal_engine import LiveSignalEngine
    from signal_log import SignalLogger

    db_path = tmp_path / "signal_log.db"
    logger = SignalLogger(db_path=str(db_path))
    monkeypatch.setattr(live_signal_engine, "_SIG_LOG_AVAIL", True)
    monkeypatch.setattr(live_signal_engine, "_get_sig_log", lambda: logger)
    monkeypatch.setattr(live_signal_engine.cfg, "SHADOW_LOG_STRATEGY_CANDIDATES", True, raising=False)
    monkeypatch.setattr(live_signal_engine.cfg, "SHADOW_MAX_CANDIDATES_PER_SYMBOL", 5, raising=False)

    engine = LiveSignalEngine.__new__(LiveSignalEngine)
    engine._log_shadow_strategy_candidates(
        symbol="NIFTY",
        india_vix=14.5,
        signal={
            "symbol": "NIFTY",
            "price": 102.0,
            "regime": "TRENDING",
            "htf_bias": "BULLISH",
            "shadow_candidates": [
                {
                    "symbol": "NIFTY",
                    "side": "BUY",
                    "strategy": "near_miss",
                    "score": 3.2,
                    "raw_score": 2.7,
                    "reason": "shadow_post_confluence_below_min",
                    "metadata": {"shadow_reason": "shadow_post_confluence_below_min"},
                }
            ],
        },
    )

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT symbol, side, strategy, score, executed, rejection_reason, india_vix "
        "FROM signal_log"
    ).fetchone()
    conn.close()

    assert row == ("NIFTY", "BUY", "near_miss", 3.2, 0, "shadow_post_confluence_below_min", 14.5)
