import pandas as pd
import sqlite3


def test_index_price_floor_rejects_wrong_instrument(tmp_path, monkeypatch):
    import data_fetcher

    fetcher = data_fetcher.DataFetcher.__new__(data_fetcher.DataFetcher)
    fetcher.cache = {}
    monkeypatch.setattr(fetcher, "_persist_intraday_candles", lambda *a, **k: None)
    idx = pd.date_range("2026-06-29 10:00", periods=4, freq="5min")
    wrong = pd.DataFrame({"open": 880, "high": 881, "low": 879, "close": 880}, index=idx)
    assert fetcher._accept_market_data("x", "MIDCPNIFTY", "5m", wrong, "test") is None
    right = wrong * 16
    assert fetcher._accept_market_data("x", "MIDCPNIFTY", "5m", right, "test") is right


def test_angel_full_quote_liquidity_extraction():
    from angel_option_chain import AngelOptionChainEngine

    engine = AngelOptionChainEngine.__new__(AngelOptionChainEngine)
    quote = {
        "tradeVolume": 12345,
        "bestFiveBuy": [{"price": 101.2, "quantity": 75}],
        "bestFiveSell": [{"price": 101.4, "quantity": 50}],
    }
    assert engine._extract_liquidity_fields_from_quote(quote) == {
        "volume": 12345.0,
        "bid": 101.2,
        "ask": 101.4,
        "bid_qty": 75.0,
        "ask_qty": 50.0,
    }


def test_quarantine_signal_log_retires_scale_mismatch(tmp_path):
    from signal_quality import quarantine_signal_log

    db = tmp_path / "signals.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE signal_log (id INTEGER PRIMARY KEY, entry_price REAL, "
            "outcome_price REAL, tb_label INTEGER, tb_r_multiple REAL, "
            "tb_r_multiple_net REAL, training_eligible INTEGER, "
            "training_exclusion_reason TEXT)"
        )
        con.execute("INSERT INTO signal_log VALUES (1,880,14300,1,1500,1499,1,'')")
        con.execute("INSERT INTO signal_log VALUES (2,100,102,1,2,1.8,1,'')")
    result = quarantine_signal_log(str(db))
    assert result["quarantined"] == 1
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT tb_label,tb_r_multiple,training_eligible,training_exclusion_reason "
            "FROM signal_log ORDER BY id"
        ).fetchall()
    assert rows[0] == (-2, 0.0, 0, "data_quality_scale_mismatch")
    assert rows[1][:3] == (1, 2.0, 1)


def test_upstox_v2_resamples_supported_base_without_relabelling():
    from upstox_data import _resample_v2

    idx = pd.date_range("2026-06-29 09:15", periods=15, freq="1min", tz="Asia/Kolkata")
    base = pd.DataFrame({
        "open": range(100, 115), "high": range(101, 116),
        "low": range(99, 114), "close": range(100, 115), "volume": 10,
    }, index=idx)
    bars = _resample_v2(base, "5m", "1minute")
    assert len(bars) == 3
    assert bars.iloc[0].to_dict() == {
        "open": 100, "high": 105, "low": 99, "close": 104, "volume": 50,
    }


def test_option_chain_liquidity_requires_volume_and_book():
    from option_chain_fetcher import NSEOptionChainFetcher

    sparse = {"records": {"data": [{"CE": {
        "totalTradedVolume": 0, "bidprice": 0, "askPrice": 0,
    }}]}}
    rich = {"records": {"data": [{"PE": {
        "totalTradedVolume": 1000, "bidprice": 10.0, "askPrice": 10.1,
    }}]}}
    assert not NSEOptionChainFetcher._has_liquidity_fields(sparse)
    assert NSEOptionChainFetcher._has_liquidity_fields(rich)


def test_option_audit_counts_verified_strike_outcomes_for_evidence():
    from option_bot_audit import _score_option_bot

    audit = {
        "option_chain_snapshots": {
            "rows": 100, "ok_rows": 100, "verified_live_rows": 100,
            "latest_ok_age_hours": 1, "verified_strike_outcomes": 150,
            "today_strike_rows": 20,
        },
        "decision_journal": {"exists": True, "rows": 20, "decisions": {}},
        "strike_autotune": {}, "historical_options": {"rows": 100000},
        "signal_log": {"exists": True}, "telegram_oi_tools": {}, "automation": {},
    }
    score = _score_option_bot(audit)
    assert "insufficient_verified_option_signal_outcomes" not in score["evidence_blocks"]


def test_manual_tracker_marks_token_missing_session_down(monkeypatch):
    from manual_trade_tracker import ManualTradeTracker

    class Angel:
        obj = object()

    tracker = ManualTradeTracker.__new__(ManualTradeTracker)
    tracker._angel = Angel()
    tracker._last_angel_attempt = 123.0
    monkeypatch.setattr(tracker, "_ensure_angel", lambda: False)
    response = {"success": False, "errorCode": "AG8003", "message": "Token missing"}
    assert tracker._angel_response_invalid(response, "test")
    assert tracker._angel.obj is None
    assert tracker._last_angel_attempt == 0.0
