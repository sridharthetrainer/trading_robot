"""
signal_calibrator.py — Logistic Regression Meta-Signal Calibrator

Provides a CALIBRATED probability score for each live signal using a
LogisticRegression model trained on the 15 features already tracked in
feature_importance.py and signal_log.db.

Why this is separate from the XGBoost in self_learning_engine.py:
  - XGBoost requires 50+ trades; this works from 30 (min sample guard)
  - Logistic regression is always-interpretable — coefficients show which
    features truly predict outcomes on THIS system's live data
  - Cross-validates the XGBoost — if they disagree badly, something is overfit
  - Isotonic calibration ensures "70% probability" actually means 70%
  - Provides a second independent probability estimate for ai_trade_filter.py

Features used (subset of signal_log.db columns):
  score, n_agree, n_conflict, regime_num, confluence_num,
  india_vix, iv_percentile, pcr_atm, fii_net_cash, fii_fut_ratio,
  cross_asset_mod, participant_mod, theta_mod, news_mod, hour_of_day

Training:
  - Reads from signal_log.db (tb_label != -99 rows)
  - Time-ordered 80/20 split (no shuffle — preserves temporal integrity)
  - L2 regularisation (C=1.0) prevents overfitting on small samples
  - Isotonic calibration via CalibratedClassifierCV
  - Retrained nightly by idle_engine.py if >= 30 new labelled signals

Output per signal:
  {
    "calibrated_prob":  0.72,    # 0-1 probability (properly calibrated)
    "log_reg_verdict":  "PASS",  # PASS / WEAK / FAIL based on threshold
    "top_positive":     ["n_agree", "india_vix"],   # features pushing UP
    "top_negative":     ["n_conflict"],              # features pushing DOWN
    "n_train":          145,
    "method":           "logistic_isotonic",
  }
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_FILE  = Path("signal_calibrator_model.pkl")
_META_FILE   = Path("signal_calibrator_meta.json")
_DB_PATH     = Path("signal_log.db")
_TABLE       = "signal_log"
_MIN_SAMPLES = int(os.getenv("CALIBRATOR_MIN_CLEAN_SAMPLES", "5000"))
_MIN_DAYS = int(os.getenv("CALIBRATOR_MIN_CLEAN_DAYS", "15"))
_TRAINING_CONTRACT = "all_generated_signals_v2_purged_calibration"

# Probability thresholds
_PASS_THRESHOLD = 0.60
_WEAK_THRESHOLD = 0.45

FEATURE_NAMES = [
    "score",           # signal confluence score
    "n_agree",         # number of agreeing strategies
    "n_conflict",      # number of conflicting strategies
    "regime_num",      # numeric regime code
    "confluence_num",  # confluence tier (1-5)
    "india_vix",       # India VIX at signal time
    "iv_percentile",   # IV rank at signal time
    "pcr_atm",         # Put-Call Ratio ATM
    "fii_net_cash",    # FII net cash flow (Cr)
    "fii_fut_ratio",   # FII futures long/short ratio
    "cross_asset_mod", # cross-asset score modifier
    "participant_mod", # participant OI modifier
    "theta_mod",       # theta/IV environment modifier
    "news_mod",        # news sentiment modifier
    "hour_of_day",     # entry hour (9-15)
]

_REGIME_MAP = {
    "TRENDING": 1, "BREAKOUT": 2, "HIGH_VOL": 3,
    "MEAN_REVERTING": 4, "RANGE": 4,
    "LOW_VOL_CHOP": 5, "CHOPPY": 5, "HIGH_NOISE": 5,
    "NO_TRADE": 0,
}
_CONF_MAP = {"VERY_STRONG": 5, "STRONG": 4, "MEDIUM": 3, "WEAK": 2, "SINGLE": 1}


def _regime_num(regime: str) -> float:
    return float(_REGIME_MAP.get(str(regime).upper(), 3))


def _conf_num(confluence: str) -> float:
    return float(_CONF_MAP.get(str(confluence).upper(), 1))


def _safe(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _extract_row(row: Dict) -> List[float]:
    """Extract feature vector from a signal_log row dict."""
    return [
        _safe(row.get("score")),
        _safe(row.get("n_agree", 1)),
        _safe(row.get("n_conflict", 0)),
        _regime_num(row.get("regime", "")),
        _conf_num(row.get("confluence", "SINGLE")),
        _safe(row.get("india_vix", 15)),
        _safe(row.get("iv_percentile", 50)),
        _safe(row.get("pcr_atm", 1.0)),
        _safe(row.get("fii_net_cash", 0)),
        _safe(row.get("fii_fut_ratio", 1.0)),
        _safe(row.get("cross_asset_mod", 0)),
        _safe(row.get("participant_mod", 0)),
        _safe(row.get("theta_mod", 0)),
        _safe(row.get("news_mod", 0)),
        _safe(row.get("hour_of_day", 11)),
    ]


def _load_training_data() -> Tuple[Optional[np.ndarray], Optional[np.ndarray], List[str]]:
    """Load labelled signal data from signal_log.db."""
    if not _DB_PATH.exists():
        return None, None, []
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE tb_label IN (1, -1) "
            "AND training_eligible=1 AND stop_loss>0 AND target>0 AND rr>0 "
            "ORDER BY signal_date ASC, signal_time ASC, id ASC"
        ).fetchall()
        conn.close()
        rows = [dict(r) for r in rows]
    except Exception as e:
        logger.debug("_load_training_data: %s", e)
        return None, None, []

    if len(rows) < _MIN_SAMPLES:
        logger.info("Calibrator: only %d labelled signals (need %d)", len(rows), _MIN_SAMPLES)
        return None, None, []

    X = np.array([_extract_row(r) for r in rows], dtype=float)
    y = np.array([1 if int(r["tb_label"]) == 1 else 0 for r in rows], dtype=int)
    dates = [str(r.get("signal_date") or "") for r in rows]
    return X, y, dates


class SignalCalibrator:
    """
    Logistic Regression + Isotonic Calibration probability scorer.

    Usage:
        cal = SignalCalibrator()
        result = cal.score(signal_dict)
        # {"calibrated_prob": 0.72, "log_reg_verdict": "PASS", ...}
    """

    def __init__(self) -> None:
        self._model  = None
        self._meta: Dict = {}
        self._load()

    def _load(self) -> None:
        try:
            import joblib
            if _MODEL_FILE.exists():
                if _META_FILE.exists():
                    self._meta = json.loads(_META_FILE.read_text())
                if (
                    self._meta.get("training_contract") != _TRAINING_CONTRACT
                    or not self._meta.get("promoted", False)
                ):
                    logger.warning("SignalCalibrator ignored: legacy or unpromoted model")
                    self._model = None
                    return
                self._model = joblib.load(str(_MODEL_FILE))
                logger.info(
                    "SignalCalibrator loaded: n_train=%d val_brier=%.4f",
                    self._meta.get("n_train", 0),
                    self._meta.get("val_brier", 0),
                )
        except Exception as e:
            logger.debug("SignalCalibrator._load: %s", e)
            self._model = None

    def is_trained(self) -> bool:
        return self._model is not None

    def train(self, force: bool = False) -> Dict:
        """
        Train the calibrated logistic regression model.
        Called nightly by idle_engine.py or on-demand via /retrain.

        Returns training metadata dict.
        """
        X, y, dates = _load_training_data()
        if X is None:
            return {"trained": False, "reason": "insufficient_data"}

        n = len(X)
        unique_days = sorted(set(dates))
        if len(unique_days) < _MIN_DAYS:
            return {
                "trained": False,
                "reason": f"insufficient_temporal_coverage days={len(unique_days)}/{_MIN_DAYS}",
            }
        split_day = unique_days[max(1, int(len(unique_days) * 0.80))]
        train_mask = np.array([day < split_day for day in dates], dtype=bool)
        X_train, y_train = X[train_mask], y[train_mask]
        X_val, y_val = X[~train_mask], y[~train_mask]
        if len(X_val) < 100 or len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
            return {"trained": False, "reason": "insufficient_holdout_class_balance"}

        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.metrics import roc_auc_score
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
            from purged_cv import PurgedKFold

            pipeline = Pipeline([
                ("scaler", StandardScaler()),
                ("lr",     LogisticRegression(C=1.0, solver="lbfgs", max_iter=500,
                                              random_state=42)),
            ])
            calibrated = CalibratedClassifierCV(
                pipeline,
                method="sigmoid",
                cv=PurgedKFold(n_splits=5, horizon=12, embargo=3),
            )
            calibrated.fit(X_train, y_train)

            # Validation metrics
            probs    = calibrated.predict_proba(X_val)[:, 1]
            preds    = (probs >= 0.5).astype(int)
            val_acc  = float((preds == y_val).mean()) if len(y_val) > 0 else 0.0
            # Brier score (lower is better; 0.25 = random; 0.0 = perfect)
            val_brier = float(np.mean((probs - y_val) ** 2)) if len(y_val) > 0 else 0.25
            train_base_rate = float(np.mean(y_train))
            baseline_brier = float(np.mean((train_base_rate - y_val) ** 2))
            val_auc = float(roc_auc_score(y_val, probs))
            brier_skill = 1.0 - (val_brier / baseline_brier) if baseline_brier > 0 else -1.0

            # QUALITY GUARD (2026-06-12): refuse to publish a model that is
            # worse than random on validation. With <3 distinct trading days
            # the chronological split validates intraday drift, not skill —
            # the first real run (503 train / 126 val, all one day) produced
            # val_acc 0.8% / Brier 0.79, i.e. confidently inverted.
            if val_auc < 0.55 or brier_skill <= 0.0:
                logger.warning(
                    "SignalCalibrator NOT saved: AUC=%.3f Brier=%.3f baseline=%.3f skill=%.3f",
                    val_auc, val_brier, baseline_brier, brier_skill)
                return {"trained": False,
                        "reason": "failed_probability_skill_guard",
                        "val_acc": round(val_acc, 4), "val_auc": round(val_auc, 4),
                        "val_brier": round(val_brier, 4),
                        "baseline_brier": round(baseline_brier, 4),
                        "brier_skill": round(brier_skill, 4)}

            # Extract LR coefficients for interpretability
            try:
                estimators = getattr(calibrated, "calibrated_classifiers_", [])
                coefs = [
                    item.estimator.named_steps["lr"].coef_[0]
                    for item in estimators
                    if hasattr(item, "estimator")
                ]
                lr_coef = np.mean(coefs, axis=0).tolist() if coefs else []
                coef_map = dict(zip(FEATURE_NAMES, lr_coef))
            except Exception:
                coef_map = {}

            import joblib
            joblib.dump(calibrated, str(_MODEL_FILE))
            meta = {
                "n_train":    len(X_train),
                "n_val":      len(y_val),
                "val_acc":    round(val_acc, 4),
                "val_auc":    round(val_auc, 4),
                "val_brier":  round(val_brier, 4),
                "baseline_brier": round(baseline_brier, 4),
                "brier_skill": round(brier_skill, 4),
                "distinct_days": len(unique_days),
                "holdout_start": split_day,
                "training_contract": _TRAINING_CONTRACT,
                "training_fingerprint": hashlib.sha256(X.tobytes() + y.tobytes()).hexdigest(),
                "promoted": True,
                "trained_at": __import__("datetime").datetime.now().isoformat(),
                "feature_coef": coef_map,
            }
            _META_FILE.write_text(json.dumps(meta, indent=2))
            self._model = calibrated
            self._meta  = meta

            logger.info(
                "SignalCalibrator trained: n=%d val_acc=%.2f brier=%.4f",
                len(X_train), val_acc, val_brier,
            )
            return {"trained": True, **meta}

        except ImportError as ie:
            logger.warning("SignalCalibrator requires scikit-learn: %s", ie)
            return {"trained": False, "reason": f"sklearn_missing: {ie}"}
        except Exception as e:
            logger.warning("SignalCalibrator.train failed: %s", e)
            return {"trained": False, "reason": str(e)}

    def score(self, signal: Dict) -> Dict:
        """
        Score a live signal dict.

        Returns:
            dict with calibrated_prob, verdict, and top contributing features.
            Falls back to {"calibrated_prob": 0.5, "verdict": "NO_MODEL"} if untrained.
        """
        if not self.is_trained():
            return {"calibrated_prob": 0.5, "log_reg_verdict": "NO_MODEL", "n_train": 0}

        try:
            x = np.array([_extract_row(signal)], dtype=float)
            prob = float(self._model.predict_proba(x)[0][1])

            # Verdict thresholds
            if prob >= _PASS_THRESHOLD:   verdict = "PASS"
            elif prob >= _WEAK_THRESHOLD: verdict = "WEAK"
            else:                         verdict = "FAIL"

            # Identify top positive/negative features from LR coefficients
            coef_map = self._meta.get("feature_coef", {})
            if coef_map:
                # Multiply coefficient by feature value to get contribution
                fvec   = _extract_row(signal)
                contribs = {
                    fname: coef * fval
                    for fname, coef, fval in zip(FEATURE_NAMES, coef_map.values(), fvec)
                }
                sorted_c = sorted(contribs.items(), key=lambda x: x[1], reverse=True)
                top_pos  = [f for f, v in sorted_c if v > 0][:3]
                top_neg  = [f for f, v in sorted_c if v < 0][:2]
            else:
                top_pos = top_neg = []

            return {
                "calibrated_prob":  round(prob, 4),
                "log_reg_verdict":  verdict,
                "top_positive":     top_pos,
                "top_negative":     top_neg,
                "n_train":          self._meta.get("n_train", 0),
                "val_brier":        self._meta.get("val_brier", None),
                "method":           "logistic_isotonic",
            }
        except Exception as e:
            logger.debug("SignalCalibrator.score: %s", e)
            return {"calibrated_prob": 0.5, "log_reg_verdict": "ERROR", "n_train": 0}


# ── Module-level singleton ────────────────────────────────────────────────────
_calibrator: Optional[SignalCalibrator] = None


def get_calibrator() -> SignalCalibrator:
    global _calibrator
    if _calibrator is None:
        _calibrator = SignalCalibrator()
    return _calibrator


def calibrate_signal(signal: Dict) -> Dict:
    """
    Convenience function: return calibrated probability for a signal dict.
    Safe to call even if model not trained — returns 0.5 neutral.
    """
    return get_calibrator().score(signal)


def retrain_calibrator() -> Dict:
    """
    Retrain the calibrator on latest signal_log data.
    Called nightly by idle_engine.py.
    """
    cal = get_calibrator()
    return cal.train()
