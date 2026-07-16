import sqlite3

from option_multistrike_signals import ensure_multistrike_schema
from option_live_edge_policy import cohort_policy


def _old_shape_conn() -> sqlite3.Connection:
    """A connection with only the pre-2026-07-16 base table -- no strategy/
    combo_id/side columns -- to prove ensure_multistrike_schema migrates it
    forward rather than assuming a fresh table."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE option_strike_signals (
            ts REAL NOT NULL, snapshot_time TEXT NOT NULL, underlying TEXT NOT NULL,
            expiry TEXT DEFAULT '', strike REAL NOT NULL, option_type TEXT NOT NULL,
            flow TEXT NOT NULL, signal TEXT NOT NULL, direction TEXT NOT NULL,
            score REAL DEFAULT 0, tradable INTEGER DEFAULT 0, price REAL DEFAULT 0,
            price_change_pct REAL DEFAULT 0, oi REAL DEFAULT 0, oi_change_pct REAL DEFAULT 0,
            volume REAL DEFAULT 0, volume_change_pct REAL DEFAULT 0, spread_pct REAL,
            reason TEXT DEFAULT '', source TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """INSERT INTO option_strike_signals
           (ts, snapshot_time, underlying, strike, option_type, flow, signal, direction, source)
           VALUES (0, '2026-06-01T09:30:00', 'NIFTY', 22000, 'CE', 'LONG_BUILDUP', 'BUY', 'BULLISH', 'angel')"""
    )
    conn.commit()
    return conn


def test_migration_adds_new_columns_and_backfills_old_rows():
    conn = _old_shape_conn()
    ensure_multistrike_schema(conn)
    row = conn.execute(
        "SELECT strategy, side, combo_id FROM option_strike_signals").fetchone()
    assert row == ("single_strike_flow", "BUY", "")


def test_migration_is_idempotent_when_rerun():
    conn = _old_shape_conn()
    ensure_multistrike_schema(conn)
    ensure_multistrike_schema(conn)  # must not raise or duplicate columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(option_strike_signals)")}
    assert {"strategy", "combo_id", "side"} <= cols


def test_unique_index_widened_to_include_strategy():
    conn = _old_shape_conn()
    ensure_multistrike_schema(conn)
    # Same (snapshot_time, underlying, strike, option_type) as an existing
    # single_strike_flow row, but a different strategy -- must NOT collide.
    conn.execute(
        """INSERT INTO option_strike_signals
           (ts, snapshot_time, underlying, strike, option_type, flow, signal, direction,
            source, strategy, side)
           VALUES (1, '2026-06-01T09:30:00', 'NIFTY', 22000, 'CE', 'SHADOW_STRATEGY',
                   'SHADOW_ENTRY', 'NEUTRAL', 'shadow_catalog', 'C1', 'SELL')"""
    )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM option_strike_signals WHERE underlying='NIFTY' AND strike=22000 AND option_type='CE'"
    ).fetchone()[0]
    assert n == 2  # both rows survive -- no INSERT OR REPLACE collision


def test_cohort_policy_strategy_dimension_filters_correctly():
    conn = _old_shape_conn()
    ensure_multistrike_schema(conn)
    # Seed 40 outcome-labelled rows for strategy C1, spread across enough
    # distinct days to (in principle) clear the sample-size gates, all
    # positive, plus 40 rows for the pre-existing single_strike_flow cohort
    # that are all negative -- proves the two are measured independently.
    for day in range(10):
        for i in range(4):
            conn.execute(
                """INSERT INTO option_strike_signals
                   (ts, snapshot_time, underlying, strike, option_type, flow, signal,
                    direction, score, source, strategy, side, outcome_label, net_pnl, net_r)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (float(day * 100 + i), f"2026-06-{day+1:02d}T09:30:00", "NIFTY", 22000 + i,
                 "CE", "SHADOW_STRATEGY", "SHADOW_ENTRY", "NEUTRAL", 60.0, "angel",
                 "C1_short_straddle", "SELL", 1, 500.0, 0.5),
            )
            conn.execute(
                """INSERT INTO option_strike_signals
                   (ts, snapshot_time, underlying, strike, option_type, flow, signal,
                    direction, score, source, strategy, side, outcome_label, net_pnl, net_r)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (float(day * 100 + i + 1000), f"2026-06-{day+1:02d}T10:30:00", "NIFTY", 23000 + i,
                 "CE", "LONG_BUILDUP", "BUY", "BULLISH", 60.0, "angel",
                 "single_strike_flow", "BUY", -1, -300.0, -0.4),
            )
    conn.commit()

    c1_policy = cohort_policy(conn, flow="SHADOW_STRATEGY", direction="NEUTRAL", score=60.0,
                               strategy="C1_short_straddle")
    flow_policy = cohort_policy(conn, flow="LONG_BUILDUP", direction="BULLISH", score=60.0,
                                 strategy="single_strike_flow")

    assert c1_policy["strategy"] == "C1_short_straddle"
    assert c1_policy["outcomes"] == 40
    assert flow_policy["outcomes"] == 40
    # The two cohorts must not leak into each other's aggregates.
    assert c1_policy["avg_net_pnl"] > 0
    assert flow_policy["avg_net_pnl"] < 0


def test_cohort_policy_without_strategy_kwarg_is_unaffected_by_migration():
    """Every existing call site (none of which passes strategy=) must see
    byte-for-byte identical behavior after this migration."""
    conn = _old_shape_conn()
    ensure_multistrike_schema(conn)
    policy = cohort_policy(conn, flow="LONG_BUILDUP", direction="BULLISH", score=10.0)
    assert policy["strategy"] is None
    assert policy["status"] == "VALIDATING"
