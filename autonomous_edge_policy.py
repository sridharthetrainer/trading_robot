"""Conservative outcome evidence for autonomous generated signals."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict

DB_PATH = Path("signal_log.db")
_CACHE: Dict[str, Any] = {"at": 0.0, "rows": {}}


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
    negative = n >= 30 and days >= 3 and avg_r <= -0.15 and pf < 0.80
    promising = n >= 100 and days >= 5 and avg_r > 0 and pf >= 1.20
    live_ready = n >= 500 and days >= 15 and avg_r > 0 and pf >= 1.20
    status = "LIVE_EVIDENCE_READY" if live_ready else "PAPER_PROMISING" if promising else "QUARANTINED" if negative else "VALIDATING"
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
    return signal
