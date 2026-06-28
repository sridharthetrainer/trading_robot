#!/usr/bin/env python3
"""
learning_coverage_report.py

Report whether the expanded learning universe is actually producing learnable
rows in signal_log.db.

Run:
    .venv/bin/python learning_coverage_report.py
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPORT_JSON = "learning_coverage_report.json"
REPORT_MD = "LEARNING_COVERAGE_REPORT.md"


def _connect(db_path: str = "signal_log.db"):
    return sqlite3.connect(db_path)


def _query_rows(db_path: str) -> List[Dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            symbol,
            COUNT(*) AS total,
            SUM(CASE WHEN tb_label IN (-1,0,1) THEN 1 ELSE 0 END) AS legacy_labelled,
            SUM(CASE WHEN tb_label IN (-1,0,1) AND training_eligible=1
                      AND stop_loss>0 AND target>0 AND rr>0 THEN 1 ELSE 0 END) AS labelled,
            SUM(CASE WHEN tb_label = -99 THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN executed = 1 THEN 1 ELSE 0 END) AS executed,
            SUM(CASE WHEN option_symbol != '' OR option_strike > 0 THEN 1 ELSE 0 END) AS option_rows,
            MAX(log_time) AS last_log_time
        FROM signal_log
        GROUP BY symbol
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_learning_coverage_report(db_path: str = "signal_log.db") -> Dict[str, Any]:
    from universe_manager import build_learning_universe, probation_universe

    universe = build_learning_universe()
    probation = probation_universe()
    rows = _query_rows(db_path)
    by_symbol = {str(r.get("symbol", "") or "").upper(): r for r in rows}

    unlogged = [s for s in universe if s not in by_symbol]
    logged_universe = [s for s in universe if s in by_symbol]
    pending_symbols = [
        s for s in logged_universe
        if int(by_symbol[s].get("pending", 0) or 0) > 0
    ]
    labelled_symbols = [
        s for s in logged_universe
        if int(by_symbol[s].get("labelled", 0) or 0) > 0
    ]
    executed_symbols = [
        s for s in logged_universe
        if int(by_symbol[s].get("executed", 0) or 0) > 0
    ]
    option_symbols = [
        s for s in logged_universe
        if int(by_symbol[s].get("option_rows", 0) or 0) > 0
    ]
    totals = {
        "signal_rows": sum(int(r.get("total", 0) or 0) for r in rows),
        "labelled_rows": sum(int(r.get("labelled", 0) or 0) for r in rows),
        "legacy_labelled_rows": sum(int(r.get("legacy_labelled", 0) or 0) for r in rows),
        "pending_rows": sum(int(r.get("pending", 0) or 0) for r in rows),
        "executed_rows": sum(int(r.get("executed", 0) or 0) for r in rows),
        "option_rows": sum(int(r.get("option_rows", 0) or 0) for r in rows),
    }
    eligible_pending = 0
    today_pending = 0
    if Path(db_path).exists():
        conn = _connect(db_path)
        try:
            eligible_pending = int(conn.execute(
                "SELECT COUNT(*) FROM signal_log "
                "WHERE tb_label = -99 AND training_eligible=1 "
                "AND signal_date <= date('now','localtime','-1 day')"
            ).fetchone()[0] or 0)
            today_pending = int(conn.execute(
                "SELECT COUNT(*) FROM signal_log "
                "WHERE tb_label = -99 AND training_eligible=1 "
                "AND signal_date = date('now','localtime')"
            ).fetchone()[0] or 0)
        finally:
            conn.close()
    totals["eligible_pending_rows"] = eligible_pending
    totals["today_pending_rows"] = today_pending
    coverage = {
        "universe_count": len(universe),
        "logged_universe_count": len(logged_universe),
        "unlogged_universe_count": len(unlogged),
        "labelled_symbol_count": len(labelled_symbols),
        "pending_symbol_count": len(pending_symbols),
        "executed_symbol_count": len(executed_symbols),
        "option_symbol_count": len(option_symbols),
        "logged_pct": round(len(logged_universe) / max(len(universe), 1), 4),
        "labelled_pct": round(len(labelled_symbols) / max(len(universe), 1), 4),
    }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_path": db_path,
        "ok": bool(totals["signal_rows"] > 0 and coverage["logged_universe_count"] > 0),
        "totals": totals,
        "coverage": coverage,
        "probation_universe": probation,
        "unlogged_universe": unlogged[:100],
        "pending_top": sorted(
            [by_symbol[s] for s in pending_symbols],
            key=lambda r: int(r.get("pending", 0) or 0),
            reverse=True,
        )[:30],
        "labelled_symbols": labelled_symbols[:100],
        "executed_symbols": executed_symbols[:100],
        "option_symbols": option_symbols[:100],
    }


def render_markdown(report: Dict[str, Any]) -> str:
    totals = report.get("totals", {})
    cov = report.get("coverage", {})
    lines = [
        "# Learning Coverage Report",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Status: `{'PASS' if report.get('ok') else 'WARN'}`",
        f"- Signal rows: `{totals.get('signal_rows', 0)}`",
        f"- Labelled rows: `{totals.get('labelled_rows', 0)}`",
        f"- Pending rows: `{totals.get('pending_rows', 0)}`",
        f"- Eligible pending rows: `{totals.get('eligible_pending_rows', 0)}`",
        f"- Today pending rows: `{totals.get('today_pending_rows', 0)}`",
        f"- Executed rows: `{totals.get('executed_rows', 0)}`",
        f"- Option rows: `{totals.get('option_rows', 0)}`",
        "",
        "## Universe Coverage",
        "",
        f"- Learning universe: `{cov.get('universe_count', 0)}`",
        f"- Logged universe symbols: `{cov.get('logged_universe_count', 0)}`",
        f"- Unlogged universe symbols: `{cov.get('unlogged_universe_count', 0)}`",
        f"- Labelled symbols: `{cov.get('labelled_symbol_count', 0)}`",
        f"- Pending-label symbols: `{cov.get('pending_symbol_count', 0)}`",
        f"- Executed symbols: `{cov.get('executed_symbol_count', 0)}`",
        "",
        "## Top Pending Symbols",
        "",
    ]
    for row in report.get("pending_top", [])[:15]:
        lines.append(
            f"- `{row.get('symbol')}` pending `{row.get('pending', 0)}` "
            f"total `{row.get('total', 0)}` executed `{row.get('executed', 0)}`"
        )
    if not report.get("pending_top"):
        lines.append("- none")
    lines.extend(["", "## Unlogged Learning Symbols", ""])
    for symbol in report.get("unlogged_universe", [])[:40]:
        lines.append(f"- `{symbol}`")
    if not report.get("unlogged_universe"):
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    report = build_learning_coverage_report()
    Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    Path(REPORT_MD).write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
