import sqlite3

from option_live_edge_policy import cohort_policy
from option_multistrike_signals import ensure_multistrike_schema


def _seed(conn, *, flow, direction, score, pnl, r_multiple, count=30):
    for i in range(count):
        day = 29 + (i % 3)
        conn.execute(
            """INSERT INTO option_strike_signals
               (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,direction,
                score,tradable,price,source,outcome_label,net_pnl,net_r)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (i, f"2026-06-{day:02d}T10:00:00+05:30", "NIFTY", "2026-07-07", 24000 + i,
             "PE", flow, "WATCH", direction, score, 0, 100, "angel", 1 if pnl > 0 else -1, pnl, r_multiple),
        )


def test_positive_cohort_is_paper_promising_not_live():
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    _seed(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75, pnl=100, r_multiple=.3)
    policy = cohort_policy(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75)
    assert policy["status"] == "PAPER_PROMISING"
    assert policy["live_ready"] is False


def test_negative_cohort_is_quarantined():
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    _seed(conn, flow="LONG_BUILDUP", direction="BULLISH", score=60, pnl=-100, r_multiple=-.2)
    policy = cohort_policy(conn, flow="LONG_BUILDUP", direction="BULLISH", score=60)
    assert policy["status"] == "QUARANTINED"
