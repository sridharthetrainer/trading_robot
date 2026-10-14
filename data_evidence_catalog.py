#!/usr/bin/env python3
"""Read-only catalog of every project SQLite dataset and its evidence role."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

REPORT_FILE = "data_evidence_catalog.json"


def _tier(path: Path) -> str:
    name = path.name.lower()
    if any(token in name for token in ("historical", "replay")):
        return "RESEARCH_REPLAY"
    if name in {"trades.db", "option_chain_snapshots.db", "signal_log.db"}:
        return "MIXED_PROVENANCE_QUERY_ROW_FLAGS"
    if name in {"experiments.db", "autonomous_knowledge.db", "confluence_features.db", "option_structure_training.db"}:
        return "DERIVED_RESEARCH"
    return "RAW_OR_OPERATIONAL"


def _catalog_db(path: Path, *, metadata_only: bool = False) -> dict:
    row = {
        "path": str(path), "bytes": path.stat().st_size, "tier": _tier(path),
        "tables": [], "ok": True,
    }
    if path.stat().st_size == 0:
        # Empty placeholders are inventory, not corruption. Keep them visible
        # so an expected feed can be diagnosed without failing other datasets.
        row.update(empty=True, quick_check="not_applicable")
        return row
    try:
        from db_util import db_conn
        with db_conn(str(path.resolve()), read_only=True, timeout=2, query_budget=2) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (table,) in tables:
                quoted = '"' + str(table).replace('"', '""') + '"'
                columns = [item[1] for item in conn.execute(f"PRAGMA table_info({quoted})")]
                item = {"name": table, "rows": None, "columns": columns, "count_complete": False}
                if not metadata_only:
                    deadline = time.monotonic() + 2
                    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                    try:
                        item.update(rows=int(conn.execute(f"SELECT COUNT(1) FROM {quoted}").fetchone()[0]),count_complete=True)
                    except sqlite3.OperationalError as exc:
                        item['count_error']=str(exc)
                    finally:
                        conn.set_progress_handler(None,0)
                row["tables"].append(item)
            if metadata_only:
                row.update(quick_check='not_run_metadata_only',integrity_verified=False)
            else:
                deadline=time.monotonic()+2
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline),1000)
                try:
                    integrity=conn.execute('PRAGMA quick_check').fetchone()
                    row['quick_check']=integrity[0] if integrity else 'unknown'
                    row['integrity_verified']=row['quick_check']=='ok'
                    row['ok']=row['integrity_verified']
                except sqlite3.OperationalError as exc:
                    row.update(quick_check='not_completed',integrity_verified=False,ok=False,error=str(exc))
    except Exception as exc:
        row.update(ok=False, error=str(exc))
    return row


def build_evidence_catalog(*, root: str = ".", report_file: str = REPORT_FILE, write: bool = True, metadata_only: bool = False) -> dict:
    base = Path(root)
    databases = [_catalog_db(path,metadata_only=metadata_only) for path in sorted(base.glob("*.db"))]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "policy": {
            "preserve_all_rows": True,
            "legacy_data_role": "research_only_until_source_and_timing_are_verified",
            "promotion_rule": "only row-level verified live provenance may unlock live trading",
        },
        "database_count": len(databases),
        "table_count": sum(len(row.get("tables", [])) for row in databases),
        "total_rows": sum(table["rows"] or 0 for row in databases for table in row.get("tables", [])),
        "total_rows_complete": not metadata_only and all(row.get('ok') for row in databases) and all(table.get('count_complete') for row in databases for table in row.get('tables',[])),
        "metadata_only": metadata_only,
        "total_bytes": sum(row.get("bytes", 0) for row in databases),
        "ok": bool(databases) and all(row.get("ok") for row in databases),
        "issues": [row["path"] for row in databases if not row.get("ok")],
        "empty_databases": [row["path"] for row in databases if row.get("empty")],
        "databases": databases,
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(build_evidence_catalog(), indent=2))
