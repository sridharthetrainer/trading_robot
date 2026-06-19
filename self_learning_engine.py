"""
self_learning_engine.py

Learns from closed trades using:
- XGBoost classifier for trade-quality probability
- Lightweight RL state for strategy-preference bias

Fixes applied
-------------
1. RL double-counting
   Original code passed get_closed_trades() — ALL trades ever — to
   _update_rl() on every learning cycle.  After N cycles each trade's
   reward was counted N times and RL scores became meaningless.

   Fix: `last_rl_processed_trade_id` is stored inside rl_state.json.
   On each run, only trades whose `id` is strictly greater than that
   watermark are processed.  The watermark is updated at the end of
   the batch, so restarts are safe — no trade is double-counted even
   if the process crashes mid-update.

2. Frozen model
   Original: `if self.model is None: _train_model()`.  Once a model
   file existed on disk it was loaded and never retrained.

   Fix: `_should_retrain()` checks two independent conditions:
   a) >= RETRAIN_NEW_TRADES_THRESHOLD new trades since last training
   b) >= RETRAIN_MAX_AGE_DAYS days since last training
   Either condition alone triggers a retrain.  Both thresholds are
   configurable at construction time.  Metadata (last_trained_at,
   trades_at_last_train) is persisted in a JSON sidecar next to the
   model file so it survives process restarts.

3. In-sample accuracy was reported as a real metric
   Original code trained and evaluated on the SAME rows, reporting
   near-100% accuracy for any tree model.  Now uses an 80/20
   time-ordered split: the model trains on the first 80% of trades
   (chronological order) and is evaluated on the last 20%.
   The reported `val_accuracy` is a genuine out-of-sample estimate.

4. XGBoost overfitting on small datasets
   Added min_child_weight=3 and reg_lambda=1.5 to constrain leaf
   splits.  The threshold for training is raised from 20 to 30 trades
   (still low, but slightly more statistically meaningful).
"""

from __future__ import annotations
try:
    from signal_log import get_signal_logger as _get_sig_log
    _SIG_LOG = True
except ImportError:
    _SIG_LOG = False
try:
    from triple_barrier import label_triple_barrier, get_dynamic_barriers
    _TB_AVAIL = True
except ImportError:
    _TB_AVAIL = False

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np

from auto_strategy_selector import AutoStrategySelector
from trade_manager import TradeManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
MODEL_FILE        = "ai_model.pkl"
MODEL_BACKUP_FILE = "ai_model_backup.pkl"
MODEL_META_FILE   = "ai_model_meta.json"   # sidecar tracking train timestamp
RL_STATE_FILE     = "rl_state.json"
RL_BACKUP_FILE    = "rl_state_backup.json"

# ---------------------------------------------------------------------------
# Retraining policy defaults (can be overridden in __init__)
# ---------------------------------------------------------------------------
RETRAIN_NEW_TRADES_THRESHOLD = 50    # retrain when this many new trades arrive
RETRAIN_MAX_AGE_DAYS         = 7     # also retrain if model is this many days old
MIN_TRADES_TO_TRAIN          = 50    # minimum total trades before any training

# RL watermark key stored inside rl_state.json
_RL_WATERMARK_KEY = "__last_processed_trade_id__"


