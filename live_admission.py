"""Cached, evidence-based admission gate shared by every live-mode selector."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict


_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}
_CACHE_TTL_SEC = 300


def evaluate_live_admission(*, force: bool = False) -> Dict[str, Any]:
    global _CACHE
    now = time.time()
    with _LOCK:
        if not force and _CACHE and now - float(_CACHE.get("checked_at", 0)) < _CACHE_TTL_SEC:
            return dict(_CACHE)
    try:
        from system_readiness_report import build_system_readiness_report
        report = build_system_readiness_report(write=False)
        blocks = list(report.get("blocks") or [])
        warnings = list(report.get("warnings") or [])
        allowed = bool(report.get("ready_for_scaled_live")) and not blocks and not warnings
        result = {
            "allowed": allowed,
            "reason": "evidence_gates_passed" if allowed else (blocks[0] if blocks else warnings[0] if warnings else "readiness_unknown"),
            "blocks": blocks,
            "warnings": warnings,
            "checked_at": now,
        }
    except Exception as exc:
        result = {
            "allowed": False,
            "reason": "readiness_evaluation_error",
            "blocks": ["readiness_evaluation_error"],
            "warnings": [str(exc)[:200]],
            "checked_at": now,
        }
    with _LOCK:
        _CACHE = dict(result)
    return result
