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
import hashlib
import logging
import os
import pickle
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
TRAINING_CONTRACT  = "all_generated_signals_v4_causal_representations"
MIN_PROMOTION_SAMPLES = int(os.getenv("ML_MIN_PROMOTION_SAMPLES", "5000"))
MIN_PROMOTION_DAYS = int(os.getenv("ML_MIN_PROMOTION_DAYS", "15"))
MIN_PROMOTION_AUC = float(os.getenv("ML_MIN_PROMOTION_AUC", "0.55"))
MAX_SYMBOL_WORKERS = max(1, int(os.getenv("ML_MAX_SYMBOL_WORKERS", "4")))

# Metadata columns — excluded from training features
_OUTCOME_ONLY_COLS = {
    "tb_outcome", "tb_label", "tb_r_multiple", "tb_r_multiple_net",
    "outcome_price", "outcome_time", "exit_price", "exit_time",
    "gross_pnl", "net_pnl", "realized_pnl", "estimated_costs",
    "labelled_at", "tb_used_custom_barrier",
}
_META_COLS = _OUTCOME_ONLY_COLS | {
    "__symbol", "__signal_date", "__strategy", "__side", "__log_time",
}


def _feature_cols(df: "pd.DataFrame") -> List[str]:
    return [c for c in df.columns if c not in _META_COLS]


