"""Process-wide provider request pacing shared by all client instances."""
from __future__ import annotations

import threading
import time
from typing import Dict

_LOCKS: Dict[str, threading.Lock] = {}
_LAST: Dict[str, float] = {}
_MASTER = threading.Lock()


def acquire(provider: str, min_interval_sec: float) -> float:
    """Block until the provider-wide interval is available; return wait time."""
    name = str(provider or "default").lower()
    with _MASTER:
        lock = _LOCKS.setdefault(name, threading.Lock())
    with lock:
        now = time.monotonic()
        wait = max(0.0, float(min_interval_sec) - (now - _LAST.get(name, 0.0)))
        if wait:
            time.sleep(wait)
        _LAST[name] = time.monotonic()
        return wait
