"""
self_learning.py

SelfLearningController — orchestrates the learning cycle and backup
management for the autonomous trading system.

Fixes applied
-------------
1. Strategy selector ran twice per learning cycle
   Original `run_learning_cycle()`:
       learning_result = self.learning_engine.run()   # runs selector internally
       selector_result = self.selector.run()          # runs selector AGAIN
   
   `SelfLearningEngine.run()` already calls `self.selector.run()` at the
   end and returns the result as `learning_result["selector_result"]`.
   Calling `self.selector.run()` again in the controller immediately
   after ran all backtest subprocesses a second time — doubling the
   time cost of every learning cycle (up to 2× the ~5-minute parallel
   backtest run).
   
   Fix: extract `selector_result` from `learning_result["selector_result"]`
   instead of running the selector a second time.

2. strategy_state.json was written twice with different formats
   After the engine wrote its own state payload, the controller
   overwrote it with a differently-structured dict. Both were valid
   but the controller's write silently discarded the engine's richer
   training metadata.
   
   Fix: write the state payload once, using the engine's selector
   result, preserving all metadata from the learning engine.
"""

from __future__ import annotations

import glob
import json
import logging
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional

from auto_strategy_selector import AutoStrategySelector
from self_learning_engine import SelfLearningEngine

logger = logging.getLogger(__name__)

DEFAULT_STRATEGY_STATE_FILE = "strategy_state.json"
DEFAULT_BEST_PARAMS_FILE    = "best_params.json"
DEFAULT_BACKUP_DIR          = "backup"
DEFAULT_LEARNING_STATE_FILE = "learning_state.json"
DEFAULT_MODEL_FILE          = "ai_model.pkl"
DEFAULT_RL_STATE_FILE       = "rl_state.json"


