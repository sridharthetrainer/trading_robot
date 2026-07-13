import sqlite3

from option_live_edge_policy import cohort_policy
from option_multistrike_signals import ensure_multistrike_schema


def _seed_days(conn, *, flow, direction, score, days, pnl, r_multiple, per_day=10):
    """Insert per_day rows on each given day string ('2026-06-25' etc)."""
    i = 0
    for day in days:
        for _ in range(per_day):
            i += 1
            conn.execute(
                """INSERT INTO option_strike_signals
                   (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,direction,
                    score,tradable,price,source,outcome_label,net_pnl,net_r)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (i, f"{day}T10:00:00+05:30", "NIFTY", "2026-07-07", 24000 + i,
                 "PE", flow, "WATCH", direction, score, 0, 100, "angel",
                 1 if pnl > 0 else -1, pnl, r_multiple),
            )


_TRAIN_DAYS = ["2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28"]
_HOLDOUT_DAYS = ["2026-06-29", "2026-06-30"]


def test_positive_cohort_confirmed_in_holdout_is_paper_promising():
    """Edge holds up in BOTH train and holdout -> genuinely promotable."""
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    _seed_days(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
               days=_TRAIN_DAYS, pnl=100, r_multiple=0.3)
    _seed_days(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
               days=_HOLDOUT_DAYS, pnl=100, r_multiple=0.3)
    policy = cohort_policy(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75)
    assert policy["status"] == "PAPER_PROMISING"
    assert policy["holdout_confirmed"] is True
    assert policy["live_ready"] is False


def test_positive_train_only_flips_negative_in_holdout_stays_validating():
    """2026-07-13 production finding: a cohort that looked PAPER_PROMISING
    on the full sample (train strongly positive) but LOST money in the days
    after must NOT be promoted just because the combined average is still
    positive — this is exactly the false-positive shape a same-sample point
    estimate cannot see."""
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    _seed_days(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
               days=_TRAIN_DAYS, pnl=800, r_multiple=0.35, per_day=25)
    _seed_days(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
               days=_HOLDOUT_DAYS, pnl=-1500, r_multiple=-0.2, per_day=10)
    policy = cohort_policy(conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75)
    assert policy["status"] != "PAPER_PROMISING"
    assert policy["status"] != "LIVE_EVIDENCE_READY"
    assert policy["holdout_confirmed"] is False


def test_negative_cohort_is_quarantined():
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    _seed_days(conn, flow="LONG_BUILDUP", direction="BULLISH", score=60,
               days=["2026-06-29", "2026-06-30", "2026-07-01"], pnl=-100,
               r_multiple=-0.2)
    policy = cohort_policy(conn, flow="LONG_BUILDUP", direction="BULLISH", score=60)
    assert policy["status"] == "QUARANTINED"
