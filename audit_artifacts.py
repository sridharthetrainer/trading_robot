"""Immutable, content-addressed snapshots for mutable audit reports."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict


def write_report_with_snapshot(
    path: str | Path,
    payload: Dict[str, Any],
    *,
    snapshot_root: str | Path = "audit_snapshots",
) -> Dict[str, str]:
    # The sha256 was previously computed but only returned to the caller --
    # both call sites (system_readiness_report.py, option_bot_audit.py)
    # discarded the return value, so a report file on disk had no way to be
    # checked against its own claimed snapshot (a 2026-07-28 audit finding).
    # Embedded into the payload itself so the report is self-verifying.
    digest_body = hashlib.sha256(
        json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    folder = Path(snapshot_root) / Path(path).stem
    immutable_name = f"{stamp}_{digest_body[:12]}.json"
    payload_with_meta = dict(payload)
    payload_with_meta["_audit_snapshot"] = {
        "sha256": digest_body,
        "snapshot_file": str(folder / immutable_name),
    }
    raw = json.dumps(payload_with_meta, indent=2, sort_keys=True, default=str).encode("utf-8")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    folder.mkdir(parents=True, exist_ok=True)
    immutable = folder / immutable_name
    if not immutable.exists():
        immutable.write_bytes(raw)
    return {"sha256": digest_body, "snapshot": str(immutable), "latest": str(target)}


def evidence_scorecard(
    *, capability: float, live_ready: int, total_strategies: int,
    paired_fills: int, target_paired_fills: int, net_pnl: float,
) -> Dict[str, Any]:
    edge = 100.0 * live_ready / max(1, total_strategies)
    execution = 100.0 * min(1.0, paired_fills / max(1, target_paired_fills))
    return {
        "capability_score": round(float(capability), 1),
        "verified_edge_score": round(edge, 1),
        "execution_evidence_score": round(execution, 1),
        "profitability_observed": bool(net_pnl > 0 and live_ready > 0),
        "interpretation": (
            "Capability measures infrastructure only; edge and execution "
            "evidence independently control trading readiness."
        ),
    }
