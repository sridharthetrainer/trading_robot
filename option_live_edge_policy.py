"""Evidence policy for option-flow cohorts; never promotes from a tiny sample."""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple


LIVE_SOURCES = ("angel", "angel_eod", "angel_fallback", "nse_live", "resilience_nse", "sensibull", "bse", "bse_oc")

# 2026-07-13 finding (option_cohort_edge_miner.py): the exact cohort this
# module flagged PAPER_PROMISING that day (SHORT_BUILDUP/BEARISH/70-85,
# n=114 over 6 days, avg_net_r=+0.29 full-sample) FLIPPED on a day-split
# holdout check — first 3 days +0.35R/PF 2.96, last 3 days -0.058R/PF 0.007.
# A point-estimate over the full sample with no significance or
# out-of-sample check is exactly the multiple-testing regime (~40
# simultaneous cohorts checked nightly) that manufactures false positives,
# and a false positive here gets a real score boost + tradable=True
# downstream. QUARANTINE (negative) stays fast — sitting out a losing
# cohort early is the safe direction — but PROMOTION now additionally
# requires the holdout partition to independently confirm sign.
_DAY_CACHE: Dict[str, Any] = {"ts": 0.0, "days": [], "cutoff": None}
_DAY_CACHE_TTL = 900.0
HOLDOUT_MIN_N = 15
HOLDOUT_MIN_DAYS = 2
TRAIN_FRAC = 0.70


def _day_cutoff(conn: sqlite3.Connection) -> Tuple[List[str], Optional[str]]:
    now = time.time()
    if now - _DAY_CACHE["ts"] < _DAY_CACHE_TTL and _DAY_CACHE["days"]:
        return _DAY_CACHE["days"], _DAY_CACHE["cutoff"]
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(snapshot_time,1,10) FROM option_strike_signals "
        "WHERE outcome_label IN (-1,0,1) ORDER BY 1")]
    cutoff = days[int(len(days) * TRAIN_FRAC) - 1] if len(days) >= 4 else None
    _DAY_CACHE.update(ts=now, days=days, cutoff=cutoff)
    return days, cutoff


def score_band(score: float) -> str:
    if score < 50:
        return "LT50"
    if score < 70:
        return "50_70"
    if score < 85:
        return "70_85"
    return "85_PLUS"


# 2026-07-15: option_cohort_edge_miner.py's day-holdout study repeatedly
# found dte:8plus the single strongest, most holdout-CONFIRMED losing
# cohort in the whole system (train t=-24.24 p=0.0, holdout n=5450
# R=-0.023 — the holdout is worse than train, not just "still negative").
# score:lt50 and flow=NEUTRAL are the next most robust losers. None of
# this was checked by the live gate below, which only ever looked at
# (flow, direction, score_band) — signals on far-dated contracts got no
# extra scrutiny despite being the clearest, best-replicated loser this
# system has measured. Same DTE bucketing as the miner, for consistency.
_DTE_BUCKET_SQL = """
CASE
  WHEN julianday(expiry) - julianday(substr(snapshot_time,1,10)) <= 0 THEN 'dte:0'
  WHEN julianday(expiry) - julianday(substr(snapshot_time,1,10)) <= 2 THEN 'dte:1-2'
  WHEN julianday(expiry) - julianday(substr(snapshot_time,1,10)) <= 7 THEN 'dte:3-7'
  ELSE 'dte:8plus'
END
"""


def dte_bucket(expiry: str, snapshot_date: str) -> str:
    """Python-side equivalent of _DTE_BUCKET_SQL, for callers that already
    have expiry/snapshot_time and just need the bucket label."""
    try:
        from datetime import date as _date
        exp = _date.fromisoformat(str(expiry)[:10])
        snap = _date.fromisoformat(str(snapshot_date)[:10])
        dte = (exp - snap).days
    except Exception:
        return "dte:8plus"
    if dte <= 0:
        return "dte:0"
    if dte <= 2:
        return "dte:1-2"
    if dte <= 7:
        return "dte:3-7"
    return "dte:8plus"


