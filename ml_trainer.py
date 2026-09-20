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
TRAINING_CONTRACT  = "all_generated_signals_v5_locked_forward_holdout"
MIN_PROMOTION_SAMPLES = int(os.getenv("ML_MIN_PROMOTION_SAMPLES", "5000"))
MIN_PROMOTION_DAYS = int(os.getenv("ML_MIN_PROMOTION_DAYS", "15"))
MIN_PROMOTION_AUC = float(os.getenv("ML_MIN_PROMOTION_AUC", "0.55"))
MAX_SYMBOL_WORKERS = max(1, int(os.getenv("ML_MAX_SYMBOL_WORKERS", "4")))
HOLDOUT_RATIO = float(os.getenv("ML_LOCKED_HOLDOUT_RATIO", "0.20"))
HOLDOUT_MIN_DAYS = int(os.getenv("ML_LOCKED_HOLDOUT_MIN_DAYS", "3"))
HOLDOUT_MIN_SELECTED = int(os.getenv("ML_LOCKED_HOLDOUT_MIN_SELECTED", "20"))

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


def _usable_feature_cols(
    df: "pd.DataFrame", feature_cols: List[str]
) -> Tuple[List[str], Dict[str, str]]:
    """Remove features that cannot carry stable predictive information.

    This is target-independent, so it cannot leak outcome labels. Supervised
    selection remains inside each CV pipeline.
    """
    usable: List[str] = []
    removed: Dict[str, str] = {}
    near_constant_threshold = float(
        os.getenv("ML_NEAR_CONSTANT_RATIO", "0.99")
    )
    for name in feature_cols:
        series = pd.to_numeric(df[name], errors="coerce")
        non_null = series.dropna()
        if non_null.empty:
            removed[name] = "all_null"
            continue
        if int(non_null.nunique(dropna=True)) <= 1:
            removed[name] = "constant"
            continue
        dominant_ratio = float(non_null.value_counts(normalize=True, dropna=True).iloc[0])
        if dominant_ratio >= near_constant_threshold:
            removed[name] = "near_constant"
            continue
        usable.append(name)
    return usable, removed


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
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.base import clone
    from sklearn.metrics import roc_auc_score
    from model_champion import compare_candidates

    tournament = compare_candidates(
        X, y, n_splits=CV_FOLDS, horizon=PURGE_HORIZON, embargo=PURGE_EMBARGO,
        net_returns=net_returns,
    )
    pipe = tournament.get("estimator")
    if pipe is None:
        evaluated = [
            row for row in tournament.get("leaderboard", [])
            if row.get("eligible")
        ]
        if evaluated:
            # This is a valid experimental result, not a pipeline crash. Keep
            # the rejected leaderboard so reports explain why no model exists.
            return {
                "label": label,
                "model": None,
                "champion_algorithm": "",
                "candidate_count": tournament.get("candidate_count", 0),
                "candidate_leaderboard": tournament.get("leaderboard", []),
                "profit_utility": {},
                "purged_brier": None,
                "purged_baseline_brier": None,
                "purged_brier_skill": None,
                "probability_calibration": "none",
                "cv_auc_mean": None,
                "cv_auc_std": None,
                "cv_method": "purged_kfold",
                "n_samples": len(y),
                "n_positive": int(y.sum()),
                "n_negative": int(len(y) - y.sum()),
                "feature_importances": [],
                "mda_importances": [],
                "noise_features": [],
                "training_contract": TRAINING_CONTRACT,
                "trained_at": datetime.now().isoformat(),
                "rejected_reason": "no_candidate_with_positive_calibrated_after_cost_utility",
            }
        raise ValueError("no_candidate_produced_two_valid_purged_folds")

    # TimeSeriesSplit (legacy, kept for comparison) — respects order but does NOT
    # purge overlapping triple-barrier label windows → leaks.
    tscv = TimeSeriesSplit(n_splits=CV_FOLDS)
    legacy_scores = []
    invalid_tscv_folds = 0
    for train_idx, test_idx in tscv.split(X):
        if len(np.unique(y[train_idx])) < 2 or len(np.unique(y[test_idx])) < 2:
            invalid_tscv_folds += 1
            continue
        estimator = clone(pipe)
        estimator.fit(X[train_idx], y[train_idx])
        legacy_scores.append(float(roc_auc_score(
            y[test_idx], estimator.predict_proba(X[test_idx])[:, 1]
        )))
    cv_scores = np.asarray(legacy_scores, dtype=float)

    # Purged K-Fold + embargo — the TRUSTWORTHY CV (removes label-window leakage).
    # This is the promotion gate. Never fall back to leaky or degenerate folds.
    purged = {"mean": float("nan"), "std": float("nan"), "n_splits_used": 0, "folds": []}
    try:
        from purged_cv import purged_cv_score
        purged = purged_cv_score(pipe, X, y, n_splits=CV_FOLDS,
                                 horizon=PURGE_HORIZON, embargo=PURGE_EMBARGO,
                                 scoring="roc_auc")
    except Exception as exc:
        logger.debug("purged CV unavailable; experiment rejected: %s", exc, exc_info=True)
    _purged_ok = purged.get("n_splits_used", 0) >= 2 and purged["mean"] == purged["mean"]
    if not _purged_ok:
        raise ValueError(
            f"invalid_purged_cv: only {purged.get('n_splits_used', 0)} valid folds"
        )
    cv_auc_primary = float(purged["mean"])
    cv_std_primary = float(purged["std"])

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
        cv_auc_primary, cv_std_primary,
        float(cv_scores.mean()) if len(cv_scores) else float("nan"),
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
        "cv_auc_mean_tscv":    round(float(cv_scores.mean()), 4) if len(cv_scores) else None,
        "cv_auc_purged":       round(float(purged["mean"]), 4) if _purged_ok else None,
        "cv_method":           "purged_kfold",
        "invalid_tscv_folds":  invalid_tscv_folds,
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


