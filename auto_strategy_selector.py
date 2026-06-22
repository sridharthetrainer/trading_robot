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
    def _is_live_ready(self, data: Dict[str, Any]) -> bool:
        verdict = str(data.get("validation_verdict", data.get("verdict", ""))).upper()
        if bool(data.get("live_ready", False)) and verdict not in ("FAIL", "INSUFFICIENT_DATA"):
            return True
        return verdict in ("PASS", "POSITIVE")

    def _score_strategy(self, name: str, data: Dict[str, Any]) -> float:
        validation_verdict = str(data.get("validation_verdict", "")).upper()
        if validation_verdict == "FAIL":
            wf_pct_prof = float(data.get("wf_pct_profitable", 0) or 0)
            wf_sharpe = float(data.get("wf_avg_sharpe", data.get("sharpe", 0)) or 0)
            wf_pnl = float(data.get("wf_avg_pnl", data.get("net_profit", 0)) or 0)
            trades = float(data.get("total_trades", data.get("num_trades", 0)) or 0)
            failed_score = -10.0 + wf_sharpe + (wf_pnl / 100_000.0) + wf_pct_prof
            if trades >= 30:
                failed_score += 0.25
            return round(min(-1.0, failed_score), 4)

        sharpe   = float(data.get("sharpe",       0))
        win_rate = float(data.get("win_rate",      0))
        drawdown = float(data.get("max_drawdown",  0))

        score = (sharpe * 2.0) + (win_rate * 1.5) - (drawdown * 1.2)

        rl       = self.rl_state.get(name.upper(), {})
        rl_score = float(rl.get("score", 0))
        score   += rl_score * 0.5

        # Walk-forward OOS adjustment — rewards validated edge, penalises noise
        wf_consistency = float(data.get("wf_consistency",    0))
        wf_pct_prof    = float(data.get("wf_pct_profitable", 0))
        wf_sharpe      = float(data.get("wf_avg_sharpe",     0))
        if validation_verdict in ("PASS", "POSITIVE"):
            score += 5.0
        if wf_consistency > 0:
            score += wf_consistency * 3.0           # OOS-validated: strong boost
        if wf_sharpe > 0:
            score += wf_sharpe * 1.0                # positive OOS Sharpe adds weight
        if 0 < wf_pct_prof < 0.4:                   # <40% OOS windows profitable
            score -= 2.0                            # penalise poor OOS performance

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
    def _extract_strategies(self) -> Dict[str, Any]:
        """Support both StrategySelector and SelfLearningController state shapes."""
        strategies = self.strategy_state.get("strategies", {})
        if isinstance(strategies, dict) and strategies:
            return strategies

        ranked = (
            self.strategy_state.get("selector_result", {}).get("ranked", [])
            if isinstance(self.strategy_state.get("selector_result"), dict)
            else []
        )
        extracted: Dict[str, Any] = {}
        if isinstance(ranked, list):
            for item in ranked:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                data = item.get("data") if isinstance(item.get("data"), dict) else item
                if name:
                    extracted[str(name)] = data
        return extracted

    def run(self, regime: Optional[str] = None) -> Dict[str, Any]:
        # Reload from disk if files have been updated since last run
        self._maybe_reload()

        try:
            strategies = self._extract_strategies()
            if not strategies:
                return self._fallback("no_strategy_data")

            strategies = self._filter_by_regime(strategies, regime)

            scored: List[Dict[str, Any]] = []
            for name, data in strategies.items():
                scored.append({
                    "name":  name,
                    "score": self._score_strategy(name, data),
                    "data":  data,
                    "live_ready": self._is_live_ready(data),
                    "validation_verdict": str(
                        data.get("validation_verdict", data.get("verdict", ""))
                    ).upper(),
                })

            scored.sort(key=lambda x: x["score"], reverse=True)
            live_ready = [
                item for item in scored
                if bool(item.get("live_ready")) and float(item.get("score", 0.0)) > 0
            ]
            if live_ready:
                best = live_ready[0]
                selection_mode = "live_ready"
                live_block_reason = ""
            else:
                best = scored[0]
                selection_mode = "paper_training_only"
                live_block_reason = "no_strategy_passed_walk_forward_validation"

            logger.info(
                "Selected Strategy: %s | mode=%s | Score: %.2f",
                best["name"], selection_mode, best["score"],
            )

            return {
                "selected_strategy": best["name"],
                "score":             best["score"],
                "reason":            "scored_selection",
                "selection_mode":    selection_mode,
                "live_ready":        selection_mode == "live_ready",
                "paper_training_only": selection_mode != "live_ready",
                "live_block_reason": live_block_reason,
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
        validation_pick = self._fallback_from_validation()
        if validation_pick:
            return {
                "selected_strategy": validation_pick["name"],
                "score": validation_pick["score"],
                "reason": f"fallback_{reason}_validation_pick",
                "selection_mode": validation_pick["selection_mode"],
                "live_ready": validation_pick["live_ready"],
                "paper_training_only": not validation_pick["live_ready"],
                "live_block_reason": validation_pick["live_block_reason"],
                "ranked": validation_pick["ranked"],
            }
        return {
            "selected_strategy": "trend",
            "score":             0,
            "reason":            f"fallback_{reason}",
            "selection_mode":     "fallback_no_data",
            "live_ready":         False,
            "paper_training_only": True,
            "live_block_reason":  "no_strategy_data",
            "ranked":            [],
        }

    def _fallback_from_validation(self) -> Optional[Dict[str, Any]]:
        """
        Pick the strongest recent validation candidate when trade history is empty.

        The system starts with zero closed trades, so the normal selector has no
        learning data.  Using validation prevents the hard-coded trend fallback
        from selecting a strategy that just failed walk-forward checks.
        """
        try:
            with open("validation_results.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return None

        results = raw.get("results", raw) if isinstance(raw, dict) else {}
        if not isinstance(results, dict):
            return None

        ranked: List[Dict[str, Any]] = []
        for name, data in results.items():
            if not isinstance(data, dict):
                continue
            verdict = str(data.get("verdict", "")).upper()
            sharpe = float(data.get("dev_avg_sharpe", 0.0) or 0.0)
            pnl = float(data.get("dev_avg_pnl", 0.0) or 0.0)
            trades = float(data.get("dev_avg_trades", 0.0) or 0.0)
            pct_profitable = float(data.get("dev_pct_profitable", 0.0) or 0.0)

            if verdict == "INSUFFICIENT_DATA" or trades <= 0:
                continue
            if pnl <= 0 and sharpe <= 0:
                continue

            score = sharpe + (pnl / 100_000.0) + pct_profitable
            if trades >= 30:
                score += 0.5
            if verdict == "FAIL":
                # Still allow a positive paper-training candidate, but keep
                # the score honest when robustness checks did not pass.
                score -= 0.5
            live_ready = verdict in ("PASS", "POSITIVE") and score > 0

            ranked.append({
                "name": name,
                "score": round(score, 4),
                "verdict": verdict,
                "live_ready": live_ready,
                "dev_avg_sharpe": sharpe,
                "dev_avg_pnl": pnl,
                "dev_avg_trades": trades,
            })

        if not ranked:
            return None

        ranked.sort(key=lambda item: item["score"], reverse=True)
        live_ready_ranked = [item for item in ranked if item.get("live_ready")]
        best = live_ready_ranked[0] if live_ready_ranked else ranked[0]
        selection_mode = "live_ready" if best.get("live_ready") else "paper_training_only"
        return {
            "name": best["name"],
            "score": best["score"],
            "selection_mode": selection_mode,
            "live_ready": selection_mode == "live_ready",
            "live_block_reason": (
                "" if selection_mode == "live_ready"
                else "no_strategy_passed_walk_forward_validation"
            ),
            "ranked": ranked[:5],
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    selector = AutoStrategySelector()
    import json as _json
    print(_json.dumps(selector.run(regime="TREND"), indent=2, default=str))
