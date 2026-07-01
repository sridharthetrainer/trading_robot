#!/usr/bin/env python3
"""Rank a small paper-only cohort from clean, after-cost generated outcomes."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict


def build_review(
    db_path: str = "signal_log.db", *, min_days: int = 3, min_samples: int = 10,
    limit: int = 5, write: bool = True, report_file: str = "forward_cohort_review.json",
) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {"ok": False, "reason": "signal_log_missing", "recommendations": []}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT strategy, COUNT(*) samples, COUNT(DISTINCT signal_date) clean_days,
                   AVG(tb_r_multiple_net) avg_net_r,
                   SUM(CASE WHEN tb_r_multiple_net>0 THEN tb_r_multiple_net ELSE 0 END) /
                   NULLIF(ABS(SUM(CASE WHEN tb_r_multiple_net<0 THEN tb_r_multiple_net ELSE 0 END)),0) profit_factor,
                   AVG(CASE WHEN tb_r_multiple_net>0 THEN 1.0 ELSE 0.0 END) positive_rate
              FROM signal_log
             WHERE training_eligible=1 AND tb_label IN (-1,0,1)
             GROUP BY strategy
            HAVING COUNT(*)>=? AND COUNT(DISTINCT signal_date)>=?
             ORDER BY AVG(tb_r_multiple_net) DESC, COUNT(*) DESC
            """, (int(min_samples), int(min_days)),
        ).fetchall()
    ranked = [dict(row) for row in rows]
    recommendations = ranked[: max(1, min(int(limit), 5))]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": True,
        "policy": "paper_forward_test_only_no_live_promotion",
        "minimums": {"clean_days": min_days, "samples": min_samples},
        "recommendations": recommendations,
        "live_approved": False,
        "live_requirements": {"clean_days": 15, "clean_outcomes": 5000, "profit_factor": 1.2, "walk_forward_stable": True},
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_review(write=not args.no_write), indent=2))
