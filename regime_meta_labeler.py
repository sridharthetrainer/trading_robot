"""
regime_meta_labeler.py — a 4-feature time/regime meta-labeler, tested against the
full 42-feature meta_labeler.py to see whether the 79-strategy confluence engine +
17 modifiers add anything beyond session-timing + macro context.

ORIGIN: an external second-opinion review (2026-07-22) flagged that meta_labeler's
own feature-importance table is dominated by hour_of_day/time_bucket_wt and
fii_cum_5d/india_vix, with only one actual confluence modifier (mtf_pivot_mod) near
the top. Ad-hoc verification that day: a 4-feature model (hour_of_day, india_vix,
fii_cum_5d, mtf_pivot_mod) matched-or-beat the full 42-feature model on the SAME
day-boundary holdout (simple AUC=0.691 vs full AUC=0.676; simple net_R=-0.049 vs
full net_R=-0.097 at threshold 0.55). This module makes that a real, repeatable,
disciplined check instead of a one-off script -- same honesty guardrails as
meta_labeler.py (time-ordered split, cost+slippage-inclusive R, min-sample/min-day
guards), plus an independent purged-K-fold CV cross-check on the same 4 features.

It REPORTS ONLY. Not wired into live trade gating. A high AUC here is not "new
edge" -- if it holds up, it means the SAME modest signal meta_labeler already found
is carried almost entirely by time/regime context, not by the 79 strategies -- a
simplification, not a discovery of profitability (net_R needs to clear COST_PCT
before this means anything live, exactly like meta_labeler.py).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MIN_SAMPLES = int(os.getenv("REGIME_META_MIN_SAMPLES", "200"))
MIN_DAYS    = int(os.getenv("REGIME_META_MIN_DAYS", "10"))
TEST_FRAC   = float(os.getenv("REGIME_META_TEST_FRAC", "0.30"))
COST_PCT    = float(os.getenv("EDGE_ANALYZER_COST_PCT", "0.12"))
NET_R_COL   = "tb_r_multiple_net"
THRESHOLDS  = (0.50, 0.55, 0.60, 0.65, 0.70)
MODEL_DIR   = "ml_models"
MODEL_FILE  = os.path.join(MODEL_DIR, "regime_meta_labeler.joblib")
REPORT_FILE = "regime_meta_labeler_report.json"

# Deliberately narrow: session-timing + macro context only, NO confluence score,
# NO strategy votes, NO modifiers except the one (mtf_pivot_mod) that showed real
# importance in the full model. This is the hypothesis under test, not a feature
# menu to expand -- adding features back defeats the point of the comparison.
FEATURES: List[str] = ["hour_of_day", "india_vix", "fii_cum_5d", "mtf_pivot_mod"]


def _load(days: int = 800):
    import sqlite3
    import pandas as pd
    from signal_quality import clean_signal_frame

    con = sqlite3.connect("signal_log.db")
    try:
        existing = {r[1] for r in con.execute("PRAGMA table_info(signal_log)").fetchall()}
        feats = [c for c in FEATURES if c in existing]
        extra = [c for c in ("side", "signal_date", "entry_price", "outcome_price",
                              NET_R_COL) if c in existing]
        cols = ", ".join(feats + extra + ["tb_label"])
        df = pd.read_sql(
            f"SELECT {cols} FROM signal_log "
            "WHERE tb_label IN (1,0,-1) AND training_eligible=1 "
            "AND stop_loss>0 AND target>0 AND rr>0 AND side IN ('BUY','SELL')", con)
    finally:
        con.close()

    if "entry_price" in df.columns and "outcome_price" in df.columns:
        df, _ = clean_signal_frame(df)
    if len(df) == 0:
        return df, feats
    df = df.sort_values("signal_date").reset_index(drop=True)
    df["meta_label"] = (df["tb_label"] == 1).astype(int)
    return df, feats


def analyze(days: int = 800) -> Dict[str, Any]:
    """Same discipline as meta_labeler.analyze(): time-ordered split, report
    precision AND cost+slippage-inclusive net_R at each threshold, plus an
    independent purged-K-fold CV AUC as a second check on the same split's number."""
    import numpy as np
    import pandas as pd
    df, feats = _load(days)
    if df is None or len(df) < MIN_SAMPLES:
        return {"error": f"insufficient labelled signals "
                         f"({0 if df is None else len(df)} < {MIN_SAMPLES})"}
    if len(feats) < len(FEATURES):
        return {"error": f"missing feature column(s): "
                         f"{sorted(set(FEATURES) - set(feats))}"}

    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import roc_auc_score
    except Exception as exc:
        return {"error": f"sklearn unavailable: {exc}"}

    X = df[feats].fillna(0.0).astype(float).values
    y = df["meta_label"].values

    dates = sorted(df["signal_date"].astype(str).unique())
    if len(dates) < MIN_DAYS:
        return {"error": f"insufficient temporal coverage: {len(dates)} distinct "
                         f"day(s) < {MIN_DAYS}",
                "distinct_days": len(dates), "n_rows": int(len(df))}
    split_date = dates[int(len(dates) * (1.0 - TEST_FRAC))]
    tr_mask = (df["signal_date"].astype(str) < split_date).values
    te_mask = ~tr_mask
    Xtr, Xte = X[tr_mask], X[te_mask]
    ytr, yte = y[tr_mask], y[te_mask]
    if len(Xte) < 50 or ytr.sum() < 10 or (len(ytr) - ytr.sum()) < 10:
        return {"error": "not enough class balance / test rows for a meta-model"}

    clf = RandomForestClassifier(
        n_estimators=300, max_depth=5, min_samples_leaf=50,
        class_weight="balanced", random_state=42, n_jobs=-1)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)[:, 1]

    base_rate = float(np.mean(yte))
    try:
        auc = float(roc_auc_score(yte, proba))
    except Exception:
        auc = float("nan")

    # Independent second check: purged K-fold CV over the WHOLE dataset (not just
    # the single day-split), same discipline used to originally confirm
    # meta_labeler's AUC wasn't a one-split artifact.
    purged_auc = None
    try:
        from purged_cv import purged_cv_score
        clf_cv = RandomForestClassifier(
            n_estimators=300, max_depth=5, min_samples_leaf=50,
            class_weight="balanced", random_state=42, n_jobs=-1)
        cv_rep = purged_cv_score(clf_cv, X, y, n_splits=5, horizon=12, embargo=5)
        purged_auc = cv_rep.get("mean")
    except Exception as exc:
        logger.debug("regime_meta_labeler purged CV skipped: %s", exc)

    # Third, primary check: CPCV over ALL C(6,2)=15 combinatorial paths from one
    # fixed 6-block partition (2026-07-22, external-review follow-up). A single
    # PurgedKFold config is itself just one arbitrary partition -- this repo saw
    # the mean AUC swing ~0.09 just from changing n_splits/horizon/embargo, with
    # no way to tell if that was a better config or just which rows landed in
    # which fold. CPCV reports the full distribution across many paths from the
    # same partition, which is the number to actually trust over either single
    # purged_cv_score reading above.
    cpcv_rep = None
    try:
        from purged_cv import cpcv_score
        clf_cpcv = RandomForestClassifier(
            n_estimators=300, max_depth=5, min_samples_leaf=50,
            class_weight="balanced", random_state=42, n_jobs=-1)
        cpcv_rep = cpcv_score(clf_cpcv, X, y, n_groups=6, n_test_groups=2,
                               horizon=15, embargo=18)
    except Exception as exc:
        logger.debug("regime_meta_labeler CPCV skipped: %s", exc)

    net_r_te = (pd.to_numeric(df[NET_R_COL], errors="coerce").values[te_mask]
                if NET_R_COL in df.columns else None)
    baseline_net_r = float(np.nanmean(net_r_te)) if net_r_te is not None else None

    by_threshold = []
    for t in THRESHOLDS:
        sel = proba >= t
        n_sel = int(sel.sum())
        prec = float(np.mean(yte[sel])) if n_sel else 0.0
        avg_net_r = (float(np.nanmean(net_r_te[sel])) if n_sel and net_r_te is not None
                     else None)
        by_threshold.append({
            "threshold": t,
            "n_selected": n_sel,
            "coverage": round(n_sel / len(yte), 4),
            "precision": round(prec, 4),
            "lift_vs_base": round(prec - base_rate, 4),
            "avg_net_r": round(avg_net_r, 4) if avg_net_r is not None else None,
        })

    imp = sorted(zip(feats, clf.feature_importances_), key=lambda kv: -kv[1])

    precision_usable = [b for b in by_threshold
                         if b["lift_vs_base"] > 0.02 and b["coverage"] >= 0.10
                         and b["n_selected"] >= 20]
    usable = [b for b in precision_usable if b["avg_net_r"] is None or b["avg_net_r"] > 0]
    best = max(usable, key=lambda b: b["lift_vs_base"]) if usable else None
    best_precision_only = (max(precision_usable, key=lambda b: b["lift_vs_base"])
                            if precision_usable and not best else None)

    # Compare against the full 42-feature model's most recently written report, if
    # present -- context only, never gates anything here.
    full_model_auc = None
    try:
        import json
        full_rep = json.loads(open("meta_labeler_report.json").read())
        full_model_auc = full_rep.get("auc")
    except Exception:
        pass

    rep = {
        "features": feats,
        "n_total": int(len(df)),
        "n_train": int(len(Xtr)),
        "n_test": int(len(Xte)),
        "base_win_rate": round(base_rate, 4),
        "baseline_net_r": round(baseline_net_r, 4) if baseline_net_r is not None else None,
        "auc": round(auc, 4),
        "purged_cv_auc": round(purged_auc, 4) if purged_auc is not None else None,
        "cpcv": cpcv_rep,
        "full_model_auc_for_comparison": full_model_auc,
        "cost_pct": COST_PCT,
        "by_threshold": by_threshold,
        "feature_importances": [{"feature": f, "importance": round(float(i), 4)}
                                 for f, i in imp],
        "best_threshold": best,
    }
    cmp_note = ""
    if full_model_auc is not None:
        delta = auc - float(full_model_auc)
        cmp_note = (f" (full 42-feature model AUC={full_model_auc:.3f}, "
                    f"delta={delta:+.3f} -- {'simple model holds up' if delta > -0.03 else 'full model ahead'})")
    cpcv_note = ""
    if cpcv_rep and cpcv_rep.get("n_paths_used"):
        cpcv_note = (f" CPCV over {cpcv_rep['n_paths_used']}/{cpcv_rep['n_paths_total']} "
                     f"paths: mean={cpcv_rep['mean']:.3f} median={cpcv_rep['median']:.3f} "
                     f"iqr={cpcv_rep['iqr']:.3f} min={cpcv_rep['min']:.3f} -- trust this "
                     f"over the single day-split AUC above, which is one partition of many.")
    if auc < 0.55:
        rep["conclusion"] = (
            f"AUC={auc:.3f} — no signal in time/regime context alone.{cmp_note}{cpcv_note}")
    elif best:
        rep["conclusion"] = (
            f"AUC={auc:.3f}. Gating at "
            f"P(win)>={best['threshold']} lifts precision {base_rate:.1%}→"
            f"{best['precision']:.1%} AND clears cost (avg net_R={best['avg_net_r']:+.3f} "
            f"vs baseline {baseline_net_r:+.3f}).{cmp_note}{cpcv_note} PROMISING — still "
            "requires a further locked-holdout pass before any live gating.")
    elif best_precision_only:
        rep["conclusion"] = (
            f"AUC={auc:.3f}. Gating at P(win)>={best_precision_only['threshold']} lifts "
            f"precision but avg net_R stays NEGATIVE at every threshold (best "
            f"{best_precision_only['avg_net_r']:+.3f} vs baseline {baseline_net_r:+.3f})."
            f"{cmp_note}{cpcv_note} Reduces losses, does not create profit. REJECTED for "
            "live gating on cost-adjusted evidence.")
    else:
        rep["conclusion"] = (
            f"AUC={auc:.3f} but no threshold clears base rate by >2% at usable "
            f"coverage.{cmp_note}{cpcv_note} Report-only.")
    return rep


