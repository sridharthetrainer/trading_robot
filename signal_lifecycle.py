"""Canonical signal lifecycle and correlated-candidate consolidation."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

# NOTE (2026-07-28 audit finding): evaluate_option_candidate() below can only
# ever return GENERATED / PRICED / COST_POSITIVE -- it evaluates a candidate
# at signal-generation time, before any order exists. LIQUID, PAPER_FILLED,
# and OUTCOME_VERIFIED describe stages of a real order's lifecycle (a fill
# actually happening, an outcome actually being observed) that no caller of
# this function can produce, because this system's option paths are
# shadow/journal-only (no real order is ever placed to fill or verify). These
# three stages are kept here as a documented, honest description of what a
# real execution layer would need to populate -- not as evidence that this
# check currently protects against them.
STAGES = (
    "GENERATED", "PRICED", "LIQUID", "COST_POSITIVE",
    "PAPER_FILLED", "OUTCOME_VERIFIED",
)
_STAGE_INDEX = {stage: index for index, stage in enumerate(STAGES)}


class SignalLifecycleStore:
    """Append-only, restart-safe lifecycle transition journal."""

    def __init__(self, db_path: str = "signal_lifecycle.db") -> None:
        self.db_path = str(db_path)
        Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS signal_lifecycle_events (
                       event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                       signal_id TEXT NOT NULL,
                       stage TEXT NOT NULL,
                       recorded_at REAL NOT NULL,
                       reasons_json TEXT NOT NULL,
                       metadata_json TEXT NOT NULL,
                       UNIQUE(signal_id, stage)
                   )"""
            )

    def current_stage(self, signal_id: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT stage FROM signal_lifecycle_events WHERE signal_id=? "
                "ORDER BY event_id DESC LIMIT 1",
                (str(signal_id),),
            ).fetchone()
        return str(row[0]) if row else None

    def transition(
        self,
        signal_id: str,
        stage: str,
        *,
        reasons: Iterable[str] = (),
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        stage = str(stage).upper()
        if stage not in _STAGE_INDEX:
            raise ValueError(f"unknown_lifecycle_stage:{stage}")
        signal_id = str(signal_id).strip()
        if not signal_id:
            raise ValueError("missing_signal_id")
        with sqlite3.connect(self.db_path) as conn:
            previous_row = conn.execute(
                "SELECT stage FROM signal_lifecycle_events WHERE signal_id=? "
                "ORDER BY event_id DESC LIMIT 1",
                (signal_id,),
            ).fetchone()
            previous = str(previous_row[0]) if previous_row else None
            expected_index = 0 if previous is None else _STAGE_INDEX[previous] + 1
            if _STAGE_INDEX[stage] != expected_index:
                raise ValueError(
                    f"illegal_lifecycle_transition:{previous or 'NONE'}->{stage}"
                )
            now = time.time()
            conn.execute(
                "INSERT INTO signal_lifecycle_events "
                "(signal_id,stage,recorded_at,reasons_json,metadata_json) "
                "VALUES (?,?,?,?,?)",
                (
                    signal_id, stage, now,
                    json.dumps(list(reasons), sort_keys=True),
                    json.dumps(metadata or {}, sort_keys=True, default=str),
                ),
            )
        return {
            "signal_id": signal_id, "stage": stage, "previous_stage": previous,
            "recorded_at": now,
        }

    def actionable(self, signal_id: str, *, live: bool = False) -> bool:
        required = "PAPER_FILLED" if live else "COST_POSITIVE"
        current = self.current_stage(signal_id)
        return bool(current and _STAGE_INDEX[current] >= _STAGE_INDEX[required])


@dataclass(frozen=True)
class LifecycleDecision:
    stage: str
    actionable: bool
    reasons: Tuple[str, ...]
    checked_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_option_candidate(candidate: Dict[str, Any]) -> LifecycleDecision:
    selected = candidate.get("selected") or candidate.get("primary_strike") or candidate
    reasons: List[str] = []
    if not str(selected.get("symbol") or "").strip():
        reasons.append("missing_contract_symbol")
    if not str(selected.get("expiry") or "").strip():
        reasons.append("missing_expiry")
    if str(selected.get("option_type") or "").upper() not in {"CE", "PE"}:
        reasons.append("missing_option_type")
    if float(selected.get("strike") or 0) <= 0:
        reasons.append("missing_strike")
    if float(selected.get("premium") or selected.get("entry_price") or 0) <= 0:
        reasons.append("missing_premium")
    for name in ("oi", "volume"):
        if float(selected.get(name) or 0) <= 0:
            reasons.append(f"missing_{name}")
    spread = selected.get("spread_pct")
    if spread is None:
        reasons.append("missing_spread")
    cost_positive = candidate.get("cost_positive")
    if cost_positive is not True:
        reasons.append("cost_edge_unverified")
    if reasons:
        priced_fields = {"missing_contract_symbol", "missing_expiry", "missing_option_type",
                         "missing_strike", "missing_premium"}
        stage = "GENERATED" if any(r in priced_fields for r in reasons) else "PRICED"
    else:
        stage = "COST_POSITIVE"
    return LifecycleDecision(stage, not reasons, tuple(reasons), time.time())


def consolidate_correlated_candidates(
    candidates: Iterable[Dict[str, Any]],
    *,
    score: Callable[[Dict[str, Any]], float],
    cluster: Callable[[Dict[str, Any]], str],
    side: Callable[[Dict[str, Any]], str],
) -> List[Dict[str, Any]]:
    """Keep one highest-ranked candidate per correlated cluster and direction."""
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for candidate in candidates:
        key = (str(cluster(candidate) or "").upper(), str(side(candidate) or "").upper())
        current = best.get(key)
        if current is None or score(candidate) > score(current):
            best[key] = candidate
    return sorted(best.values(), key=score, reverse=True)