def _train_model(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: List[str],
    label: str = "cross_symbol",
    net_returns: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Train a GradientBoostingClassifier with TimeSeriesSplit cross-validation.
    Returns dict with model, cv_scores, feature_importances.
    """
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from model_champion import compare_candidates

    tournament = compare_candidates(
        X, y, n_splits=CV_FOLDS, horizon=PURGE_HORIZON, embargo=PURGE_EMBARGO,
        net_returns=net_returns,
    )
    pipe = tournament.get("estimator")
    if pipe is None:
        raise ValueError("No candidate produced at least two valid purged folds")

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
    clf = pipe.named_steps.get("clf") if hasattr(pipe, "named_steps") else pipe
    selector = pipe.named_steps.get("select") if hasattr(pipe, "named_steps") else None
    if hasattr(clf, "feature_importances_"):
        importances = np.asarray(clf.feature_importances_, dtype=float)
    elif hasattr(clf, "coef_"):
        importances = np.abs(np.asarray(clf.coef_, dtype=float)[0])
        total = float(importances.sum())
        importances = importances / total if total > 0 else importances
    else:
        importances = np.zeros(len(feature_names), dtype=float)
    if selector is not None and hasattr(selector, "get_support"):
        support = np.asarray(selector.get_support(), dtype=bool)
        full_importances = np.zeros(len(feature_names), dtype=float)
        if int(support.sum()) == len(importances):
            full_importances[support] = importances
        importances = full_importances
    feat_imp = sorted(
        zip(feature_names, importances),
        key=lambda x: x[1], reverse=True
    )

    # Class balance report
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos

    # MDA (permutation) importance under PurgedKFold — leakage-free, model-agnostic.
    # Unlike GBM impurity importance, MDA reveals features that are NOISE (importance
    # ~0 / negative): candidates to prune ("reduce, don't add"). Fit-once-per-fold,
    # so it's cheap; guard with a flag in case of tiny data.
    mda = []
    if os.getenv("ML_COMPUTE_MDA", "true").lower() == "true":
        try:
            from model_quality import mda_importance
            mda = mda_importance(pipe, X, y, feature_names, n_splits=CV_FOLDS,
                                 horizon=PURGE_HORIZON, embargo=PURGE_EMBARGO,
                                 n_repeats=int(os.getenv("ML_MDA_REPEATS", "3")))
        except Exception as exc:
            logger.debug("[%s] MDA importance skipped: %s", label, exc, exc_info=True)

    logger.info(
        "[%s] CV AUC(purged)=%.3f±%.3f  (TSCV=%.3f) | n=%d (W=%d L=%d) | top: %s (%.3f)",
        label,
        cv_auc_primary, cv_std_primary, cv_scores.mean(),
        len(y), n_pos, n_neg,
        feat_imp[0][0] if feat_imp else "—",
        feat_imp[0][1] if feat_imp else 0,
    )

    # Calibrate the selected classifier using the same purged/embargoed split
    # contract. This probability is consumed by the after-cost economics gate.
    calibrated_model = pipe
    calibration_method = "none"
    try:
        from sklearn.calibration import CalibratedClassifierCV
        from purged_cv import PurgedKFold
        calibrated_model = CalibratedClassifierCV(
            pipe, method="sigmoid",
            cv=PurgedKFold(CV_FOLDS, PURGE_HORIZON, PURGE_EMBARGO),
        ).fit(X, y)
        calibration_method = "sigmoid_purged_cv"
    except Exception as exc:
        logger.warning("[%s] probability calibration unavailable: %s", label, exc)

    champion_metrics = next(
        (row for row in tournament.get("leaderboard", [])
         if row.get("name") == tournament.get("champion")),
        {},
    )
    return {
        "label":               label,
        "model":               calibrated_model,
        "champion_algorithm":  tournament.get("champion", ""),
        "candidate_count":     tournament.get("candidate_count", 0),
        "candidate_leaderboard": tournament.get("leaderboard", []),
        "profit_utility":      champion_metrics.get("utility", {}),
        "purged_brier":        champion_metrics.get("brier"),
        "purged_baseline_brier": champion_metrics.get("baseline_brier"),
        "purged_brier_skill":  champion_metrics.get("brier_skill"),
        "probability_calibration": calibration_method,
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
        # leakage-free permutation importance; noise_features = MDA <= 0 (prunable)
        "mda_importances":     [{"feature": m["feature"], "importance": round(m["importance"], 5),
                                 "std": round(m["std"], 5)} for m in mda],
        "noise_features":      [m["feature"] for m in mda if m["importance"] <= 0.0],
        "training_contract":   TRAINING_CONTRACT,
        "trained_at":          datetime.now().isoformat(),
    }


def _save_model(result: Dict[str, Any]) -> Path:
    """Persist model + metadata to ml_models/<label>_model.pkl."""
    MODEL_DIR.mkdir(exist_ok=True)
    safe_label = result["label"].replace(" ", "_").replace("/", "_")
    path = MODEL_DIR / f"{safe_label}_model.pkl"
    with open(path, "wb") as f:
        pickle.dump(result, f, protocol=4)
    return path


def _training_fingerprint(df: "pd.DataFrame", feature_names: List[str]) -> str:
    """Stable lineage hash over labels, selected features, and sample identity."""
    columns = [
        col for col in (
            "__symbol", "__signal_date", "__strategy", "__side", "__log_time",
            "tb_outcome", *feature_names,
        ) if col in df.columns
    ]
    if not columns:
        return ""
    normalized = df[columns].copy()
    row_hashes = pd.util.hash_pandas_object(normalized, index=True).to_numpy()
    digest = hashlib.sha256()
    digest.update(TRAINING_CONTRACT.encode("utf-8"))
    digest.update("|".join(columns).encode("utf-8"))
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def _model_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _train_one_symbol(
    symbol: str, Xs: np.ndarray, ys: np.ndarray, feat_cols: List[str],
    sym_days: int, sym_n: int, fingerprint: str,
    net_returns: Optional[np.ndarray] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Runs in a worker process. Each symbol's training is fully independent
    (its own data slice, its own model), so this is embarrassingly parallel
    -- moved out of train_all()'s serial loop 2026-07-27 after that loop
    alone grew to 2228 of 2537s total nightly runtime (~25s/symbol,
    consistent across the observed history), driven by per_symbol_models
    count growing 63->102 in 4 days as more symbols cross MIN_SYMBOL_SAMPLES.
    Returns (symbol, None) on any failure -- one bad symbol must never lose
    the whole nightly pipeline (2026-07-11 incident, same reasoning as the
    prior serial loop's try/except/continue)."""
    try:
        sym_result = _train_model(Xs, ys, feat_cols, label=symbol, net_returns=net_returns)
    except Exception as exc:
        logger.warning("per-symbol model %s skipped: %s", symbol, exc)
        return symbol, None
    sym_result["distinct_days"] = sym_days
    sym_result["promoted"] = bool(
        sym_n >= MIN_PROMOTION_SAMPLES
        and sym_days >= MIN_PROMOTION_DAYS
        and sym_result.get("cv_method") == "purged_kfold"
        and float(sym_result.get("cv_auc_mean") or 0) >= MIN_PROMOTION_AUC
        and float(sym_result.get("purged_brier_skill") or -1) > 0
        and sym_result.get("probability_calibration") == "sigmoid_purged_cv"
        and (
            not (sym_result.get("profit_utility") or {}).get("available")
            or float((sym_result.get("profit_utility") or {}).get("best_avg_net_r") or 0) > 0
        )
    )
    sym_result["training_data_fingerprint"] = fingerprint
    sym_result["selected_features"] = list(feat_cols)
    return symbol, sym_result


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

    # Supervised feature selection lives INSIDE each sklearn pipeline so every
    # CV fold selects using training rows only. Pre-selecting against the full
    # target leaks holdout labels into model design.

    X_all   = df_clean[feat_cols].values.astype(np.float32)
    y_all   = df_clean["tb_outcome"].values.astype(int)
    net_returns_all = None
    for ret_col in ("tb_r_multiple_net", "tb_r_multiple"):
        if ret_col in df_clean.columns:
            net_returns_all = df_clean[ret_col].values.astype(np.float32)
            break
    training_fingerprint = _training_fingerprint(df_clean, feat_cols)

    saved_paths = []
    results: Dict[str, Any] = {
        "per_symbol": {},
        "timestamp":  datetime.now().isoformat(),
    }

    # ── Cross-symbol model ────────────────────────────────────────────────────
    logger.info("Training cross-symbol model on %d samples", len(df_clean))
    cross_result = _train_model(
        X_all, y_all, feat_cols, label="cross_symbol", net_returns=net_returns_all
    )
    distinct_days = int(df_clean["__signal_date"].astype(str).nunique()) if "__signal_date" in df_clean else 0
    cross_result["distinct_days"] = distinct_days
    cross_result["promoted"] = bool(
        len(df_clean) >= MIN_PROMOTION_SAMPLES
        and distinct_days >= MIN_PROMOTION_DAYS
        and cross_result.get("cv_method") == "purged_kfold"
        and float(cross_result.get("cv_auc_mean") or 0) >= MIN_PROMOTION_AUC
        and float(cross_result.get("purged_brier_skill") or -1) > 0
        and cross_result.get("probability_calibration") == "sigmoid_purged_cv"
        and (
            not (cross_result.get("profit_utility") or {}).get("available")
            or float((cross_result.get("profit_utility") or {}).get("best_avg_net_r") or 0) > 0
        )
    )
    cross_result["training_data_fingerprint"] = training_fingerprint
    cross_result["selected_features"] = list(feat_cols)
    results["cross_symbol"] = {k: v for k, v in cross_result.items() if k != "model"}
    if cross_result["promoted"]:
        saved_paths.append(str(_save_model(cross_result)))

    # ── Per-symbol models ─────────────────────────────────────────────────────
    # 2026-07-11: a single sparse symbol (enough rows to pass MIN_SYMBOL_SAMPLES
    # but too few distinct days for 2 valid purged folds) raised out of
    # _train_model and killed the ENTIRE nightly pipeline (learned filters,
    # forward-holdout, autopsy all lost for the day — caught by the
    # job-catchup retry, rc=1 twice). Per-symbol models are optional extras;
    # skip the symbol, keep going -- preserved below via _train_one_symbol's
    # own try/except, now running in a worker process instead of inline.
    if "__symbol" in df_clean.columns:
        sym_counts = df_clean["__symbol"].value_counts()
        eligible: List[tuple] = []
        for symbol, count in sym_counts.items():
            if count < MIN_SYMBOL_SAMPLES:
                continue
            sym_df = df_clean[df_clean["__symbol"] == symbol]
            Xs = sym_df[feat_cols].values.astype(np.float32)
            ys = sym_df["tb_outcome"].values.astype(int)
            sym_returns = None
            for ret_col in ("tb_r_multiple_net", "tb_r_multiple"):
                if ret_col in sym_df.columns:
                    sym_returns = sym_df[ret_col].values.astype(np.float32)
                    break
            if len(np.unique(ys)) < 2:
                continue   # only one class — can't train
            sym_days = int(sym_df["__signal_date"].astype(str).nunique()) if "__signal_date" in sym_df else 0
            fingerprint = _training_fingerprint(sym_df, feat_cols)
            eligible.append((symbol, Xs, ys, sym_days, len(sym_df), fingerprint, sym_returns))

        # Each eligible symbol's training is fully independent (own data
        # slice, own model) -- parallelize across processes rather than
        # training them one at a time. Leaves 2 cores free for the live bot,
        # which shares this box (post_market_ml is guarded to post-market
        # hours, but the box itself isn't exclusively idle).
        max_workers = max(1, min(len(eligible), (os.cpu_count() or 4) - 2, MAX_SYMBOL_WORKERS))
        if eligible:
            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_train_one_symbol, symbol, Xs, ys, feat_cols,
                                sym_days, sym_n, fingerprint, sym_returns): symbol
                    for symbol, Xs, ys, sym_days, sym_n, fingerprint, sym_returns in eligible
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        _, sym_result = future.result()
                    except Exception as exc:
                        logger.warning("per-symbol model %s worker failed: %s", symbol, exc)
                        continue
                    if sym_result is None:
                        continue
                    results["per_symbol"][symbol] = {
                        k: v for k, v in sym_result.items() if k != "model"
                    }
                    if sym_result["promoted"]:
                        saved_paths.append(str(_save_model(sym_result)))

    results["saved_paths"] = saved_paths
    results["training_contract"] = TRAINING_CONTRACT
    results["training_data_fingerprint"] = training_fingerprint
    results["model_artifacts"] = [
        {"path": path, "sha256": _model_sha256(path)} for path in saved_paths
    ]

    # Save importances to JSON for human review
    imp_path = MODEL_DIR / "feature_importances.json"
    MODEL_DIR.mkdir(exist_ok=True)
    with open(imp_path, "w") as f:
        json.dump({
            "timestamp": results["timestamp"],
            "cross_symbol_top20": cross_result["feature_importances"][:20],
            "cross_symbol_cv_auc": cross_result["cv_auc_mean"],
            "training_contract": TRAINING_CONTRACT,
            "training_data_fingerprint": training_fingerprint,
            "model_artifacts": results["model_artifacts"],
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
    if model_result.get("training_contract") != TRAINING_CONTRACT:
        return {
            "win_prob": 0.5, "model_used": model_used, "available": False,
            "reason": "legacy_training_contract",
        }
    if not model_result.get("promoted", False):
        return {
            "win_prob": 0.5, "model_used": model_used, "available": False,
            "reason": "model_not_promoted",
        }

    try:
        pipe      = model_result["model"]
        feat_cols = list(model_result.get("selected_features") or [])
        if not feat_cols:
            return {
                "win_prob": 0.5, "model_used": model_used, "available": False,
                "reason": "ordered_feature_contract_missing",
            }

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
