"""
tick_order_flow.py

Real-time tick-level order flow analysis from WebSocket price feed.
Accumulates tick directions (up/down price changes) to compute the
Order Imbalance Measure (OIM) over a rolling time window.

OIM = (up_ticks − down_ticks) / total_ticks
  +1.0  →  all up-ticks (strong buying pressure)
  −1.0  →  all down-ticks (strong selling pressure)

Integration:
  # In main_autonomous.py or live_signal_engine.py after WebSocket init:
  from tick_order_flow import get_global_instance as _tof
  ws_engine.register_tick_callback(_tof().on_tick)

Querying from signal_engine.py:
  from tick_order_flow import get_global_tick_bias
  bias = get_global_tick_bias("NIFTY")
  # → {"oim": 0.3, "pressure": "BULLISH", "score_mod": 0.045, ...}
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

WINDOW_SEC    = 120    # rolling 2-minute window
MIN_TICKS     = 10     # minimum ticks required for a valid signal
STRONG_OIM    = 0.40   # |OIM| above this threshold = strong directional pressure
MAX_SCORE_MOD = 0.15   # maximum score modifier magnitude


class TickOrderFlow:
    """
    Thread-safe accumulator of tick-level price direction signals.
    Register on_tick as a WebSocket callback; query get_bias() from signal_engine.
    """

    def __init__(self, window_sec: int = WINDOW_SEC) -> None:
        self._window  = window_sec
        self._prices: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._lock    = threading.Lock()

    def on_tick(self, symbol: str, ltp: float, tick: Optional[Dict[str, Any]] = None) -> None:
        """WebSocket tick callback. Must be fast, must not raise."""
        try:
            tick = tick or {}
            quantity = max(1.0, float(tick.get("last_traded_quantity", 1) or 1))
            with self._lock:
                self._prices[symbol].append((time.monotonic(), ltp, quantity))
        except Exception:
            pass

    def get_bias(self, symbol: str,
                 window_sec: Optional[int] = None) -> Dict[str, Any]:
        """
        Compute tick order imbalance for a symbol over the rolling window.
        Returns a dict with oim, pressure, score_mod, and raw tick counts.
        """
        win    = window_sec or self._window
        cutoff = time.monotonic() - win

        with self._lock:
            raw = [item for item in self._prices.get(symbol, []) if item[0] >= cutoff]

        if len(raw) < 2:
            return self._empty(symbol)

        up = dn = 0.0
        for i in range(1, len(raw)):
            delta = raw[i][1] - raw[i - 1][1]
            quantity = raw[i][2] if len(raw[i]) > 2 else 1.0
            if delta > 0:
                up += quantity
            elif delta < 0:
                dn += quantity

        total = up + dn
        directional_ticks = sum(
            1 for i in range(1, len(raw)) if raw[i][1] != raw[i - 1][1]
        )
        if directional_ticks < MIN_TICKS:
            return self._empty(symbol)

        oim = (up - dn) / total

        span = raw[-1][0] - raw[0][0]
        velocity = round(directional_ticks / max(span, 1e-9), 2)

        prices   = [item[1] for item in raw]
        momentum = round((prices[-1] - prices[0]) / max(prices[0], 1e-9) * 100, 4)

        pressure  = "BULLISH" if oim > STRONG_OIM else ("BEARISH" if oim < -STRONG_OIM else "NEUTRAL")
        score_mod = round(oim * MAX_SCORE_MOD, 4) if abs(oim) > 0.20 else 0.0

        return {
            "symbol":     symbol,
            "oim":        round(oim, 4),
            "up_ticks":   sum(1 for i in range(1, len(raw)) if raw[i][1] > raw[i - 1][1]),
            "dn_ticks":   sum(1 for i in range(1, len(raw)) if raw[i][1] < raw[i - 1][1]),
            "up_quantity": round(up, 2),
            "down_quantity": round(dn, 2),
            "total":      directional_ticks,
            "velocity":   velocity,
            "momentum":   momentum,
            "pressure":   pressure,
            "score_mod":  score_mod,
            "window_sec": win,
        }

    def all_symbols(self) -> Dict[str, Dict[str, Any]]:
        """Return tick bias for all currently tracked symbols."""
        with self._lock:
            symbols = list(self._prices.keys())
        return {s: self.get_bias(s) for s in symbols}

    def tracked_symbol_count(self) -> int:
        with self._lock:
            return len(self._prices)

    @staticmethod
    def _empty(symbol: str) -> Dict[str, Any]:
        return {
            "symbol":    symbol,
            "oim":       0.0,
            "up_ticks":  0,
            "dn_ticks":  0,
            "total":     0,
            "pressure":  "NEUTRAL",
            "score_mod": 0.0,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[TickOrderFlow] = None
_lock = threading.Lock()


def get_global_instance() -> TickOrderFlow:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = TickOrderFlow()
    return _instance


def get_global_tick_bias(symbol: str) -> Dict[str, Any]:
    """Convenience function for signal_engine.py. Thread-safe, never raises."""
    try:
        return get_global_instance().get_bias(symbol)
    except Exception:
        return TickOrderFlow._empty(symbol)
