import json
import sqlite3

from option_multistrike_signals import (
    build_multistrike_signals,
    build_multistrike_edge_model,
    ensure_multistrike_schema,
    label_multistrike_outcomes,
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
    assert ce.score < 60


def test_regime_gate_blocks_countertrend_side_and_keeps_aligned_side():
    previous = [_row(25000, 100, 1000, 1000, pe_price=100, pe_oi=1000, pe_volume=1000)]
    current = [_row(25000, 112, 1300, 1700, pe_price=112, pe_oi=1300, pe_volume=1700)]

    signals = build_multistrike_signals(
        underlying="NIFTY", current_rows=current, previous_rows=previous,
        market_regime="WEAK_TREND", market_bias="BEARISH",
    )
    ce = next(item for item in signals if item.option_type == "CE")
    pe = next(item for item in signals if item.option_type == "PE")

    assert ce.regime_aligned is False
    assert ce.tradable is False
    assert "counter_regime_direction" in ce.reason
    assert pe.regime_aligned is True
    assert pe.tradable is True


def test_correlated_strikes_are_deduplicated_for_execution():
    previous = [_row(25000, 100, 1000, 1000), _row(25050, 100, 1000, 1000)]
    current = [_row(25000, 112, 1300, 1700), _row(25050, 111, 1250, 1600)]

    signals = build_multistrike_signals(
        underlying="NIFTY", current_rows=current, previous_rows=previous,
        market_bias="BULLISH", max_tradable_per_snapshot=2,
    )
    ce_rows = [row for row in signals if row.option_type == "CE"]

    assert sum(row.tradable for row in ce_rows) == 1
    assert any("correlated_strike_deduplicated" in row.reason for row in ce_rows)
    assert len({row.score for row in ce_rows}) > 1


def test_persisted_flow_is_available_to_strike_ranker(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OPTION_EDGE_POLICY_REQUIRE_PROMISING", False)
    monkeypatch.setattr(config, "OPTION_FOCUSED_PAPER_ENABLED", False)
    monkeypatch.setenv("OPTION_EDGE_POLICY_REQUIRE_PROMISING", "false")
    monkeypatch.setenv("OPTION_FOCUSED_PAPER_ENABLED", "false")
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


def test_focused_paper_funnel_blocks_repeated_correlated_snapshot(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OPTION_EDGE_POLICY_REQUIRE_PROMISING", False)
    monkeypatch.setattr(config, "OPTION_FOCUSED_PAPER_ENABLED", True)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(config, "PAPER_CAPITAL", 100000.0)
    monkeypatch.setenv("OPTION_EDGE_POLICY_REQUIRE_PROMISING", "false")
    monkeypatch.setenv("OPTION_FOCUSED_PAPER_ENABLED", "true")
    monkeypatch.setenv("OPTION_FOCUSED_MIN_SCORE", "70")
    monkeypatch.setenv("OPTION_FOCUSED_COOLDOWN_SEC", "3600")

    db = tmp_path / "snapshots.db"
    first = [_row(25000, 100, 1000, 1000)]
    second = [_row(25000, 110, 1200, 1500)]
    third = [_row(25000, 121, 1440, 2200)]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE option_chain_snapshots "
            "(ts REAL,snapshot_time TEXT,underlying TEXT,expiry TEXT,ok INTEGER,rows_json TEXT)"
        )
        conn.execute("INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)", (
            1, "2026-06-25T10:00:00+05:30", "NIFTY", "2026-06-30", 1, json.dumps(first),
        ))
        first_result = persist_multistrike_signals(
            conn=conn, snapshot_time="2026-06-25T10:05:00+05:30",
            underlying="NIFTY", expiry="2026-06-30", current_rows=second, source="angel",
        )
        conn.execute("INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)", (
            2, "2026-06-25T10:05:00+05:30", "NIFTY", "2026-06-30", 1, json.dumps(second),
        ))
        second_result = persist_multistrike_signals(
            conn=conn, snapshot_time="2026-06-25T10:15:00+05:30",
            underlying="NIFTY", expiry="2026-06-30", current_rows=third, source="angel",
        )
        qualified = conn.execute(
            "SELECT focused_paper,paper_risk_budget,expected_move_pct,break_even_move_pct "
            "FROM option_strike_signals WHERE snapshot_time=? AND option_type='CE'",
            ("2026-06-25T10:05:00+05:30",),
        ).fetchone()
        repeated = conn.execute(
            "SELECT tradable,reason FROM option_strike_signals "
            "WHERE snapshot_time=? AND option_type='CE'",
            ("2026-06-25T10:15:00+05:30",),
        ).fetchone()

    assert first_result["tradable"] == 1
    assert second_result["tradable"] == 0
    assert qualified[0] == 1
    assert qualified[1] == 250.0
    assert qualified[2] > qualified[3]
    assert repeated[0] == 0
    assert "focused_temporal_dedup" in repeated[1]


def test_persist_requires_promising_edge_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTION_EDGE_POLICY_REQUIRE_PROMISING", raising=False)
    import config
    monkeypatch.setattr(config, "OPTION_EDGE_POLICY_REQUIRE_PROMISING", True)
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
        stored = conn.execute(
            "SELECT tradable,edge_policy,reason FROM option_strike_signals "
            "WHERE snapshot_time='2026-06-25T10:05:00+0530' AND option_type='CE'"
        ).fetchone()

    assert result["tradable"] == 0
    assert stored[0] == 0
    assert stored[1] == "VALIDATING"
    assert "edge_still_validating" in stored[2]


def test_persist_blocks_quarantined_negative_forward_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTION_EDGE_POLICY_BLOCK_QUARANTINED", "true")
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
        ensure_multistrike_schema(conn)
        # Dates land 1-2 days before the 2026-06-30 expiry (dte:1-2 bucket)
        # to match the actual call under test below (snapshot 06-29,
        # expiry 06-30 -> 1 day) now that cohort_policy filters by DTE
        # bucket too (2026-07-15).
        for idx in range(30):
            conn.execute(
                """INSERT INTO option_strike_signals
                   (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,direction,
                    score,tradable,price,source,outcome_label,net_pnl,net_r)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idx,
                    f"2026-06-{28 + (idx % 2):02d}T10:00:00+05:30",
                    "NIFTY",
                    "2026-06-30",
                    24000 + idx,
                    "CE",
                    "LONG_BUILDUP",
                    "BUY_CE",
                    "BULLISH",
                    70,
                    0,
                    100,
                    "angel",
                    -1,
                    -100,
                    -0.2,
                ),
            )
        conn.execute(
            "INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)",
            (1, "2026-06-29T10:00:00+0530", "NIFTY", "2026-06-30", 1, json.dumps(previous)),
        )
        result = persist_multistrike_signals(
            conn=conn,
            snapshot_time="2026-06-29T10:05:00+0530",
            underlying="NIFTY",
            expiry="2026-06-30",
            current_rows=current,
            source="nse_live",
        )
        stored = conn.execute(
            "SELECT tradable,edge_policy,reason FROM option_strike_signals "
            "WHERE snapshot_time='2026-06-29T10:05:00+0530' AND option_type='CE'"
        ).fetchone()

    assert result["tradable"] == 0
    assert stored[0] == 0
    assert stored[1] == "QUARANTINED"
    assert "quarantined_negative_forward_edge" in stored[2]


def test_generated_strike_signals_receive_after_cost_outcomes(tmp_path):
    db = tmp_path / "snapshots.db"
    first = [_row(25000, 100, 1000, 1000)]
    second = [_row(25000, 110, 1200, 1500)]
    third = [_row(25000, 120, 1500, 2000)]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE option_chain_snapshots "
            "(ts REAL,snapshot_time TEXT,underlying TEXT,expiry TEXT,ok INTEGER,rows_json TEXT)"
        )
        conn.execute(
            "INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)",
            (1, "2026-06-25T10:00:00+0530", "NIFTY", "2026-06-30", 1, json.dumps(first)),
        )
        persist_multistrike_signals(
            conn=conn, snapshot_time="2026-06-25T10:05:00+05:30",
            underlying="NIFTY", expiry="2026-06-30", current_rows=second, source="angel",
        )
        conn.execute(
            "INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)",
            (2, "2026-06-25T10:05:00+05:30", "NIFTY", "2026-06-30", 1, json.dumps(second)),
        )
        persist_multistrike_signals(
            conn=conn, snapshot_time="2026-06-25T10:25:00+05:30",
            underlying="NIFTY", expiry="2026-06-30", current_rows=third, source="angel",
        )
        conn.commit()

    result = label_multistrike_outcomes(db_path=str(db), min_horizon_sec=900)
    with sqlite3.connect(db) as conn:
        labelled = conn.execute(
            "SELECT COUNT(*),SUM(estimated_costs>0) FROM option_strike_signals "
            "WHERE outcome_label IN (-1,0,1)"
        ).fetchone()
    assert result["verified_labelled"] >= 1
    assert labelled[0] >= 1
    assert labelled[1] == labelled[0]
    model = build_multistrike_edge_model(
        db_path=str(db), output_file=str(tmp_path / "edge.json"), min_samples=1
    )
    assert model["verified_outcomes"] >= 1
    assert model["weights"]


def test_lifecycle_marks_target_and_moves_stop_to_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTION_EDGE_POLICY_REQUIRE_PROMISING", "false")
    db = tmp_path / "snapshots.db"
    first = [_row(25000, 100, 1000, 1000)]
    second = [_row(25000, 110, 1200, 1500)]
    target_hit = [_row(25000, 140, 1500, 2200)]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE option_chain_snapshots "
            "(ts REAL,snapshot_time TEXT,underlying TEXT,expiry TEXT,ok INTEGER,rows_json TEXT)"
        )
        conn.execute("INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)", (
            1, "2026-06-25T10:00:00+05:30", "NIFTY", "2026-06-30", 1, json.dumps(first),
        ))
        persist_multistrike_signals(
            conn=conn, snapshot_time="2026-06-25T10:05:00+05:30",
            underlying="NIFTY", expiry="2026-06-30", current_rows=second, source="angel",
        )
        conn.execute("INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?)", (
            2, "2026-06-25T10:05:00+05:30", "NIFTY", "2026-06-30", 1, json.dumps(second),
        ))
        result = persist_multistrike_signals(
            conn=conn, snapshot_time="2026-06-25T10:10:00+05:30",
            underlying="NIFTY", expiry="2026-06-30", current_rows=target_hit, source="angel",
        )
        event = next(e for e in result["lifecycle_events"] if e["option_type"] == "CE")
        stored = conn.execute(
            "SELECT lifecycle_status,entry_price,stop_loss FROM option_strike_signals "
            "WHERE snapshot_time='2026-06-25T10:05:00+05:30' AND option_type='CE'"
        ).fetchone()
    assert event["status"] in {"TARGET1_HIT", "TARGET2_HIT"}
    if event["status"] == "TARGET1_HIT":
        assert stored[2] == stored[1]


def _insert_leg(conn, *, ts, snapshot_time, side, price):
    """Directly seed one strike-signal row with an explicit side, the way
    option_core_strategies' shadow legs land (labeller-input shape)."""
    conn.execute(
        """INSERT INTO option_strike_signals
           (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,
            direction,score,tradable,price,spread_pct,volume,reason,source,
            strategy,combo_id,side)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, snapshot_time, "NIFTY", "2026-06-30", 25000, "CE",
         "SHADOW_STRATEGY", "SHADOW_ENTRY", "NEUTRAL", 0.0, 0, price,
         0.01, 1000, "test", "angel", "C1", "combo-1", side),
    )


def test_labeller_scores_sell_legs_as_shorts_not_longs(tmp_path):
    """Regression for the labeller's former hardcoded side='BUY': premium
    falls 100 -> 80, so the SELL leg must label profitable (+1) and the BUY
    control leg must label losing (-1). Before the fix both came out as
    longs and the short's P&L was computed backwards."""
    db = tmp_path / "snapshots.db"
    with sqlite3.connect(db) as conn:
        ensure_multistrike_schema(conn)
        _insert_leg(conn, ts=1_000.0, snapshot_time="2026-06-25T10:00:00+05:30",
                    side="SELL", price=100.0)
        _insert_leg(conn, ts=1_060.0, snapshot_time="2026-06-25T10:01:00+05:30",
                    side="BUY", price=100.0)
        # future observation of the same contract at a lower premium
        _insert_leg(conn, ts=1_000.0 + 1200, snapshot_time="2026-06-25T10:20:00+05:30",
                    side="BUY", price=80.0)
        conn.commit()

    result = label_multistrike_outcomes(db_path=str(db), min_horizon_sec=900)
    assert result["labelled"] >= 2
    with sqlite3.connect(db) as conn:
        sell = conn.execute(
            "SELECT outcome_label, net_pnl FROM option_strike_signals "
            "WHERE side='SELL' AND outcome_label IN (-1,0,1)").fetchone()
        buy = conn.execute(
            "SELECT outcome_label, net_pnl FROM option_strike_signals "
            "WHERE side='BUY' AND ts=1060 AND outcome_label IN (-1,0,1)").fetchone()
    assert sell is not None and buy is not None
    assert sell[0] == 1 and sell[1] > 0, f"short should profit when premium decays: {sell}"
    assert buy[0] == -1 and buy[1] < 0, f"long should lose when premium decays: {buy}"