@dataclass
class LearningRunResult:
    status:               str
    timestamp:            float
    restored_from_backup: bool
    backup_file:          Optional[str]
    selected_strategy:    Optional[str]
    model_ready:          bool
    rl_ready:             bool
    notes:                str
    payload:              Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SelfLearningController:
    """
    Orchestrates the learning cycle and backup management.

    Accepts legacy kwargs (history_file / metric / min_trades /
    timeout_sec) from older main_autonomous.py calls without breaking.
    """

    def __init__(
        self,
        strategy_state_file:  str          = DEFAULT_STRATEGY_STATE_FILE,
        best_params_file:     str          = DEFAULT_BEST_PARAMS_FILE,
        backup_dir:           str          = DEFAULT_BACKUP_DIR,
        learning_state_file:  str          = DEFAULT_LEARNING_STATE_FILE,
        model_file:           str          = DEFAULT_MODEL_FILE,
        rl_state_file:        str          = DEFAULT_RL_STATE_FILE,
        max_backup_age_hours: int          = 168,
        # Legacy compat kwargs — accepted but not used
        history_file:         Optional[str] = None,
        metric:               Optional[str] = None,
        min_trades:           Optional[int] = None,
        timeout_sec:          Optional[int] = None,
        **kwargs,
    ) -> None:
        self.strategy_state_file  = Path(strategy_state_file)
        self.best_params_file     = Path(best_params_file)
        self.backup_dir           = Path(backup_dir)
        self.learning_state_file  = Path(learning_state_file)
        self.model_file           = Path(model_file)
        self.rl_state_file        = Path(rl_state_file)
        self.max_backup_age_hours = int(max_backup_age_hours)

        # Legacy compat — intentionally unused
        self.history_file  = history_file
        self.metric        = metric
        self.min_trades    = min_trades
        self.timeout_sec   = timeout_sec
        self.extra_kwargs  = kwargs

        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # AutoStrategySelector is used only by the engine now.
        # We keep a reference here for callers that call self.selector.run()
        # directly (e.g. from outside the controller).
        self.selector = AutoStrategySelector(
            strategy_state_file=str(self.strategy_state_file),
            rl_state_file=str(self.rl_state_file),
        )

        self.learning_engine = SelfLearningEngine(
            strategy_state_file=str(self.strategy_state_file),
            model_file=str(self.model_file),
            rl_state_file=str(self.rl_state_file),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def bootstrap_or_learn(self, force_relearn: bool = False) -> Dict[str, Any]:
        """
        Return usable state as quickly as possible.

        Priority:
        1. Existing valid strategy_state.json (within max_backup_age_hours)
        2. Most recent backup snapshot
        3. Full learning cycle (slow — runs all backtests)
        """
        notes: list[str] = []

        if not force_relearn:
            if self._has_valid_live_state():
                logger.info("Using existing live strategy state")
                notes.append("used_existing_strategy_state")
                payload = self._current_payload()
                return LearningRunResult(
                    status               = "success",
                    timestamp            = time.time(),
                    restored_from_backup = False,
                    backup_file          = None,
                    selected_strategy    = payload.get("selected_strategy"),
                    model_ready          = self.model_file.exists(),
                    rl_ready             = self.rl_state_file.exists(),
                    notes                = " | ".join(notes),
                    payload              = payload,
                ).to_dict()

            restored, restored_backup_file = self.restore_latest_backup()
            if restored:
                logger.info("Restored from backup: %s", restored_backup_file)
                notes.append("restored_from_backup")
                payload = self._current_payload()
                self._write_learning_state({
                    "last_bootstrap_ts":    time.time(),
                    "restored_from_backup": True,
                    "backup_file":          restored_backup_file,
                    "selected_strategy":    payload.get("selected_strategy"),
                })
                return LearningRunResult(
                    status               = "success",
                    timestamp            = time.time(),
                    restored_from_backup = True,
                    backup_file          = restored_backup_file,
                    selected_strategy    = payload.get("selected_strategy"),
                    model_ready          = self.model_file.exists(),
                    rl_ready             = self.rl_state_file.exists(),
                    notes                = " | ".join(notes),
                    payload              = payload,
                ).to_dict()

        logger.info(
            "No valid state/backup found or relearn forced. Running learning."
        )
        result = self.run_learning_cycle(
            reason="bootstrap" if not force_relearn else "forced"
        )
        result["restored_from_backup"] = False
        result["backup_file"] = None
        return result

    def run_learning_cycle(self, reason: str = "scheduled") -> Dict[str, Any]:
        """
        Run a full learning cycle: ML training + RL update + strategy selection.

        The strategy selector is run ONCE inside learning_engine.run().
        We extract the selector_result from the engine's return value
        instead of running the selector a second time (which would double
        the subprocess execution time).
        """
        logger.info("Starting learning cycle | reason=%s", reason)

        # Single call — engine handles training, RL, and strategy selection
        learning_result = self.learning_engine.run()

        # Extract selector result from engine output (do NOT call self.selector.run() again)
        selector_result   = learning_result.get("selector_result") or {}
        selected_strategy = (
            selector_result.get("selected_strategy")
            or learning_result.get("best_strategy")
        )

        # Write strategy_state.json once, preserving all engine metadata
        state_payload = {
            "timestamp":        time.time(),
            "selected_strategy": selected_strategy,
            "selector_result":  selector_result,
            "learning_result":  {
                k: v for k, v in learning_result.items()
                if k != "rl_state"   # rl_state can be large; stored separately
            },
        }
        self._write_json(self.strategy_state_file, state_payload)

        # Run walk-forward validation if module available
        wf_results = {}
        try:
            from walk_forward_backtest import run_walk_forward_all
            logger.info("Running walk-forward validation...")
            from walk_forward_backtest import _get_angel_data_fetcher
            _wf_fetcher = (
                self.learning_engine.data_fetcher
                if getattr(getattr(self.learning_engine, "data_fetcher", None), "angel", None)
                else _get_angel_data_fetcher()
            )
            wf_results = run_walk_forward_all(data_fetcher=_wf_fetcher)
            # Inject WF consistency into strategy_state for selector use
            if wf_results:
                payload = self._load_json(self.strategy_state_file) or {}
                strategies = payload.get("strategies", {})
                for strat_name, wf_r in wf_results.items():
                    if strat_name in strategies:
                        strategies[strat_name]["wf_consistency"] = wf_r.get("consistency_score", 0)
                        strategies[strat_name]["wf_pct_profitable"] = wf_r.get("pct_profitable", 0)
                        strategies[strat_name]["wf_avg_sharpe"] = wf_r.get("avg_sharpe", 0)
                payload["strategies"] = strategies
                payload["walk_forward"] = wf_results
                self._write_json(self.strategy_state_file, payload)
                logger.info("Walk-forward results merged into strategy_state.json")
        except Exception as exc:
            logger.info("Walk-forward skipped: %s", exc)

        backup_file = self.create_backup_snapshot(extra_payload={
            "reason":          reason,
            "learning_result": learning_result,
            "selector_result": selector_result,
            "walk_forward":    wf_results,
        })

        self._write_learning_state({
            "last_learning_ts":  time.time(),
            "reason":            reason,
            "backup_file":       backup_file,
            "selected_strategy": selected_strategy,
            "model_ready":       self.model_file.exists(),
            "rl_ready":          self.rl_state_file.exists(),
        })

        return LearningRunResult(
            status               = "success" if learning_result.get("status") != "failed" else "failed",
            timestamp            = time.time(),
            restored_from_backup = False,
            backup_file          = backup_file,
            selected_strategy    = selected_strategy,
            model_ready          = self.model_file.exists(),
            rl_ready             = self.rl_state_file.exists(),
            notes                = f"learning_cycle:{reason}",
            payload={
                "learning_result": learning_result,
                "selector_result": selector_result,
            },
        ).to_dict()

    def restore_latest_backup(self) -> tuple[bool, Optional[str]]:
        latest = self._get_latest_backup_file()
        if latest is None:
            return False, None
        try:
            snapshot = self._load_json(latest)
            if not snapshot:
                return False, None
            self._restore_snapshot_files(snapshot)
            return True, str(latest)
        except Exception:
            logger.exception("Backup restore failed")
            return False, None

    def create_backup_snapshot(
        self, extra_payload: Optional[Dict[str, Any]] = None
    ) -> str:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_path = self.backup_dir / f"strategy_snapshot_{timestamp}.json"

        payload = {
            "snapshot_version": 2,
            "created_at":       time.time(),
            "strategy_state":   self._load_json(self.strategy_state_file),
            "best_params":      self._load_json(self.best_params_file),
            "learning_state":   self._load_json(self.learning_state_file),
            "rl_state":         self._load_json(self.rl_state_file),
            "model_file":       str(self.model_file) if self.model_file.exists() else None,
            "selected_strategy": self._safe_selected_strategy(),
            "extra_payload":    extra_payload or {},
        }

        file_path.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

        for src, dst_name in [
            (self.strategy_state_file, "strategy_state.latest.json"),
            (self.best_params_file,    "best_params.latest.json"),
            (self.learning_state_file, "learning_state.latest.json"),
            (self.rl_state_file,       "rl_state.latest.json"),
        ]:
            self._copy_if_exists(src, self.backup_dir / dst_name)

        self._copy_if_exists(self.model_file, self.backup_dir / "ai_model.latest.pkl")

        logger.info("Backup snapshot created: %s", file_path)
        return str(file_path)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def _has_valid_live_state(self) -> bool:
        payload = self._load_json(self.strategy_state_file)
        if not payload or not payload.get("selected_strategy"):
            return False
        ts = payload.get("timestamp")
        if ts is None:
            return True
        try:
            age_hours = (time.time() - float(ts)) / 3600.0
            if age_hours > self.max_backup_age_hours:
                logger.info(
                    "strategy_state.json is %.1f hours old (limit %d h) — treating as stale",
                    age_hours, self.max_backup_age_hours,
                )
                return False
            return True
        except Exception:
            return True

    def _current_payload(self) -> Dict[str, Any]:
        return self._load_json(self.strategy_state_file) or {}

    def _safe_selected_strategy(self) -> Optional[str]:
        return self._current_payload().get("selected_strategy")

    def _write_learning_state(self, payload: Dict[str, Any]) -> None:
        current = self._load_json(self.learning_state_file) or {}
        current.update(payload)
        self.learning_state_file.write_text(
            json.dumps(current, indent=2, default=str), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Backup helpers
    # ------------------------------------------------------------------
    def _get_latest_backup_file(self) -> Optional[Path]:
        files = sorted(glob.glob(str(self.backup_dir / "strategy_snapshot_*.json")))
        if not files:
            return None
        latest = Path(files[-1])
        try:
            data       = self._load_json(latest)
            created_at = float(data.get("created_at", latest.stat().st_mtime))
            age_hours  = (time.time() - created_at) / 3600.0
            if age_hours > self.max_backup_age_hours:
                logger.warning(
                    "Latest backup is %.1f hours old (limit %d h) — still usable",
                    age_hours, self.max_backup_age_hours,
                )
        except Exception:
            pass
        return latest

    def _restore_snapshot_files(self, snapshot: Dict[str, Any]) -> None:
        for key, path in [
            ("strategy_state", self.strategy_state_file),
            ("best_params",    self.best_params_file),
            ("learning_state", self.learning_state_file),
            ("rl_state",       self.rl_state_file),
        ]:
            if snapshot.get(key) is not None:
                self._write_json(path, snapshot[key])

        latest_model = self.backup_dir / "ai_model.latest.pkl"
        if latest_model.exists() and not self.model_file.exists():
            shutil.copy2(latest_model, self.model_file)

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------
    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load JSON: %s", path)
            return {}

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _copy_if_exists(self, src: Path, dst: Path) -> None:
        try:
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        except Exception:
            logger.exception("Failed to copy %s → %s", src, dst)


# ---------------------------------------------------------------------------
# Module-level convenience functions (used by main_autonomous.py)
# ---------------------------------------------------------------------------

def bootstrap_learning_system(
    force_relearn: bool = False,
    strategy_state_file: str = DEFAULT_STRATEGY_STATE_FILE,
) -> Dict[str, Any]:
    controller = SelfLearningController(strategy_state_file=strategy_state_file)
    return controller.bootstrap_or_learn(force_relearn=force_relearn)


def run_after_hours_learning(
    reason: str = "scheduled",
    strategy_state_file: str = DEFAULT_STRATEGY_STATE_FILE,
) -> Dict[str, Any]:
    controller = SelfLearningController(strategy_state_file=strategy_state_file)
    return controller.run_learning_cycle(reason=reason)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    controller = SelfLearningController()
    result     = controller.bootstrap_or_learn(force_relearn=False)

    print("\n" + "=" * 70)
    print("SELF LEARNING CONTROLLER RESULT")
    print("=" * 70)
    print(f"Status              : {result.get('status')}")
    print(f"Restored Backup     : {result.get('restored_from_backup')}")
    print(f"Backup File         : {result.get('backup_file')}")
    print(f"Selected Strategy   : {result.get('selected_strategy')}")
    print(f"Model Ready         : {result.get('model_ready')}")
    print(f"RL Ready            : {result.get('rl_ready')}")
    print(f"Notes               : {result.get('notes')}")
    print("=" * 70)


if __name__ == "__main__":
    main()
