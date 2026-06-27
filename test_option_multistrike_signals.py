import json
import sqlite3

from option_multistrike_signals import (
    build_multistrike_signals,
    latest_flow_scores,
    persist_multistrike_signals,
)


def _row(strike, ce_price, ce_oi, ce_volume, pe_price=100, pe_oi=1000, pe_volume=1000):
    return {
        "strikePrice": strike,
        "CE_lastPrice": ce_price,
        "CE_openInterest": ce_oi,
        "CE_totalTradedVolume": ce_volume,
        "CE_bidPrice": ce_price - 0.5,
        "CE_askPrice": ce_price + 0.5,
        "PE_lastPrice": pe_price,
        "PE_openInterest": pe_oi,
        "PE_totalTradedVolume": pe_volume,
        "PE_bidPrice": pe_price - 0.5,
        "PE_askPrice": pe_price + 0.5,
    }


def test_multistrike_flow_emits_ranked_ce_and_pe_signals():
    previous = [_row(25000, 100, 1000, 1000)]
    current = [_row(25000, 110, 1200, 1500)]

    signals = build_multistrike_signals(
        underlying="NIFTY",
        current_rows=current,
        previous_rows=previous,
        source="nse_live",
        top_n_per_side=5,
    )
    ce = next(item for item in signals if item.option_type == "CE")

    assert len(signals) == 2
    assert ce.flow == "LONG_BUILDUP"
    assert ce.signal == "BUY_CE"
    assert ce.tradable is True
    assert ce.score >= 60


def test_multistrike_flow_warmup_never_marks_tradeable():
    signals = build_multistrike_signals(
        underlying="NIFTY",
        current_rows=[_row(25000, 110, 1200, 1500)],
        previous_rows=[],
    )

    assert signals
    assert not any(item.tradable for item in signals)
    assert all("warmup_no_previous_snapshot" in item.reason for item in signals)


def test_directional_signal_remains_shadow_when_liquidity_is_missing():
    previous = [_row(25000, 100, 1000, 0)]
    current = [_row(25000, 115, 1400, 0)]
    for row in previous + current:
        row.pop("CE_bidPrice")
        row.pop("CE_askPrice")

    signals = build_multistrike_signals(
        underlying="NIFTY", current_rows=current, previous_rows=previous
    )
    ce = next(item for item in signals if item.option_type == "CE")

    assert ce.signal == "BUY_CE"
    assert ce.tradable is False
    assert ce.score >= 60


def test_persisted_flow_is_available_to_strike_ranker(tmp_path):
    db = tmp_path / "snapshots.db"
    previous = [_row(25000, 100, 1000, 1000)]
    current = [_row(25000, 110, 1200, 1500)]
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE option_chain_snapshots (
                ts REAL, snapshot_time TEXT, underlying TEXT, expiry TEXT,
                ok INTEGER, rows_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)",
            (1, "2026-06-25T10:00:00+0530", "NIFTY", "2026-06-30", 1, json.dumps(previous)),
        )
        result = persist_multistrike_signals(
            conn=conn,
            snapshot_time="2026-06-25T10:05:00+0530",
            underlying="NIFTY",
            expiry="2026-06-30",
            current_rows=current,
            source="nse_live",
        )
        conn.commit()

    scores = latest_flow_scores("NIFTY", "CE", db_path=str(db), max_age_sec=10**9)

    assert result["written"] == 2
    assert result["tradable"] >= 1
    assert scores[25000.0]["tradable"] is True
