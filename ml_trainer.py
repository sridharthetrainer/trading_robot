"""
ml_trainer.py  —  Gradient-boosted ML model for signal quality prediction.

Trains one cross-symbol model + per-symbol models (if >= MIN_SYMBOL_SAMPLES).
Uses sklearn GradientBoostingClassifier (no external ML deps beyond sklearn).
Saves models to ml_models/ and returns feature importances.

Usage:
    from ml_trainer import train_all
    result = train_all(df)   # df from ml_feature_builder.build_feature_matrix()
"""
from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_DIR          = Path(os.getenv("ML_MODEL_DIR", "ml_models"))
# Per-symbol models need n>=100 to be reliable at 60:1 feature-to-sample ratio.
# With ~10 signals/symbol/day the current threshold of 30 trains on noise.
MIN_SYMBOL_SAMPLES = int(os.getenv("ML_MIN_SYMBOL_SAMPLES", "100"))
N_ESTIMATORS       = int(os.getenv("ML_N_ESTIMATORS", "200"))
# depth=3 (was 4): each split doubles overfitting risk at low sample sizes
MAX_DEPTH          = int(os.getenv("ML_MAX_DEPTH", "3"))
LEARNING_RATE      = float(os.getenv("ML_LEARNING_RATE", "0.05"))
CV_FOLDS           = int(os.getenv("ML_CV_FOLDS", "5"))
# Purged-CV params: triple-barrier labels span a forward horizon, so plain CV
# leaks. Horizon ≈ triple-barrier max_bars; embargo adds a serial-correlation gap.
PURGE_HORIZON      = int(os.getenv("ML_PURGE_HORIZON", "12"))
PURGE_EMBARGO      = int(os.getenv("ML_PURGE_EMBARGO", "3"))

# Metadata columns — excluded from training features
_META_COLS = {"tb_outcome", "tb_label", "__symbol", "__signal_date",
              "__strategy", "__side", "__log_time"}


def _feature_cols(df: "pd.DataFrame") -> List[str]:
    return [c for c in df.columns if c not in _META_COLS]


