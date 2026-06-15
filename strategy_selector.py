"""
strategy_selector.py

Runs available backtest scripts, extracts metrics, ranks strategies,
and writes strategy_state.json for the autonomous system.

Fixes applied
-------------
- Removed duplicate StrategySelector class (broken stub at bottom of original)
- Backtests run in parallel (ThreadPoolExecutor) - no longer blocks for 90 min
- Sharpe extraction handles both raw decimal and percent win-rate formats
- score_strategy normalises win_rate to 0-1 regardless of source format
- Scripts checked for existence before attempting subprocess run
- Timeout configurable per strategy (short scripts don't wait for slow ones)
- select_best() never raises - always returns a valid fallback
- strategy_state.json format is fully compatible with SelfLearningController
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Walk-forward validation (optional — skipped if module missing)
_WF_AVAILABLE = False
try:
    from walk_forward_backtest import run_walk_forward_all
    _WF_AVAILABLE = True
except ImportError:
    pass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy registry
# All scripts that exist in the project. Missing scripts are skipped at
# runtime - no error is raised.
# ---------------------------------------------------------------------------
STRATEGY_REGISTRY: List[Dict[str, Any]] = [
    {"name": "trend",          "script": "backtest_trend.py",          "timeout": 300},
    {"name": "mean_reversion", "script": "backtest_mr_enhanced.py",    "timeout": 300},
    {"name": "breakout",       "script": "backtest_breakout.py",       "timeout": 300},
    {"name": "ma_cross",       "script": "backtest_ma_cross.py",       "timeout": 300},
    {"name": "scalping",       "script": "backtest_scalping.py",       "timeout": 300},
    {"name": "ema_5min",       "script": "backtest_5min_ema.py",       "timeout": 300},
    {"name": "cpr",            "script": "backtest_cpr.py",            "timeout": 300},
    {"name": "orb",            "script": "backtest_orb.py",            "timeout": 240},
    {"name": "vwap_reversion", "script": "backtest_vwap_reversion.py", "timeout": 240},
    {"name": "supertrend_mtf", "script": "backtest_supertrend_mtf.py", "timeout": 300},
]

# Fallback strategy used when no backtest succeeds
FALLBACK_STRATEGY = "trend"


class StrategySelector:
    """
    Runs available backtest scripts, extracts metrics, ranks strategies,
    and writes a strategy_state.json compatible with the autonomous system.

    Usage
    -----
    selector = StrategySelector()
    state = selector.select_best()          # returns strategy_state dict
    selector.run()                          # alias used by SelfLearningEngine
    """

    def __init__(
        self,
        strategy_state_file: str = "strategy_state.json",
        results_file: str = "strategy_results.json",
        python_executable: str = "python",
        max_workers: int = 3,
    ) -> None:
        self.strategy_state_file = strategy_state_file
        self.results_file = results_file
        self.python_executable = python_executable
        self.max_workers = max_workers

        # Only include strategies whose script files actually exist on disk
        self.strategies: List[Dict[str, Any]] = [
            s for s in STRATEGY_REGISTRY if Path(s["script"]).exists()
        ]

        if not self.strategies:
            logger.warning(
                "No backtest scripts found on disk. "
                "Will use fallback strategy '%s'.",
                FALLBACK_STRATEGY,
            )

    # ------------------------------------------------------------------
    # Public aliases
    # ------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        """Alias for select_best() — used by SelfLearningEngine."""
        return self.select_best()

    # ------------------------------------------------------------------
    # Subprocess runner
    # ------------------------------------------------------------------
    def _run_one_backtest(self, strat: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run a single backtest script in a subprocess and return a result dict.
        Never raises — errors are captured in the result.
        """
        name = strat["name"]
        script = strat["script"]
        timeout = int(strat.get("timeout", 300))

        if not Path(script).exists():
            logger.warning("Backtest script not found, skipping: %s", script)
            return {
                "name": name,
                "script": script,
                "score": -999.0,
                "error": "script_not_found",
                "metrics": {},
            }

        try:
            result = subprocess.run(
                [self.python_executable, script],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")

            if result.returncode != 0:
                logger.warning(
                    "Backtest exited with code %d: %s", result.returncode, script
                )

            metrics = self.extract_metrics(output)
            score = self.score_strategy(metrics)

            logger.info(
                "Backtest complete | strategy=%s score=%.4f trades=%d sharpe=%.3f",
                name,
                score,
                metrics.get("total_trades", 0),
                metrics.get("sharpe", 0.0),
            )

            return {
                "name": name,
                "script": script,
                "score": score,
                "metrics": metrics,
            }

        except subprocess.TimeoutExpired:
            logger.warning("Backtest timed out (%ds): %s", timeout, script)
            return {
                "name": name,
                "script": script,
                "score": -999.0,
                "error": "timeout",
                "metrics": {},
            }
        except Exception as exc:
            logger.exception("Backtest subprocess error: %s | %s", script, exc)
            return {
                "name": name,
                "script": script,
                "score": -999.0,
                "error": str(exc),
                "metrics": {},
            }

    # ------------------------------------------------------------------
    # Parallel runner
    # ------------------------------------------------------------------
    def _run_all_backtests(self) -> List[Dict[str, Any]]:
        """
        Run all strategy backtests in parallel.
        Returns results in the order strategies were submitted.
        """
        if not self.strategies:
            return []

        results_map: Dict[str, Dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_name = {
                pool.submit(self._run_one_backtest, strat): strat["name"]
                for strat in self.strategies
            }

            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    results_map[name] = future.result()
                except Exception as exc:
                    logger.exception("Future failed for strategy %s: %s", name, exc)
                    results_map[name] = {
                        "name": name,
                        "script": "",
                        "score": -999.0,
                        "error": str(exc),
                        "metrics": {},
                    }

        # Preserve registry order
        return [
            results_map[s["name"]]
            for s in self.strategies
            if s["name"] in results_map
        ]

    def _load_validation_results(self) -> Dict[str, Dict[str, Any]]:
        """Load rigorous walk-forward/holdout verdicts when available."""
        path = Path("validation_results.json")
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            results = raw.get("results", raw)
            return results if isinstance(results, dict) else {}
        except Exception as exc:
            logger.warning("Could not read validation_results.json: %s", exc)
            return {}

    def _apply_validation_gate(self, ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Penalise strategies that recently failed walk-forward validation.

        This keeps the selector from chasing a lucky single-run backtest while
        still allowing a fallback if every strategy lacks a robust edge.
        """
        validations = self._load_validation_results()
        if not validations:
            return ranked

        aliases = {
            "mr_enhanced": "mean_reversion",
            "ma_grid": "ma_cross",
        }

        adjusted = []
        for result in ranked:
            name = result.get("name", "")
            v_name = aliases.get(name, name)
            validation = validations.get(v_name, {})
            if not validation:
                adjusted.append(result)
                continue

            result = dict(result)
            metrics = dict(result.get("metrics") or {})
            verdict = str(validation.get("verdict", "")).upper()
            metrics.update({
                "validation_verdict": verdict,
                "wf_pct_profitable": validation.get("dev_pct_profitable", 0.0),
                "wf_avg_sharpe": validation.get("dev_avg_sharpe", 0.0),
                "wf_avg_pnl": validation.get("dev_avg_pnl", 0.0),
            })
            result["metrics"] = metrics

            result["validation_verdict"] = verdict
            result["live_ready"] = verdict in ("PASS", "POSITIVE")

            if verdict == "FAIL":
                result["score"] = min(float(result.get("score", 0.0)), -100.0)
                result["validation_blocked"] = True
            elif verdict in ("PASS", "POSITIVE"):
                result["score"] = float(result.get("score", 0.0)) + 5.0
                result["validation_blocked"] = False
            elif verdict == "INSUFFICIENT_DATA":
                result["validation_blocked"] = True

            adjusted.append(result)

        return adjusted

    def _rank_from_validation_results(self) -> List[Dict[str, Any]]:
        """
        Build selector candidates directly from rigorous validation output.

        This is preferred over ad-hoc standalone backtest scripts when
        validation_results.json exists, because it already contains the
        walk-forward verdict used for live gating.
        """
        validations = self._load_validation_results()
        if not validations:
            return []

        scripts = {s["name"]: s["script"] for s in STRATEGY_REGISTRY}
        ranked: List[Dict[str, Any]] = []
        for name, validation in validations.items():
            if not isinstance(validation, dict):
                continue
            verdict = str(validation.get("verdict", "")).upper()
            sharpe = float(validation.get("dev_avg_sharpe", 0.0) or 0.0)
            pnl = float(validation.get("dev_avg_pnl", 0.0) or 0.0)
            trades = int(float(validation.get("dev_avg_trades", 0.0) or 0.0))
            win_rate = float(validation.get("dev_pct_profitable", 0.0) or 0.0)
            live_ready = verdict in ("PASS", "POSITIVE")

            metrics = {
                "sharpe": sharpe,
                "win_rate": win_rate,
                "net_profit": pnl,
                "max_drawdown": 0.0,
                "total_trades": trades,
                "validation_verdict": verdict,
                "wf_pct_profitable": win_rate,
                "wf_avg_sharpe": sharpe,
                "wf_avg_pnl": pnl,
            }
            score = self.score_strategy(metrics)
            if verdict == "FAIL":
                score = min(score, -100.0)
            elif verdict == "INSUFFICIENT_DATA":
                score = min(score, -50.0)
            elif live_ready:
                score += 5.0

            ranked.append({
                "name": name,
                "script": scripts.get(name, f"backtest_{name}.py"),
                "score": round(score, 4),
                "metrics": metrics,
                "validation_verdict": verdict,
                "validation_blocked": not live_ready,
                "live_ready": live_ready,
            })

        return ranked

    # ------------------------------------------------------------------
    # Metric extraction
    # ------------------------------------------------------------------
    def _extract_number(
        self, pattern: str, text: str, default: float = 0.0
    ) -> float:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            return default
        try:
            return float(m.group(1))
        except Exception:
            return default

    def extract_metrics(self, output: str) -> Dict[str, Any]:
        """
        Extract numeric metrics from backtest stdout.

        Handles two common win-rate formats:
          Win Rate : 0.62        (decimal, 0-1)
          Win Rate : 62.00%      (percent string)
        Both are normalised to 0-1 in the returned dict.
        """
        if not output:
            return {
                "sharpe": 0.0,
                "win_rate": 0.0,
                "net_profit": 0.0,
                "max_drawdown": 0.0,
                "total_trades": 0,
            }

        sharpe = self._extract_number(
            r"Sharpe(?:\s*Ratio)?\s*[:=]\s*(-?[\d.]+)", output, 0.0
        )

        # Handle "Win Rate : 62.00%" or "Win Rate : 0.62"
        win_raw = self._extract_number(
            r"Win\s*Rate\s*[:=]\s*(-?[\d.]+)", output, 0.0
        )
        # If the raw value is > 1 it was expressed as a percentage
        win_rate = win_raw / 100.0 if win_raw > 1.0 else win_raw

        net_profit = self._extract_number(
            r"(?:Net\s*Profit|Total\s*P&L|Total\s*PnL|PnL)\s*[:=₹\s]*(-?[\d,]+(?:\.\d+)?)",
            output,
            0.0,
        )
        # Strip thousands commas if present
        try:
            net_profit = float(str(net_profit).replace(",", ""))
        except Exception:
            net_profit = 0.0

        max_drawdown = self._extract_number(
            r"(?:Max\s*Drawdown|Drawdown)\s*[:=₹\s]*(-?[\d,]+(?:\.\d+)?)",
            output,
            0.0,
        )
        try:
            max_drawdown = float(str(max_drawdown).replace(",", ""))
        except Exception:
            max_drawdown = 0.0

        total_trades = int(
            self._extract_number(
                r"(?:Total\s*Trades?|Trades?)\s*[:=]\s*(\d+)", output, 0.0
            )
        )

        return {
            "sharpe": round(sharpe, 4),
            "win_rate": round(win_rate, 4),     # always 0-1
            "net_profit": round(net_profit, 2),
            "max_drawdown": round(abs(max_drawdown), 2),
            "total_trades": total_trades,
        }

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def score_strategy(self, metrics: Dict[str, Any]) -> float:
        """
        Composite score for ranking strategies.

        Components
        ----------
        sharpe       × 3.0   — primary quality signal
        win_rate     × 2.0   — win_rate is 0-1 here; max contribution = 2.0
        net_profit           — normalised by ₹1,00,000 starting capital
        max_drawdown         — penalised on same scale as profit
        trade_count bonus    — prefer strategies with a meaningful sample size
        """
        sharpe       = float(metrics.get("sharpe",       0.0) or 0.0)
        win_rate     = float(metrics.get("win_rate",     0.0) or 0.0)
        net_profit   = float(metrics.get("net_profit",   0.0) or 0.0)
        max_drawdown = abs(float(metrics.get("max_drawdown", 0.0) or 0.0))
        total_trades = int(metrics.get("total_trades",   0)   or 0)

        # Sanity-clamp win_rate to [0, 1] in case of bad extraction
        win_rate = max(0.0, min(1.0, win_rate))

        score = 0.0
        score += sharpe * 3.0
        score += win_rate * 2.0
        score += net_profit   / 100_000.0
        score -= max_drawdown / 100_000.0

        # Trade volume bonus — prefer strategies with enough history
        if total_trades >= 30:
            score += 1.5
        elif total_trades >= 20:
            score += 1.0
        elif total_trades >= 5:
            score += 0.5
        elif total_trades == 0:
            score -= 1.5

        return round(score, 4)

    # ------------------------------------------------------------------
    # Main selector
    # ------------------------------------------------------------------
    def select_best(self) -> Dict[str, Any]:
        """
        Run all available backtests (in parallel), rank by composite score,
        persist results + strategy_state.json, and return the state dict.

        Never raises — falls back to FALLBACK_STRATEGY on any error.
        """
        ranked = self._rank_from_validation_results()
        if not ranked:
            try:
                ranked = self._apply_validation_gate(self._run_all_backtests())
            except Exception as exc:
                logger.exception("_run_all_backtests failed: %s", exc)
                ranked = []

        # Sort valid results; keep failed entries at the bottom.  "Runnable" is
        # not the same as live-ready; failed validation candidates are retained
        # only for paper training so the system can continue collecting labels.
        valid = sorted(
            [r for r in ranked if r.get("score", -999.0) > -999.0],
            key=lambda x: x.get("score", -999.0),
            reverse=True,
        )
        invalid = [r for r in ranked if r.get("score", -999.0) <= -999.0]
        all_ranked = valid + invalid

        live_ready = [
            r for r in valid
            if bool(r.get("live_ready", False))
            and not bool(r.get("validation_blocked", False))
            and float(r.get("score", -999.0)) > 0
        ]

        if live_ready:
            selected = live_ready[0]
            selection_mode = "live_ready"
        elif valid:
            selected = valid[0]
            selection_mode = "paper_training_only"
            logger.info(
                "Best strategy: %s | mode=%s score=%.4f | sharpe=%.3f | win_rate=%.2f | trades=%d",
                selected["name"],
                selection_mode,
                selected.get("score", 0.0),
                selected.get("metrics", {}).get("sharpe", 0.0),
                selected.get("metrics", {}).get("win_rate", 0.0),
                selected.get("metrics", {}).get("total_trades", 0),
            )
        else:
            logger.warning(
                "No valid backtest results. Falling back to '%s'.", FALLBACK_STRATEGY
            )
            selection_mode = "fallback_no_valid_backtest"
            selected = {
                "name": FALLBACK_STRATEGY,
                "script": f"backtest_{FALLBACK_STRATEGY}.py",
                "score": 0.0,
                "metrics": {},
                "reason": "fallback_no_valid_backtest",
            }

        # ---------------------------------------------------------------
        # Persist raw results
        # ---------------------------------------------------------------
        try:
            with open(self.results_file, "w", encoding="utf-8") as f:
                json.dump(all_ranked, f, indent=2)
        except Exception as exc:
            logger.warning("Could not write results file %s: %s", self.results_file, exc)

        # ---------------------------------------------------------------
        # Persist strategy_state.json (format consumed by SelfLearningController
        # and LiveSignalEngine)
        # ---------------------------------------------------------------
        state_payload: Dict[str, Any] = {
            "timestamp": time.time(),
            "selected_strategy": selected["name"],
            "selected_script":   selected.get("script", ""),
            "selection_mode":     selection_mode,
            "live_ready":         selection_mode == "live_ready",
            "paper_training_only": selection_mode != "live_ready",
            "live_block_reason": (
                "" if selection_mode == "live_ready"
                else "no_strategy_passed_walk_forward_validation"
            ),
            "selector_result": {
                "selected_strategy": selected["name"],
                "score": selected.get("score", 0.0),
                "selection_mode": selection_mode,
                "live_ready": selection_mode == "live_ready",
                "ranked": [
                    {
                        "name":  r["name"],
                        "script": r.get("script", ""),
                        "score": r.get("score", 0.0),
                        "validation_verdict": r.get(
                            "validation_verdict",
                            (r.get("metrics") or {}).get("validation_verdict", ""),
                        ),
                        "live_ready": bool(r.get("live_ready", False)),
                    }
                    for r in valid[:10]
                ],
            },
            "strategies": {
                r["name"]: {
                    "script": r.get("script", ""),
                    "score":  r.get("score", 0.0),
                    "live_ready": bool(r.get("live_ready", False)),
                    "validation_blocked": bool(r.get("validation_blocked", False)),
                    "validation_verdict": r.get(
                        "validation_verdict",
                        (r.get("metrics") or {}).get("validation_verdict", ""),
                    ),
                    **(r.get("metrics") or {}),
                }
                for r in valid
            },
        }

        try:
            Path(self.strategy_state_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.strategy_state_file, "w", encoding="utf-8") as f:
                json.dump(state_payload, f, indent=2)
            logger.info(
                "strategy_state.json written | selected=%s file=%s",
                selected["name"],
                self.strategy_state_file,
            )
        except Exception as exc:
            logger.exception(
                "Failed to write strategy_state.json (%s): %s",
                self.strategy_state_file,
                exc,
            )

        return state_payload


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    selector = StrategySelector()
    result = selector.select_best()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
