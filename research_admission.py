"""
research_admission.py — enforces the 2026-07-23 edge-thesis decision
document's research-admission gate (published decision document, GPT-5.6-
audited across 3 rounds): while config.ALPHA_RESEARCH_MODE == "MAINTENANCE",
no new historical alpha research may start without a logged, cooling-off-
period-elapsed, explicitly approved proposal. "New alpha research" is defined
by function, not label, in that document's Section 9: any new predictor,
strategy, feature, threshold, combination, subgroup, regime split, trade
expression, ranking rule, sizing rule, or entry/exit rule -- regardless of
whether it's called a diagnostic, monitoring enhancement, robustness check,
architecture cleanup, shadow experiment, or exploratory report.

This module is a convention and an audit trail, not a sandbox -- it cannot
stop a human or an AI assistant from simply not calling require_admission().
Its value is making the admission bar visible and creating a timestamped,
append-only record, not making violation technically impossible.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

LOG_FILE = Path("research_admission_log.jsonl")
COOLING_OFF_DAYS = 7

# The 6 questions from the decision document's Section 8 admission rule.
REQUIRED_QUESTIONS = (
    "new_information_or_mechanism",
    "why_not_already_arbitraged",
    "why_capturable_after_costs",
    "how_different_from_rejected_families",
    "termination_evidence",
    "max_budget",
)


def _now() -> datetime:
    return datetime.now()


def _read_log() -> List[Dict[str, Any]]:
    if not LOG_FILE.exists():
        return []
    entries = []
    with open(LOG_FILE) as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _append_log(entry: Dict[str, Any]) -> None:
    with open(LOG_FILE, "a") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def propose(answers: Dict[str, str], proposal_id: Optional[str] = None) -> str:
    """Log a new research proposal. Requires all 6 admission-gate questions
    answered (non-empty). Returns the proposal_id. Status starts 'pending' --
    cannot be approved until COOLING_OFF_DAYS has elapsed since this call."""
    missing = [q for q in REQUIRED_QUESTIONS if not str(answers.get(q, "")).strip()]
    if missing:
        raise ValueError(f"proposal missing required answers: {missing}")
    proposal_id = proposal_id or f"prop_{uuid.uuid4().hex[:8]}"
    entry = {
        "proposal_id": proposal_id,
        "event": "proposed",
        "logged_at": _now().isoformat(),
        "cooling_off_until": (_now() + timedelta(days=COOLING_OFF_DAYS)).isoformat(),
        "status": "pending",
        "answers": {q: answers[q] for q in REQUIRED_QUESTIONS},
        "approved_at": None,
        "approved_by": None,
    }
    _append_log(entry)
    return proposal_id


def approve(proposal_id: str, approved_by: str) -> Dict[str, Any]:
    """Mark a logged proposal approved. Refuses if the cooling-off period
    hasn't elapsed yet, or if no matching pending proposal exists (including
    if it was already approved -- approval is one-shot). Uses each
    proposal_id's LATEST log entry to determine current status -- earlier
    append-only log lines still literally say 'pending' forever, since
    entries are never rewritten, only appended to."""
    matching = [e for e in _read_log() if e["proposal_id"] == proposal_id]
    if not matching or matching[-1]["status"] != "pending":
        raise ValueError(f"no pending proposal found for id={proposal_id!r}")
    current = matching[-1]
    cooling_off_until = datetime.fromisoformat(current["cooling_off_until"])
    if _now() < cooling_off_until:
        raise ValueError(
            f"cooling-off period not elapsed for {proposal_id!r}: "
            f"wait until {cooling_off_until.isoformat()}"
        )
    approved_entry = {
        **current,
        "event": "approved",
        "status": "approved",
        "approved_at": _now().isoformat(),
        "approved_by": approved_by,
    }
    _append_log(approved_entry)
    return approved_entry


def _latest_status(proposal_id: str) -> Optional[str]:
    status = None
    for e in _read_log():
        if e["proposal_id"] == proposal_id:
            status = e["status"]
    return status


def require_admission(proposal_id: str) -> None:
    """Call this at the top of any new research script/module. No-op if
    config.ALPHA_RESEARCH_MODE isn't 'MAINTENANCE'. Otherwise raises
    RuntimeError unless proposal_id is logged and approved."""
    if config.ALPHA_RESEARCH_MODE != "MAINTENANCE":
        return
    status = _latest_status(proposal_id)
    if status != "approved":
        raise RuntimeError(
            f"ALPHA_RESEARCH_MODE=MAINTENANCE: research proposal "
            f"{proposal_id!r} is not approved (status={status!r}). See the "
            "2026-07-23 edge-thesis decision document -- log a proposal with "
            "research_admission.propose() and wait out the "
            f"{COOLING_OFF_DAYS}-day cooling-off period before approving it."
        )


def list_proposals(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Latest-state view: one entry per proposal_id, showing its most recent
    status (pending/approved)."""
    latest: Dict[str, Dict[str, Any]] = {}
    for e in _read_log():
        latest[e["proposal_id"]] = e
    entries = list(latest.values())
    if status is not None:
        entries = [e for e in entries if e["status"] == status]
    return entries