def _split_locked_forward_days(df: "pd.DataFrame") -> Tuple["pd.DataFrame", "pd.DataFrame"]:
    """Reserve the latest complete signal days before any model selection.

    Row-count splits can put the same trading session in both partitions.  A
    day boundary is the minimum defensible lock for this cross-symbol dataset.
    """
    if "__signal_date" not in df.columns:
        return df.copy(), df.iloc[0:0].copy()
    days = sorted(str(day) for day in df["__signal_date"].dropna().unique())
    if len(days) <= HOLDOUT_MIN_DAYS:
        return df.copy(), df.iloc[0:0].copy()
    n_holdout = max(HOLDOUT_MIN_DAYS, int(np.ceil(len(days) * HOLDOUT_RATIO)))
    n_holdout = min(n_holdout, len(days) - 1)
    holdout_days = set(days[-n_holdout:])
    is_holdout = df["__signal_date"].astype(str).isin(holdout_days)
    return df.loc[~is_holdout].copy(), df.loc[is_holdout].copy()


def _evaluate_locked_forward_holdout(
    result: Dict[str, Any], X: np.ndarray, y: np.ndarray,
    net_returns: Optional[np.ndarray], *, distinct_days: int,
) -> Dict[str, Any]:
    """Evaluate once at the development-selected threshold, fail closed.

    Promotion requires the one-sided 95% lower confidence bound of mean
    after-cost R to be positive, not just a noisy positive point estimate.
    """
    out: Dict[str, Any] = {
        "available": False, "passed": False, "rows": int(len(y)),
        "days": int(distinct_days), "threshold": None, "selected": 0,
        "coverage": 0.0, "avg_net_r": None, "net_r_lcb95": None,
        "auc": None, "brier_skill": None,
    }
    utility = result.get("profit_utility") or {}
    threshold = utility.get("best_threshold")
    if (
        len(y) == 0 or distinct_days < HOLDOUT_MIN_DAYS
        or net_returns is None or threshold is None
    ):
        return out
    try:
        probability = np.asarray(result["model"].predict_proba(X), dtype=float)[:, 1]
        actual = np.asarray(y, dtype=int)
        returns = np.asarray(net_returns, dtype=float)
        selected = probability >= float(threshold)
        n_selected = int(selected.sum())
        coverage = n_selected / max(len(actual), 1)
        out.update({
            "available": True,
            "threshold": float(threshold),
            "selected": n_selected,
            "coverage": round(float(coverage), 6),
        })
        if len(np.unique(actual)) >= 2:
            from sklearn.metrics import roc_auc_score
            out["auc"] = round(float(roc_auc_score(actual, probability)), 6)
        dev_rate = float(result.get("n_positive", 0)) / max(int(result.get("n_samples", 0)), 1)
        brier = float(np.mean((probability - actual) ** 2))
        baseline_brier = float(np.mean((dev_rate - actual) ** 2))
        out["brier_skill"] = round(1.0 - brier / baseline_brier, 6) if baseline_brier > 0 else -1.0
        if n_selected < HOLDOUT_MIN_SELECTED:
            return out
        selected_returns = returns[selected]
        avg_r = float(np.mean(selected_returns))
        if n_selected > 1:
            standard_error = float(np.std(selected_returns, ddof=1) / np.sqrt(n_selected))
            lower_bound = avg_r - 1.645 * standard_error
        else:
            lower_bound = float("-inf")
        out["avg_net_r"] = round(avg_r, 6)
        out["net_r_lcb95"] = round(float(lower_bound), 6)
        out["passed"] = bool(
            lower_bound > 0.0 and float(out["brier_skill"] or -1.0) > 0.0
        )
    except Exception as exc:
        out["reason"] = f"evaluation_failed:{type(exc).__name__}"
    return out


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
    holdout_x: Optional[np.ndarray] = None,
    holdout_y: Optional[np.ndarray] = None,
    holdout_returns: Optional[np.ndarray] = None,
    holdout_days: int = 0,
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
    if holdout_x is not None and holdout_y is not None:
        sym_result["locked_forward_holdout"] = _evaluate_locked_forward_holdout(
            sym_result, holdout_x, holdout_y, holdout_returns,
            distinct_days=holdout_days,
        )
    sym_result["distinct_days"] = sym_days
    sym_result["promoted"] = bool(
        sym_n >= MIN_PROMOTION_SAMPLES
        and sym_days >= MIN_PROMOTION_DAYS
        and sym_result.get("cv_method") == "purged_kfold"
        and float(sym_result.get("cv_auc_mean") or 0) >= MIN_PROMOTION_AUC
        and float(sym_result.get("purged_brier_skill") or -1) > 0
        and sym_result.get("probability_calibration") == "sigmoid_purged_cv"
        # Fail closed: promotion requires a PROVEN positive after-cost R, not
        # merely "we couldn't prove it's negative". Previously this bypassed
        # the check entirely when utility data was thin/unavailable.
        and bool((sym_result.get("profit_utility") or {}).get("available"))
        and float((sym_result.get("profit_utility") or {}).get("best_avg_net_r") or 0) > 0
        and bool((sym_result.get("locked_forward_holdout") or {}).get("passed"))
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

    feat_cols, removed_features = _usable_feature_cols(df, _feature_cols(df))
    min_usable_features = max(1, int(os.getenv("ML_MIN_USABLE_FEATURES", "2")))
    if len(feat_cols) < min_usable_features:
        return {
            "error": (
                f"Only {len(feat_cols)} usable features — need at least "
                f"{min_usable_features}"
            ),
            "removed_features": removed_features,
        }
    df_clean  = df.dropna(subset=feat_cols)

    if len(df_clean) < 20:
        return {"error": f"Only {len(df_clean)} clean rows — need at least 20"}

    # Supervised feature selection lives INSIDE each sklearn pipeline so every
    # CV fold selects using training rows only. Pre-selecting against the full
    # target leaks holdout labels into model design.

    development_df, locked_holdout_df = _split_locked_forward_days(df_clean)
    X_all   = development_df[feat_cols].values.astype(np.float32)
    y_all   = development_df["tb_outcome"].values.astype(int)
    net_returns_all = None
    for ret_col in ("tb_r_multiple_net", "tb_r_multiple"):
        if ret_col in development_df.columns:
            net_returns_all = development_df[ret_col].values.astype(np.float32)
            break
    training_fingerprint = _training_fingerprint(df_clean, feat_cols)

    saved_paths = []
    results: Dict[str, Any] = {
        "per_symbol": {},
        "timestamp":  datetime.now().isoformat(),
        "removed_features": removed_features,
        "usable_features": list(feat_cols),
    }

    # ── Cross-symbol model ────────────────────────────────────────────────────
    # Wrapped in try/except for the same reason _train_one_symbol is (2026-07-11
    # incident): a data condition that yields <2 valid purged folds (e.g. a
    # thin/one-class window) must not abort the entire nightly pipeline. Was
    # previously unguarded, unlike the per-symbol loop below.
    logger.info(
        "Training cross-symbol model on %d development samples; %d locked holdout rows",
        len(development_df), len(locked_holdout_df),
    )
    try:
        cross_result = _train_model(
            X_all, y_all, feat_cols, label="cross_symbol", net_returns=net_returns_all
        )
    except Exception as exc:
        logger.warning("cross-symbol model skipped: %s", exc)
        cross_result = None
    distinct_days = int(df_clean["__signal_date"].astype(str).nunique()) if "__signal_date" in df_clean else 0
    if cross_result is not None:
        holdout_returns = None
        for ret_col in ("tb_r_multiple_net", "tb_r_multiple"):
            if ret_col in locked_holdout_df.columns:
                holdout_returns = locked_holdout_df[ret_col].values.astype(np.float32)
                break
        holdout_days = int(locked_holdout_df["__signal_date"].astype(str).nunique()) if "__signal_date" in locked_holdout_df else 0
        cross_result["locked_forward_holdout"] = _evaluate_locked_forward_holdout(
            cross_result,
            locked_holdout_df[feat_cols].values.astype(np.float32),
            locked_holdout_df["tb_outcome"].values.astype(int),
            holdout_returns,
            distinct_days=holdout_days,
        )
        cross_result["distinct_days"] = distinct_days
        cross_result["promoted"] = bool(
            len(df_clean) >= MIN_PROMOTION_SAMPLES
            and distinct_days >= MIN_PROMOTION_DAYS
            and cross_result.get("cv_method") == "purged_kfold"
            and float(cross_result.get("cv_auc_mean") or 0) >= MIN_PROMOTION_AUC
            and float(cross_result.get("purged_brier_skill") or -1) > 0
            and cross_result.get("probability_calibration") == "sigmoid_purged_cv"
            # Fail closed: see matching comment in _train_one_symbol.
            and bool((cross_result.get("profit_utility") or {}).get("available"))
            and float((cross_result.get("profit_utility") or {}).get("best_avg_net_r") or 0) > 0
            and bool((cross_result.get("locked_forward_holdout") or {}).get("passed"))
        )
        cross_result["training_data_fingerprint"] = training_fingerprint
        cross_result["selected_features"] = list(feat_cols)
        results["cross_symbol"] = {k: v for k, v in cross_result.items() if k != "model"}
        if cross_result["promoted"]:
            saved_paths.append(str(_save_model(cross_result)))
    else:
        results["cross_symbol"] = {"error": "cross_symbol_training_failed"}

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
            # Do not spend minutes training research artifacts that are
            # mathematically ineligible for promotion.  The old 100-row gate
            # launched dozens of model tournaments even though the promotion
            # contract requires 5,000 rows; none could ever be saved or used.
            if count < max(MIN_SYMBOL_SAMPLES, MIN_PROMOTION_SAMPLES):
                continue
            sym_df = df_clean[df_clean["__symbol"] == symbol]
            sym_development, sym_holdout = _split_locked_forward_days(sym_df)
            Xs = sym_development[feat_cols].values.astype(np.float32)
            ys = sym_development["tb_outcome"].values.astype(int)
            sym_returns = None
            for ret_col in ("tb_r_multiple_net", "tb_r_multiple"):
                if ret_col in sym_development.columns:
                    sym_returns = sym_development[ret_col].values.astype(np.float32)
                    break
            sym_holdout_returns = None
            for ret_col in ("tb_r_multiple_net", "tb_r_multiple"):
                if ret_col in sym_holdout.columns:
                    sym_holdout_returns = sym_holdout[ret_col].values.astype(np.float32)
                    break
            if len(np.unique(ys)) < 2:
                continue   # only one class — can't train
            sym_days = int(sym_df["__signal_date"].astype(str).nunique()) if "__signal_date" in sym_df else 0
            sym_holdout_days = int(sym_holdout["__signal_date"].astype(str).nunique()) if "__signal_date" in sym_holdout else 0
            fingerprint = _training_fingerprint(sym_df, feat_cols)
            eligible.append((
                symbol, Xs, ys, sym_days, len(sym_df), fingerprint, sym_returns,
                sym_holdout[feat_cols].values.astype(np.float32),
                sym_holdout["tb_outcome"].values.astype(int),
                sym_holdout_returns, sym_holdout_days,
            ))

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
                                sym_days, sym_n, fingerprint, sym_returns,
                                holdout_x, holdout_y, holdout_returns,
                                holdout_days): symbol
                    for (symbol, Xs, ys, sym_days, sym_n, fingerprint, sym_returns,
                         holdout_x, holdout_y, holdout_returns, holdout_days) in eligible
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
            "cross_symbol_top20": cross_result["feature_importances"][:20] if cross_result else [],
            "cross_symbol_cv_auc": cross_result["cv_auc_mean"] if cross_result else None,
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
    if not bool((model_result.get("locked_forward_holdout") or {}).get("passed")):
        return {
            "win_prob": 0.5, "model_used": model_used, "available": False,
            "reason": "locked_forward_holdout_not_passed",
        }
    utility = model_result.get("profit_utility") or {}
    if (
        not utility.get("available")
        or float(utility.get("best_avg_net_r") or 0.0) <= 0.0
    ):
        return {
            "win_prob": 0.5, "model_used": model_used, "available": False,
            "reason": "missing_positive_profit_utility",
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