def _train_model(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: List[str],
    label: str = "cross_symbol",
) -> Dict[str, Any]:
    """
    Train a GradientBoostingClassifier with TimeSeriesSplit cross-validation.
    Returns dict with model, cv_scores, feature_importances.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    GradientBoostingClassifier(
            n_estimators  = N_ESTIMATORS,
            max_depth     = MAX_DEPTH,
            learning_rate = LEARNING_RATE,
            subsample     = 0.8,
            random_state  = 42,
        )),
    ])

    # TimeSeriesSplit (legacy, kept for comparison) — respects order but does NOT
    # purge overlapping triple-barrier label windows → leaks.
    tscv = TimeSeriesSplit(n_splits=CV_FOLDS)
    cv_scores = cross_val_score(pipe, X, y, cv=tscv, scoring="roc_auc")

    # Purged K-Fold + embargo — the TRUSTWORTHY CV (removes label-window leakage).
    # This becomes the promotion gate; fall back to TimeSeriesSplit if it can't run.
    purged = {"mean": float("nan"), "std": float("nan"), "n_splits_used": 0, "folds": []}
    try:
        from purged_cv import purged_cv_score
        purged = purged_cv_score(pipe, X, y, n_splits=CV_FOLDS,
                                 horizon=PURGE_HORIZON, embargo=PURGE_EMBARGO,
                                 scoring="roc_auc")
    except Exception as exc:
        logger.debug("purged CV unavailable, using TimeSeriesSplit: %s", exc, exc_info=True)
    _purged_ok = purged.get("n_splits_used", 0) >= 2 and purged["mean"] == purged["mean"]
    cv_auc_primary = float(purged["mean"]) if _purged_ok else float(cv_scores.mean())
    cv_std_primary = float(purged["std"]) if _purged_ok else float(cv_scores.std())

    pipe.fit(X, y)

    # Feature importances from the GBM (not affected by scaling)
    clf        = pipe.named_steps["clf"]
    importances = clf.feature_importances_
    feat_imp   = sorted(
        zip(feature_names, importances),
        key=lambda x: x[1], reverse=True
    )

    # Class balance report
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos

    logger.info(
        "[%s] CV AUC(purged)=%.3f±%.3f  (TSCV=%.3f) | n=%d (W=%d L=%d) | top: %s (%.3f)",
        label,
        cv_auc_primary, cv_std_primary, cv_scores.mean(),
        len(y), n_pos, n_neg,
        feat_imp[0][0] if feat_imp else "—",
        feat_imp[0][1] if feat_imp else 0,
    )

    return {
        "label":               label,
        "model":               pipe,
        # Gate uses the PURGED (leakage-free) AUC; legacy TSCV kept for comparison.
        "cv_auc_mean":         round(cv_auc_primary, 4),
        "cv_auc_std":          round(cv_std_primary, 4),
        "cv_auc_mean_tscv":    round(float(cv_scores.mean()), 4),
        "cv_auc_purged":       round(float(purged["mean"]), 4) if _purged_ok else None,
        "cv_method":           "purged_kfold" if _purged_ok else "timeseries_split",
        "n_samples":           len(y),
        "n_positive":          n_pos,
        "n_negative":          n_neg,
        "feature_importances": [(f, round(float(imp), 5)) for f, imp in feat_imp],
    }


def _save_model(result: Dict[str, Any]) -> Path:
    """Persist model + metadata to ml_models/<label>_model.pkl."""
    MODEL_DIR.mkdir(exist_ok=True)
    safe_label = result["label"].replace(" ", "_").replace("/", "_")
    path = MODEL_DIR / f"{safe_label}_model.pkl"
    with open(path, "wb") as f:
        pickle.dump(result, f, protocol=4)
    return path


def _load_model(label: str) -> Optional[Dict[str, Any]]:
    """Load a previously saved model, or None if not found."""
    safe_label = label.replace(" ", "_").replace("/", "_")
    path = MODEL_DIR / f"{safe_label}_model.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception as exc:
        logger.debug("model load failed (%s): %s", path, exc)
        return None


def train_all(df: "pd.DataFrame") -> Dict[str, Any]:
    """
    Train cross-symbol model + per-symbol models.

    Returns dict:
      "cross_symbol": {cv_auc_mean, cv_auc_std, feature_importances, ...}
      "per_symbol":   {"NIFTY": {...}, "BANKNIFTY": {...}, ...}
      "saved_paths":  list of model file paths
      "timestamp":    ISO timestamp
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas required: pip install pandas")

    if df.empty or "tb_outcome" not in df.columns:
        return {"error": "Empty DataFrame or missing tb_outcome column"}

    feat_cols = _feature_cols(df)
    df_clean  = df.dropna(subset=feat_cols)

    if len(df_clean) < 20:
        return {"error": f"Only {len(df_clean)} clean rows — need at least 20"}

    # Feature selection: reduce 60+ features to top 20 to prevent overfitting.
    # With 5-fold CV, each training fold has ~400 samples; 10:1 sample-to-feature
    # ratio requires no more than 40 features — target 20 for safety margin.
    try:
        from ml_feature_builder import select_top_features as _sel
        feat_cols = _sel(df_clean, feat_cols, n=20)
        logger.info("Feature selection: %d features selected", len(feat_cols))
    except Exception as _fe:
        logger.debug("Feature selection unavailable, using all %d: %s", len(feat_cols), _fe)

    X_all   = df_clean[feat_cols].values.astype(np.float32)
    y_all   = df_clean["tb_outcome"].values.astype(int)

    saved_paths = []
    results: Dict[str, Any] = {
        "per_symbol": {},
        "timestamp":  datetime.now().isoformat(),
    }

    # ── Cross-symbol model ────────────────────────────────────────────────────
    logger.info("Training cross-symbol model on %d samples", len(df_clean))
    cross_result = _train_model(X_all, y_all, feat_cols, label="cross_symbol")
    results["cross_symbol"] = {k: v for k, v in cross_result.items() if k != "model"}
    saved_paths.append(str(_save_model(cross_result)))

    # ── Per-symbol models ─────────────────────────────────────────────────────
    if "__symbol" in df_clean.columns:
        sym_counts = df_clean["__symbol"].value_counts()
        for symbol, count in sym_counts.items():
            if count < MIN_SYMBOL_SAMPLES:
                continue
            sym_df = df_clean[df_clean["__symbol"] == symbol]
            Xs = sym_df[feat_cols].values.astype(np.float32)
            ys = sym_df["tb_outcome"].values.astype(int)
            if len(np.unique(ys)) < 2:
                continue   # only one class — can't train
            sym_result = _train_model(Xs, ys, feat_cols, label=symbol)
            results["per_symbol"][symbol] = {
                k: v for k, v in sym_result.items() if k != "model"
            }
            saved_paths.append(str(_save_model(sym_result)))

    results["saved_paths"] = saved_paths

    # Save importances to JSON for human review
    imp_path = MODEL_DIR / "feature_importances.json"
    MODEL_DIR.mkdir(exist_ok=True)
    with open(imp_path, "w") as f:
        json.dump({
            "timestamp": results["timestamp"],
            "cross_symbol_top20": cross_result["feature_importances"][:20],
            "cross_symbol_cv_auc": cross_result["cv_auc_mean"],
        }, f, indent=2)

    logger.info("Training complete. %d models saved.", len(saved_paths))
    return results


def predict(
    signal_features: Dict[str, float],
    symbol:          str = "",
) -> Dict[str, Any]:
    """
    Predict win probability for a live signal.
    Tries per-symbol model first, falls back to cross-symbol.
    Returns {"win_prob": float, "model_used": str, "available": bool}
    """
    model_result = None
    model_used   = "none"

    if symbol:
        model_result = _load_model(symbol)
        if model_result:
            model_used = f"per_symbol:{symbol}"

    if model_result is None:
        model_result = _load_model("cross_symbol")
        if model_result:
            model_used = "cross_symbol"

    if model_result is None:
        return {"win_prob": 0.5, "model_used": "none", "available": False}

    try:
        pipe      = model_result["model"]
        feat_cols = [f for f, _ in model_result["feature_importances"]]

        # Build feature vector in same column order as training
        x_vec = np.array(
            [float(signal_features.get(fc, 0.0)) for fc in feat_cols],
            dtype=np.float32,
        ).reshape(1, -1)

        proba    = pipe.predict_proba(x_vec)[0]
        win_prob = float(proba[1]) if len(proba) > 1 else 0.5

        return {
            "win_prob":   round(win_prob, 4),
            "model_used": model_used,
            "available":  True,
            "cv_auc":     model_result.get("cv_auc_mean", 0),
        }
    except Exception as exc:
        logger.debug("predict failed: %s", exc)
        return {"win_prob": 0.5, "model_used": "error", "available": False}
