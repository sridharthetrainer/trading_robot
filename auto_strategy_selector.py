"""
auto_strategy_selector.py

Selects best strategy dynamically using:
- historical backtest performance (strategy_state.json)
- RL bias (rl_state.json)
- regime awareness
- fallback safety

Fixes applied
-------------
State files loaded only once in __init__, never refreshed.

SelfLearningEngine writes updated strategy_state.json after every
learning cycle (typically every 20 new trades or every 7 days).
AutoStrategySelector was instantiated once at system startup and
kept its in-memory snapshot forever — so the strategy with the best
scores at startup was always selected, even after the learning engine
discovered a better one.

Fix: reload both files at the start of run() with a configurable
staleness threshold (default 60 seconds).  If the files have not
been modified since the last load, the cached versions are reused
to avoid unnecessary I/O on every 30-second cycle.

A refresh_state() method is also exposed for callers who want to
force an immediate reload (e.g. right after a learning cycle).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AutoStrategySelector:
    """
    Selects best strategy dynamically.
    """

    def __init__(
        self,
        strategy_state_file: str   = "strategy_state.json",
        rl_state_file:       str   = "rl_state.json",
        reload_interval_sec: float = 60.0,
    ) -> None:
        self.strategy_state_file = strategy_state_file
        self.rl_state_file       = rl_state_file
        self.reload_interval_sec = float(reload_interval_sec)

        self.strategy_state: Dict[str, Any] = {}
        self.rl_state:       Dict[str, Any] = {}

        # Track last file modification time so we only reload when files change
        self._strategy_mtime: float = 0.0
        self._rl_mtime:       float = 0.0
        self._last_load_ts:   float = 0.0

        # Initial load
        self._load_all()

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------
    def _load_json(self, file_path: str) -> Dict[str, Any]:
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            logger.exception("Failed loading %s", file_path)
        return {}

    def _file_mtime(self, path: str) -> float:
        try:
            return os.path.getmtime(path)
        except Exception:
            return 0.0

    def _load_all(self) -> None:
        self.strategy_state     = self._load_json(self.strategy_state_file)
        self.rl_state           = self._load_json(self.rl_state_file)
        self._strategy_mtime    = self._file_mtime(self.strategy_state_file)
        self._rl_mtime          = self._file_mtime(self.rl_state_file)
        self._last_load_ts      = time.time()
        logger.debug("Strategy/RL state reloaded from disk")

    def _maybe_reload(self) -> None:
        """
        Reload state files if:
        - reload_interval_sec has elapsed since last load, OR
        - either file's mtime has changed (learning cycle wrote new data)
        Avoids redundant I/O on every cycle when files haven't changed.
        """
        now = time.time()
        if (now - self._last_load_ts) < self.reload_interval_sec:
            return

        new_strat_mtime = self._file_mtime(self.strategy_state_file)
        new_rl_mtime    = self._file_mtime(self.rl_state_file)

        if new_strat_mtime != self._strategy_mtime or new_rl_mtime != self._rl_mtime:
            self._load_all()

        self._last_load_ts = now   # reset timer even if no reload

    def refresh_state(self) -> None:
        """Force an immediate reload of both state files."""
        self._load_all()

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _score_strategy(self, name: str, data: Dict[str, Any]) -> float:
        sharpe   = float(data.get("sharpe",       0))
        win_rate = float(data.get("win_rate",      0))
        drawdown = float(data.get("max_drawdown",  0))

        score = (sharpe * 2.0) + (win_rate * 1.5) - (drawdown * 1.2)

        rl       = self.rl_state.get(name.upper(), {})
        rl_score = float(rl.get("score", 0))
        score   += rl_score * 0.5

        return score

    # ------------------------------------------------------------------
    # Regime filter
    # ------------------------------------------------------------------
    def _filter_by_regime(
        self, strategies: Dict[str, Any], regime: str
    ) -> Dict[str, Any]:
        regime   = (regime or "").upper()
        filtered = {}

        for name, data in strategies.items():
            name_u = name.upper()
            if regime in ("TREND", "BULLISH_TREND", "BEARISH_TREND", "BREAKOUT"):
                if "TREND" in name_u or "BREAKOUT" in name_u:
                    filtered[name] = data
            elif regime in ("SIDEWAYS", "RANGE"):
                if "MEAN" in name_u or "REVERSAL" in name_u:
                    filtered[name] = data

        return filtered if filtered else strategies

    # ------------------------------------------------------------------
    # Main selector
    # ------------------------------------------------------------------
    def run(self, regime: Optional[str] = None) -> Dict[str, Any]:
        # Reload from disk if files have been updated since last run
        self._maybe_reload()

        try:
            strategies = self.strategy_state.get("strategies", {})
            if not strategies:
                return self._fallback("no_strategy_data")

            strategies = self._filter_by_regime(strategies, regime)

            scored: List[Dict[str, Any]] = []
            for name, data in strategies.items():
                scored.append({
                    "name":  name,
                    "score": self._score_strategy(name, data),
                    "data":  data,
                })

            scored.sort(key=lambda x: x["score"], reverse=True)
            best = scored[0]

            logger.info(
                "Selected Strategy: %s | Score: %.2f", best["name"], best["score"]
            )

            return {
                "selected_strategy": best["name"],
                "score":             best["score"],
                "reason":            "scored_selection",
                "ranked":            scored[:5],
            }

        except Exception:
            logger.exception("Strategy selection failed")
            return self._fallback("exception")

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------
    def _fallback(self, reason: str) -> Dict[str, Any]:
        logger.warning("Strategy fallback triggered: %s", reason)
        return {
            "selected_strategy": "trend",
            "score":             0,
            "reason":            f"fallback_{reason}",
            "ranked":            [],
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    selector = AutoStrategySelector()
    import json as _json
    print(_json.dumps(selector.run(regime="TREND"), indent=2, default=str))