class SelfLearningEngine:
    """
    Learns from closed trades using XGBoost + lightweight RL.

    Typical call sequence (once per after-hours learning cycle):
        engine = SelfLearningEngine(strategy_state_file="strategy_state.json")
        result = engine.run()
    """

    def __init__(
        self,
        strategy_state_file: str,
        model_file: str = MODEL_FILE,
        rl_state_file: str = RL_STATE_FILE,
        trades_db_path: str = "trades.db",
        retrain_new_trades_threshold: int = RETRAIN_NEW_TRADES_THRESHOLD,
        retrain_max_age_days: int = RETRAIN_MAX_AGE_DAYS,
    ) -> None:
        self.strategy_state_file          = strategy_state_file
        self.model_file                   = model_file
        self.model_meta_file              = str(Path(model_file).with_suffix("")) + "_meta.json"
        self.rl_state_file                = rl_state_file
        self.trades_db_path               = trades_db_path
        self.retrain_new_trades_threshold = int(retrain_new_trades_threshold)
        self.retrain_max_age_days         = int(retrain_max_age_days)

        self.selector = AutoStrategySelector(
            strategy_state_file=strategy_state_file
        )
        self.trade_manager = TradeManager(
            broker_manager=None,
            db_path=trades_db_path,
            restore_state=True,
        )

        self.model: Any = None
        self.rl_state: Dict[str, Any] = {}
        self._model_meta: Dict[str, Any] = {}

        # Per-strategy models — trained on each strategy's own trade history
        self._strategy_models: Dict[str, Any] = {}
        self._strategy_model_dir = Path(model_file).parent

        self._load_model()
        self._load_rl_state()
        self._load_model_meta()
        self._load_strategy_models()

    # ------------------------------------------------------------------
    # Market-hours safety
    # ------------------------------------------------------------------
    def _is_market_hours(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        current_minutes = now.hour * 60 + now.minute
        return (9 * 60 + 15) <= current_minutes <= (15 * 60 + 30)

    # ------------------------------------------------------------------
    # Model persistence
    # ------------------------------------------------------------------
    def _load_model(self) -> None:
        for path in (self.model_file, MODEL_BACKUP_FILE):
            if os.path.exists(path):
                try:
                    self.model = joblib.load(path)
                    logger.info("AI model loaded from %s", path)
                    return
                except Exception:
                    logger.exception("Failed to load model from %s", path)
        self.model = None

    def _save_model(self) -> None:
        try:
            joblib.dump(self.model, self.model_file)
            joblib.dump(self.model, MODEL_BACKUP_FILE)
            logger.info("AI model saved: %s + %s", self.model_file, MODEL_BACKUP_FILE)
        except Exception:
            logger.exception("Model save failed")

    # ------------------------------------------------------------------
    # Model metadata (tracks training history for retraining policy)
    # ------------------------------------------------------------------
    def _load_model_meta(self) -> None:
        try:
            if os.path.exists(self.model_meta_file):
                with open(self.model_meta_file, "r", encoding="utf-8") as f:
                    self._model_meta = json.load(f)
                return
        except Exception:
            logger.exception("Model meta load failed")
        self._model_meta = {}

    def _save_model_meta(self, total_trades: int) -> None:
        self._model_meta = {
            "last_trained_at":      datetime.now().isoformat(),
            "last_trained_ts":      datetime.now().timestamp(),
            "trades_at_last_train": total_trades,
        }
        try:
            with open(self.model_meta_file, "w", encoding="utf-8") as f:
                json.dump(self._model_meta, f, indent=2)
        except Exception:
            logger.exception("Model meta save failed")

    def _should_retrain(self, total_trades: int) -> Tuple[bool, str]:
        """
        Return (should_retrain, reason_string).

        Conditions that trigger a retrain (either is sufficient):
        - No model exists on disk
        - New trades since last training >= retrain_new_trades_threshold
        - Days since last training >= retrain_max_age_days
        """
        if self.model is None:
            return True, "no_model"

        if not self._model_meta:
            return True, "no_meta"

        trades_at_last = int(self._model_meta.get("trades_at_last_train", 0))
        new_trades = total_trades - trades_at_last
        if new_trades >= self.retrain_new_trades_threshold:
            return True, f"new_trades={new_trades}>={self.retrain_new_trades_threshold}"

        last_ts = float(self._model_meta.get("last_trained_ts", 0))
        age_days = (datetime.now().timestamp() - last_ts) / 86400.0
        if age_days >= self.retrain_max_age_days:
            return True, f"age={age_days:.1f}d>={self.retrain_max_age_days}d"

        return False, f"no_retrain_needed (new={new_trades}, age={age_days:.1f}d)"

    # ------------------------------------------------------------------
    # RL state persistence
    # ------------------------------------------------------------------
    def _load_rl_state(self) -> None:
        for path in (self.rl_state_file, RL_BACKUP_FILE):
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self.rl_state = json.load(f)
                    return
                except Exception:
                    logger.exception("RL state load failed from %s", path)
        self.rl_state = {}

    def _save_rl_state(self) -> None:
        try:
            for path in (self.rl_state_file, RL_BACKUP_FILE):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.rl_state, f, indent=2)
            logger.info("RL state saved: %s + %s", self.rl_state_file, RL_BACKUP_FILE)
        except Exception:
            logger.exception("RL state save failed")

    def _get_rl_watermark(self) -> int:
        """Return the highest trade ID already processed by the RL updater."""
        return int(self.rl_state.get(_RL_WATERMARK_KEY, 0))

    def _set_rl_watermark(self, trade_id: int) -> None:
        self.rl_state[_RL_WATERMARK_KEY] = int(trade_id)

    # ------------------------------------------------------------------
    # Feature engineering
    # ------------------------------------------------------------------
    @staticmethod
    def _regime_to_num(regime: str) -> float:
        r = str(regime or "").upper()
        if r in ("TREND", "BULLISH_TREND", "BEARISH_TREND", "BREAKOUT"):
            return 1.0
        if r in ("RANGE", "SIDEWAYS", "EARLY_TREND"):
            return 0.0
        if r in ("NO_TRADE", "VOLATILE", "UNCLEAR", "UNKNOWN"):
            return -1.0
        return 0.0

    @staticmethod
    def _side_to_num(side: str) -> float:
        s = str(side or "").upper()
        return 1.0 if s == "BUY" else (-1.0 if s == "SELL" else 0.0)

    @staticmethod
    def _strategy_to_num(strategy: str) -> float:
        return {
            "TREND":          1.0,
            "BREAKOUT":       2.0,
            "MEAN_REVERSION": 3.0,
            "FALLBACK":       4.0,
            "AUTO":           5.0,
            "SCALPING":       6.0,
            "SWING":          7.0,
            "MA_CROSS":       8.0,
        }.get(str(strategy or "").upper(), 0.0)


    @staticmethod
    def _compute_dte(entry_time_ts: float) -> float:
        """
        Approximate days-to-expiry at trade entry.
        Uses the next Thursday (NSE weekly expiry) as the reference.
        Returns 0.0 if computation fails.
        """
        try:
            from datetime import datetime
            entry_dt   = datetime.fromtimestamp(float(entry_time_ts))
            weekday    = entry_dt.weekday()          # Mon=0 … Sun=6
            days_to_thu = (3 - weekday) % 7         # 3 = Thursday
            # If it IS Thursday and already past 15:00, next expiry is 7 days out
            if days_to_thu == 0 and entry_dt.hour >= 15:
                days_to_thu = 7
            return float(days_to_thu)
        except Exception:
            return 0.0

    @staticmethod
    def _enrich_trades_for_features(
        trades: list,
    ) -> list:
        """
        Compute derived features that require cross-trade context:
          - hour_of_day   : hour of entry (0–23)
          - day_of_week   : 0 Mon … 4 Fri (expiry day = 3)
          - dte           : approximate days-to-expiry at entry
          - hold_bars     : position duration in 5-min bars
          - trade_number  : sequential number within the same trading day (1-based)
          - daily_pnl_before : cumulative P&L of earlier trades same day

        Enriched values are added to each trade dict (copy, not in-place).
        """
        from datetime import datetime
        import time as _time

        enriched = [dict(t) for t in trades]

        # Sort by entry_time so trade_number and daily_pnl_before are correct
        enriched.sort(key=lambda t: float(t.get("entry_time") or 0.0))

        day_counter: dict = {}   # date_str → count of trades that day
        day_pnl:     dict = {}   # date_str → cumulative P&L so far

        for t in enriched:
            entry_ts = float(t.get("entry_time") or 0.0)
            exit_ts  = float(t.get("exit_time")  or 0.0)
            pnl      = float(t.get("pnl")        or 0.0)

            try:
                entry_dt  = datetime.fromtimestamp(entry_ts) if entry_ts > 0 else datetime.now()
                date_str  = entry_dt.strftime("%Y-%m-%d")
                hour      = float(entry_dt.hour)
                dow       = float(entry_dt.weekday())   # 0=Mon, 3=Thu

                # Trade number within the day (1-based)
                day_counter[date_str] = day_counter.get(date_str, 0) + 1
                trade_num = float(day_counter[date_str])

                # Cumulative P&L of earlier trades same day
                daily_pnl_before = float(day_pnl.get(date_str, 0.0))
                day_pnl[date_str] = daily_pnl_before + pnl

                # Hold duration in 5-min bars
                if exit_ts > entry_ts:
                    hold_bars = float((exit_ts - entry_ts) / 300.0)   # 5 min = 300 sec
                else:
                    hold_bars = 0.0

                dte = SelfLearningEngine._compute_dte(entry_ts)

            except Exception:
                hour = dow = trade_num = daily_pnl_before = hold_bars = dte = 0.0

            t["_hour_of_day"]       = hour
            t["_day_of_week"]       = dow
            t["_dte"]               = dte
            t["_hold_bars"]         = min(hold_bars, 100.0)   # cap outliers
            t["_trade_number"]      = trade_num
            t["_daily_pnl_before"]  = daily_pnl_before

        return enriched

    def _weight_by_mode(self, trades: list) -> list:
        """
        Give live trades 3× weight over paper trades in training.
        
        Paper trades test signal logic but don't capture real-world
        execution effects (slippage, partial fills, gaps).
        Live trades reflect actual market interaction.
        
        Method: duplicate live trades 3 times in training set.
        This does NOT remove paper trades — paper data is still valuable
        for learning signal patterns, just less so than live outcomes.
        """
        weighted = []
        for t in trades:
            weighted.append(t)
            if str(t.get("mode", "PAPER")).upper() == "LIVE":
                weighted.append(t)  # duplicate 1
                weighted.append(t)  # duplicate 2 = 3× total weight
        return weighted

    # Feature names must match _extract_features(), predict_for_strategy(), and
    # explain_signal() exactly.
    FEATURE_NAMES = [
        "confidence",
        "score",
        "regime_score",
        "volatility",
        "entry_atr",
        "regime_num",
        "side_num",
        "strategy_num",
        "hour_of_day",
        "day_of_week",
        "dte",
        "hold_bars",
        "trade_number",
        "daily_pnl_before",
    ]

    _CONFLUENCE_NUM = {"VERY_STRONG":5,"STRONG":4,"MEDIUM":3,"WEAK":2,"SINGLE":1}

    def _extract_features(
        self, trades: List[Dict[str, Any]]
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Build 40-feature matrix from trades/signal_log rows.
        Works with both old trade dicts and new signal_log rows.
        """
        # Enrich trades with derived temporal context
        enriched = self._enrich_trades_for_features(trades)

        rows_x: List[List[float]] = []
        rows_y: List[int] = []

        for t in enriched:
            pnl          = float(t.get("pnl",          0.0) or 0.0)
            confidence   = float(t.get("confidence",   0.0) or 0.0)
            score        = float(t.get("score",        0.0) or 0.0)
            regime_score = float(t.get("regime_score", 0.0) or 0.0)
            volatility   = float(t.get("volatility",   0.0) or 0.0)
            entry_atr    = float(t.get("entry_atr",    0.0) or 0.0)

            rows_x.append([
                # Original 8 features
                confidence,
                score,
                regime_score,
                volatility,
                entry_atr,
                self._regime_to_num(t.get("regime",    "UNKNOWN")),
                self._side_to_num(  t.get("side",      "")),
                self._strategy_to_num(t.get("strategy","" )),
                # New 6 temporal / context features
                float(t.get("_hour_of_day",      0.0)),
                float(t.get("_day_of_week",      0.0)),
                float(t.get("_dte",              0.0)),
                float(t.get("_hold_bars",        0.0)),
                float(t.get("_trade_number",     1.0)),
                float(t.get("_daily_pnl_before", 0.0)),
            ])
            rows_y.append(1 if pnl > 0 else 0)

        if not rows_x:
            return None, None

        return np.array(rows_x, dtype=float), np.array(rows_y, dtype=int)


    def _strategy_model_path(self, strategy: str) -> str:
        return str(self._strategy_model_dir / f"ai_model_{strategy.lower()}.pkl")

    def _load_strategy_models(self) -> None:
        """Load per-strategy models from disk if they exist."""
        strategies = ["trend", "mean_reversion", "breakout", "scalping", "ma_cross"]
        for strat in strategies:
            path = self._strategy_model_path(strat)
            if os.path.exists(path):
                try:
                    self._strategy_models[strat] = joblib.load(path)
                    logger.info("Per-strategy model loaded: %s", strat)
                except Exception as exc:
                    logger.warning("Failed to load strategy model %s: %s", strat, exc)

    def _train_strategy_models(
        self, all_trades: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Train a separate XGBoost model for each strategy.
        Each model only sees trades from its own strategy.
        Returns a summary dict.
        """
        strategies = ["trend", "mean_reversion", "breakout", "scalping", "ma_cross"]
        results = {}

        for strat in strategies:
            strat_trades = [
                t for t in all_trades
                if str(t.get("strategy", "")).lower() in (strat, strat.replace("_", ""))
            ]
            if len(strat_trades) < 20:
                results[strat] = {"trained": False, "reason": f"only {len(strat_trades)} trades"}
                continue

            # Use same feature extraction as shared model
            enriched = self._enrich_trades_for_features(strat_trades)
            rows_x, rows_y = [], []
            for t in enriched:
                pnl = float(t.get("pnl", 0) or 0)
                rows_x.append([
                    float(t.get("confidence",    0) or 0),
                    float(t.get("score",         0) or 0),
                    float(t.get("regime_score",  0) or 0),
                    float(t.get("volatility",    0) or 0),
                    float(t.get("entry_atr",     0) or 0),
                    self._regime_to_num(t.get("regime", "")),
                    self._side_to_num(t.get("side", "")),
                    self._strategy_to_num(t.get("strategy", "")),
                    float(t.get("_hour_of_day",      0) or 0),
                    float(t.get("_day_of_week",      0) or 0),
                    float(t.get("_dte",              0) or 0),
                    float(t.get("_hold_bars",        0) or 0),
                    float(t.get("_trade_number",     1) or 1),
                    float(t.get("_daily_pnl_before", 0) or 0),
                ])
                rows_y.append(1 if pnl > 0 else 0)

            if len(rows_x) < 10:
                continue

            try:
                x = np.array(rows_x, dtype=float)
                y = np.array(rows_y, dtype=int)
                split = max(1, int(len(x) * 0.8))
                x_train, x_val = x[:split], x[split:]
                y_train, y_val = y[:split], y[split:]

                from xgboost import XGBClassifier
                model = XGBClassifier(
                    n_estimators=80, max_depth=4, min_child_weight=3,
                    learning_rate=0.08, subsample=0.85, colsample_bytree=0.85,
                    reg_lambda=1.5, objective="binary:logistic",
                    eval_metric="logloss", random_state=42, verbosity=0,
                )
                model.fit(x_train, y_train)

                val_acc = 0.0
                if len(x_val) >= 3:
                    val_acc = float((model.predict(x_val) == y_val).mean())

                self._strategy_models[strat] = model
                joblib.dump(model, self._strategy_model_path(strat))

                results[strat] = {
                    "trained": True, "trades": len(strat_trades),
                    "val_accuracy": round(val_acc, 4),
                }
                logger.info(
                    "Per-strategy model trained: %s | trades=%d val_acc=%.3f",
                    strat, len(strat_trades), val_acc,
                )
            except Exception as exc:
                results[strat] = {"trained": False, "reason": str(exc)}

        return results

    def predict_for_strategy(
        self, signal: Dict[str, Any], strategy: str
    ) -> float:
        """
        Get AI probability from the strategy-specific model.
        Falls back to shared model if per-strategy model unavailable.
        """
        strat_key = str(strategy).lower().replace(" ", "_")
        model = self._strategy_models.get(strat_key) or self.model
        if model is None:
            return 0.5

        try:
            enriched = self._enrich_trades_for_features([signal])
            t = enriched[0] if enriched else signal
            x = np.array([[
                float(t.get("confidence",    0.5) or 0.5),
                float(t.get("score",         0)   or 0),
                float(t.get("regime_score",  0)   or 0),
                float(t.get("volatility",    0)   or 0),
                float(t.get("entry_atr",     0)   or 0),
                self._regime_to_num(t.get("regime", "")),
                self._side_to_num(t.get("side", "")),
                self._strategy_to_num(t.get("strategy", strategy)),
                float(t.get("_hour_of_day",      0) or 0),
                float(t.get("_day_of_week",      0) or 0),
                float(t.get("_dte",              0) or 0),
                float(t.get("_hold_bars",        0) or 0),
                float(t.get("_trade_number",     1) or 1),
                float(t.get("_daily_pnl_before", 0) or 0),
            ]], dtype=float)
            prob = model.predict_proba(x)[0][1]
            return float(prob)
        except Exception:
            return 0.5

    def explain_signal(
        self, signal: Dict[str, Any], strategy: str, top_n: int = 3
    ) -> Dict[str, Any]:
        """
        Return SHAP-based explanation for why the model scored this signal.

        Uses TreeExplainer (fast, exact for XGBoost) to compute per-feature
        SHAP values. Returns the top_n features by |SHAP value| with:
          - feature name (human-readable)
          - direction (pushed probability UP or DOWN)
          - magnitude (how much it moved the probability)

        Falls back to model feature_importances_ if SHAP fails (rare).
        Returns empty dict if model not trained yet.

        Usage:
            explanation = engine.explain_signal(signal, strategy)
            # {"top_features": [{"name": "n_agree", "direction": "UP", "delta": 0.12}, ...],
            #  "base_prob": 0.50, "final_prob": 0.72, "method": "shap"}
        """
        strat_key = str(strategy).lower().replace(" ", "_")
        model = self._strategy_models.get(strat_key) or self.model
        if model is None:
            return {}

        try:
            enriched = self._enrich_trades_for_features([signal])
            t = enriched[0] if enriched else signal
            x = np.array([[
                float(t.get("confidence",    0.5) or 0.5),
                float(t.get("score",         0)   or 0),
                float(t.get("regime_score",  0)   or 0),
                float(t.get("volatility",    0)   or 0),
                float(t.get("entry_atr",     0)   or 0),
                self._regime_to_num(t.get("regime", "")),
                self._side_to_num(t.get("side", "")),
                self._strategy_to_num(t.get("strategy", strategy)),
                float(t.get("_hour_of_day",      0) or 0),
                float(t.get("_day_of_week",      0) or 0),
                float(t.get("_dte",              0) or 0),
                float(t.get("_hold_bars",        0) or 0),
                float(t.get("_trade_number",     1) or 1),
                float(t.get("_daily_pnl_before", 0) or 0),
            ]], dtype=float)

            final_prob = float(model.predict_proba(x)[0][1])

            try:
                import shap as _shap
                explainer  = _shap.TreeExplainer(model)
                shap_vals  = explainer.shap_values(x)
                # For binary classifier, shap_values may be [neg_class, pos_class]
                if isinstance(shap_vals, list) and len(shap_vals) == 2:
                    sv = shap_vals[1][0]   # positive class SHAP for single row
                else:
                    sv = shap_vals[0] if hasattr(shap_vals, "__len__") else shap_vals

                base_prob  = float(_shap.TreeExplainer(model).expected_value
                                   if not isinstance(_shap.TreeExplainer(model).expected_value, (list,))
                                   else _shap.TreeExplainer(model).expected_value[1])

                # Build feature name list — use FEATURE_NAMES if lengths match
                fnames = self.FEATURE_NAMES if len(self.FEATURE_NAMES) == len(sv) else [
                    f"f{i}" for i in range(len(sv))
                ]

                # Sort by |SHAP| descending
                shap_pairs = sorted(
                    zip(fnames, sv), key=lambda p: abs(p[1]), reverse=True
                )[:top_n]

                top_features = [
                    {
                        "name":      name,
                        "direction": "UP" if val > 0 else "DOWN",
                        "delta":     round(float(val), 4),
                        "label":     f"{name} pushed probability {'up' if val>0 else 'down'} by {abs(val):.3f}",
                    }
                    for name, val in shap_pairs
                ]

                return {
                    "top_features": top_features,
                    "base_prob":    round(base_prob, 4),
                    "final_prob":   round(final_prob, 4),
                    "method":       "shap",
                }

            except Exception as shap_err:
                logger.debug("SHAP failed, using feature_importances_: %s", shap_err)
                # Fallback: use model's own feature importances as proxy
                if hasattr(model, "feature_importances_"):
                    fnames = self.FEATURE_NAMES
                    pairs = sorted(
                        zip(fnames, model.feature_importances_),
                        key=lambda p: p[1], reverse=True
                    )[:top_n]
                    return {
                        "top_features": [
                            {"name": n, "direction": "UP", "delta": round(float(v), 4),
                             "label": f"{n} importance={v:.3f}"}
                            for n, v in pairs
                        ],
                        "base_prob":  0.5,
                        "final_prob": round(final_prob, 4),
                        "method":     "feature_importance_fallback",
                    }
                return {"final_prob": round(final_prob, 4), "method": "prob_only"}

        except Exception as exc:
            logger.debug("explain_signal failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # ML training
    # ------------------------------------------------------------------
    def _train_model(self, all_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Train XGBoost on all_trades using an 80/20 time-ordered split.
        The first 80% are used for training, the last 20% for validation.
        Reports val_accuracy (out-of-sample) — NOT train_accuracy.
        """
        n = len(all_trades)
        if n < MIN_TRADES_TO_TRAIN:
            logger.warning("Not enough trades to train: %d < %d", n, MIN_TRADES_TO_TRAIN)
            return {"trained": False, "reason": "not_enough_trades", "num_trades": n}

        # Time-ordered split — preserves temporal integrity
        split_idx   = max(1, int(n * 0.80))
        train_trades = all_trades[:split_idx]
        val_trades   = all_trades[split_idx:]

        # Weight live trades 3× in training (not validation)
        train_trades_weighted = self._weight_by_mode(train_trades)
        x_train, y_train = self._extract_features(train_trades_weighted)
        x_val,   y_val   = self._extract_features(val_trades)

        if x_train is None or len(x_train) < 10:
            return {"trained": False, "reason": "feature_extraction_failed", "num_trades": n}

        try:
            from xgboost import XGBClassifier

            model = XGBClassifier(
                n_estimators=100,
                max_depth=4,
                min_child_weight=3,      # prevents splits on tiny leaf groups
                learning_rate=0.08,
                subsample=0.85,
                colsample_bytree=0.85,
                reg_lambda=1.5,          # L2 regularisation
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=42,
                verbosity=0,
            )
            model.fit(x_train, y_train)

            self.model = model
            self._save_model()
            # Train specialised sub-models
            try:
                self._train_specialised_models(train_trades_weighted, val_trades)
            except Exception as _sme:
                logger.debug("Specialised model training: %s", _sme)
            self._save_model_meta(total_trades=n)

            # Out-of-sample accuracy
            val_acc: float = 0.0
            val_n: int = 0
            if x_val is not None and len(x_val) >= 3:
                preds   = model.predict(x_val)
                val_acc = float((preds == y_val).mean())
                val_n   = len(y_val)

            logger.info(
                "AI model trained | total=%d train=%d val=%d val_accuracy=%.4f",
                n, split_idx, val_n, val_acc,
            )

            return {
                "trained":      True,
                "reason":       "ok",
                "num_trades":   n,
                "train_size":   split_idx,
                "val_size":     val_n,
                "val_accuracy": round(val_acc, 4),
            }

        except Exception as exc:
            logger.exception("Model training failed")
            return {
                "trained":    False,
                "reason":     "training_exception",
                "error":      str(exc),
                "num_trades": n,
            }

    # ------------------------------------------------------------------
    # RL update — only processes NEW trades (watermark-based)
    # ------------------------------------------------------------------
    def _update_rl(self, all_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process only trades whose `id` exceeds the stored watermark.
        Updates the watermark after processing so no trade is ever
        counted twice across restarts or repeated learning cycles.
        """
        watermark   = self._get_rl_watermark()
        new_trades  = [
            t for t in all_trades
            if int(t.get("id", 0)) > watermark
        ]

        if not new_trades:
            logger.info("RL update: no new trades since watermark=%d", watermark)
            return {
                "updated":          False,
                "strategies":       len(self.rl_state),
                "trades_processed": 0,
                "watermark":        watermark,
            }

        max_id_seen = watermark

        for t in new_trades:
            trade_id  = int(t.get("id", 0))
            strategy  = str(t.get("strategy", "unknown")).upper()
            pnl       = float(t.get("pnl",        0.0))
            confidence = float(t.get("confidence", 0.0) or 0.0)
            regime    = str(t.get("regime",  "UNKNOWN")).upper()

            if strategy not in self.rl_state:
                self.rl_state[strategy] = {
                    "score":       0.0,
                    "trades":      0,
                    "wins":        0,
                    "losses":      0,
                    "avg_pnl":     0.0,
                    "last_regime": regime,
                }

            # Reward: P&L normalised to ~[-3, +3], scaled by confidence
            reward = (pnl / 1000.0) * max(0.5, confidence if confidence > 0 else 1.0)

            state             = self.rl_state[strategy]
            state["score"]    = float(state.get("score",   0.0)) + reward
            state["trades"]   = int(  state.get("trades",  0))   + 1
            state["wins"]     = int(  state.get("wins",    0))   + (1 if pnl > 0 else 0)
            state["losses"]   = int(  state.get("losses",  0))   + (1 if pnl <= 0 else 0)

            n              = state["trades"]
            old_avg        = float(state.get("avg_pnl", 0.0))
            state["avg_pnl"] = ((old_avg * (n - 1)) + pnl) / n
            state["last_regime"] = regime

            if trade_id > max_id_seen:
                max_id_seen = trade_id

        # Commit watermark AFTER successful processing
        self._set_rl_watermark(max_id_seen)
        self._save_rl_state()

        logger.info(
            "RL updated | new_trades=%d watermark %d→%d strategies=%d",
            len(new_trades), watermark, max_id_seen, len(self.rl_state),
        )

        return {
            "updated":          True,
            "strategies":       len(self.rl_state),
            "trades_processed": len(new_trades),
            "watermark":        max_id_seen,
        }

    # ------------------------------------------------------------------
    # Strategy selector bridge
    # ------------------------------------------------------------------
    def _select_strategy(self) -> Dict[str, Any]:
        try:
            result = self.selector.run()
            return result if isinstance(result, dict) else {"selected_strategy": None}
        except Exception as exc:
            logger.exception("Strategy selection failed")
            return {"selected_strategy": None, "error": str(exc)}

    # ------------------------------------------------------------------
    # Public run
    # ------------------------------------------------------------------

    def _train_specialised_models(self, train: list, val: list) -> None:
        """
        Train specialised models for:
          - Indices (NIFTY, BANKNIFTY etc.)
          - Stocks
          - High-VIX regime (VIX > 18)
          - Low-VIX regime  (VIX <= 15)
          - Morning session (9:15-11:00)
          - Power hour      (14:30-15:25)
        Each sub-model only trains when it has >= 20 samples.
        """
        try:
            from xgboost import XGBClassifier
        except ImportError:
            return

        segments = {
            "index":      lambda t: t.get("symbol_type","") == "INDEX" or "symbol_type" not in t and t.get("symbol","") in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"},
            "stock":      lambda t: t.get("symbol_type","STOCK") == "STOCK",
            "high_vix":   lambda t: float(t.get("india_vix",0) or 0) > 18,
            "morning":    lambda t: int(t.get("hour_of_day",10) or 10) <= 10,
            "power_hour": lambda t: int(t.get("hour_of_day",14) or 14) >= 14,
        }
        for seg_name, seg_filter in segments.items():
            try:
                seg_train = [t for t in train if seg_filter(t)]
                seg_val   = [t for t in val   if seg_filter(t)]
                if len(seg_train) < 20:
                    continue
                X, y = self._extract_features(seg_train)
                if X is None or len(X) < 10:
                    continue
                m = XGBClassifier(
                    n_estimators=50, max_depth=3, learning_rate=0.1,
                    subsample=0.8, reg_lambda=2.0,
                    objective="binary:logistic", verbosity=0,
                )
                m.fit(X, y)
                _path = self._strategy_model_path(f"_seg_{seg_name}")
                import pickle
                with open(_path, "wb") as f:
                    pickle.dump(m, f)
                logger.info("Specialised model %s trained on %d samples", seg_name, len(seg_train))
            except Exception as e:
                logger.debug("Specialised model %s: %s", seg_name, e)

    def run(self) -> Dict[str, Any]:
        logger.info("Self-learning engine started")

        try:
            all_closed_trades = self.trade_manager.get_closed_trades() or []
            total_trades      = len(all_closed_trades)

            # ---- Market hours: skip heavy work, still return state ----
            if self._is_market_hours():
                logger.info("Market hours — skipping training/RL update")
                selector_result = self._select_strategy()
                return {
                    "status":          "skipped_market_hours",
                    "best_strategy":   selector_result.get("selected_strategy"),
                    "model_ready":     self.model is not None,
                    "model_file":      self.model_file if self.model is not None else None,
                    "rl_state":        self._rl_state_public(),
                    "training_result": {"trained": False, "reason": "market_hours_skip",
                                        "num_trades": total_trades},
                    "rl_result":       {"updated": False, "strategies": len(self.rl_state),
                                        "trades_processed": 0},
                    "selector_result": selector_result,
                    "closed_trade_count": total_trades,
                }

            # ---- RL update (new trades only) ---------------------------
            rl_result = self._update_rl(all_closed_trades)

            # ---- Model training (conditional) --------------------------
            should_train, train_reason = self._should_retrain(total_trades)

            if should_train:
                logger.info("Retraining model | reason=%s", train_reason)
                training_result = self._train_model(all_closed_trades)
                # Also train per-strategy models
                strategy_model_results = self._train_strategy_models(all_closed_trades)
                training_result["per_strategy"] = strategy_model_results
            else:
                logger.info("Skipping model retrain | %s", train_reason)
                training_result = {
                    "trained":    False,
                    "reason":     train_reason,
                    "num_trades": total_trades,
                }

            # ---- Strategy selection ------------------------------------
            selector_result = self._select_strategy()
            best_strategy   = selector_result.get("selected_strategy")

            # Count paper vs live trades for reporting
            n_paper = sum(1 for t in all_closed_trades if str(t.get("mode","PAPER")).upper() == "PAPER")
            n_live  = sum(1 for t in all_closed_trades if str(t.get("mode","PAPER")).upper() == "LIVE")
            logger.info("Trade breakdown: %d paper, %d live", n_paper, n_live)
            logger.info(
                "Self-learning complete | strategy=%s model_ready=%s rl_new=%d",
                best_strategy,
                self.model is not None,
                rl_result.get("trades_processed", 0),
            )

            return {
                "status":             "success",
                "best_strategy":      best_strategy,
                "model_ready":        self.model is not None,
                "model_file":         self.model_file if self.model is not None else None,
                "rl_state":           self._rl_state_public(),
                "training_result":    training_result,
                "rl_result":          rl_result,
                "selector_result":    selector_result,
                "closed_trade_count": total_trades,
            }

        except Exception as exc:
            logger.exception("Self-learning failed")
            return {
                "status":      "failed",
                "error":       str(exc),
                "best_strategy": None,
                "model_ready": self.model is not None,
                "rl_state":    self._rl_state_public(),
            }

    def _rl_state_public(self) -> Dict[str, Any]:
        """Return rl_state without the internal watermark key."""
        return {k: v for k, v in self.rl_state.items() if k != _RL_WATERMARK_KEY}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    engine = SelfLearningEngine(strategy_state_file="strategy_state.json")
    import json as _json
    print(_json.dumps(engine.run(), indent=2, default=str))