def train_and_save(days: int = 800) -> Dict[str, Any]:
    rep = analyze(days)
    if "error" in rep:
        return rep
    if rep.get("auc", 0.0) < 0.55 or not rep.get("best_threshold"):
        rep["model_saved"] = False
        rep["model_save_reason"] = "no_threshold_clears_precision_lift_and_net_cost"
        return rep
    try:
        import joblib
        from sklearn.ensemble import RandomForestClassifier
        df, feats = _load(days)
        X = df[feats].fillna(0.0).astype(float).values
        y = df["meta_label"].values
        clf = RandomForestClassifier(
            n_estimators=300, max_depth=5, min_samples_leaf=50,
            class_weight="balanced", random_state=42, n_jobs=-1).fit(X, y)
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump({"model": clf, "features": feats}, MODEL_FILE)
        rep["model_saved"] = MODEL_FILE
    except Exception as exc:
        rep["model_saved"] = f"FAILED: {exc}"
    return rep


def format_report(rep: Dict[str, Any]) -> str:
    if "error" in rep:
        return f"regime-meta-labeler: {rep['error']}"
    lines = [
        "🕐 <b>Regime Meta-Labeling Report</b> (4-feature: hour/VIX/FII/pivot)",
        f"train={rep['n_train']} test={rep['n_test']} "
        f"base_win={rep['base_win_rate']:.1%} AUC={rep['auc']} "
        f"purged_cv_auc={rep.get('purged_cv_auc')}",
    ]
    cpcv = rep.get("cpcv")
    if cpcv and cpcv.get("n_paths_used"):
        lines.append(f"cpcv: mean={cpcv['mean']:.3f} median={cpcv['median']:.3f} "
                     f"std={cpcv['std']:.3f} iqr={cpcv['iqr']:.3f} "
                     f"[{cpcv['min']:.3f}, {cpcv['max']:.3f}] "
                     f"over {cpcv['n_paths_used']}/{cpcv['n_paths_total']} paths")
    lines += [
        "",
        "  thresh  cover  precision  lift    net_R",
    ]
    for b in rep["by_threshold"]:
        net_r_str = f"{b['avg_net_r']:+.3f}" if b.get("avg_net_r") is not None else "n/a"
        lines.append(f"  {b['threshold']:.2f}   {b['coverage']:.0%}    "
                     f"{b['precision']:.1%}    {b['lift_vs_base']:+.1%}   {net_r_str}")
    lines += ["", "feature importances: " + ", ".join(
        f"{f['feature']}={f['importance']}" for f in rep["feature_importances"])]
    lines += ["", f"<b>{rep['conclusion']}</b>"]
    return "\n".join(lines)


def run_nightly(send_telegram: bool = False) -> Dict[str, Any]:
    """Entry point for the post-market pipeline. Report-only — never gates live
    trades. Writes regime_meta_labeler_report.json."""
    import json
    from pathlib import Path
    rep = train_and_save()
    logger.info("regime-meta-labeler: %s", rep.get("conclusion", rep.get("error")))
    try:
        Path(REPORT_FILE).write_text(json.dumps(rep, indent=2, default=str))
    except Exception as exc:
        logger.debug("regime_meta_labeler_report write: %s", exc)
    return rep
