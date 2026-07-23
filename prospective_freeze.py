"""
prospective_freeze.py — the 2026-07-22 prospective-holdout freeze.

WHY: every AUC/net_R check run against this system's history (day-split,
purged K-fold, CPCV) shares one blind spot none of them can fix -- they all
draw from the SAME fixed historical dataset that has already shaped which
features to keep, which models to try, and which hypotheses to chase.
Multiple independent external reviews converged on this point: CPCV protects
against overfitting to one PARTITION of a fixed dataset, it does not protect
against the cumulative effect of the research PROCESS itself. A regression
tried this session (regress on net_R instead of classifying P(win)) looked
positive on a single split, then failed under CPCV -- exactly the kind of
false lead this freeze exists to stop happening again. The only real fix is
scoring a genuinely frozen, never-retrained model against data that did not
exist when it was built.

WHAT THIS FREEZES:
  - PRIMARY confirmatory model: the 4-feature regime classifier (hour_of_day,
    india_vix, fii_cum_5d, mtf_pivot_mod), trained on all data through the
    freeze date, statistically tied with the full model under CPCV (~0.62-
    0.63 AUC either way) but simpler and less prone to feature drift.
  - SECONDARY locked challenger: the 42-feature full classifier. It only
    "wins" over the primary if it beats it by a pre-specified, non-trivial
    margin on PROSPECTIVE data -- decided at evaluation time, not by
    whichever looks better after the fact.
  - Explicitly NOT frozen: a hand-crafted VIX/hour threshold rule. A
    manually-chosen rule, if its thresholds were picked after looking at the
    same history, carries the identical meta-overfitting risk as an ML
    model, just in lower dimensions -- interpretability doesn't exempt it
    from selection bias.

EVALUATION PROTOCOL (write-once, no goalpost-moving -- see manifest.json
written alongside the frozen models for the machine-readable copy):
  - Window: EVAL_WINDOW_TRADING_DAYS trading days from the freeze date,
    fixed in advance. No early stopping "when it looks good," no interim
    peeking, no retuning based on partial results.
  - Two SEPARATE claims, both pre-specified -- don't conflate them:
      * Ranking claim: prospective AUC (95% CI must exclude 0.50),
        calibration slope/intercept, Spearman(predicted, realized net_R).
      * Economic claim: net_R under ONE absolute-threshold policy
        (P(win) >= FROZEN_THRESHOLD, the value already identified in prior
        historical work -- NOT chosen after seeing prospective data, and
        NOT a percentile/"top-K%" cohort, since top-K% of all future
        signals vs. per-day vs. among concurrent signals are different,
        incompatible policies that need future-population knowledge).
  - Pass requires ALL of: prospective AUC CI excludes 0.50 AND the frozen
    economic policy's cohort shows positive mean net_R AND that result
    survives excluding the single best day AND no pipeline change touched
    the predictions being evaluated.
  - While waiting: keep accruing the real-spread instrumentation
    (signal_spread_pct) and options shadow data; audit the 79 equity
    strategies for bar-close/bar-open feature-timing leakage (the one
    methodology concern never directly checked). Do NOT run further
    historical mining, model variants, or parameter searches against this
    same locked dataset during the window -- every additional test against
    already-used history adds to the exact risk this freeze controls for.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)

FREEZE_DATE = "2026-07-22"
FROZEN_DIR = os.path.join("ml_models", f"frozen_{FREEZE_DATE}")
REGIME_MODEL_FILE = os.path.join(FROZEN_DIR, "regime_model_primary.joblib")
FULL_MODEL_FILE = os.path.join(FROZEN_DIR, "full_model_secondary.joblib")
MANIFEST_FILE = os.path.join(FROZEN_DIR, "manifest.json")

EVAL_WINDOW_TRADING_DAYS = 120
FROZEN_THRESHOLD = 0.55  # already-identified historical value; not re-chosen here
CHALLENGER_MARGIN_AUC = 0.03  # 42-feature model must beat primary by this much to "win"
# Machine-checkable end date (computed once, via trading_calendar.is_trading_day,
# walking 120 valid trading days forward from FREEZE_DATE) -- "120 trading days"
# alone is not a terminable condition without a holiday-aware calendar; this is.
EVAL_WINDOW_END_DATE = "2027-01-15"
# Prospective observations start strictly AFTER the freeze date, not "sometime on
# 2026-07-22" -- freeze_today() and this constant were both set on the same
# calendar day, so the boundary is the trading session, not a wall-clock time.
PROSPECTIVE_START_AFTER_DATE = FREEZE_DATE


def _git_commit_hash() -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=os.path.dirname(os.path.abspath(__file__))
        ).decode().strip()
    except Exception as exc:
        logger.debug("git commit hash unavailable: %s", exc)
        return "unknown"


def _prospective_window_sessions() -> list:
    """The actual ordered list of the EVAL_WINDOW_TRADING_DAYS session dates,
    not just the end date -- an external review correctly noted that a single
    end date is fragile to later holiday-calendar corrections/discrepancies,
    while the explicit list is self-auditable regardless of what the calendar
    module says later."""
    from datetime import date, timedelta
    from trading_calendar import is_trading_day
    d = date.fromisoformat(FREEZE_DATE)
    sessions = []
    while len(sessions) < EVAL_WINDOW_TRADING_DAYS:
        d = d + timedelta(days=1)
        if is_trading_day(d):
            sessions.append(d.isoformat())
    return sessions


def record_backfill_hash(db_path: str = "signal_log.db") -> Dict[str, Any]:
    """One-time (but safe to re-run): hashes the (id, frozen_regime_pwin,
    frozen_full_pwin) tuples of every historical_backfill row and stores it
    in the manifest. If these rows are ever silently re-scored (e.g. a bug
    causes the nightly job to recompute rather than skip already-scored
    rows), the hash will change and the discrepancy becomes detectable
    instead of silent."""
    import hashlib
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, frozen_regime_pwin, frozen_full_pwin FROM signal_log "
            "WHERE prediction_origin='historical_backfill' ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    h = hashlib.sha256()
    for rid, pr, pf_ in rows:
        h.update(f"{rid}:{pr}:{pf_}".encode())
    digest = h.hexdigest()

    manifest = {}
    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE) as fh:
            manifest = json.load(fh)
    manifest["backfill_integrity"] = {
        "n_rows": len(rows),
        "sha256_of_id_and_scores": digest,
        "recorded_at": datetime.now().isoformat(),
        "purpose": "detect accidental silent re-scoring of historical_backfill "
                   "rows -- these must never change after this point",
    }
    with open(MANIFEST_FILE, "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return {"n_rows": len(rows), "sha256": digest}


TREND_CLUSTER_STRATEGIES = [
    "ma_cross", "trend", "alligator_ao", "cpr", "supertrend_mtf", "ema_ribbon",
]
TREND_CLUSTER_GROSS_EQUIVALENCE_MARGIN = 0.05  # R; pre-specified, not fitted post-hoc
TREND_CLUSTER_MIN_ACTIVE_PROSPECTIVE_DAYS = 10


def register_trend_cluster_hypothesis() -> Dict[str, Any]:
    """One-time (idempotent): logs a pre-registered, falsifiable prediction
    about strategy_pair_edge_miner.py's 2026-07-23 finding into the SAME
    manifest.json as the frozen models, so it can be checked against
    genuinely prospective data instead of re-mined against this same
    17-day historical window.

    ORIGIN: 298 pairs / 2,191 triples among the top-25 most frequent
    strategies were tested for cost-adjusted net_R edge (strategy_pair_edge_
    miner.py). Zero showed a positive edge; the strongest, most statistically
    extreme findings (t ~ -16 to -19) all involved TREND_CLUSTER_STRATEGIES.
    A follow-up diagnostic (gross tb_r_multiple vs net tb_r_multiple_net) on
    the 8 worst pairs showed gross means near zero (-0.003 to -0.034) against
    an almost perfectly constant ~0.1826R cost drag across every pair -- a
    5-AI, 2-round cross-review (Grok, Gemini 2.5 Pro, Claude 3.5 Sonnet,
    Kimi, GPT-5.6 Thinking) converged that this fully explains the HURTS
    verdicts without needing a genuine negative-interaction term, and
    unanimously recommended pre-registering the finding for this freeze
    rather than mining this same dataset further.

    CATEGORY (GPT-5.6's refinement, the most precise of the round): this is
    NO_GROSS_EDGE (gross ~ 0), NOT "cost-eaten" in the sense of a genuinely
    positive gross edge insufficient to cover costs -- that distinction
    matters because the two would call for different fixes (there is no
    edge here to preserve via cheaper execution).

    DECISION RULE (equivalence-margin test, not a mere fail-to-reject-zero):
    computed once genuinely prospective data clears
    TREND_CLUSTER_MIN_ACTIVE_PROSPECTIVE_DAYS, over signals where >=2 of
    TREND_CLUSTER_STRATEGIES co-fire (via signal_log.agreeing_strats):
      - CONFIRMED (NO_GROSS_EDGE) if |mean tb_r_multiple| <
        TREND_CLUSTER_GROSS_EQUIVALENCE_MARGIN (0.05R) AND mean
        tb_r_multiple_net stays negative, on BOTH the signal-weighted and
        equal-day-weighted aggregation.
      - REJECTED if mean tb_r_multiple falls outside +/-0.05R in either
        aggregation -- i.e. a real gross edge (positive or negative)
        emerged that this historical sample did not show.
    Not evaluated before the SAME EVAL_WINDOW_END_DATE as the primary/
    secondary models -- no separate early-peek schedule for this hypothesis.
    """
    if not os.path.exists(MANIFEST_FILE):
        return {"error": "not_frozen_yet", "detail": "run freeze_today() first"}
    with open(MANIFEST_FILE) as fh:
        manifest = json.load(fh)

    manifest.setdefault("auxiliary_hypotheses", {})["trend_cluster_2026-07-23"] = {
        "registered_at": datetime.now().isoformat(),
        "git_commit_at_registration": _git_commit_hash(),
        "source": "strategy_pair_edge_miner.py (298 pairs, 2191 triples, top-25 "
                  "strategies, signal_log 2026-06-29..2026-07-23) + a 5-AI "
                  "2-round cross-review",
        "cluster_strategies": TREND_CLUSTER_STRATEGIES,
        "historical_evidence": {
            "verdict_in_historical_sample": "NO_GROSS_EDGE / net-negative after "
                "cost drag (not WRONG_DIRECTION, not a demonstrated pair-specific "
                "negative interaction)",
            "worst_pairs_gross_mean_range": [-0.034, -0.003],
            "worst_pairs_cost_drag": 0.1826,
            "day_level_check": "negative on every active day for all 8 worst "
                "pairs (9/9 or 16/17), sign-test p<=0.0039, leave-one-day-out "
                "stayed negative after dropping the worst day",
            "redundancy_caveat": "several top pairs are highly non-independent "
                "(e.g. ma_cross present in 98.8% of all trend-tagged signals) "
                "-- the pair count overstates how many distinct discoveries "
                "this represents",
        },
        "hypothesis": (
            "Over genuinely prospective signals (prediction_origin="
            "'live_prospective') where >=2 of cluster_strategies co-fire, "
            "gross tb_r_multiple will remain within "
            f"+/-{TREND_CLUSTER_GROSS_EQUIVALENCE_MARGIN}R of zero (NO_GROSS_EDGE, "
            "an equivalence-margin test, not merely failing to reject zero) and "
            "mean tb_r_multiple_net will stay negative, consistent with a "
            "cost-driven (not directional) explanation."
        ),
        "min_active_prospective_days_before_evaluation": TREND_CLUSTER_MIN_ACTIVE_PROSPECTIVE_DAYS,
        "evaluate_no_earlier_than": EVAL_WINDOW_END_DATE,
        "aggregations_required": ["signal_weighted_mean", "equal_day_weighted_mean"],
        "confirmation_rule": (
            f"CONFIRMED if |gross_mean| < {TREND_CLUSTER_GROSS_EQUIVALENCE_MARGIN} "
            "AND net_mean < 0, in BOTH aggregations. REJECTED otherwise."
        ),
        "not_evaluated_early": "no interim peeking before evaluate_no_earlier_than "
                               "-- same discipline as the primary/secondary models",
        "production_impact": "NONE -- no confluence weighting, gating, or scoring "
                              "change was made as a result of this finding; this "
                              "entry exists solely to be checked against "
                              "prospective data later",
    }
    with open(MANIFEST_FILE, "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return {"registered": "trend_cluster_2026-07-23",
            "manifest": manifest["auxiliary_hypotheses"]["trend_cluster_2026-07-23"]}


def freeze_today(days: int = 800) -> Dict[str, Any]:
    """Train both models on ALL data through today and save them where no
    future nightly retraining run will ever touch them, plus a manifest
    capturing the full pipeline (not just model weights) and the evaluation
    protocol. Meant to run ONCE, at the start of the protocol."""
    import joblib
    from sklearn.ensemble import RandomForestClassifier

    os.makedirs(FROZEN_DIR, exist_ok=True)
    results: Dict[str, Any] = {}

    import regime_meta_labeler as rml
    df_r, feats_r = rml._load(days)
    if df_r is None or len(df_r) == 0:
        return {"error": "regime model: no data to freeze"}
    Xr = df_r[feats_r].fillna(0.0).astype(float).values
    yr = df_r["meta_label"].values
    clf_r = RandomForestClassifier(
        n_estimators=300, max_depth=5, min_samples_leaf=50,
        class_weight="balanced", random_state=42, n_jobs=-1).fit(Xr, yr)
    joblib.dump({"model": clf_r, "features": feats_r}, REGIME_MODEL_FILE)
    results["regime_model"] = {"path": REGIME_MODEL_FILE, "n_train": int(len(df_r)),
                                "features": feats_r}

    import meta_labeler as ml
    df_f, feats_f = ml._load(days)
    if df_f is None or len(df_f) == 0:
        return {"error": "full model: no data to freeze"}
    Xf = df_f[feats_f].fillna(0.0).astype(float).values
    yf = df_f["meta_label"].values
    clf_f = RandomForestClassifier(
        n_estimators=300, max_depth=5, min_samples_leaf=50,
        class_weight="balanced", random_state=42, n_jobs=-1).fit(Xf, yf)
    joblib.dump({"model": clf_f, "features": feats_f}, FULL_MODEL_FILE)
    results["full_model"] = {"path": FULL_MODEL_FILE, "n_train": int(len(df_f)),
                              "features": feats_f}

    manifest = {
        "freeze_date": FREEZE_DATE,
        "frozen_at": datetime.now().isoformat(),
        "git_commit_at_freeze": _git_commit_hash(),
        "prospective_window_sessions": _prospective_window_sessions(),
        "primary_model": {
            "role": "PRIMARY confirmatory model",
            "file": REGIME_MODEL_FILE,
            "features": feats_r,
            "n_train_rows": int(len(df_r)),
            "hyperparameters": {"n_estimators": 300, "max_depth": 5,
                                 "min_samples_leaf": 50, "class_weight": "balanced",
                                 "random_state": 42},
        },
        "secondary_model": {
            "role": "SECONDARY locked challenger -- wins only by beating primary "
                    f"AUC by >= {CHALLENGER_MARGIN_AUC} on prospective data",
            "file": FULL_MODEL_FILE,
            "features": feats_f,
            "n_train_rows": int(len(df_f)),
            "hyperparameters": {"n_estimators": 300, "max_depth": 5,
                                 "min_samples_leaf": 50, "class_weight": "balanced",
                                 "random_state": 42},
        },
        "rejected_artifact": {
            "hand_crafted_vix_hour_rule": "Not frozen -- a manually-chosen rule "
                "picked after looking at the same history carries the identical "
                "meta-overfitting risk as an ML model, just in lower dimensions."
        },
        "pipeline_hash_context": {
            "cost_model": "triple_barrier.cost_aware_r_multiple (flat 0.05% "
                          "equity slippage; real-spread instrumentation "
                          "(signal_spread_pct) accruing since 2026-07-22, not "
                          "yet incorporated into this cost model)",
            "net_r_column": "tb_r_multiple_net",
            "label_definition": "meta_label = 1 iff tb_label == 1 (hit full "
                                 "profit target before stop/timeout)",
            "training_eligible_filter": "valid risk levels (0<stop<entry<target "
                                         "or reverse) AND valid trading session",
            "side_encoding": "side_buy = 1 if side=='BUY' else 0",
        },
        "evaluation_protocol": {
            "window_trading_days": EVAL_WINDOW_TRADING_DAYS,
            "window_end_date_machine_checkable": EVAL_WINDOW_END_DATE,
            "prospective_start_boundary": f"signal_date > {PROSPECTIVE_START_AFTER_DATE} "
                "-- rows with signal_date <= this are 'historical_backfill' "
                "(scored retroactively to exercise the scoring code path, NOT "
                "prospective evidence), rows with signal_date > this are "
                "'live_prospective'. Mechanically enforced via signal_log."
                "prediction_origin, assigned at write time by "
                "score_new_signals_with_frozen_models(), not reconstructed "
                "later by an analyst.",
            "point_in_time_feature_integrity": "raw feature columns (hour_of_day, "
                "india_vix, fii_cum_5d, mtf_pivot_mod, and the 42-feature set) are "
                "written ONCE at signal-generation time by signal_log.log_candidate() "
                "and never updated afterward -- only tb_label/outcome_price/"
                "training_eligible get updated later by the separate labelling job. "
                "The nightly scorer therefore reads the SAME immutable snapshot "
                "regardless of when it happens to run; it does not recompute "
                "features fresh, so same-day-vs-nightly timing does not introduce "
                "leakage by construction. NOT yet verified: whether any individual "
                "feature's OWN computation (e.g. mtf_pivot_mod using a same-bar-"
                "close pivot) is itself leaky relative to its signal's timestamp -- "
                "that is exactly the open bar-close/bar-open audit question.",
            "no_early_stopping": True,
            "no_interim_peeking": True,
            "ranking_claim": {
                "metric": "prospective AUC on frozen-model predictions logged "
                          "from freeze date forward (prediction_origin="
                          "'live_prospective' rows only)",
                "pass_condition": "95% CI (day-clustered) excludes 0.50",
            },
            "economic_claim": {
                "policy": f"absolute threshold P(win) >= {FROZEN_THRESHOLD} "
                          "(pre-existing value, not chosen after seeing "
                          "prospective data); NOT a percentile/top-K% cohort",
                "pass_condition": "mean net_R > 0 for the selected cohort AND "
                                   "(median net_R > 0 OR day-level mean net_R > 0) "
                                   "-- mean alone can be juiced by one large winner, "
                                   "requiring median-or-day-level agreement makes "
                                   "the claim harder to satisfy by chance -- AND "
                                   "remains > 0 after excluding the single best day "
                                   "(the actual hurdle: only removing the worst day "
                                   "can only ever help a positive result, so that "
                                   "exclusion is reported as a downside-concentration "
                                   "diagnostic, not an additional pass requirement -- "
                                   "reported SEPARATELY, not as a joint double-exclusion, "
                                   "since the best and worst day may share a regime "
                                   "and a joint exclusion could over-correct)",
                "diagnostic_only_not_gating": "log the full distribution of "
                    "P(win) scores in the prospective period (not just the "
                    ">=0.55 cohort) -- if it shifts materially from the "
                    "training-period distribution, a fail could mean regime "
                    "shift/signal decay rather than the threshold being wrong; "
                    "this is recorded for interpretation, it does not change "
                    "the pass/fail rule above",
            },
            "frozen_prediction_statement_written_2026-07-22_unedited": (
                "We expect the ranking claim to survive prospectively (AUC CI "
                "excludes 0.50) but do NOT expect the economic claim to pass -- "
                "consistent with every expression tested this session (gating, "
                "ranking, continuous sizing, regression-on-R) failing to clear "
                "costs despite the ranking signal being real."
            ),
            "challenger_rule": f"42-feature model only 'wins' over the 4-feature "
                                f"model if BOTH: its prospective AUC exceeds the "
                                f"4-feature model's by >= {CHALLENGER_MARGIN_AUC} "
                                f"AND its own prospective AUC's 95% CI excludes 0.50 "
                                f"-- the margin alone is not sufficient, since two "
                                f"models that both degrade prospectively could still "
                                f"differ by >= {CHALLENGER_MARGIN_AUC} while neither "
                                f"actually works; a 'least-bad' model must not be "
                                f"declared the winner",
            "invalidating_conditions": [
                "any post-freeze change to feature definitions, cost model, "
                "or model files used to produce the scored predictions",
                "evaluation triggered before the window elapses because "
                "interim results 'look good' or 'look bad'",
                "leakage found during the parallel audit in the candidate-"
                "generation eligibility, label construction, entry-price "
                "timing, or any of the 4 primary features -- this INVALIDATES "
                "the confirmation (does not silently patch it); the window "
                "still runs to completion and is reported, but a material "
                "finding here means 'protocol invalid', not pass/fail",
            ],
            "while_waiting": [
                "keep accruing signal_spread_pct real market-depth capture",
                "keep accruing options catalog (C1/C3/D1) shadow data",
                "audit the 79 equity strategies for bar-close/bar-open "
                "feature-timing leakage (never directly checked)",
                "do NOT run further historical mining, model variants, or "
                "parameter searches against this same locked dataset",
            ],
        },
    }
    with open(MANIFEST_FILE, "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    results["manifest_path"] = MANIFEST_FILE
    return results


def score_new_signals_with_frozen_models(db_path: str = "signal_log.db") -> Dict[str, Any]:
    """Nightly step: score every signal_log row that doesn't yet have a
    frozen-model prediction, using the FROZEN (never retrained since
    freeze_today()) models. Idempotent -- only fills NULL rows. This is what
    makes the prospective test genuine: predictions come from the frozen
    snapshot, not from whatever a same-day retraining run would produce."""
    import joblib
    import sqlite3
    import pandas as pd

    if not (os.path.exists(REGIME_MODEL_FILE) and os.path.exists(FULL_MODEL_FILE)):
        return {"error": "frozen models not found -- run freeze_today() first"}

    regime_bundle = joblib.load(REGIME_MODEL_FILE)
    full_bundle = joblib.load(FULL_MODEL_FILE)

    from signal_log import SignalLogger
    SignalLogger(db_path=db_path)  # ensures frozen_* columns exist via migration

    con = sqlite3.connect(db_path)
    try:
        raw_feats = set(regime_bundle["features"]) | set(full_bundle["features"])
        raw_feats.discard("side_buy")
        raw_feats.add("side")
        raw_feats.add("signal_date")
        existing = {r[1] for r in con.execute("PRAGMA table_info(signal_log)").fetchall()}
        cols = [c for c in raw_feats if c in existing]
        df = pd.read_sql(
            f"SELECT id, {', '.join(cols)} FROM signal_log "
            "WHERE frozen_regime_pwin IS NULL OR frozen_full_pwin IS NULL", con)
        if df.empty:
            return {"scored": 0}
        if "side" in df.columns:
            df["side_buy"] = (df["side"] == "BUY").astype(int)

        Xr = df[regime_bundle["features"]].fillna(0.0).astype(float).values
        pr = regime_bundle["model"].predict_proba(Xr)[:, 1]
        Xf = df[full_bundle["features"]].fillna(0.0).astype(float).values
        pf = full_bundle["model"].predict_proba(Xf)[:, 1]
        # Mechanical, write-time segregation (not reconstructed at analysis
        # time) -- an external review correctly flagged that without this,
        # a later evaluation could accidentally treat already-seen historical
        # rows as fresh prospective evidence.
        origin = df["signal_date"].astype(str).apply(
            lambda d: "live_prospective" if d > FREEZE_DATE else "historical_backfill")

        con.executemany(
            "UPDATE signal_log SET frozen_regime_pwin=?, frozen_full_pwin=?, "
            "frozen_model_version=?, prediction_origin=? WHERE id=?",
            [(round(float(p_r), 4), round(float(p_f), 4), FREEZE_DATE, org, int(rid))
             for rid, p_r, p_f, org in zip(df["id"], pr, pf, origin)],
        )
        con.commit()
        n_backfill = int((origin == "historical_backfill").sum())
        n_prospective = int((origin == "live_prospective").sum())
        return {"scored": int(len(df)), "historical_backfill": n_backfill,
                "live_prospective": n_prospective}
    finally:
        con.close()


def run_nightly() -> Dict[str, Any]:
    """Entry point for the post-market pipeline. Report-only, additive --
    never gates or affects any live decision. Scores new signals with the
    frozen models so prospective evidence accrues day by day."""
    if not os.path.exists(MANIFEST_FILE):
        logger.debug("prospective_freeze: not yet frozen, skipping nightly scoring")
        return {"skipped": "not_frozen_yet"}
    rep = score_new_signals_with_frozen_models()
    logger.info("prospective_freeze: %s", rep)
    return rep


def get_prospective_rows(db_path: str = "signal_log.db"):
    """THE canonical way to query genuinely prospective evidence -- always
    filters prediction_origin='live_prospective'. Built so the eventual
    2027-01-15 evaluation (or anyone checking in before then) reuses this
    instead of writing an ad-hoc query that could forget the filter and
    silently mix in the 30,259 historical_backfill rows. Returns a
    pandas.DataFrame with every signal_log column for eligible rows."""
    import sqlite3
    import pandas as pd
    con = sqlite3.connect(db_path)
    try:
        return pd.read_sql(
            "SELECT * FROM signal_log WHERE prediction_origin='live_prospective' "
            "AND frozen_regime_pwin IS NOT NULL", con)
    finally:
        con.close()
