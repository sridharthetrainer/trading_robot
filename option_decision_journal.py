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
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_JOURNAL_FILE = "option_decision_journal.jsonl"
MAX_FIELD_CHARS = 2000
AUTO_SHADOW_CANDIDATES = os.getenv("OPTION_JOURNAL_AUTO_SHADOWS", "true").lower() == "true"
AUTO_SHADOW_WINGS = max(1, int(os.getenv("OPTION_JOURNAL_SHADOW_WINGS", "4")))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _underlying_root(symbol: str) -> str:
    m = re.match(r"^([A-Z]+)", str(symbol or "").upper())
    return m.group(1) if m else ""


def _strike_step(root: str, strike: float) -> int:
    root = str(root or "").upper()
    if root in {"BANKNIFTY", "SENSEX", "BANKEX"}:
        return 100
    if root in {"NIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}:
        return 50
    if strike >= 10000:
        return 100
    if strike >= 1000:
        return 50
    return 10


def _build_symbol_like(selected_symbol: str, strike: int, option_type: str) -> str:
    raw = str(selected_symbol or "").upper()
    if not raw:
        return ""
    return re.sub(r"\d{4,6}(CE|PE)$", f"{int(strike)}{option_type}", raw)


def synthesize_shadow_candidates(
    selected: Optional[Dict[str, Any]],
    *,
    spot: float = 0.0,
    side: str = "",
    wings: int = AUTO_SHADOW_WINGS,
) -> list:
    """
    Build a small comparison ladder when live option-chain candidates are absent.

    The candidates are explicitly marked synthetic; they are a coverage fallback
    for journaling and EOD comparison, not a replacement for real chain rows.
    """
    if not isinstance(selected, dict):
        return []
    strike = _safe_int(selected.get("strike"), 0)
    option_type = str(selected.get("option_type") or "").upper()
    if strike <= 0 or option_type not in {"CE", "PE"}:
        return []
    symbol = str(selected.get("symbol") or "")
    root = _underlying_root(symbol) or str(selected.get("underlying") or "")
    step = _strike_step(root, strike)
    premium = _safe_float(selected.get("premium") or selected.get("entry_price"), 0.0)
    out = []
    for offset in range(-int(wings), int(wings) + 1):
        cand_strike = int(strike + offset * step)
        if cand_strike <= 0:
            continue
        out.append({
            "symbol": _build_symbol_like(symbol, cand_strike, option_type),
            "strike": cand_strike,
            "option_type": option_type,
            "premium": premium,
            "spot": _safe_float(selected.get("spot"), _safe_float(spot, 0.0)),
            "dte": _safe_int(selected.get("dte"), 0),
            "expiry": selected.get("expiry") or selected.get("option_expiry") or "",
            "strike_type": "SELECTED" if offset == 0 else f"{offset:+d}STEP",
            "shadow": offset != 0,
            "synthetic_shadow": True,
            "entry_source": "selected_premium_fallback",
            "side": str(side or ""),
        })
    return out


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
    metadata: Optional[Dict[str, Any]] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Append one option decision to JSONL and return the payload.

    decision examples: selected, blocked_quality, blocked_no_tradeable_strike.
    """
    out_path = Path(ensure_option_journal(path))
    strike_rows = strikes or []
    if (
        AUTO_SHADOW_CANDIDATES
        and str(decision or "").startswith("selected")
        and not strike_rows
    ):
        strike_rows = synthesize_shadow_candidates(selected, spot=spot, side=side)
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
        "strikes": _clean(strike_rows),
        "trade_id": str(trade_id or ""),
        "source_id": str(source_id or ""),
    }
    if metadata:
        payload["metadata"] = _clean(metadata)
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
        row_trade_id = str(row.get("trade_id", "") or "").strip()
        row_source_id = str(row.get("source_id", "") or "").strip()
        if (
            (row_trade_id == trade_id or row_source_id == trade_id)
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


def repair_missing_shadow_candidates(
    *,
    path: str = DEFAULT_JOURNAL_FILE,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Enrich existing selected rows that have no strike comparison ladder.

    Returns counts and rewrites the journal only when rows are changed.
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "reason": "journal_missing", "updated": 0}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    new_lines = []
    updated = 0
    selected_seen = 0
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if str(row.get("decision", "")).startswith("selected"):
            selected_seen += 1
            strikes = row.get("strikes")
            needs_synthetic_refresh = (
                isinstance(strikes, list)
                and strikes
                and len(strikes) < (AUTO_SHADOW_WINGS * 2 + 1)
                and all(isinstance(s, dict) and s.get("synthetic_shadow") for s in strikes)
            )
            if not isinstance(strikes, list) or not strikes or needs_synthetic_refresh:
                existing_outcomes = {}
                if isinstance(strikes, list):
                    for item in strikes:
                        if isinstance(item, dict) and isinstance(item.get("shadow_outcome"), dict):
                            key = (
                                str(item.get("symbol") or ""),
                                str(item.get("strike") or ""),
                                str(item.get("option_type") or ""),
                            )
                            existing_outcomes[key] = item["shadow_outcome"]
                synth = synthesize_shadow_candidates(
                    row.get("selected") if isinstance(row.get("selected"), dict) else {},
                    spot=_safe_float(row.get("spot"), 0.0),
                    side=str(row.get("side") or ""),
                )
                if synth:
                    for item in synth:
                        key = (
                            str(item.get("symbol") or ""),
                            str(item.get("strike") or ""),
                            str(item.get("option_type") or ""),
                        )
                        if key in existing_outcomes:
                            item["shadow_outcome"] = existing_outcomes[key]
                    row["strikes"] = synth
                    row["shadow_repaired_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    updated += 1
        new_lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
    if updated and not dry_run:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "journal_file": str(p),
        "selected_seen": selected_seen,
        "updated": updated,
        "dry_run": dry_run,
    }
