"""
meta_labeler.py — López de Prado meta-labeling on signal_log.

The PRIMARY model is the existing 57-strategy confluence engine: it picks the SIDE
(BUY/SELL) and a confluence score, logged per candidate in signal_log with a
triple-barrier outcome (`tb_label`: +1 win / -1 loss / 0 timeout). This module is
the SECONDARY (meta) model: given only the features available AT SIGNAL TIME, it
predicts P(the primary signal is a WIN) so we can

  1. GATE — skip signals whose P(win) is too low (precision ↑, false positives ↓), and
  2. SIZE — scale position by confidence,

i.e. separate SIDE (primary) from SIZE/BET (meta), per Advances in Financial ML.

It REPORTS ONLY. It is NOT wired into live trade gating — promotion requires beating
the baseline out-of-sample and a locked-holdout pass, same discipline as
strategy_edge_analyzer / modifier_edge_analyzer. Meta-labeling improves PRECISION on
an existing signal set; it CANNOT manufacture edge from edgeless primary signals — if
the primary has no edge, expect AUC ~0.5 and no precision lift, and that is a valid,
honest result.

Honesty guardrails:
  - TIME-ORDERED split (train older, test newer) — never random CV on non-IID data.
  - corrupt rows dropped (signal_quality.clean_signal_frame).
  - min-sample guard; shallow forest (resists overfit).
  - reports AUC + precision/coverage at several thresholds vs the BASELINE win rate
    (what taking every signal gives) — lift must also beat round-trip cost to matter.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MIN_SAMPLES = int(os.getenv("META_MIN_SAMPLES", "200"))
MIN_DAYS    = int(os.getenv("META_MIN_DAYS", "10"))        # distinct trading days required
# NOTE: 10 distinct days with ~40 features is statistically THIN — treat early
# results as provisional/directional, not validated edge. A locked-holdout pass is
# still required before any live gating. Raise via META_MIN_DAYS for more rigour.
TEST_FRAC   = float(os.getenv("META_TEST_FRAC", "0.30"))   # newest fraction = OOS
COST_PCT    = float(os.getenv("EDGE_ANALYZER_COST_PCT", "0.12"))  # round-trip %, context
THRESHOLDS  = (0.50, 0.55, 0.60, 0.65, 0.70)
MODEL_DIR   = "ml_models"
MODEL_FILE  = os.path.join(MODEL_DIR, "meta_labeler.joblib")
REPORT_FILE = "meta_labeler_report.json"

# Signal-time features only (NO outcome columns — would leak the label).
_FEATURES: List[str] = [
    # confluence
    "score", "raw_score", "n_agree", "n_conflict",
    # 17 instrumented modifiers
    "bhav_delivery", "cross_asset_mod", "participant_mod", "expiry_mod", "sip_boost",
    "bulk_deal_mod", "theta_mod", "rebal_mod", "news_mod", "mtf_pivot_mod",
    "time_bucket_wt", "gex_mod", "skew_mod", "whale_mod", "sr_level_mod",
    "pivot_boss_mod", "oi_mod",
    # other score inputs
    "ai_score", "rl_bias", "weinstein_mod", "volume_ratio",
    # market context
    "india_vix", "iv_percentile", "pcr_atm", "fii_net_cash", "fii_fut_ratio",
    "fii_cum_5d", "client_ce_pct", "sensex_nifty_div",
    # structure / pivots
    "above_weekly_pvt", "above_monthly_pvt", "pct_from_w_r1", "pct_from_m_r1",
    "expiry_dte",
    # time / symbol
    "hour_of_day", "day_of_week", "sector_code",
]


def _load(days: int = 800):
    """Labelled signals + a binary meta-label (1=win, else 0). Time-ordered.
    Reuses signal_quality to drop scale-corrupt rows."""
    import sqlite3
    import pandas as pd
    from signal_quality import clean_signal_frame

    con = sqlite3.connect("signal_log.db")
    try:
        existing = {r[1] for r in con.execute("PRAGMA table_info(signal_log)").fetchall()}
        feats = [c for c in _FEATURES if c in existing]
        extra = [c for c in ("side", "signal_date", "entry_price", "outcome_price") if c in existing]
        cols = ", ".join(feats + extra + ["tb_label"])
        df = pd.read_sql(
            f"SELECT {cols} FROM signal_log "
            "WHERE tb_label IN (1,0,-1) AND side IN ('BUY','SELL')", con)
    finally:
        con.close()

    if "entry_price" in df.columns and "outcome_price" in df.columns:
        df, _ = clean_signal_frame(df)              # drop price-scale corruption
    if len(df) == 0:
        return df, feats
    df = df.sort_values("signal_date").reset_index(drop=True)
    df["side_buy"] = (df["side"] == "BUY").astype(int)
    df["meta_label"] = (df["tb_label"] == 1).astype(int)
    return df, feats + ["side_buy"]


def analyze(days: int = 800) -> Dict[str, Any]:
    """Train the meta-model on the older split, evaluate on the newer split, and
    report precision/coverage vs baseline. Returns a structured report."""
    import numpy as np
    df, feats = _load(days)
    if df is None or len(df) < MIN_SAMPLES:
        return {"error": f"insufficient labelled signals "
                         f"({0 if df is None else len(df)} < {MIN_SAMPLES})"}

    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import roc_auc_score
    except Exception as exc:
        return {"error": f"sklearn unavailable: {exc}"}

    X = df[feats].fillna(0.0).astype(float).values
    y = df["meta_label"].values

    # Temporal-coverage guard: meta-labeling on a handful of days overfits intraday
    # regime — a high AUC from train+test INSIDE the same day is an ARTIFACT, not edge.
    # Require enough distinct trading days and split on a DATE boundary so the test
    # set is genuinely FUTURE days (no intra-day leakage).
    dates = sorted(df["signal_date"].astype(str).unique())
    if len(dates) < MIN_DAYS:
        return {"error": f"insufficient temporal coverage: {len(dates)} distinct "
                         f"day(s) < {MIN_DAYS} — meta-labeling needs weeks of labelled "
                         "data; refusing to train (a high AUC here would be an overfit "
                         "artifact of one day's intraday regime).",
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

    base_rate = float(np.mean(yte))                 # win rate taking EVERY signal
    try:
        auc = float(roc_auc_score(yte, proba))
    except Exception:
        auc = float("nan")

    by_threshold = []
    for t in THRESHOLDS:
        sel = proba >= t
        n_sel = int(sel.sum())
        prec = float(np.mean(yte[sel])) if n_sel else 0.0   # actual win rate among selected
        by_threshold.append({
            "threshold": t,
            "n_selected": n_sel,
            "coverage": round(n_sel / len(yte), 4),
            "precision": round(prec, 4),
            "lift_vs_base": round(prec - base_rate, 4),
        })

    # feature importances (top 12)
    imp = sorted(zip(feats, clf.feature_importances_), key=lambda kv: -kv[1])[:12]

    # best usable threshold: precision beats base by a clear margin AND keeps >=10% coverage
    usable = [b for b in by_threshold
              if b["lift_vs_base"] > 0.02 and b["coverage"] >= 0.10 and b["n_selected"] >= 20]
    best = max(usable, key=lambda b: b["lift_vs_base"]) if usable else None

    rep = {
        "n_total": int(len(df)),
        "n_train": int(len(Xtr)),
        "n_test": int(len(Xte)),
        "n_features": len(feats),
        "base_win_rate": round(base_rate, 4),
        "auc": round(auc, 4),
        "cost_pct": COST_PCT,
        "by_threshold": by_threshold,
        "top_features": [{"feature": f, "importance": round(float(i), 4)} for f, i in imp],
        "best_threshold": best,
    }
    if auc < 0.55:
        rep["conclusion"] = (
            f"AUC={auc:.3f} — meta-model has ~no signal; meta-labeling adds nothing on "
            "the current data (consistent with an edgeless primary). Do NOT gate live. "
            "Re-run as the 17 modifiers accrue real values over trading days.")
    elif best:
        rep["conclusion"] = (
            f"AUC={auc:.3f}. Gating at P(win)>={best['threshold']} lifts precision "
            f"{base_rate:.1%}→{best['precision']:.1%} (+{best['lift_vs_base']:.1%}) at "
            f"{best['coverage']:.0%} coverage. PROMISING — requires a locked-holdout "
            "pass + beating round-trip cost before any live gating.")
    else:
        rep["conclusion"] = (
            f"AUC={auc:.3f} but no threshold clears base rate by >2% at usable coverage "
            "— no actionable precision lift yet. Report-only.")
    return rep


def train_and_save(days: int = 800) -> Dict[str, Any]:
    """Train on ALL available data and persist the model (for later wiring once
    validated). Returns the analyze() report (which uses the time-ordered split)."""
    rep = analyze(days)
    if "error" in rep:
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
        return f"meta-labeler: {rep['error']}"
    lines = [
        "🏷️ <b>Meta-Labeling Report</b>",
        f"train={rep['n_train']} test={rep['n_test']} feats={rep['n_features']} "
        f"base_win={rep['base_win_rate']:.1%} AUC={rep['auc']}",
        "",
        "  thresh  cover  precision  lift",
    ]
    for b in rep["by_threshold"]:
        lines.append(f"  {b['threshold']:.2f}   {b['coverage']:.0%}    "
                     f"{b['precision']:.1%}    {b['lift_vs_base']:+.1%}")
    lines += ["", "top features: " + ", ".join(f["feature"] for f in rep["top_features"][:6])]
    lines += ["", f"<b>{rep['conclusion']}</b>"]
    return "\n".join(lines)


def run_nightly(send_telegram: bool = False) -> Dict[str, Any]:
    """Entry point for the post-market pipeline. Trains, saves model, writes report.
    Report-only — never gates live trades."""
    import json
    from pathlib import Path
    rep = train_and_save()
    logger.info("meta-labeler: %s", rep.get("conclusion", rep.get("error")))
    try:
        Path(REPORT_FILE).write_text(json.dumps(rep, indent=2, default=str))
    except Exception as exc:
        logger.debug("meta_labeler_report write: %s", exc)
    return rep


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    rep = train_and_save()
    print(format_report(rep).replace("<b>", "").replace("</b>", ""))
