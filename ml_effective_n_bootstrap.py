"""
ml_effective_n_bootstrap.py — day-clustered bootstrap significance test for
the ML promotion AUC (2026-07-15, operator: "get external AI views on ML
audit... bugs... strategies").

The single most cross-confirmed critique across four independent external
AI second-opinions: purged K-fold CV (already implemented, purged_cv.py)
removes label-window LEAKAGE but does not make observations INDEPENDENT.
Many rows share the same trading day, the same underlying move, or
overlapping option-chain/expiry state — so N=15,000 raw rows can carry the
statistical power of a few hundred independent days. This module's own
docstring already documents seeing "AUC swing 0.5<->0.74 just by changing
the splitter" — that instability is exactly the symptom four AI systems
independently predicted from this mechanism.

This script builds real out-of-fold predictions (same PurgedKFold splitter,
same champion-selection tournament as the live ml_trainer.py/model_champion.py
pipeline — not a synthetic replica) tagged with __signal_date, then runs a
DAY-CLUSTERED block bootstrap: resample whole trading days with replacement
(never individual rows), recompute AUC each draw. If the resulting 95% CI
still excludes 0.5, the point-estimate AUC is real signal, not an artifact
of overstated effective sample size. Read-only diagnostic; does not change
the promotion gate itself.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import numpy as np

logger = logging.getLogger(__name__)

REPORT_FILE = Path("ml_effective_n_bootstrap_report.json")
N_BOOTSTRAP = 2000
RNG_SEED = 20260715


def _oof_predictions(X: np.ndarray, y: np.ndarray, dates: np.ndarray,
                      *, n_splits: int, horizon: int, embargo: int) -> Dict[str, np.ndarray]:
    """Out-of-fold (y_true, y_proba, date) for every row, using the SAME
    champion-selection tournament and PurgedKFold splitter as the live
    training pipeline (model_champion.compare_candidates / purged_cv.py)."""
    from sklearn.base import clone
    from purged_cv import PurgedKFold
    from model_champion import candidate_estimators

    estimators = candidate_estimators(len(y), X.shape[1])
    splitter = PurgedKFold(n_splits=n_splits, horizon=horizon, embargo=embargo)

    # Pick the champion by the SAME leaderboard logic as compare_candidates,
    # but keep the OOF arrays this time instead of discarding them.
    best_name, best_auc, best_oof = None, -1.0, None
    from sklearn.metrics import roc_auc_score
    for name, estimator in estimators.items():
        idx_all, obs_all, prob_all = [], [], []
        folds = 0
        for train, test in splitter.split(X, y):
            if len(np.unique(y[train])) < 2 or len(np.unique(y[test])) < 2:
                continue
            fitted = clone(estimator).fit(X[train], y[train])
            proba = np.clip(fitted.predict_proba(X[test])[:, 1], 1e-6, 1 - 1e-6)
            idx_all.extend(test.tolist())
            obs_all.extend(y[test].tolist())
            prob_all.extend(proba.tolist())
            folds += 1
        if folds < 2 or len(set(obs_all)) < 2:
            continue
        auc = float(roc_auc_score(obs_all, prob_all))
        if auc > best_auc:
            best_auc = auc
            best_name = name
            best_oof = (np.asarray(idx_all), np.asarray(obs_all), np.asarray(prob_all))

    if best_oof is None:
        return {}
    idx_arr, y_true, y_proba = best_oof
    return {
        "champion": best_name,
        "pooled_auc": best_auc,
        "y_true": y_true,
        "y_proba": y_proba,
        "date": np.asarray(dates)[idx_arr],
    }


def _day_clustered_bootstrap(y_true: np.ndarray, y_proba: np.ndarray, date: np.ndarray,
                              *, n_boot: int = N_BOOTSTRAP, seed: int = RNG_SEED) -> Dict[str, Any]:
    from sklearn.metrics import roc_auc_score

    unique_days = np.unique(date)
    n_days = len(unique_days)
    by_day = {d: np.where(date == d)[0] for d in unique_days}
    rng = np.random.default_rng(seed)

    aucs = []
    skipped = 0
    for _ in range(n_boot):
        draw_days = rng.choice(unique_days, size=n_days, replace=True)
        rows = np.concatenate([by_day[d] for d in draw_days])
        yt, yp = y_true[rows], y_proba[rows]
        if len(np.unique(yt)) < 2:
            skipped += 1
            continue
        aucs.append(float(roc_auc_score(yt, yp)))

    aucs = np.asarray(aucs)
    naive_auc = float(roc_auc_score(y_true, y_proba))
    ci_lo, ci_hi = np.percentile(aucs, [2.5, 97.5]) if len(aucs) else (float("nan"), float("nan"))
    p_above_half = float(np.mean(aucs > 0.5)) if len(aucs) else float("nan")

    return {
        "n_days": n_days,
        "n_rows": len(y_true),
        "rows_per_day_avg": round(len(y_true) / max(n_days, 1), 1),
        "naive_pooled_auc": round(naive_auc, 4),
        "bootstrap_draws": len(aucs),
        "bootstrap_skipped_single_class": skipped,
        "bootstrap_mean_auc": round(float(aucs.mean()), 4) if len(aucs) else None,
        "bootstrap_std_auc": round(float(aucs.std()), 4) if len(aucs) else None,
        "ci_95_low": round(float(ci_lo), 4),
        "ci_95_high": round(float(ci_hi), 4),
        "p_auc_above_half": round(p_above_half, 4),
        # Response-4's proposed promotion bar: Pr(AUC>0.50) >= 97.5% under
        # day-clustered bootstrap. Applied here as a read-only diagnostic.
        "clears_day_clustered_bar_975": bool(p_above_half >= 0.975) if p_above_half == p_above_half else False,
        "ci_excludes_half": bool(ci_lo > 0.5) if ci_lo == ci_lo else False,
    }


def run(days: int = 90, df: "Any | None" = None) -> Dict[str, Any]:
    """df: an already-built feature matrix (ml_feature_builder.build_feature_matrix
    output) to reuse — avoids a duplicate load when called from a pipeline that
    already has one in hand (post_market_ml.py). Builds its own if not given,
    for standalone/CLI use."""
    from ml_trainer import (
        _feature_cols, CV_FOLDS, PURGE_HORIZON, PURGE_EMBARGO, MIN_PROMOTION_AUC,
    )

    if df is None:
        from ml_feature_builder import build_feature_matrix
        df = build_feature_matrix(days=days, executed_only=False)
    if df.empty or "tb_outcome" not in df.columns:
        return {"error": "empty_feature_matrix_or_missing_tb_outcome"}
    feat_cols = _feature_cols(df)
    df_clean = df.dropna(subset=feat_cols)
    if len(df_clean) < 50 or "__signal_date" not in df_clean:
        return {"error": f"insufficient_clean_rows_or_no_date_col n={len(df_clean)}"}

    X = df_clean[feat_cols].values.astype(np.float32)
    y = df_clean["tb_outcome"].values.astype(int)
    dates = df_clean["__signal_date"].astype(str).values

    oof = _oof_predictions(X, y, dates, n_splits=CV_FOLDS,
                           horizon=PURGE_HORIZON, embargo=PURGE_EMBARGO)
    if not oof:
        return {"error": "no_champion_produced_valid_oof_folds"}

    boot = _day_clustered_bootstrap(oof["y_true"], oof["y_proba"], oof["date"])
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days_requested": days,
        "raw_rows": len(df_clean),
        "champion_model": oof["champion"],
        "current_promotion_auc_gate": MIN_PROMOTION_AUC,
        **boot,
    }
    verdict = (
        "REAL_SIGNAL" if report["ci_excludes_half"] and report["clears_day_clustered_bar_975"]
        else "INDISTINGUISHABLE_FROM_NOISE"
    )
    report["verdict"] = verdict
    REPORT_FILE.write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    rep = run()
    if rep.get("error"):
        print(f"error: {rep['error']}")
        return 1
    print(f"Champion: {rep['champion_model']} | naive pooled AUC: {rep['naive_pooled_auc']}")
    print(f"Raw rows: {rep['raw_rows']} | distinct trading days: {rep['n_days']} "
          f"({rep['rows_per_day_avg']} rows/day avg)")
    print(f"Day-clustered bootstrap ({rep['bootstrap_draws']} draws): "
          f"mean={rep['bootstrap_mean_auc']} std={rep['bootstrap_std_auc']}")
    print(f"95% CI: [{rep['ci_95_low']}, {rep['ci_95_high']}] "
          f"| P(AUC>0.5) = {rep['p_auc_above_half']}")
    print(f"Current promotion gate: AUC >= {rep['current_promotion_auc_gate']}")
    print(f"VERDICT: {rep['verdict']}")
    print(f"report -> {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
