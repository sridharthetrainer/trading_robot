#!/usr/bin/env python3
"""Simulate top logged signals without placing orders."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPORT_JSON = "shadow_portfolio_report.json"


def simulate_shadow_portfolio(
    *,
    signal_db: str = "signal_log.db",
    days: int = 30,
    top_per_day: int = 5,
) -> Dict[str, Any]:
    if not Path(signal_db).exists():
        return {"ok": False, "reason": "signal_log_missing"}
    conn = sqlite3.connect(signal_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT *
          FROM signal_log
         WHERE signal_date >= date('now','localtime', ?)
           AND tb_label IN (1, 0, -1)
           AND training_eligible=1
           AND stop_loss>0 AND target>0 AND rr>0
         ORDER BY signal_date, score DESC, log_time
        """,
        (f"-{int(days)} day",),
    ).fetchall()
    conn.close()
    by_day: Dict[str, List[sqlite3.Row]] = {}
    for row in rows:
        by_day.setdefault(str(row["signal_date"]), []).append(row)
    picked = []
    for day, items in by_day.items():
        picked.extend(items[:top_per_day])
    wins = sum(1 for r in picked if int(r["tb_label"]) == 1)
    losses = sum(1 for r in picked if int(r["tb_label"]) == -1)
    timeouts = sum(1 for r in picked if int(r["tb_label"]) == 0)
    rejected_wins = sum(1 for r in picked if int(r["tb_label"]) == 1 and int(r["executed"] or 0) == 0)
    net_r = [float(r["tb_r_multiple_net"] or 0) for r in picked]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": True,
        "days": days,
        "top_per_day": top_per_day,
        "labelled_seen": len(rows),
        "shadow_trades": len(picked),
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "target_rate": round(wins / max(len(picked), 1), 4),
        "rejected_wins": rejected_wins,
        "total_net_r": round(sum(net_r), 4),
        "average_net_r": round(sum(net_r) / max(len(net_r), 1), 4),
        "after_cost_positive": bool(net_r and sum(net_r) > 0),
        "top": [
            {
                "date": r["signal_date"], "symbol": r["symbol"], "side": r["side"],
                "strategy": r["strategy"], "score": r["score"], "label": r["tb_label"],
                "executed": r["executed"],
            }
            for r in picked[:50]
        ],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--top-per-day", type=int, default=5)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = simulate_shadow_portfolio(days=args.days, top_per_day=args.top_per_day)
    if not args.no_write:
        Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
