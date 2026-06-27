#!/usr/bin/env python3
"""Backfill option-chain snapshot evidence into the option decision journal."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable


SNAPSHOT_DB = "option_chain_snapshots.db"
JOURNAL = "option_decision_journal.jsonl"


def _load_existing_source_ids(path: str = JOURNAL) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        sid = str(row.get("source_id", "") or "")
        if sid:
            out.add(sid)
    return out


def _side_from_summary(summary: Dict[str, Any]) -> str:
    bias = str(summary.get("net_bias", "") or "").upper()
    if "BULL" in bias:
        return "BUY"
    if "BEAR" in bias:
        return "SELL"
    return ""


def _score_from_summary(summary: Dict[str, Any]) -> float:
    try:
        bull = float(summary.get("bullish_score", 0.0) or 0.0)
        bear = float(summary.get("bearish_score", 0.0) or 0.0)
        return round(max(bull, bear), 4)
    except Exception:
        return 0.0


def backfill_option_signal_evidence(
    *,
    db_path: str = SNAPSHOT_DB,
    journal_path: str = JOURNAL,
    date_prefix: str = "",
    limit: int = 0,
) -> Dict[str, Any]:
    from option_decision_journal import record_option_decision

    if not Path(db_path).exists():
        return {"ok": False, "reason": "snapshot_db_missing", "inserted": 0}
    existing = _load_existing_source_ids(journal_path)
    params: list[Any] = []
    where = "WHERE ok=1"
    if date_prefix:
        where += " AND substr(snapshot_time,1,10)=?"
        params.append(str(date_prefix))
    sql = (
        "SELECT snapshot_time, underlying, spot, expiry, atm_strike, summary_json "
        f"FROM option_chain_snapshots {where} ORDER BY snapshot_time"
    )
    if int(limit or 0) > 0:
        sql += " LIMIT ?"
        params.append(int(limit))

    inserted = 0
    skipped = 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    for snapshot_time, underlying, spot, expiry, atm_strike, summary_json in rows:
        source_id = f"option_chain_snapshot:{underlying}:{snapshot_time}"
        if source_id in existing:
            skipped += 1
            continue
        try:
            summary = json.loads(summary_json or "{}")
        except Exception:
            summary = {}
        side = _side_from_summary(summary)
        decision = "chain_snapshot_evidence" if side else "chain_snapshot_neutral"
        record_option_decision(
            strategy="option_chain_snapshot",
            symbol=str(underlying or ""),
            decision=decision,
            reason=str(summary.get("net_bias", "snapshot_evidence") or "snapshot_evidence"),
            side=side,
            spot=float(spot or summary.get("spot", 0.0) or 0.0),
            setup_score=_score_from_summary(summary),
            quality={
                "snapshot_time": snapshot_time,
                "expiry": expiry,
                "atm_strike": atm_strike,
                "summary": summary,
            },
            selected={},
            strikes=[],
            source_id=source_id,
            path=journal_path,
        )
        existing.add(source_id)
        inserted += 1

    return {
        "ok": True,
        "db_path": db_path,
        "journal_path": journal_path,
        "date_prefix": date_prefix,
        "snapshots_seen": len(rows),
        "inserted": inserted,
        "skipped_existing": skipped,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = backfill_option_signal_evidence(date_prefix=args.date, limit=args.limit)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(
            "option signal evidence | "
            f"inserted={report.get('inserted', 0)} "
            f"skipped={report.get('skipped_existing', 0)} "
            f"seen={report.get('snapshots_seen', 0)}"
        )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
