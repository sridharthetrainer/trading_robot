#!/usr/bin/env python3
"""
db_health.py — report-only integrity check for the project's SQLite DBs.

Audit gap: 17 SQLite DBs with no integrity/version guard. This runs SQLite's own
`PRAGMA integrity_check`, confirms each DB opens, lists tables, and counts rows —
so corruption / truncation / accidental empties are caught EARLY (surfaced on the
dashboard + alerted nightly) instead of silently poisoning the pipeline.

Read-only: no migrations, no writes, never deletes. Safe to run anytime.

Usage:
    python db_health.py                # check all *.db in the project dir
    python db_health.py --db trades.db
"""
from __future__ import annotations

import argparse
import glob
import sqlite3
from typing import Any, Dict, List


def check_db(path: str) -> Dict[str, Any]:
    """Integrity + shape of one DB. Never raises."""
    res: Dict[str, Any] = {"db": path, "ok": False, "integrity": "?",
                           "tables": 0, "rows": 0, "error": None}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            res["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tbls = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            res["tables"] = len(tbls)
            total = 0
            for t in tbls:
                try:
                    total += conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                except Exception:
                    pass
            res["rows"] = total
            res["ok"] = (res["integrity"] == "ok")
        finally:
            conn.close()
    except Exception as exc:
        res["error"] = str(exc)[:120]
    return res


def check_all(pattern: str = "*.db") -> List[Dict[str, Any]]:
    return [check_db(p) for p in sorted(glob.glob(pattern))]


def summary() -> Dict[str, Any]:
    rows = check_all()
    bad = [r["db"] for r in rows if not r["ok"]]
    return {"n_dbs": len(rows), "healthy": len(rows) - len(bad), "bad": bad, "detail": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="SQLite DB health check (report-only)")
    ap.add_argument("--db", help="check a single db")
    args = ap.parse_args()
    rows = [check_db(args.db)] if args.db else check_all()
    print(f"\nDB HEALTH — {len(rows)} database(s)")
    print("-" * 60)
    for r in rows:
        flag = "✅" if r["ok"] else "❌"
        extra = r["integrity"] if r["ok"] else (r["error"] or r["integrity"])
        print(f"  {flag} {r['db']:34s} tables={r['tables']:<3} rows={r['rows']:<9} {extra}")
    bad = [r["db"] for r in rows if not r["ok"]]
    print("-" * 60)
    print(f"  {len(rows)-len(bad)}/{len(rows)} healthy" + (f"  ⚠ BAD: {bad}" if bad else "  ✅ all OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
