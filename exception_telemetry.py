"""Low-overhead persistent visibility for degraded-but-running subsystems."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Dict

_LOCK = threading.Lock()
_PATH = Path("exception_telemetry.jsonl")


def record_exception(
    component: str,
    operation: str,
    exc: BaseException,
    *,
    severity: str = "warning",
    context: Dict[str, Any] | None = None,
) -> None:
    row = {
        "ts": time.time(),
        "component": str(component),
        "operation": str(operation),
        "severity": str(severity),
        "error_type": type(exc).__name__,
        "error": str(exc)[:500],
        "context": context or {},
    }
    try:
        with _LOCK:
            with _PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        logging.getLogger(__name__).exception("exception telemetry write failed")
