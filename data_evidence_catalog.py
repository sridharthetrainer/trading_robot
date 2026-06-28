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
    if name in {"experiments.db", "confluence_features.db", "option_structure_training.db"}:
        return "DERIVED_RESEARCH"
    return "RAW_OR_OPERATIONAL"


def _catalog_db(path: Path) -> dict:
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
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5) as conn:
            integrity = conn.execute("PRAGMA quick_check").fetchone()
            row["quick_check"] = integrity[0] if integrity else "unknown"
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (table,) in tables:
                quoted = '"' + str(table).replace('"', '""') + '"'
                count = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                columns = [item[1] for item in conn.execute(f"PRAGMA table_info({quoted})")]
                row["tables"].append({"name": table, "rows": int(count), "columns": columns})
            row["ok"] = row.get("quick_check") == "ok"
    except Exception as exc:
        row.update(ok=False, error=str(exc))
    return row


def build_evidence_catalog(*, root: str = ".", report_file: str = REPORT_FILE, write: bool = True) -> dict:
    base = Path(root)
    databases = [_catalog_db(path) for path in sorted(base.glob("*.db"))]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "policy": {
            "preserve_all_rows": True,
            "legacy_data_role": "research_only_until_source_and_timing_are_verified",
            "promotion_rule": "only row-level verified live provenance may unlock live trading",
        },
        "database_count": len(databases),
        "table_count": sum(len(row.get("tables", [])) for row in databases),
        "total_rows": sum(table["rows"] for row in databases for table in row.get("tables", [])),
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
