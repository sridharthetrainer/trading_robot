"""
mfe_threshold_labeler.py -- ML: predict MFE threshold, not final win/loss
(RESEARCH_QUEUE_2026-09-13.md item 4).

meta_labeler.py's existing model predicts final WIN/LOSS (tb_label==1).
This module trains the SAME feature set (reused from meta_labeler._FEATURES,
not duplicated) against a DIFFERENT label: did the trade's max favorable
excursion (max_favorable_move, 94%+ populated, no new instrumentation
needed) reach a given R threshold, regardless of how it closed. Motivated
by the 2026-09-20 finding that entry quality clearly separates winners
from losers early (median MFE 0.85R vs 0.234R) even though final outcome
gating (meta_labeler) shows no economically usable edge.

Methodology difference from meta_labeler.py, DELIBERATE: that module
selects its best P(win) gating threshold by scanning MULTIPLE thresholds'
performance directly on its held-out test set -- which is itself a mild
form of threshold-shopping against the holdout (picking the best of 5
options after seeing holdout results). This module avoids that: a genuine
3-way, date-ordered split --
  TRAIN (60%)      -- fit the classifier only.
  VALIDATION (20%) -- select the single best P(threshold-hit)>=cutoff
                      value from THRESHOLDS using ONLY this slice's
                      avg_net_r, before the holdout is ever touched.
  HOLDOUT (20%)    -- apply the ALREADY-CHOSEN cutoff exactly once, report
                      whatever comes out. Never used for model or cutoff
                      selection.
Also reports purged CPCV (same cpcv_score helper meta_labeler.py uses) on
the combined train+validation portion only -- the holdout is never fed to
CPCV either, keeping it genuinely untouched until the single final check.

Report-only. Does NOT gate or promote anything live under any
circumstance, regardless of result, per the item's own instruction --
this measures whether the approach has any economically usable signal at
all, that decision is separate and requires its own full validation gate
plus explicit sign-off even if this comes back positive.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from meta_labeler import _FEATURES, NET_R_COL, COST_PCT

logger = logging.getLogger(__name__)

MIN_SAMPLES = int(os.getenv("MFE_MIN_SAMPLES", "200"))
MIN_DAYS = int(os.getenv("MFE_MIN_DAYS", "10"))
TRAIN_FRAC = 0.60
VAL_FRAC = 0.20            # remaining 0.20 is the locked holdout
GATE_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70)
MFE_TARGETS = (0.5, 0.75, 1.0)   # R-multiple MFE thresholds to predict


def _load(mfe_target: float, days: int = 800):
    """Same feature set and cleaning as meta_labeler._load, but the label is
    (max_favorable_move >= mfe_target) instead of final win/loss."""
    import sqlite3
    from signal_quality import clean_signal_frame

    con = sqlite3.connect("signal_log.db")
    try:
        existing = {r[1] for r in con.execute("PRAGMA table_info(signal_log)").fetchall()}
        feats = [c for c in _FEATURES if c in existing]
        extra = [c for c in ("side", "signal_date", "entry_price", "outcome_price",
                              "max_favorable_move", NET_R_COL) if c in existing]
        cols = ", ".join(feats + extra)
        df = pd.read_sql(
            f"SELECT {cols} FROM signal_log "
            "WHERE training_eligible=1 AND stop_loss>0 AND target>0 AND rr>0 "
            "AND side IN ('BUY','SELL') AND max_favorable_move IS NOT NULL", con)
    finally:
        con.close()

    if "entry_price" in df.columns and "outcome_price" in df.columns:
        df, _ = clean_signal_frame(df)
    if len(df) == 0:
        return df, feats
    df = df.sort_values("signal_date").reset_index(drop=True)
    df["side_buy"] = (df["side"] == "BUY").astype(int)
    df["mfe_label"] = (df["max_favorable_move"] >= mfe_target).astype(int)
    return df, feats + ["side_buy"]


def analyze_mfe_threshold(mfe_target: float, days: int = 800) -> Dict[str, Any]:
    df, feats = _load(mfe_target, days)
    if df is None or len(df) < MIN_SAMPLES:
        return {"error": f"insufficient labelled signals "
                         f"({0 if df is None else len(df)} < {MIN_SAMPLES})",
                "mfe_target": mfe_target}

    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import roc_auc_score
    except Exception as exc:
        return {"error": f"sklearn unavailable: {exc}", "mfe_target": mfe_target}

    dates = sorted(df["signal_date"].astype(str).unique())
    if len(dates) < MIN_DAYS:
        return {"error": f"insufficient temporal coverage: {len(dates)} day(s) "
                         f"< {MIN_DAYS}", "mfe_target": mfe_target,
                "distinct_days": len(dates), "n_rows": int(len(df))}

    train_cut = dates[int(len(dates) * TRAIN_FRAC)]
    val_cut = dates[int(len(dates) * (TRAIN_FRAC + VAL_FRAC))]
    sd = df["signal_date"].astype(str)
    tr_mask = (sd < train_cut).values
    val_mask = ((sd >= train_cut) & (sd < val_cut)).values
    ho_mask = (sd >= val_cut).values

    X = df[feats].fillna(0.0).astype(float).values
    y = df["mfe_label"].values
    net_r = pd.to_numeric(df[NET_R_COL], errors="coerce").values if NET_R_COL in df.columns else None

    Xtr, Xval, Xho = X[tr_mask], X[val_mask], X[ho_mask]
    ytr, yval, yho = y[tr_mask], y[val_mask], y[ho_mask]
    if len(Xval) < 30 or len(Xho) < 30 or ytr.sum() < 10 or (len(ytr) - ytr.sum()) < 10:
        return {"error": "not enough class balance / split rows for a model",
                "mfe_target": mfe_target,
                "n_train": int(len(Xtr)), "n_val": int(len(Xval)), "n_holdout": int(len(Xho))}

    clf = RandomForestClassifier(
        n_estimators=300, max_depth=5, min_samples_leaf=50,
        class_weight="balanced", random_state=42, n_jobs=-1)
    clf.fit(Xtr, ytr)

    # --- CPCV on train+validation ONLY (holdout never enters this) ---
    cpcv_rep = None
    try:
        from purged_cv import cpcv_score
        trval_mask = tr_mask | val_mask
        clf_cpcv = RandomForestClassifier(
            n_estimators=300, max_depth=5, min_samples_leaf=50,
            class_weight="balanced", random_state=42, n_jobs=-1)
        cpcv_rep = cpcv_score(clf_cpcv, X[trval_mask], y[trval_mask],
                               n_groups=6, n_test_groups=2, horizon=15, embargo=18)
    except Exception as exc:
        logger.debug("mfe_threshold_labeler CPCV skipped: %s", exc)

    # --- select cutoff on VALIDATION ONLY, never touching holdout ---
    proba_val = clf.predict_proba(Xval)[:, 1]
    val_net_r = net_r[val_mask] if net_r is not None else None
    val_by_threshold = []
    for t in GATE_THRESHOLDS:
        sel = proba_val >= t
        n_sel = int(sel.sum())
        avg_net_r = float(np.nanmean(val_net_r[sel])) if n_sel and val_net_r is not None else None
        val_by_threshold.append({"threshold": t, "n_selected": n_sel,
                                  "coverage": round(n_sel / len(yval), 4) if len(yval) else 0.0,
                                  "avg_net_r": round(avg_net_r, 4) if avg_net_r is not None else None})
    usable_val = [b for b in val_by_threshold if b["n_selected"] >= 20 and b["avg_net_r"] is not None]
    chosen = max(usable_val, key=lambda b: b["avg_net_r"]) if usable_val else None
    chosen_cutoff = chosen["threshold"] if chosen else None

    # --- apply the ALREADY-CHOSEN cutoff exactly once to the locked holdout ---
    proba_ho = clf.predict_proba(Xho)[:, 1]
    ho_net_r = net_r[ho_mask] if net_r is not None else None
    baseline_ho_net_r = float(np.nanmean(ho_net_r)) if ho_net_r is not None else None
    holdout_result = None
    if chosen_cutoff is not None:
        sel = proba_ho >= chosen_cutoff
        n_sel = int(sel.sum())
        avg_net_r_ho = float(np.nanmean(ho_net_r[sel])) if n_sel and ho_net_r is not None else None
        precision_ho = float(np.mean(yho[sel])) if n_sel else None
        holdout_result = {
            "cutoff_used": chosen_cutoff, "n_selected": n_sel,
            "coverage": round(n_sel / len(yho), 4) if len(yho) else 0.0,
            "precision_mfe_hit": round(precision_ho, 4) if precision_ho is not None else None,
            "avg_net_r": round(avg_net_r_ho, 4) if avg_net_r_ho is not None else None,
        }

    try:
        auc_ho = float(roc_auc_score(yho, proba_ho))
    except Exception:
        auc_ho = float("nan")

    imp = sorted(zip(feats, clf.feature_importances_), key=lambda kv: -kv[1])[:12]

    rep = {
        "mfe_target": mfe_target,
        "n_total": int(len(df)), "n_train": int(len(Xtr)), "n_val": int(len(Xval)),
        "n_holdout": int(len(Xho)), "n_features": len(feats),
        "holdout_auc": round(auc_ho, 4),
        "cpcv_trainval": cpcv_rep,
        "baseline_holdout_net_r": round(baseline_ho_net_r, 4) if baseline_ho_net_r is not None else None,
        "val_by_threshold": val_by_threshold,
        "chosen_cutoff_from_validation": chosen_cutoff,
        "holdout_result_LOCKED": holdout_result,
        "top_features": [{"feature": f, "importance": round(float(i), 4)} for f, i in imp],
    }

    if chosen_cutoff is None:
        rep["conclusion"] = ("No validation-side threshold cleared the usability bar "
                              "(n_selected>=20 with a real avg_net_r) -- nothing to apply "
                              "to holdout. Report-only, no gating signal found.")
    elif holdout_result and holdout_result["avg_net_r"] is not None and baseline_ho_net_r is not None:
        beats_baseline = holdout_result["avg_net_r"] > baseline_ho_net_r
        positive = holdout_result["avg_net_r"] > 0
        rep["conclusion"] = (
            f"Cutoff P(MFE>={mfe_target}R)>={chosen_cutoff} chosen on validation only. "
            f"Applied ONCE to locked holdout: avg_net_r={holdout_result['avg_net_r']:+.4f} "
            f"vs baseline (take every signal) {baseline_ho_net_r:+.4f} "
            f"({'beats' if beats_baseline else 'does NOT beat'} baseline, "
            f"{'positive' if positive else 'still NEGATIVE'} in absolute terms). "
            "NOT promoted regardless of this result -- report only, per this item's "
            "own instruction.")
    else:
        rep["conclusion"] = "Holdout result incomplete (insufficient selected rows or missing net_r)."
    return rep


def main() -> int:
    import json
    from pathlib import Path

    report: Dict[str, Any] = {"targets": {}}
    for target in MFE_TARGETS:
        print(f"\n=== MFE >= {target}R threshold model ===")
        rep = analyze_mfe_threshold(target)
        report["targets"][str(target)] = rep
        if rep.get("error"):
            print(f"  ERROR: {rep['error']}")
            continue
        print(f"  n_total={rep['n_total']} train={rep['n_train']} val={rep['n_val']} holdout={rep['n_holdout']}")
        print(f"  holdout AUC={rep['holdout_auc']}")
        if rep.get("cpcv_trainval"):
            c = rep["cpcv_trainval"]
            print(f"  CPCV(train+val) mean={c.get('mean'):.3f} median={c.get('median'):.3f} "
                  f"min={c.get('min'):.3f} n_paths={c.get('n_paths_used')}/{c.get('n_paths_total')}")
        print(f"  chosen_cutoff (validation-selected)={rep.get('chosen_cutoff_from_validation')}")
        print(f"  holdout_result_LOCKED={rep.get('holdout_result_LOCKED')}")
        print(f"  baseline_holdout_net_r={rep.get('baseline_holdout_net_r')}")
        print(f"  CONCLUSION: {rep.get('conclusion')}")

    try:
        Path("mfe_threshold_labeler_report.json").write_text(json.dumps(report, indent=2, default=str))
    except Exception as exc:
        print(f"report write failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
