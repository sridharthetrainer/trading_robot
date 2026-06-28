import json
import sqlite3
from datetime import date

import pandas as pd


def test_calendar_freshness_uses_sessions_not_weekend_age(tmp_path):
    from trading_calendar import is_trading_day, latest_expected_session, session_lag

    holiday_file = tmp_path / "holidays.json"
    holiday_file.write_text(json.dumps(["2026-06-26"]))
    assert not is_trading_day(date(2026, 6, 26), holiday_path=holiday_file)
    assert latest_expected_session(date(2026, 6, 28), holiday_path=holiday_file) == date(2026, 6, 25)
    # Project calendar contains the same exchange holiday.
    assert session_lag("2026-06-25T15:30:00+05:30", date(2026, 6, 28)) == 0


def test_option_recorder_persists_live_provenance(tmp_path, monkeypatch):
    import option_chain_fetcher
    from option_chain_recorder import record_option_chain_snapshot

    class Result:
        spot = 25000.0
        expiry = "2026-07-02"
        atm_strike = 25000.0
        summary = {"pcr_oi": 1.1}
        dataframe = pd.DataFrame([{
            "strikePrice": 25000, "CE_OI": 1000, "PE_OI": 1200,
            "CE_LTP": 100, "PE_LTP": 90, "CE_VOLUME": 500, "PE_VOLUME": 600,
        }])

    class Fetcher:
        last_source = "angel"
        last_request_id = "request-1"
        def __init__(self, underlying): self.underlying = underlying
        def fetch_and_analyze(self): return Result()

    monkeypatch.setattr(option_chain_fetcher, "NSEOptionChainFetcher", Fetcher)
    db = tmp_path / "options.db"
    result = record_option_chain_snapshot("NIFTY", db_path=str(db))
    assert result["ok"] is True
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT source,is_live,provider_request_id FROM option_chain_snapshots"
        ).fetchone()
    assert row == ("angel", 1, "request-1")


def test_option_journal_marks_synthetic_research(tmp_path):
    from option_decision_journal import record_option_decision

    row = record_option_decision(
        strategy="PIVOT_SCALPING", symbol="NIFTY", decision="selected",
        reason="backfill_signal_log", side="BUY", spot=0,
        selected={"symbol": "NIFTY25000CE", "strike": 25000, "option_type": "CE", "premium": 50},
        path=str(tmp_path / "journal.jsonl"),
    )
    assert row["evidence_class"] == "RESEARCH_SYNTHETIC"
    assert row["is_live_data"] is False


def test_candidate_payload_derives_signal_time_risk_levels():
    from live_signal_engine import LiveSignalEngine

    engine = LiveSignalEngine.__new__(LiveSignalEngine)
    row = engine._candidate_signal_log_payload({
        "symbol": "NIFTY", "score": 8,
        "signal": {"symbol": "NIFTY", "side": "BUY", "price": 100, "strategy": "test", "atr": 2},
    })
    assert row["stop_loss"] == 98
    assert row["target"] == 103
    assert row["rr"] == 1.5
    assert row["risk_level_source"] == "signal_atr"


def test_signal_training_requires_valid_risk_and_session(tmp_path, monkeypatch):
    import trading_calendar
    from signal_log import SignalLogger

    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda *_args, **_kwargs: True)
    logger = SignalLogger(db_path=str(tmp_path / "signals.db"))
    valid = logger.log_candidate({
        "symbol": "NIFTY", "side": "BUY", "entry_price": 100,
        "stop_loss": 98, "target": 104, "strategy": "valid",
    })
    invalid = logger.log_candidate({
        "symbol": "NIFTY", "entry_price": 100, "strategy": "invalid",
    })
    with sqlite3.connect(tmp_path / "signals.db") as conn:
        rows = conn.execute(
            "SELECT id,training_eligible,training_exclusion_reason FROM signal_log ORDER BY id"
        ).fetchall()
    assert rows[0][0] == valid and rows[0][1] == 1
    assert rows[1][0] == invalid and rows[1][1] == 0
    assert "missing_or_invalid_risk_levels" in rows[1][2]


def test_signal_logger_derives_risk_for_every_generated_signal(tmp_path, monkeypatch):
    import trading_calendar
    from signal_log import SignalLogger

    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda *_args, **_kwargs: True)
    logger = SignalLogger(db_path=str(tmp_path / "signals.db"))
    row_id = logger.log_candidate({
        "symbol": "SBIN", "side": "SELL", "entry_price": 100,
        "strategy": "generated_shadow", "style": "scalping",
    })
    with sqlite3.connect(tmp_path / "signals.db") as conn:
        row = conn.execute(
            "SELECT stop_loss,target,rr,risk_level_source,training_eligible "
            "FROM signal_log WHERE id=?", (row_id,),
        ).fetchone()
    assert row == (100.5, 99.25, 1.5, "signal_policy_scalping", 1)


def test_live_order_requires_algo_tag(tmp_path, monkeypatch):
    import execution_compliance

    monkeypatch.setattr(execution_compliance, "AUDIT_FILE", tmp_path / "audit.jsonl")
    allowed, reason = execution_compliance.preflight_order(
        symbol="NIFTY25000CE", qty=65, side="BUY", order_type="LIMIT",
        price=100, exchange="NFO", order_tag="", live=True,
    )
    assert not allowed
    assert reason == "live_algo_order_tag_missing"


def test_shadow_option_execution_is_after_cost():
    from shadow_execution import simulate_option_round_trip

    flat = simulate_option_round_trip(100, 100, qty=65, slippage_pct_per_leg=0.005)
    assert flat["gross_pnl"] < 0
    assert flat["estimated_costs"] > 0
    assert flat["net_pnl"] < flat["gross_pnl"]
    assert flat["label"] == -1
