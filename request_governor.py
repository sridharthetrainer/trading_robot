"""Request pacing shared across ALL processes using the same credentials.

Angel enforces some rate limits (e.g. historical candles) account-wide, not
per-process. A prior purely in-memory implementation only paced calls within
one Python process; when multiple services share the same broker login
(main_autonomous.py's scan loop + manual_trade_tracker.py's 30s poll loop,
here), each paced itself independently against its own private state and the
two combined still exceeded the account-wide limit (sustained AB1021 "Too
many requests" for hours, found 2026-08-05). State is now persisted to a
per-provider lockfile so every process observes the same last-call time.
"""
from __future__ import annotations

import fcntl
import threading
import time
from pathlib import Path
from typing import Dict

_LOCKS: Dict[str, threading.Lock] = {}
_MASTER = threading.Lock()
_STATE_DIR = Path(__file__).resolve().parent / ".request_governor_state"


def acquire(provider: str, min_interval_sec: float) -> float:
    """Block until the provider-wide interval is available; return wait time.

    Serializes within this process (threading.Lock, avoids redundant file
    I/O for the common single-threaded case) AND across processes (flock on
    a per-provider state file).
    """
    name = str(provider or "default").lower()
    with _MASTER:
        thread_lock = _LOCKS.setdefault(name, threading.Lock())
    with thread_lock:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = _STATE_DIR / f"{name}.last"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                raw = handle.read().strip()
                last = float(raw) if raw else 0.0
                wait = max(0.0, float(min_interval_sec) - (time.time() - last))
                if wait:
                    time.sleep(wait)
                handle.seek(0)
                handle.truncate()
                handle.write(repr(time.time()))
                handle.flush()
                return wait
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
