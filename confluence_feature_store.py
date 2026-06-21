#!/usr/bin/env python3
"""Build a durable confluence feature table from signal_log rows."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable


DB_PATH = "confluence_features.db"


def _conn(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS confluence_features (
            signal_id INTEGER PRIMARY KEY,
            log_time REAL DEFAULT 0,
            symbol TEXT DEFAULT '',
            side TEXT DEFAULT '',
            strategy TEXT DEFAULT '',
            score REAL DEFAULT 0,
            raw_score REAL DEFAULT 0,
            score_boost REAL DEFAULT 0,
            n_agree INTEGER DEFAULT 0,
            n_conflict INTEGER DEFAULT 0,
            agreeing_strats_json TEXT DEFAULT '[]',
            regime TEXT DEFAULT '',
            htf_bias TEXT DEFAULT '',
            confluence TEXT DEFAULT '',
            volume_ratio REAL DEFAULT 0,
            indicator_coverage REAL DEFAULT 0,
            candidate_confirmations INTEGER DEFAULT 0,
            modifier_sum REAL DEFAULT 0,
            rejection_reason TEXT DEFAULT '',
            executed INTEGER DEFAULT 0,
            tb_label INTEGER DEFAULT -99,
            updated_at TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn


def refresh_confluence_features(
    *,
    signal_db: str = "signal_log.db",
    db_path: str = DB_PATH,
    limit: int = 5000,
) -> Dict[str, Any]:
    if not Path(signal_db).exists():
        return {"ok": False, "reason": "signal_log_missing", "updated": 0}
    src = sqlite3.connect(signal_db)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        """
        SELECT *
          FROM signal_log
         ORDER BY id DESC
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    src.close()
    conn = _conn(db_path)
    modifier_cols = [
        "bhav_delivery", "cross_asset_mod", "participant_mod", "expiry_mod",
        "sip_boost", "bulk_deal_mod", "theta_mod", "rebal_mod", "news_mod",
        "mtf_pivot_mod", "gex_mod", "skew_mod", "whale_mod", "sr_level_mod",
        "pivot_boss_mod", "oi_mod", "structure_mod", "market_quality_mod",
        "market_profile_mod", "candidate_quality_mod", "ai_score", "rl_bias", "weinstein_mod",
    ]
    updated = 0
    for row in rows:
        keys = set(row.keys())
        mod_sum = sum(float(row[c] or 0) for c in modifier_cols if c in keys)
        score = float(row["score"] or 0)
        raw = float(row["raw_score"] or 0)
        conn.execute(
            """
            INSERT OR REPLACE INTO confluence_features
            (signal_id, log_time, symbol, side, strategy, score, raw_score, score_boost,
             n_agree, n_conflict, agreeing_strats_json, regime, htf_bias, confluence,
             volume_ratio, indicator_coverage, candidate_confirmations, modifier_sum,
             rejection_reason, executed, tb_label, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(row["id"]),
                float(row["log_time"] or 0),
                str(row["symbol"] or ""),
                str(row["side"] or ""),
                str(row["strategy"] or ""),
                score,
                raw,
                score - raw,
                int(row["n_agree"] or 0),
                int(row["n_conflict"] or 0),
                str(row["agreeing_strats"] or "[]"),
                str(row["regime"] or ""),
                str(row["htf_bias"] or ""),
                str(row["confluence"] or ""),
                float(row["volume_ratio"] or 0),
                float(row["indicator_coverage"] or 0),
                int(row["candidate_confirmations"] or 0),
                round(mod_sum, 4),
                str(row["rejection_reason"] or ""),
                int(row["executed"] or 0),
                int(row["tb_label"] or -99),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            ),
        )
        updated += 1
    conn.commit()
    conn.close()
    return {"ok": True, "rows_seen": len(rows), "updated": updated}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(json.dumps(refresh_confluence_features(limit=args.limit), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
