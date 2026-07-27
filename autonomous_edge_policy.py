"""Conservative outcome evidence for autonomous generated signals."""

from __future__ import annotations

import sqlite3
import time
import os
from pathlib import Path
from typing import Any, Dict

DB_PATH = Path("signal_log.db")
_CACHE: Dict[str, Any] = {"at": 0.0, "rows": {}}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


MIN_NEGATIVE_OUTCOMES = int(os.getenv("PROFIT_DISCIPLINE_MIN_NEGATIVE_OUTCOMES", "30"))
MIN_NEGATIVE_DAYS = int(os.getenv("PROFIT_DISCIPLINE_MIN_NEGATIVE_DAYS", "3"))
NEGATIVE_AVG_NET_R = float(os.getenv("PROFIT_DISCIPLINE_NEGATIVE_AVG_NET_R", "-0.15"))
NEGATIVE_MAX_PF = float(os.getenv("PROFIT_DISCIPLINE_NEGATIVE_MAX_PF", "0.80"))
EARLY_LOSS_OUTCOMES = int(os.getenv("PROFIT_DISCIPLINE_EARLY_LOSS_OUTCOMES", "10"))
EARLY_LOSS_DAYS = int(os.getenv("PROFIT_DISCIPLINE_EARLY_LOSS_DAYS", "3"))
EARLY_LOSS_AVG_NET_R = float(os.getenv("PROFIT_DISCIPLINE_EARLY_LOSS_AVG_NET_R", "-0.75"))
EARLY_LOSS_MAX_PF = float(os.getenv("PROFIT_DISCIPLINE_EARLY_LOSS_MAX_PF", "0.50"))
BLOCK_QUARANTINED = _env_bool("PROFIT_DISCIPLINE_BLOCK_QUARANTINED", True)


def _stats(db_path: Path) -> Dict[str, Dict[str, Any]]:
    now = time.time()
    if db_path == DB_PATH and now - float(_CACHE["at"]) < 60:
        return dict(_CACHE["rows"])
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT lower(strategy) strategy,COUNT(*) outcomes,
                  COUNT(DISTINCT signal_date) days,
                  AVG(CASE WHEN tb_label=1 THEN 1.0 ELSE 0.0 END) win_rate,
                  AVG(tb_r_multiple_net) avg_net_r,
                  SUM(CASE WHEN tb_r_multiple_net>0 THEN tb_r_multiple_net ELSE 0 END)/
                  NULLIF(ABS(SUM(CASE WHEN tb_r_multiple_net<0 THEN tb_r_multiple_net ELSE 0 END)),0) profit_factor
             FROM signal_log
            WHERE tb_label IN (1,-1) AND training_eligible=1
              AND stop_loss>0 AND target>0 AND rr>0
              AND abs(tb_r_multiple_net)>0.000001
            GROUP BY lower(strategy)"""
    ).fetchall(); conn.close()
    result = {str(row["strategy"]): dict(row) for row in rows}
    if db_path == DB_PATH:
        _CACHE.update(at=now, rows=result)
    return result


def strategy_policy(strategy: str, db_path: Path = DB_PATH) -> Dict[str, Any]:
    row = _stats(db_path).get(str(strategy or "").strip().lower(), {})
    n, days = int(row.get("outcomes") or 0), int(row.get("days") or 0)
    wr, avg_r = float(row.get("win_rate") or 0), float(row.get("avg_net_r") or 0)
    raw_pf = row.get("profit_factor")
    pf = float(raw_pf) if raw_pf is not None else (999.0 if n and avg_r > 0 else 0.0)
    negative = (
        n >= MIN_NEGATIVE_OUTCOMES
        and days >= MIN_NEGATIVE_DAYS
        and avg_r <= NEGATIVE_AVG_NET_R
        and pf < NEGATIVE_MAX_PF
    )
    early_loss = (
        n >= EARLY_LOSS_OUTCOMES
        and days >= EARLY_LOSS_DAYS
        and avg_r <= EARLY_LOSS_AVG_NET_R
        and pf < EARLY_LOSS_MAX_PF
    )
    # PAPER_PROMISING is a forward-test/probation label, not a full live
    # promotion. Keep it reachable for small but clean cohorts so the bot stops
    # drowning in raw signal volume and starts tracking the few cohorts with
    # positive after-cost evidence. LIVE_EVIDENCE_READY remains deliberately
    # much harder below.
    promising = n >= 30 and days >= 3 and avg_r > 0 and pf >= 1.20
    live_ready = n >= 500 and days >= 15 and avg_r > 0 and pf >= 1.20
    status = "LIVE_EVIDENCE_READY" if live_ready else "PAPER_PROMISING" if promising else "QUARANTINED" if (negative or early_loss) else "VALIDATING"
    return {"status": status, "outcomes": n, "days": days, "win_rate": round(wr, 4),
            "avg_net_r": round(avg_r, 4), "profit_factor": round(pf, 3), "live_ready": live_ready}


def apply_policy(signal: Dict[str, Any], db_path: Path = DB_PATH) -> Dict[str, Any]:
    if not isinstance(signal, dict) or not signal:
        return signal
    evidence = strategy_policy(str(signal.get("strategy", "")), db_path)
    signal["autonomous_edge_evidence"] = evidence
    signal["autonomous_edge_status"] = evidence["status"]
    if evidence["status"] == "QUARANTINED":
        signal["paper_training_mode"] = True
        existing = str(signal.get("live_block_reason", "") or "")
        signal["live_block_reason"] = ",".join(x for x in (existing, "negative_generated_signal_edge") if x)
        if BLOCK_QUARANTINED:
            signal["side_would_have_been"] = signal.get("side") or signal.get("direction")
            signal["direction_would_have_been"] = signal.get("direction") or signal.get("side")
            signal["side"] = None
            signal["direction"] = None
            signal["reason"] = "profit_discipline_quarantined_strategy"
            signal.setdefault("rejections", []).append(
                f"{signal.get('strategy', 'unknown')}: profit_discipline_quarantined "
                f"avg_net_r={evidence['avg_net_r']} pf={evidence['profit_factor']} "
                f"n={evidence['outcomes']} days={evidence['days']}"
            )
    return signal