def _agg(conn: sqlite3.Connection, *, flow: str, direction: str, band_clause: str,
         placeholders: str, day_cmp: str, cutoff: Optional[str],
         dte: Optional[str] = None, strategy: Optional[str] = None,
         underlying: Optional[str] = None, option_type: Optional[str] = None,
         ) -> Tuple[int, int, float, float, float, float]:
    # Param order MUST match placeholder order in the SQL string below:
    # flow, direction, [dte], [strategy], [underlying], [option_type],
    # *LIVE_SOURCES, [cutoff].
    params: Tuple[Any, ...] = (flow, direction)
    dte_clause = ""
    if dte:
        dte_clause = f"AND ({_DTE_BUCKET_SQL}) = ?"
        params = params + (dte,)
    strategy_clause = ""
    if strategy:
        strategy_clause = "AND strategy = ?"
        params = params + (strategy,)
    underlying_clause = ""
    if underlying:
        underlying_clause = "AND upper(underlying) = upper(?)"
        params = params + (underlying,)
    option_type_clause = ""
    if option_type:
        option_type_clause = "AND upper(option_type) = upper(?)"
        params = params + (option_type,)
    params = params + tuple(LIVE_SOURCES)
    if cutoff is not None:
        params = params + (cutoff,)
    row = conn.execute(
        f"""SELECT COUNT(*),COUNT(DISTINCT substr(snapshot_time,1,10)),
                    AVG(CASE WHEN outcome_label=1 THEN 1.0 ELSE 0.0 END),
                    AVG(net_pnl),AVG(net_r),
                    SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) /
                    NULLIF(ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)),0)
               FROM option_strike_signals
              WHERE flow=? AND direction=? AND {band_clause} {dte_clause} {strategy_clause}
                {underlying_clause} {option_type_clause}
                AND source IN ({placeholders}) AND outcome_label IN (-1,0,1) {day_cmp}""",
        params,
    ).fetchone()
    n, days = int(row[0] or 0), int(row[1] or 0)
    wr, avg_net, avg_r = (float(value or 0) for value in row[2:5])
    pf = float(row[5]) if row[5] is not None else (999.0 if n and avg_net > 0 else 0.0)
    return n, days, wr, avg_net, avg_r, pf


def cohort_policy(conn: sqlite3.Connection, *, flow: str, direction: str, score: float,
                   dte: Optional[str] = None, strategy: Optional[str] = None,
                   underlying: Optional[str] = None,
                   option_type: Optional[str] = None) -> Dict[str, Any]:
    band = score_band(float(score or 0))
    clauses = {
        "LT50": "score<50",
        "50_70": "score>=50 AND score<70",
        "70_85": "score>=70 AND score<85",
        "85_PLUS": "score>=85",
    }
    placeholders = ",".join("?" for _ in LIVE_SOURCES)
    # dte is an already-bucketed label (e.g. "dte:8plus") when the caller
    # knows it; filtering by the SQL-computed bucket keeps this evidence
    # query identical to option_cohort_edge_miner.py's regardless. strategy
    # (added 2026-07-16 for the multi-strategy option catalog) narrows the
    # same evidence query to one strategy's own rows when the caller passes
    # it; every existing call site leaves it None and is byte-for-byte
    # unaffected.
    n, days, wr, avg_net, avg_r, pf = _agg(
        conn, flow=flow, direction=direction, band_clause=clauses[band],
        placeholders=placeholders, day_cmp="", cutoff=None, dte=dte, strategy=strategy,
        underlying=underlying, option_type=option_type)

    # Promotion (positive status) additionally requires a day-split holdout
    # to independently confirm sign — see module note above. QUARANTINE
    # (negative) is judged on the full sample as before: fast to distrust.
    _, cutoff = _day_cutoff(conn)
    holdout_confirmed = False
    ho_n = ho_days = 0
    ho_avg_r = 0.0
    if cutoff is not None:
        ho_n, ho_days, _, ho_avg_net, ho_avg_r, ho_pf = _agg(
            conn, flow=flow, direction=direction, band_clause=clauses[band],
            placeholders=placeholders, day_cmp="AND substr(snapshot_time,1,10) > ?",
            cutoff=cutoff, dte=dte, strategy=strategy,
            underlying=underlying, option_type=option_type)
        holdout_confirmed = (
            ho_n >= HOLDOUT_MIN_N and ho_days >= HOLDOUT_MIN_DAYS
            and ho_avg_net > 0 and ho_avg_r > 0
        )

    promising = n >= 30 and days >= 3 and avg_net > 0 and avg_r > 0 and pf >= 1.2 and holdout_confirmed
    live_ready = n >= 500 and days >= 15 and avg_net > 0 and avg_r > 0 and pf >= 1.2 and holdout_confirmed
    negative = n >= 20 and (avg_net <= 0 or avg_r <= 0 or pf < 1.0)
    status = "LIVE_EVIDENCE_READY" if live_ready else "PAPER_PROMISING" if promising else "QUARANTINED" if negative else "VALIDATING"
    return {
        "status": status, "band": band, "dte": dte, "strategy": strategy,
        "underlying": underlying, "option_type": option_type,
        "outcomes": n, "days": days,
        "win_rate": round(wr, 4), "avg_net_pnl": round(avg_net, 2),
        "avg_net_r": round(avg_r, 4), "profit_factor": round(pf, 3),
        "live_ready": live_ready,
        "holdout_confirmed": holdout_confirmed,
        "holdout_outcomes": ho_n, "holdout_days": ho_days,
        "holdout_avg_net_r": round(ho_avg_r, 4),
    }


