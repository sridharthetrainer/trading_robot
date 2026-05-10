"""
quote_cache.py

Thread-safe quote cache with TTL and request-rate throttling.

Fixes applied
-------------
API signature mismatch with angel_broker.py

angel_broker.py calls:
    self.quote_cache.get(symbol, exchange)
    self.quote_cache.set(symbol, exchange, ltp)
    self.quote_cache.wait_for_slot(symbol, exchange)

QuoteCache only accepted a single `key` string:
    def get(self, key: str)
    def set(self, key, value)
    def wait_for_slot(self, key="default")

Result: every LTP lookup in production raised:
    TypeError: get() takes 2 positional arguments but 3 were given

Fix: the three methods now accept either:
    (key)                     — original single-key form
    (symbol, exchange)        — two-arg form used by angel_broker.py

When two args are given, the composite key "exchange:symbol" is
constructed internally, matching the existing pattern used elsewhere
in the codebase (e.g. _token_cache uses "NFO:NIFTY24MAR22000CE").

Backward-compatible: all existing callers using a single key string
are unaffected.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class CacheEntry:
    value: Any
    ts:    float


def _make_key(key_or_symbol: str, exchange: Optional[str] = None) -> str:
    """
    Build a cache key from either:
    - a single composite key string, or
    - (symbol, exchange) as used by angel_broker.py
    """
    if exchange is not None:
        return f"{exchange}:{key_or_symbol}"
    return key_or_symbol


class QuoteCache:
    """
    Thread-safe quote cache with:
    - TTL-based value caching
    - optional request throttling via min_request_gap_seconds

    Compatible with both call patterns:
        cache.get("NSE:NIFTY")            # single composite key
        cache.get("NIFTY", "NSE")         # symbol + exchange (angel_broker style)
    """

    def __init__(
        self,
        ttl_seconds:             float = 2.0,
        max_size:                int   = 2000,
        min_request_gap_seconds: float = 0.0,
    ) -> None:
        self.ttl_seconds             = float(ttl_seconds)
        self.max_size                = int(max_size)
        self.min_request_gap_seconds = float(min_request_gap_seconds)

        self._lock              = threading.Lock()
        self._data:             Dict[str, CacheEntry] = {}
        self._last_request_ts:  Dict[str, float]      = {}

    def _now(self) -> float:
        return time.time()

    def _is_expired(self, entry: CacheEntry) -> bool:
        return (self._now() - entry.ts) > self.ttl_seconds

    def _prune_if_needed(self) -> None:
        if len(self._data) <= self.max_size:
            return

        expired_keys = [k for k, v in self._data.items() if self._is_expired(v)]
        for k in expired_keys:
            self._data.pop(k, None)

        if len(self._data) <= self.max_size:
            return

        sorted_items = sorted(self._data.items(), key=lambda kv: kv[1].ts)
        overflow = len(self._data) - self.max_size
        for k, _ in sorted_items[:overflow]:
            self._data.pop(k, None)

    # ------------------------------------------------------------------
    # Core cache operations — accept (key) or (symbol, exchange)
    # ------------------------------------------------------------------

    def get(
        self,
        key_or_symbol: str,
        exchange: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Retrieve a cached value.

        Accepts:
            get("NSE:NIFTY")         → single composite key
            get("NIFTY", "NSE")      → symbol + exchange
        """
        key = _make_key(key_or_symbol, exchange)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if self._is_expired(entry):
                self._data.pop(key, None)
                return None
            return entry.value

    def get_with_age(
        self,
        key_or_symbol: str,
        exchange: Optional[str] = None,
    ) -> Tuple[Optional[Any], Optional[float]]:
        """Return (value, age_in_seconds) or (None, None) if missing/expired."""
        key = _make_key(key_or_symbol, exchange)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None, None
            age = self._now() - entry.ts
            if age > self.ttl_seconds:
                self._data.pop(key, None)
                return None, None
            return entry.value, age

    def set(
        self,
        key_or_symbol: str,
        value_or_exchange: Any,
        value: Any = None,
    ) -> None:
        """
        Store a value.

        Accepts:
            set("NSE:NIFTY", 22050.0)        → single composite key
            set("NIFTY", "NSE", 22050.0)     → symbol + exchange + value
        """
        if value is None:
            # Two-arg form: set(key, value)
            key   = key_or_symbol
            store = value_or_exchange
        else:
            # Three-arg form: set(symbol, exchange, value)
            key   = _make_key(key_or_symbol, str(value_or_exchange))
            store = value

        with self._lock:
            self._data[key] = CacheEntry(value=store, ts=self._now())
            self._prune_if_needed()

    def delete(self, key_or_symbol: str, exchange: Optional[str] = None) -> None:
        key = _make_key(key_or_symbol, exchange)
        with self._lock:
            self._data.pop(key, None)
            self._last_request_ts.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._last_request_ts.clear()

    def has(self, key_or_symbol: str, exchange: Optional[str] = None) -> bool:
        return self.get(key_or_symbol, exchange) is not None

    def cleanup(self) -> int:
        with self._lock:
            expired = [k for k, v in self._data.items() if self._is_expired(v)]
            for k in expired:
                self._data.pop(k, None)
            return len(expired)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            live = expired = 0
            now  = self._now()
            for entry in self._data.values():
                if (now - entry.ts) > self.ttl_seconds:
                    expired += 1
                else:
                    live += 1
            return {
                "ttl_seconds":             self.ttl_seconds,
                "max_size":                self.max_size,
                "min_request_gap_seconds": self.min_request_gap_seconds,
                "total_entries":           len(self._data),
                "live_entries":            live,
                "expired_entries":         expired,
            }

    # ------------------------------------------------------------------
    # Request throttling — accept (key) or (symbol, exchange)
    # ------------------------------------------------------------------

    def can_request(
        self,
        key_or_symbol: str = "default",
        exchange: Optional[str] = None,
    ) -> bool:
        key = _make_key(key_or_symbol, exchange)
        with self._lock:
            last_ts = self._last_request_ts.get(key)
            if last_ts is None:
                return True
            return (self._now() - last_ts) >= self.min_request_gap_seconds

    def mark_request(
        self,
        key_or_symbol: str = "default",
        exchange: Optional[str] = None,
    ) -> None:
        key = _make_key(key_or_symbol, exchange)
        with self._lock:
            self._last_request_ts[key] = self._now()

    def wait_for_slot(
        self,
        key_or_symbol: str = "default",
        exchange: Optional[str] = None,
    ) -> None:
        """
        Sleep only if needed to satisfy min_request_gap_seconds.

        Accepts:
            wait_for_slot("NSE:NIFTY")       → single composite key
            wait_for_slot("NIFTY", "NSE")    → symbol + exchange
        """
        if self.min_request_gap_seconds <= 0:
            return

        key = _make_key(key_or_symbol, exchange)

        while True:
            with self._lock:
                last_ts   = self._last_request_ts.get(key)
                now       = self._now()
                if last_ts is None:
                    self._last_request_ts[key] = now
                    return
                elapsed   = now - last_ts
                remaining = self.min_request_gap_seconds - elapsed
                if remaining <= 0:
                    self._last_request_ts[key] = now
                    return
            time.sleep(min(remaining, 0.05))

    def get_or_wait(
        self,
        cache_key:   str,
        request_key: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Return cached value if present; otherwise wait for throttle slot and return None.
        Uses single-key form only (original callers).
        """
        value = self.get(cache_key)
        if value is not None:
            return value
        self.wait_for_slot(request_key or cache_key)
        return None
