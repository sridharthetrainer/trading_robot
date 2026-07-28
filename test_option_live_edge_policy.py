import sqlite3

import pytest

import option_live_edge_policy as olep
from option_live_edge_policy import cohort_policy
from option_multistrike_signals import ensure_multistrike_schema


@pytest.fixture(autouse=True)
def _reset_day_cache():
    """_day_cutoff() caches the day-list/cutoff in a module-level dict keyed
    only by a time-based TTL, not by which database it came from — running
    after another test file that populated it from a DIFFERENT :memory: db
    leaks a stale cutoff into this file's tests (found 2026-07-15, surfaced
    by test collection order rather than anything wrong in the fix itself)."""
    olep._DAY_CACHE.update(ts=0.0, days=[], cutoff=None)
    yield
    olep._DAY_CACHE.update(ts=0.0, days=[], cutoff=None)


def _seed_days(conn, *, flow, direction, score, days, pnl, r_multiple, per_day=10,
               underlying="NIFTY", option_type="PE"):
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
                (i, f"{day}T10:00:00+05:30", underlying, "2026-07-07", 24000 + i,
                 option_type, flow, "WATCH", direction, score, 0, 100, "angel",
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


def test_segmented_policy_does_not_dilute_profitable_pe_with_losing_ce():
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    all_days = _TRAIN_DAYS + _HOLDOUT_DAYS
    _seed_days(
        conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
        days=all_days, pnl=100, r_multiple=0.3, option_type="PE",
    )
    _seed_days(
        conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
        days=all_days, pnl=-300, r_multiple=-0.5, option_type="CE",
    )

    pooled = cohort_policy(
        conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
    )
    segmented = cohort_policy(
        conn, flow="SHORT_BUILDUP", direction="BEARISH", score=75,
        underlying="NIFTY", option_type="PE",
    )

    assert pooled["status"] == "QUARANTINED"
    assert segmented["status"] == "PAPER_PROMISING"
    assert segmented["holdout_confirmed"] is True


# ── Combo-level unit (2026-07-17): a multi-leg structure's legs are one
# observation, not four ─────────────────────────────────────────────────

def _seed_combo(conn, *, strategy, combo_id, day, leg_pnls, entry_minute=30,
                capital_per_leg=6500.0):
    for j, pnl in enumerate(leg_pnls):
        conn.execute(
            """INSERT INTO option_strike_signals
               (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,direction,
                score,tradable,price,source,outcome_label,net_pnl,net_r,capital_at_risk,
                strategy,combo_id,side)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hash((combo_id, j)) % 10**9, f"{day}T09:{entry_minute:02d}:00+05:30",
             "NIFTY", "2026-07-07",
             24000 + 100 * j, "CE" if j % 2 == 0 else "PE", "SHADOW_STRATEGY",
             "SHADOW_ENTRY", "NEUTRAL", 0, 0, 100, "angel",
             1 if pnl > 0 else -1, pnl, pnl / capital_per_leg, capital_per_leg,
             strategy, combo_id, "SELL" if j < 2 else "BUY"),
        )


def test_combo_policy_counts_a_condor_once_not_four_times():
    """3 winning legs + 1 catastrophic leg is ONE losing combo. Per-leg
    counting would read it as a 3:1 winner — the exact inflation all four
    external audits flagged."""
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    for i, day in enumerate(["2026-06-25", "2026-06-26", "2026-06-27",
                              "2026-06-28", "2026-06-29", "2026-06-30"]):
        for k in range(4):
            _seed_combo(conn, strategy="C3", combo_id=f"NIFTY_C3_{i}_{k}", day=day,
                        leg_pnls=[300, 250, 200, -2000], entry_minute=30 + k)
    combo = olep.strategy_combo_policy(conn, strategy="C3")
    assert combo["outcomes"] == 24            # 24 combos, not 96 legs
    assert combo["win_rate"] == 0.0           # every combo nets -1250
    assert combo["status"] == "QUARANTINED"   # combo-level truth: it loses
    # per-leg attribution view still sees the individual legs
    leg_view = cohort_policy(conn, flow="SHADOW_STRATEGY", direction="NEUTRAL",
                              score=0, strategy="C3")
    assert leg_view["outcomes"] == 96


def test_combo_policy_singleton_fallback_for_empty_combo_id():
    """Legs with no combo_id are their own singleton combos, so single-leg
    strategies flow through the same policy unchanged."""
    conn = sqlite3.connect(":memory:")
    ensure_multistrike_schema(conn)
    for i, day in enumerate(["2026-06-25", "2026-06-26", "2026-06-27"]):
        for k in range(8):
            conn.execute(
                """INSERT INTO option_strike_signals
                   (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,
                    direction,score,tradable,price,source,outcome_label,net_pnl,net_r,
                    capital_at_risk,strategy,combo_id,side)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (i * 100 + k, f"{day}T10:00:00+05:30", "NIFTY", "2026-07-07",
                 24000 + k, "CE", "SHADOW_STRATEGY", "SHADOW_ENTRY", "NEUTRAL",
                 0, 0, 100, "angel", -1, -50, -0.01, 6500.0, "X9", "", "BUY"),
            )
    combo = olep.strategy_combo_policy(conn, strategy="X9")
    assert combo["outcomes"] == 24            # each leg its own combo
    assert combo["status"] == "QUARANTINED"