# ── Combo-level evidence for multi-leg catalog strategies (2026-07-17) ──
#
# Four independent external audits converged on the same statistical hole:
# a multi-leg structure's legs are near-perfectly correlated observations
# of ONE underlying path. Counting a condor as 4 rows inflates n ~4x,
# understates variance, and leg-level profit factor is not any function of
# combo-level profit factor (3 winning legs + 1 catastrophic leg reads
# ~3:1 per-leg and <1 per-combo). Per-leg rows remain the substrate for
# execution/attribution; PROMOTION statistics for catalog strategies come
# only from this combo-aggregated view. cohort_policy(strategy=...) above
# stays for per-leg attribution and the nightly miner — it must not be
# used as a promotion authority for multi-leg strategies.

def _combo_agg(conn: sqlite3.Connection, *, strategy: str, day_cmp: str,
                cutoff: Optional[str]) -> Tuple[int, int, float, float, float, float]:
    """Aggregate labelled legs into combo outcomes (SUM net_pnl per
    combo_id), then compute the same summary stats _agg produces. Legs
    with an empty combo_id fall back to being their own singleton combo
    (rowid-keyed) so single-leg strategies work identically."""
    params: Tuple[Any, ...] = (strategy,) + tuple(LIVE_SOURCES)
    if cutoff is not None:
        params = params + (cutoff,)
    row = conn.execute(
        f"""SELECT COUNT(*),COUNT(DISTINCT day),
                    AVG(CASE WHEN combo_net>0 THEN 1.0 ELSE 0.0 END),
                    AVG(combo_net),AVG(combo_r),
                    SUM(CASE WHEN combo_net>0 THEN combo_net ELSE 0 END) /
                    NULLIF(ABS(SUM(CASE WHEN combo_net<0 THEN combo_net ELSE 0 END)),0)
               FROM (
                 SELECT COALESCE(NULLIF(combo_id,''), 'row:'||rowid) AS cid,
                        substr(MIN(snapshot_time),1,10) AS day,
                        SUM(net_pnl) AS combo_net,
                        SUM(net_pnl) / NULLIF(SUM(ABS(capital_at_risk)),0) AS combo_r
                   FROM option_strike_signals
                  WHERE strategy=? AND source IN ({','.join('?' for _ in LIVE_SOURCES)})
                    AND outcome_label IN (-1,0,1)
                  GROUP BY cid
               )
              WHERE 1=1 {day_cmp}""",
        params,
    ).fetchone()
    n, days = int(row[0] or 0), int(row[1] or 0)
    wr, avg_net, avg_r = (float(value or 0) for value in row[2:5])
    pf = float(row[5]) if row[5] is not None else (999.0 if n and avg_net > 0 else 0.0)
    return n, days, wr, avg_net, avg_r, pf


def strategy_combo_policy(conn: sqlite3.Connection, *, strategy: str) -> Dict[str, Any]:
    """Promotion policy for a catalog strategy judged at COMBO granularity.
    Same thresholds and day-split-holdout discipline as cohort_policy; the
    only difference is the observation unit."""
    n, days, wr, avg_net, avg_r, pf = _combo_agg(conn, strategy=strategy,
                                                  day_cmp="", cutoff=None)
    _, cutoff = _day_cutoff(conn)
    holdout_confirmed = False
    ho_n = ho_days = 0
    ho_avg_r = 0.0
    if cutoff is not None:
        ho_n, ho_days, _, ho_avg_net, ho_avg_r, ho_pf = _combo_agg(
            conn, strategy=strategy, day_cmp="AND day > ?", cutoff=cutoff)
        holdout_confirmed = (
            ho_n >= HOLDOUT_MIN_N and ho_days >= HOLDOUT_MIN_DAYS
            and ho_avg_net > 0 and ho_avg_r > 0
        )

    promising = n >= 30 and days >= 3 and avg_net > 0 and avg_r > 0 and pf >= 1.2 and holdout_confirmed
    live_ready = n >= 500 and days >= 15 and avg_net > 0 and avg_r > 0 and pf >= 1.2 and holdout_confirmed
    negative = n >= 20 and (avg_net <= 0 or avg_r <= 0 or pf < 1.0)
    status = "LIVE_EVIDENCE_READY" if live_ready else "PAPER_PROMISING" if promising else "QUARANTINED" if negative else "VALIDATING"
    return {
        "status": status, "strategy": strategy, "unit": "combo",
        "outcomes": n, "days": days,
        "win_rate": round(wr, 4), "avg_net_pnl": round(avg_net, 2),
        "avg_net_r": round(avg_r, 4), "profit_factor": round(pf, 3),
        "live_ready": live_ready,
        "holdout_confirmed": holdout_confirmed,
        "holdout_outcomes": ho_n, "holdout_days": ho_days,
        "holdout_avg_net_r": round(ho_avg_r, 4),
    }
