"""
param_bridge.py

Bridges backtest-optimised parameters into live signal generation.

This solves the #1 grey area in the system:
  Grid search produces best_params_trend.json etc. with optimal fast_ema,
  slow_ema, adx_threshold, stop_atr_mult, etc.
  But live_signal_engine calls generate_signal() with NO config argument,
  so every trade uses hardcoded defaults regardless of what backtest found.

Architecture
------------

1.  PER-STRATEGY GLOBAL PARAMS
    Loaded from best_params_trend.json, best_params_mr.json, etc.
    Applied to ALL symbols for that strategy.
    Refreshed every 30 minutes from disk.

2.  PER-SYMBOL PER-STRATEGY PARAMS  (optional, computed on-the-fly)
    For NIFTY, BANKNIFTY, FINNIFTY: run a quick per-symbol grid search
    nightly. Results saved in symbol_params/{symbol}_{strategy}.json.
    For NIFTY 200 stocks: use global strategy params (too many to
    individualise nightly).

3.  PARAM VALIDATION
    Before applying any params from backtest:
      - Require minimum 20 trades in the backtest run
      - Require Sharpe > 0.5 (basic positive expectancy)
      - Require win rate > 45%
    If params fail validation → fall back to safe defaults.

4.  STRATEGY-SYMBOL AFFINITY
    Tracks which strategies historically work best for each symbol.
    After 30+ live trades per symbol:
      - Trend works better on NIFTY (strong directional moves)
      - MR works better on BANKNIFTY (more mean-reverting)
      - ORB works best on NIFTY/BANKNIFTY in 9:30-10:30 window
    Stored in symbol_strategy_affinity.json, updated nightly.

Usage
-----
    from param_bridge import ParamBridge
    bridge = ParamBridge()

    # Get config for a specific symbol + strategy
    config = bridge.get_config("NIFTY", "trend")
    signal = generate_signal(df=df, df_htf=df_htf, symbol="NIFTY", config=config)

    # Get best strategy for a symbol
    best = bridge.get_best_strategy("BANKNIFTY")
    # → "breakout" or "mean_reversion" etc.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Safe defaults (used when backtest params fail validation) ─────────────────
SAFE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "trend": {
        "fast_ema": 9, "slow_ema": 21, "adx_threshold": 18,
        "stop_atr_mult": 2.0, "trail_atr_mult": 1.5,
    },
    "mean_reversion": {
        "rsi_period": 14, "bb_period": 20, "bb_std": 2.0,
        "oversold": 30, "overbought": 70,
    },
    "breakout": {
        "channel_period": 20, "adx_threshold": 20,
        "stop_atr_mult": 1.5,
    },
    "scalping": {
        "fast_ema": 9, "slow_ema": 20, "rsi_period": 7,
        "rsi_long_threshold": 55, "rsi_short_threshold": 45,
        "use_vwap_filter": True,
    },
    "ma_cross": {
        "fast_ema": 9, "slow_ema": 21, "adx_threshold": 15,
    },
    "ema_5min": {
        "fast_ema": 9, "slow_ema": 21,
        "stop_atr_mult": 1.5, "trail_atr_mult": 1.0,
    },
    "cpr": {
        "stop_atr_mult": 1.5, "trail_atr_mult": 1.0,
        "require_narrow": False,
    },
    "orb": {
        "adx_min": 18.0, "volume_min": 1.1,
        "stop_mult": 1.0, "target_mult": 1.5,
    },
    "vwap_reversion": {
        "dev_min": 0.0035, "rsi_os": 35, "rsi_ob": 65,
        "vol_min": 1.0,
    },
    "supertrend_mtf": {
        "st_period": 10, "st_mult": 3.0,
    },
}

# ── Param file mapping ────────────────────────────────────────────────────────
STRATEGY_PARAM_FILES: Dict[str, str] = {
    "trend":           "best_params_trend.json",
    "mean_reversion":  "best_params_mr.json",
    "breakout":        "best_params_breakout.json",
    "scalping":        "best_params_scalping.json",
    "ma_cross":        "best_params_ma.json",
    "ema_5min":        "best_params_ema_5min.json",
    "cpr":             "best_params_cpr.json",
    "orb":             "best_params_orb.json",
    "vwap_reversion":  "best_params_vwap_reversion.json",
    "supertrend_mtf":  "best_params_supertrend_mtf.json",
}

# ── Validation thresholds ─────────────────────────────────────────────────────
VALIDATION_MIN_TRADES  = 20
VALIDATION_MIN_SHARPE  = 0.50
VALIDATION_MIN_WINRATE = 0.45

# ── Tier-1 symbols that get per-symbol params ─────────────────────────────────
TIER1_SYMBOLS_FOR_PEROPT = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

REFRESH_INTERVAL_SEC = 1800   # reload from disk every 30 minutes


class ParamBridge:
    """
    Central bridge between backtest-optimised parameters and live signal
    generation. Provides get_config(symbol, strategy) → dict.
    """

    def __init__(
        self,
        params_dir:     str = ".",
        symbol_dir:     str = "symbol_params",
        affinity_file:  str = "symbol_strategy_affinity.json",
    ) -> None:
        self._params_dir    = Path(params_dir)
        self._symbol_dir    = Path(symbol_dir)
        self._affinity_file = Path(affinity_file)

        # Cache: {strategy: config_dict}
        self._global_cache: Dict[str, Dict[str, Any]] = {}
        # Cache: {strategy: config_dict}; failed validation but allowed for paper training
        self._paper_cache: Dict[str, Dict[str, Any]] = {}
        # Cache: {symbol_strategy_key: config_dict}
        self._symbol_cache: Dict[str, Dict[str, Any]] = {}
        # Cache: {symbol_strategy_key: config_dict}; paper-training only
        self._symbol_paper_cache: Dict[str, Dict[str, Any]] = {}
        # Strategy affinity: {symbol: {strategy: score}}
        self._affinity: Dict[str, Dict[str, float]] = {}

        self._last_refresh: float = 0.0
        self._load_all()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load all params from disk."""
        self._load_global_params()
        self._load_symbol_params()
        self._load_affinity()
        self._last_refresh = time.time()

    def _load_global_params(self) -> None:
        """Load per-strategy global params from best_params_*.json files."""
        self._global_cache.clear()
        self._paper_cache.clear()
        for strategy, filename in STRATEGY_PARAM_FILES.items():
            path = self._params_dir / filename
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text())
                params  = payload.get("params", {})
                metrics = payload.get("metrics", {})
                if self._validate_params(params, metrics, strategy):
                    self._global_cache[strategy] = params
                    logger.debug(
                        "ParamBridge loaded %s params: %s", strategy, params
                    )
                elif self._paper_params_allowed(params, metrics):
                    self._paper_cache[strategy] = params
                    logger.info(
                        "ParamBridge loaded %s params for PAPER training only",
                        strategy,
                    )
                else:
                    logger.warning(
                        "ParamBridge: %s params FAILED validation (metrics=%s) — using defaults",
                        strategy, metrics,
                    )
            except Exception as exc:
                logger.warning("ParamBridge: failed to load %s: %s", path, exc)

    def _load_symbol_params(self) -> None:
        """Load per-symbol params from symbol_params/ directory."""
        self._symbol_cache.clear()
        self._symbol_paper_cache.clear()
        if not self._symbol_dir.exists():
            return
        for path in self._symbol_dir.glob("*_*.json"):
            try:
                parts = path.stem.split("_", 1)
                if len(parts) != 2:
                    continue
                symbol, strategy = parts[0], parts[1]
                payload = json.loads(path.read_text())
                params  = payload.get("params", {})
                metrics = payload.get("metrics", {})
                if self._validate_params(params, metrics, strategy):
                    key = f"{symbol}_{strategy}"
                    self._symbol_cache[key] = params
                elif self._paper_params_allowed(params, metrics):
                    key = f"{symbol}_{strategy}"
                    self._symbol_paper_cache[key] = params
            except Exception:
                pass

    def _load_affinity(self) -> None:
        """Load symbol→strategy affinity scores."""
        if not self._affinity_file.exists():
            return
        try:
            self._affinity = json.loads(self._affinity_file.read_text())
        except Exception:
            pass

    def _maybe_refresh(self) -> None:
        """Refresh caches if stale."""
        if time.time() - self._last_refresh > REFRESH_INTERVAL_SEC:
            self._load_all()

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_params(
        self, params: Dict, metrics: Dict, strategy: str
    ) -> bool:
        """
        Reject params that:
        - Are empty
        - Come from a backtest with too few trades
        - Have Sharpe < threshold
        - Have win rate < threshold
        """
        if not params:
            return False
        num_trades = int(metrics.get("total_trades", metrics.get("num_trades", 0)))
        sharpe     = float(metrics.get("sharpe", 0.0) or 0.0)
        win_rate   = float(metrics.get("win_rate", 0.0) or 0.0)
        # Normalise win_rate if stored as 0-100
        if win_rate > 1.0:
            win_rate /= 100.0
        if num_trades < VALIDATION_MIN_TRADES:
            logger.debug("Params rejected: only %d trades < %d",
                         num_trades, VALIDATION_MIN_TRADES)
            return False
        if sharpe < VALIDATION_MIN_SHARPE:
            logger.debug("Params rejected: sharpe %.2f < %.2f", sharpe, VALIDATION_MIN_SHARPE)
            return False
        if win_rate < VALIDATION_MIN_WINRATE:
            logger.debug("Params rejected: win_rate %.2f < %.2f", win_rate, VALIDATION_MIN_WINRATE)
            return False
        return True

    def _paper_params_allowed(self, params: Dict, metrics: Dict) -> bool:
        """Allow explicitly marked failed params only for paper-training mode."""
        if not params:
            return False
        return bool(metrics.get("paper_training_only")) or str(
            metrics.get("validation_verdict", "")
        ).upper() == "FAIL"

    def _paper_training_enabled(self) -> bool:
        """Read current runtime mode without importing config at module import time."""
        try:
            import config as cfg  # local import keeps tests/lightweight use simple
            return bool(
                getattr(cfg, "PAPER_ORDERS_ONLY", False)
                or getattr(cfg, "PAPER_TRADING", False)
                or getattr(cfg, "PAPER_TRADE", False)
            )
        except Exception:
            return False

    # ── Main API ──────────────────────────────────────────────────────────────

    def get_config(self, symbol: str, strategy: str) -> Dict[str, Any]:
        """
        Return the best validated config dict for a symbol + strategy pair.

        Priority:
        1. Per-symbol validated params (NIFTY/BANKNIFTY tier-1 only)
        2. Global validated params
        3. Paper-training params, only while runtime is in paper mode
        4. Safe hardcoded defaults

        Always returns a non-empty dict — never raises.
        """
        self._maybe_refresh()
        strategy = strategy.lower().strip()

        # 1. Per-symbol params (only for tier-1)
        if symbol in TIER1_SYMBOLS_FOR_PEROPT:
            key = f"{symbol}_{strategy}"
            if key in self._symbol_cache:
                return self._symbol_cache[key].copy()

        # 2. Global strategy params
        if strategy in self._global_cache:
            return self._global_cache[strategy].copy()

        # 3. Paper-training params are never used for live mode.
        if self._paper_training_enabled():
            if symbol in TIER1_SYMBOLS_FOR_PEROPT:
                key = f"{symbol}_{strategy}"
                if key in self._symbol_paper_cache:
                    return self._symbol_paper_cache[key].copy()
            if strategy in self._paper_cache:
                return self._paper_cache[strategy].copy()

        # 4. Safe defaults
        return SAFE_DEFAULTS.get(strategy, {}).copy()

    def get_best_strategy(self, symbol: str) -> Optional[str]:
        """
        Return the historically best-performing strategy for a symbol.
        Based on affinity scores from live trade results.
        Returns None if no affinity data yet.
        """
        self._maybe_refresh()
        affinities = self._affinity.get(symbol, {})
        if not affinities:
            return None
        # GA-13: filter old entries, extract scores
        cutoff = time.time() - 90 * 86400
        scores = {}
        for k, v in affinities.items():
            if isinstance(v, dict):
                if v.get('updated_ts', 0) >= cutoff:
                    scores[k] = v.get('score', 0.0)
            else:
                scores[k] = float(v)
        return max(scores, key=scores.get) if scores else None

    def get_strategy_ranking(self, symbol: str) -> List[Tuple[str, float]]:
        """
        Return sorted [(strategy, affinity_score)] for a symbol.
        Highest score first.
        """
        self._maybe_refresh()
        affinities = self._affinity.get(symbol, {})
        # GA-13: filter out entries older than 90 days, extract score
        cutoff = time.time() - 90 * 86400
        cleaned = {}
        for k, v in affinities.items():
            if isinstance(v, dict):
                if v.get('updated_ts', 0) >= cutoff:
                    cleaned[k] = v.get('score', 0.0)
            else:
                cleaned[k] = float(v)  # legacy format
        return sorted(cleaned.items(), key=lambda x: -x[1])

    def update_affinity(
        self,
        symbol:   str,
        strategy: str,
        pnl:      float,
        won:      bool,
    ) -> None:
        """
        Update symbol→strategy affinity after a closed trade.
        Call this from trade_manager when a trade closes.

        Affinity score uses exponential moving average of risk-adjusted return.
        """
        key = symbol
        if key not in self._affinity:
            self._affinity[key] = {}
        strat_key = strategy.lower()
        current = float(self._affinity[key].get(strat_key, {}).get('score', 0.0)
                        if isinstance(self._affinity[key].get(strat_key), dict)
                        else self._affinity[key].get(strat_key, 0.0))
        reward = (pnl / 1000.0) * (1.5 if won else 0.5)
        new_score = round(0.90 * current + 0.10 * reward, 4)
        # GA-13: store with timestamp for TTL cleanup
        self._affinity[key][strat_key] = {
            'score': new_score,
            'updated_ts': time.time(),
            'trades': self._affinity[key].get(strat_key, {}).get('trades', 0) + 1
                      if isinstance(self._affinity[key].get(strat_key), dict) else 1,
        }
        self._save_affinity()

    def _save_affinity(self) -> None:
        try:
            self._affinity_file.write_text(
                json.dumps(self._affinity, indent=2)
            )
        except Exception:
            pass

    def save_symbol_params(
        self,
        symbol:   str,
        strategy: str,
        params:   Dict[str, Any],
        metrics:  Dict[str, Any],
    ) -> None:
        """
        Save per-symbol params after a nightly per-symbol backtest.
        Only saves if params pass validation.
        """
        if not self._validate_params(params, metrics, strategy):
            logger.info(
                "ParamBridge: symbol params for %s/%s failed validation — not saved",
                symbol, strategy,
            )
            return
        self._symbol_dir.mkdir(parents=True, exist_ok=True)
        path = self._symbol_dir / f"{symbol}_{strategy}.json"
        try:
            path.write_text(json.dumps({
                "symbol": symbol, "strategy": strategy,
                "params": params, "metrics": metrics,
            }, indent=2))
            key = f"{symbol}_{strategy}"
            self._symbol_cache[key] = params
            logger.info(
                "ParamBridge: saved symbol params for %s/%s (sharpe=%.2f)",
                symbol, strategy, metrics.get("sharpe", 0),
            )
        except Exception as exc:
            logger.warning("ParamBridge: save failed for %s/%s: %s", symbol, strategy, exc)

    def get_status_summary(self) -> Dict[str, Any]:
        """Return a summary dict for logging/diagnostics."""
        return {
            "global_strategies_loaded": list(self._global_cache.keys()),
            "symbol_overrides_loaded":  list(self._symbol_cache.keys()),
            "affinity_symbols":         list(self._affinity.keys()),
            "last_refresh_ago_sec":     round(time.time() - self._last_refresh),
        }


# ── Module-level singleton ────────────────────────────────────────────────────
_bridge_instance: Optional[ParamBridge] = None


def get_param_bridge() -> ParamBridge:
    """Get or create the module-level ParamBridge singleton."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = ParamBridge()
    return _bridge_instance


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    bridge = ParamBridge()
    print("Status:", bridge.get_status_summary())
    print()

    for sym in ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK"]:
        for strat in ["trend", "breakout", "mean_reversion"]:
            config = bridge.get_config(sym, strat)
            best   = bridge.get_best_strategy(sym)
            print(f"{sym:15} {strat:15} → {config}")
        print(f"  Best strategy for {sym}: {bridge.get_best_strategy(sym) or 'no affinity data yet'}")
        print()
