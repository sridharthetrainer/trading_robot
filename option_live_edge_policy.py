"""Evidence policy for option-flow cohorts; never promotes from a tiny sample."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict


LIVE_SOURCES = ("angel", "angel_eod", "angel_fallback", "nse_live", "resilience_nse", "sensibull", "bse", "bse_oc")


def score_band(score: float) -> str:
    if score < 50:
        return "LT50"
    if score < 70:
        return "50_70"
    if score < 85:
        return "70_85"
    return "85_PLUS"


def cohort_policy(conn: sqlite3.Connection, *, flow: str, direction: str, score: float) -> Dict[str, Any]:
    band = score_band(float(score or 0))
    clauses = {
        "LT50": "score<50",
        "50_70": "score>=50 AND score<70",
        "70_85": "score>=70 AND score<85",
        "85_PLUS": "score>=85",
    }
    placeholders = ",".join("?" for _ in LIVE_SOURCES)
    row = conn.execute(
        f"""SELECT COUNT(*),COUNT(DISTINCT substr(snapshot_time,1,10)),
                    AVG(CASE WHEN outcome_label=1 THEN 1.0 ELSE 0.0 END),
                    AVG(net_pnl),AVG(net_r),
                    SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) /
                    NULLIF(ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)),0)
               FROM option_strike_signals
              WHERE flow=? AND direction=? AND {clauses[band]}
                AND source IN ({placeholders}) AND outcome_label IN (-1,0,1)""",
        (flow, direction, *LIVE_SOURCES),
    ).fetchone()
    n, days = int(row[0] or 0), int(row[1] or 0)
    wr, avg_net, avg_r = (float(value or 0) for value in row[2:5])
    pf = float(row[5]) if row[5] is not None else (999.0 if n and avg_net > 0 else 0.0)
    promising = n >= 30 and days >= 3 and avg_net > 0 and avg_r > 0 and pf >= 1.2
    live_ready = n >= 500 and days >= 15 and avg_net > 0 and avg_r > 0 and pf >= 1.2
    negative = n >= 20 and (avg_net <= 0 or avg_r <= 0 or pf < 1.0)
    status = "LIVE_EVIDENCE_READY" if live_ready else "PAPER_PROMISING" if promising else "QUARANTINED" if negative else "VALIDATING"
    return {
        "status": status, "band": band, "outcomes": n, "days": days,
        "win_rate": round(wr, 4), "avg_net_pnl": round(avg_net, 2),
        "avg_net_r": round(avg_r, 4), "profit_factor": round(pf, 3),
        "live_ready": live_ready,
    }
