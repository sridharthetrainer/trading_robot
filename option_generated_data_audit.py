#!/usr/bin/env python3
"""Summarise option-bot generated evidence into a compact JSON report."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable


SNAPSHOT_DB = "option_chain_snapshots.db"
JOURNAL = "option_decision_journal.jsonl"
REPORT = "option_generated_data_audit.json"
LIVE_SOURCES = ("angel", "angel_eod", "angel_fallback", "nse_live", "resilience_nse", "sensibull", "bse", "bse_oc")


def _json_rows(path: str) -> Iterable[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def build_report(
    *,
    db_path: str = SNAPSHOT_DB,
    journal_path: str = JOURNAL,
    output_path: str = REPORT,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "snapshot_db": db_path,
        "journal": journal_path,
        "snapshot_days": [],
        "tradable_cohorts": [],
        "best_live_cohorts": [],
        "worst_live_cohorts": [],
        "journal_evidence": {},
        "improvement_notes": [],
    }
    if Path(db_path).exists():
        placeholders = ",".join("?" for _ in LIVE_SOURCES)
        with sqlite3.connect(db_path) as conn:
            report["snapshot_days"] = _fetchall(
                conn,
                """
                SELECT substr(snapshot_time,1,10) day, source, is_live, ok,
                       COUNT(*) snapshots, MAX(snapshot_time) latest
                  FROM option_chain_snapshots
                 GROUP BY 1,2,3,4
                 ORDER BY day DESC, snapshots DESC
                 LIMIT 30
                """,
            )
            cohort_sql = f"""
                SELECT flow,direction,
                       CASE WHEN score<50 THEN 'LT50'
                            WHEN score<70 THEN '50_70'
                            WHEN score<85 THEN '70_85'
                            ELSE '85_PLUS' END score_band,
                       COUNT(*) samples,
                       COUNT(DISTINCT substr(snapshot_time,1,10)) days,
                       ROUND(AVG(CASE WHEN outcome_label=1 THEN 1.0 ELSE 0 END),4) win_rate,
                       ROUND(AVG(net_pnl),2) avg_net_pnl,
                       ROUND(SUM(net_pnl),2) total_net_pnl,
                       ROUND(AVG(net_r),4) avg_net_r,
                       ROUND(SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) /
                             NULLIF(ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)),0),3)
                             profit_factor
                  FROM option_strike_signals
                 WHERE source IN ({placeholders}) AND outcome_label IN (-1,1)
                 GROUP BY 1,2,3
            """
            report["tradable_cohorts"] = _fetchall(
                conn,
                cohort_sql + " HAVING SUM(CASE WHEN tradable=1 THEN 1 ELSE 0 END)>0 ORDER BY total_net_pnl DESC",
                LIVE_SOURCES,
            )
            report["best_live_cohorts"] = _fetchall(
                conn,
                cohort_sql + " HAVING samples>=20 ORDER BY avg_net_r DESC LIMIT 10",
                LIVE_SOURCES,
            )
            report["worst_live_cohorts"] = _fetchall(
                conn,
                cohort_sql + " HAVING samples>=20 ORDER BY avg_net_r ASC LIMIT 10",
                LIVE_SOURCES,
            )
    rows = list(_json_rows(journal_path))
    if rows:
        evidence: Dict[str, int] = {}
        decisions: Dict[str, int] = {}
        live_selected: Dict[str, int] = {}
        for row in rows:
            evidence[str(row.get("evidence_class") or "")] = evidence.get(str(row.get("evidence_class") or ""), 0) + 1
            decisions[str(row.get("decision") or "")] = decisions.get(str(row.get("decision") or ""), 0) + 1
            if str(row.get("decision") or "").startswith("selected") and row.get("is_live_data"):
                strategy = str(row.get("strategy") or "")
                live_selected[strategy] = live_selected.get(strategy, 0) + 1
        report["journal_evidence"] = {
            "rows": len(rows),
            "evidence_class": dict(sorted(evidence.items(), key=lambda item: item[1], reverse=True)),
            "decisions": dict(sorted(decisions.items(), key=lambda item: item[1], reverse=True)[:20]),
            "live_selected_by_strategy": dict(sorted(live_selected.items(), key=lambda item: item[1], reverse=True)),
        }
    best = report.get("best_live_cohorts") or []
    tradable = report.get("tradable_cohorts") or []
    if best:
        report["improvement_notes"].append(
            "Require PAPER_PROMISING/LIVE_EVIDENCE_READY before promoting option strike-flow rows to tradable."
        )
    if tradable and all(float(row.get("avg_net_r") or 0) <= 0 for row in tradable):
        report["improvement_notes"].append(
            "Historically tradable strike-flow cohorts are negative; permissive promotion should stay disabled."
        )
    Path(output_path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default=REPORT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_report(output_path=args.output)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(
            "option generated-data audit | "
            f"snapshot_days={len(report.get('snapshot_days') or [])} "
            f"best_cohorts={len(report.get('best_live_cohorts') or [])} "
            f"worst_cohorts={len(report.get('worst_live_cohorts') or [])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
