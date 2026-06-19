"""
option_decision_journal.py

Append-only JSONL journal for option-bot decisions.

This is separate from signal_log.db because it captures option-specific
pre-signal decisions: chain health, expected move, strike quality, selected
candidate, and block reasons.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_JOURNAL_FILE = "option_decision_journal.jsonl"
MAX_FIELD_CHARS = 2000


def ensure_option_journal(path: Optional[str] = None) -> str:
    out_path = Path(path or os.getenv("OPTION_DECISION_JOURNAL", DEFAULT_JOURNAL_FILE))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.touch(exist_ok=True)
    return str(out_path)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
            return value[:MAX_FIELD_CHARS] + "...[truncated]"
        return value
    try:
        return float(value)
    except Exception:
        return str(value)[:MAX_FIELD_CHARS]


def record_option_decision(
    *,
    strategy: str,
    symbol: str,
    decision: str,
    reason: str = "",
    side: str = "",
    spot: float = 0.0,
    setup_score: float = 0.0,
    quality: Optional[Dict[str, Any]] = None,
    selected: Optional[Dict[str, Any]] = None,
    strikes: Optional[Any] = None,
    trade_id: str = "",
    source_id: str = "",
    outcome: Optional[Dict[str, Any]] = None,
    outcome_label: Optional[int] = None,
    pnl: Optional[float] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Append one option decision to JSONL and return the payload.

    decision examples: selected, blocked_quality, blocked_no_tradeable_strike.
    """
    out_path = Path(ensure_option_journal(path))
    payload = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "strategy": str(strategy or ""),
        "symbol": str(symbol or ""),
        "decision": str(decision or ""),
        "reason": str(reason or ""),
        "side": str(side or ""),
        "spot": float(spot or 0.0),
        "setup_score": float(setup_score or 0.0),
        "quality": _clean(quality or {}),
        "selected": _clean(selected or {}),
        "strikes": _clean(strikes or []),
        "trade_id": str(trade_id or ""),
        "source_id": str(source_id or ""),
    }
    if outcome:
        payload["outcome"] = _clean(outcome)
    if outcome_label is not None:
        payload["outcome_label"] = int(outcome_label)
    if pnl is not None:
        payload["pnl"] = float(pnl)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
    return payload


def load_recent_option_decisions(
    path: str = DEFAULT_JOURNAL_FILE,
    limit: int = 100,
) -> list:
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-int(limit):]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def label_option_decision(
    trade_id: str,
    *,
    outcome_label: int,
    pnl: float,
    exit_reason: str = "",
    path: str = DEFAULT_JOURNAL_FILE,
) -> int:
    """
    Attach outcome data to selected journal rows matching trade_id.

    Returns number of rows updated. The rewrite is idempotent and preserves
    unrelated rows. If no matching row exists, returns 0.
    """
    trade_id = str(trade_id or "").strip()
    if not trade_id:
        return 0
    p = Path(path)
    if not p.exists():
        return 0

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    updated = 0
    new_lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if (
            str(row.get("trade_id", "") or "") == trade_id
            and str(row.get("decision", "")) == "selected"
        ):
            row["outcome"] = {
                "label": int(outcome_label),
                "pnl": float(pnl),
                "exit_reason": str(exit_reason or ""),
                "labelled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            row["outcome_label"] = int(outcome_label)
            row["pnl"] = float(pnl)
            updated += 1
        new_lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))

    if updated:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


def label_option_shadow_decisions(
    trade_id: str,
    shadow_outcomes: Any,
    *,
    path: str = DEFAULT_JOURNAL_FILE,
) -> int:
    """
    Attach outcome data to shadow strike candidates for a selected trade row.

    shadow_outcomes may be a list of dicts keyed by symbol/strike/option_type, or
    a dict keyed by symbol. Each outcome can include pnl, label, exit_price, and
    exit_reason. Returns number of shadow candidates updated.
    """
    trade_id = str(trade_id or "").strip()
    if not trade_id:
        return 0
    p = Path(path)
    if not p.exists():
        return 0

    if isinstance(shadow_outcomes, dict):
        outcomes = []
        for key, value in shadow_outcomes.items():
            item = dict(value) if isinstance(value, dict) else {"pnl": value}
            item.setdefault("symbol", key)
            outcomes.append(item)
    elif isinstance(shadow_outcomes, list):
        outcomes = [o for o in shadow_outcomes if isinstance(o, dict)]
    else:
        outcomes = []
    if not outcomes:
        return 0

    def _match(candidate: Dict[str, Any], outcome: Dict[str, Any]) -> bool:
        c_sym = str(candidate.get("symbol", "") or "")
        o_sym = str(outcome.get("symbol", "") or "")
        if c_sym and o_sym and c_sym == o_sym:
            return True
        try:
            c_strike = float(candidate.get("strike", 0) or 0)
            o_strike = float(outcome.get("strike", 0) or 0)
        except Exception:
            c_strike = o_strike = 0.0
        c_type = str(candidate.get("option_type", "") or "")
        o_type = str(outcome.get("option_type", "") or "")
        return bool(c_strike and o_strike and c_strike == o_strike and c_type == o_type)

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    updated = 0
    new_lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if (
            str(row.get("trade_id", "") or "") == trade_id
            and str(row.get("decision", "")) == "selected"
            and isinstance(row.get("strikes"), list)
        ):
            for candidate in row["strikes"]:
                if not isinstance(candidate, dict):
                    continue
                for outcome in outcomes:
                    if not _match(candidate, outcome):
                        continue
                    pnl = float(outcome.get("pnl", 0.0) or 0.0)
                    label = int(outcome.get("label", 1 if pnl > 0 else -1 if pnl < 0 else 0))
                    candidate["shadow_outcome"] = {
                        "label": label,
                        "pnl": pnl,
                        "exit_price": outcome.get("exit_price"),
                        "exit_reason": str(outcome.get("exit_reason", "") or ""),
                        "labelled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                    updated += 1
                    break
        new_lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))

    if updated:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated
