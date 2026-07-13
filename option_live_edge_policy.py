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


def _agg(conn: sqlite3.Connection, *, flow: str, direction: str, band_clause: str,
         placeholders: str, day_cmp: str, cutoff: Optional[str]) -> Tuple[int, int, float, float, float, float]:
    params: Tuple[Any, ...] = (flow, direction, *LIVE_SOURCES)
    if cutoff is not None:
        params = params + (cutoff,)
    row = conn.execute(
        f"""SELECT COUNT(*),COUNT(DISTINCT substr(snapshot_time,1,10)),
                    AVG(CASE WHEN outcome_label=1 THEN 1.0 ELSE 0.0 END),
                    AVG(net_pnl),AVG(net_r),
                    SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) /
                    NULLIF(ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)),0)
               FROM option_strike_signals
              WHERE flow=? AND direction=? AND {band_clause}
                AND source IN ({placeholders}) AND outcome_label IN (-1,0,1) {day_cmp}""",
        params,
    ).fetchone()
    n, days = int(row[0] or 0), int(row[1] or 0)
    wr, avg_net, avg_r = (float(value or 0) for value in row[2:5])
    pf = float(row[5]) if row[5] is not None else (999.0 if n and avg_net > 0 else 0.0)
    return n, days, wr, avg_net, avg_r, pf


def cohort_policy(conn: sqlite3.Connection, *, flow: str, direction: str, score: float) -> Dict[str, Any]:
    band = score_band(float(score or 0))
    clauses = {
        "LT50": "score<50",
        "50_70": "score>=50 AND score<70",
        "70_85": "score>=70 AND score<85",
        "85_PLUS": "score>=85",
    }
    placeholders = ",".join("?" for _ in LIVE_SOURCES)
    n, days, wr, avg_net, avg_r, pf = _agg(
        conn, flow=flow, direction=direction, band_clause=clauses[band],
        placeholders=placeholders, day_cmp="", cutoff=None)

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
            cutoff=cutoff)
        holdout_confirmed = (
            ho_n >= HOLDOUT_MIN_N and ho_days >= HOLDOUT_MIN_DAYS
            and ho_avg_net > 0 and ho_avg_r > 0
        )

    promising = n >= 30 and days >= 3 and avg_net > 0 and avg_r > 0 and pf >= 1.2 and holdout_confirmed
    live_ready = n >= 500 and days >= 15 and avg_net > 0 and avg_r > 0 and pf >= 1.2 and holdout_confirmed
    negative = n >= 20 and (avg_net <= 0 or avg_r <= 0 or pf < 1.0)
    status = "LIVE_EVIDENCE_READY" if live_ready else "PAPER_PROMISING" if promising else "QUARANTINED" if negative else "VALIDATING"
    return {
        "status": status, "band": band, "outcomes": n, "days": days,
        "win_rate": round(wr, 4), "avg_net_pnl": round(avg_net, 2),
        "avg_net_r": round(avg_r, 4), "profit_factor": round(pf, 3),
        "live_ready": live_ready,
        "holdout_confirmed": holdout_confirmed,
        "holdout_outcomes": ho_n, "holdout_days": ho_days,
        "holdout_avg_net_r": round(ho_avg_r, 4),
    }
