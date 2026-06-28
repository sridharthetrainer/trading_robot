"""
post_market_ml.py  —  Nightly post-market ML training & auto-upgrade pipeline.

Run after 15:30 IST every trading day:
  1. Load labelled signals from signal_log.db  (default last 15 days)
  2. Build multi-timeframe feature matrix       (signal_log features + candle augment)
  3. Run failure autopsy                        (why do signals fail?)
  4. Train / update ML models                   (cross-symbol + per-symbol)
  5. Write learned_filters.json                 (auto-upgrade applied next day)
  6. Update strategy_performance_matrix         (condition-based weights)
  7. Send Telegram summary

Usage:
    python3 post_market_ml.py                  # full run, all symbols, 15 days
    python3 post_market_ml.py --days 30        # longer lookback
    python3 post_market_ml.py --no-candles     # skip candle API calls (faster)
    python3 post_market_ml.py --dry-run        # run but don't write learned_filters.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DEFAULT_ML_TRAINING_DAYS = max(1, int(os.getenv("ML_TRAINING_DAYS", "15")))
DEFAULT_ML_CANDLE_DAYS = max(1, int(os.getenv("ML_CANDLE_DAYS", "15")))


# ── Market hours guard ─────────────────────────────────────────────────────────

def _is_post_market() -> bool:
    """True after 15:30 IST or outside market hours (weekend, holiday)."""
    import datetime as _dt
    now = _dt.datetime.now()
    # Weekends always OK
    if now.weekday() >= 5:
        return True
    t = now.time()
    return t >= _dt.time(15, 30)


# ── Signal labeller: update pending tb_label = -99 → actual outcome ───────────

def _update_pending_labels() -> int:
    """
    For signals with tb_label=-99, check if the corresponding trade has closed
    and update the label to +1 (profit) / -1 (loss) / 0 (timeout).
    Returns number of labels updated.
    """
    try:
        import sqlite3
        from pathlib import Path

        sig_db = Path(os.getenv("SIGNAL_LOG_DB", "signal_log.db"))
        trd_db = Path(os.getenv("TRADES_DB",        "trades.db"))
        if not sig_db.exists() or not trd_db.exists():
            return 0

        sig_conn = sqlite3.connect(str(sig_db))
        trd_conn = sqlite3.connect(str(trd_db))

        pending = sig_conn.execute(
            "SELECT id, trade_id FROM signal_log WHERE tb_label = -99 AND trade_id IS NOT NULL"
        ).fetchall()

        updated = 0
        for row_id, trade_id in pending:
            if not trade_id:
                continue
            closed = trd_conn.execute(
                "SELECT realized_pnl, exit_reason FROM trades WHERE trade_id = ?",
                (trade_id,)
            ).fetchone()
            if not closed:
                continue
            pnl, exit_reason = closed
            pnl = float(pnl or 0)
            if exit_reason and "timeout" in str(exit_reason).lower():
                label = 0
            elif pnl > 0:
                label = 1
            elif pnl < 0:
                label = -1
            else:
                label = 0
            sig_conn.execute(
                "UPDATE signal_log SET tb_label = ? WHERE id = ?", (label, row_id)
            )
            updated += 1

        sig_conn.commit()
        sig_conn.close()
        trd_conn.close()
        logger.info("Updated %d pending labels", updated)
        return updated

    except Exception as exc:
        logger.error("_update_pending_labels failed: %s", exc)
        return 0


# ── Strategy performance matrix update ────────────────────────────────────────

def _update_strategy_matrix(df: "pd.DataFrame") -> None:
    """
    Update strategy_performance_matrix with recent labelled signals.
    Writes condition-based win rates so the live engine gets fresh multipliers.
    """
    try:
        from strategy_performance_matrix import get_strategy_matrix
        matrix = get_strategy_matrix()
        # Idempotent EOD refresh: drop the previous night's replay before re-adding,
        # so re-running the pipeline never double-counts the same signals. Live
        # trade-close records (src='live') are preserved.
        matrix.purge_source("clean_v4")

        distinct_days = int(df["__signal_date"].astype(str).nunique()) if "__signal_date" in df else 0
        if len(df) < 5000 or distinct_days < 15:
            logger.info(
                "strategy_performance_matrix promotion deferred: clean=%d/5000 days=%d/15",
                len(df), distinct_days,
            )
            return

        import datetime as _dt

        for _, row in df.iterrows():
            strategy = str(row.get("__strategy", "")).strip()
            if not strategy:
                continue
            outcome     = int(row.get("tb_label", 0))
            # Weight by realised R-magnitude when available — a +3R signal should
            # count more than a +0.3R scratch (sign preserved → win-rate unchanged;
            # the average becomes avg-R). Falls back to the binary tb_label.
            r_mult      = float(row.get("tb_r_multiple", 0) or 0)
            weighted_pnl = r_mult if r_mult != 0 else float(outcome)
            day_type    = "EXPIRY" if row.get("near_expiry") else (
                "MONDAY" if int(row.get("day_of_week", 3)) == 0 else "NORMAL"
            )
            hour        = int(row.get("hour_of_day", 12))
            time_bucket = ("OPEN" if hour <= 10 else "MID" if hour <= 13 else "CLOSE")
            vix         = float(row.get("india_vix", 15))
            regime      = {2: "TREND", 0: "RANGING", 1: "VOLATILE"}.get(
                int(row.get("regime_enc", -1)), "UNKNOWN"
            )
            try:
                matrix.record_trade(
                    strategy    = strategy,
                    pnl         = weighted_pnl,  # realised R-magnitude (fallback: binary tb_label)
                    day_type    = day_type,
                    time_bucket = time_bucket,
                    vix         = vix,
                    regime      = regime,
                    autosave    = False,   # bulk replay: save once at the end (was 11.6h)
                    src         = "clean_v4",
                )
            except Exception:
                pass

        try:
            matrix._save()   # single write after the bulk replay
        except Exception:
            pass
        logger.info("strategy_performance_matrix updated from %d signals", len(df))
    except Exception as exc:
        logger.error("matrix update failed: %s", exc)


# ── Telegram summary ───────────────────────────────────────────────────────────

def _send_telegram_summary(
    autopsy:  Dict[str, Any],
    training: Dict[str, Any],
    dry_run:  bool = False,
) -> None:
    try:
        from alerts import AlertManager
        alerts = AlertManager()

        summary = autopsy.get("__summary", {})
        wr      = summary.get("overall_wr", 0)
        n       = summary.get("n_total", 0)
        dz      = summary.get("danger_zones", 0)
        ml_auc  = (training.get("cross_symbol") or {}).get("cv_auc_mean", 0)
        per_sym = len(training.get("per_symbol", {}))

        top_danger = []
        for feat_name, feat_dict in sorted(autopsy.items()):
            if feat_name.startswith("__") or not isinstance(feat_dict, dict):
                continue
            for bin_name, stats in feat_dict.items():
                if isinstance(stats, dict) and stats.get("danger"):
                    top_danger.append(
                        f"  • {feat_name} {bin_name}: "
                        f"LR={stats['loss_rate']:.0%} n={stats['n']}"
                    )
            if len(top_danger) >= 5:
                break

        lines = [
            "🤖 <b>Post-Market ML Report</b>",
            f"Signals: {n}  WR: {wr:.1%}  Danger zones: {dz}",
            f"ML model AUC: {ml_auc:.3f}  Per-symbol models: {per_sym}",
            "",
            "<b>Top danger zones (auto-filtered tomorrow):</b>",
        ] + top_danger[:5]

        if dry_run:
            lines.append("\n⚠ DRY RUN — learned_filters.json NOT updated")
        else:
            lines.append("\n✅ learned_filters.json updated — active from next scan")

        alerts.send("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        logger.debug("Telegram summary failed: %s", exc)


def _alert_pipeline_failure(detail: str) -> None:
    """Telegram-alert when the nightly pipeline crashes or errors. The success
    path already pings via _send_telegram_summary; genuine failures (crash /
    empty feature matrix) were previously SILENT, so a dead nightly evidence
    pipeline could go unnoticed for days. Benign skips (e.g. market_open) are
    filtered by the caller to avoid false alarms."""
    try:
        from alerts import AlertManager
        AlertManager().send(
            "🚨 <b>Post-Market ML pipeline FAILED</b>\n"
            f"{detail}\n"
            "Nightly evidence pipeline did not complete — labels / learned_filters "
            "may be stale. Check the journal or run manually:\n"
            "<code>venv/bin/python3 post_market_ml.py --days 90</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.debug("pipeline-failure alert failed: %s", exc)


def _autonomous_monitoring() -> None:
    """Step 9 (autonomous): push the consolidated daily dashboard to Telegram and
    run the offline chaos suite, alerting ONLY if a resilience scenario regresses.
    Both best-effort — monitoring must never break the pipeline. Runs as part of
    the existing nightly cron, so no separate scheduling is needed."""
    try:
        import macro_global_profit_engine as _macro
        if _macro.log_sentiment():
            logger.info("  macro_global_sentiment snapshot logged")
    except Exception as exc:
        logger.debug("macro snapshot failed: %s", exc)
    try:
        # PAPER defined-risk iron-condor forward-test (no live orders ever) —
        # accrues a real OOS selling track record for later promotion decisions.
        import condor_forward_test
        condor_forward_test.step()
        logger.info("  paper iron-condor forward-test stepped")
    except Exception as exc:
        logger.debug("condor forward-test step failed: %s", exc)
    try:
        # Refresh prune SUGGESTIONS from the edge analyzers (report-only; operator
        # promotes to pruned.json deliberately — data-gated, never auto-disable).
        import pruning
        pruning.suggest_from_analyzers()
        logger.info("  prune suggestions refreshed")
    except Exception as exc:
        logger.debug("prune suggest failed: %s", exc)
    try:
        # DB integrity check — alert early if any SQLite DB is corrupt/unreadable.
        import db_health
        _dbh = db_health.summary()
        if _dbh["bad"]:
            from alerts import AlertManager
            AlertManager().send(
                "🚨 <b>DB integrity FAILED</b>\n" + ", ".join(_dbh["bad"]),
                parse_mode="HTML")
            logger.warning("  DB health: BAD %s", _dbh["bad"])
        else:
            logger.info("  DB health: %d/%d ok", _dbh["healthy"], _dbh["n_dbs"])
    except Exception as exc:
        logger.debug("db health check failed: %s", exc)
    try:
        # Autonomous OI-flow validation — auto-reports a verdict once the intraday
        # OI snapshots accrue (data-gated). Alerts ONLY if it ever shows a POSSIBLE
        # edge (then run locked-holdout/DSR before wiring). Report-only otherwise.
        import validate_oi_flow
        _oiv = validate_oi_flow.validate()
        logger.info("  OI-flow validation: %s", _oiv.get("status"))
        if _oiv.get("status") == "OK" and "POSSIBLE" in str(_oiv.get("verdict", "")):
            from alerts import AlertManager
            AlertManager().send(
                "📈 <b>OI-flow shows a POSSIBLE edge</b>\n"
                f"IC={_oiv.get('IC')} (bar {_oiv.get('significance_bar')})\n"
                "Run locked-holdout + DSR before ANY live wiring.",
                parse_mode="HTML")
            logger.warning("  OI-flow POSSIBLE edge: %s", _oiv.get("IC"))
    except Exception as exc:
        logger.debug("oi-flow validation failed: %s", exc)
    try:
        # Post-market gap-fill: backfill any stale EOD stores (nifty_daily, option
        # premia) from NSE bhavcopy so missed live values are reconstructed nightly.
        import data_gap_filler
        _gf = data_gap_filler.fill(execute=True)
        _filled = [r["name"] for r in _gf if r.get("action") == "filled"]
        if _filled:
            logger.info("  data gap-fill: backfilled %s", _filled)
    except Exception as exc:
        logger.debug("data gap-fill failed: %s", exc)
    try:
        import daily_dashboard
        from alerts import AlertManager
        report = daily_dashboard.build_report()
        AlertManager().send("<pre>" + report + "</pre>", parse_mode="HTML")
        logger.info("  dashboard pushed to Telegram")
    except Exception as exc:
        logger.debug("dashboard push failed: %s", exc)
    try:
        import chaos_tests
        results = chaos_tests.run()
        failed = [n for n, s, _ in results if s == "fail"]
        if failed:
            from alerts import AlertManager
            AlertManager().send(
                "🚨 <b>Chaos test regression</b>\nFailing: " + ", ".join(failed),
                parse_mode="HTML",
            )
            logger.warning("  chaos regressions: %s", failed)
        else:
            logger.info("  chaos suite: all offline scenarios graceful")
    except Exception as exc:
        logger.debug("chaos check failed: %s", exc)


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(
    days:           int  = DEFAULT_ML_TRAINING_DAYS,
    include_candles: bool = True,
    candle_days:     int  = DEFAULT_ML_CANDLE_DAYS,
    dry_run:         bool = False,
    force:           bool = False,
) -> Dict[str, Any]:
    """
    Run the full post-market ML pipeline.

    Parameters
    ----------
    days           : lookback for signal_log (default 15 days)
    include_candles: fetch candle-based indicator features (slower, richer)
    candle_days    : recent-signal window for candle API calls
    dry_run        : analyse but do NOT write learned_filters.json
    force          : run even if still in market hours

    Returns
    -------
    Pipeline result summary dict.
    """
    start = time.time()
    logger.info("═" * 60)
    logger.info("POST-MARKET ML PIPELINE  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("═" * 60)

    if not force and not _is_post_market():
        logger.warning("Market is still open (before 15:30). Use --force to override.")
        return {"error": "market_open"}

    # ML-training window guard: all training must run 07:00–21:00 (config) so heavy
    # jobs never fire overnight. --force bypasses for manual/ad-hoc runs.
    try:
        from trading_calendar import in_ml_training_window
        _win_ok, _win = in_ml_training_window()
    except Exception:
        _win_ok, _win = True, "07:00-21:00"
    if not force and not _win_ok:
        logger.warning("Outside ML training window %s (now %s) — skipping. Use --force.",
                       _win, datetime.now().strftime("%H:%M"))
        return {"error": "outside_training_window", "window": _win,
                "now": datetime.now().strftime("%H:%M")}

    # ── Step 1: Update pending labels ─────────────────────────────────────────
    logger.info("[1/6] Updating pending signal labels …")
    n_updated = _update_pending_labels()

    # ── Autonomous edge analysis — runs EARLY and independently of the feature
    # matrix (which can be empty when executed_only filters everything out), so
    # the nightly self-learning edge report is always produced. Gated + report-only.
    logger.info("[+] Autonomous edge analysis (significance-gated) …")
    _edge: Dict[str, Any] = {}
    try:
        from strategy_edge_analyzer import run_nightly as _edge_run
        _edge = _edge_run()
        logger.info("  edge analysis: %s", _edge.get("conclusion", "n/a"))
        Path("edge_analysis_last_run.json").write_text(
            json.dumps({**_edge, "timestamp": datetime.now().isoformat()},
                       indent=2, default=str))
    except Exception as _ee:
        logger.debug("Edge analysis failed: %s", _ee)

    # ── Per-modifier edge attribution — measures whether each logged confluence
    # modifier (participant_oi, theta, news, mtf_pivot, ...) actually improves
    # outcomes vs being silent. Report-only; writes modifier_edge_report.json.
    # Verdicts stay DEAD until the modifier instrumentation accrues data over
    # trading days (the modifiers were logged as 0 before 2026-06-14).
    logger.info("[+] Per-modifier edge attribution (significance-gated) …")
    try:
        from modifier_edge_analyzer import run_nightly as _mod_edge_run
        _mod_edge = _mod_edge_run()
        logger.info("  modifier attribution: %s", _mod_edge.get("conclusion", "n/a"))
    except Exception as _me:
        logger.debug("Modifier edge attribution failed: %s", _me)

    # ── Meta-labeling (López de Prado): a SECONDARY model predicting P(win) on the
    # triple-barrier labels, for precision-gating + confidence sizing. Report-only;
    # refuses until enough distinct trading days accrue (guards against an intraday
    # overfit artifact). Writes meta_labeler_report.json + ml_models/meta_labeler.joblib.
    logger.info("[+] Meta-labeling (secondary precision model) …")
    try:
        from meta_labeler import run_nightly as _meta_run
        _meta = _meta_run()
        logger.info("  meta-labeler: %s", _meta.get("conclusion", _meta.get("error", "n/a")))
    except Exception as _mle:
        logger.debug("Meta-labeling failed: %s", _mle)

    # ── Capture FII/DII + sector snapshots (historical series for analysis) ────
    logger.info("[+] EOD market capture (FII/DII + sector) …")
    _capture: Dict[str, Any] = {}
    try:
        from eod_market_capture import run_eod_capture
        _capture = run_eod_capture()
        logger.info("  capture: FII/DII saved=%s, sectors saved=%s",
                    _capture.get("fii_dii", {}).get("saved"),
                    _capture.get("sectors", {}).get("saved"))
    except Exception as _ce:
        logger.debug("EOD market capture failed: %s", _ce)

    # ── Step 2: Build feature matrix ──────────────────────────────────────────
    logger.info("[2/6] Building feature matrix (last %d days) …", days)
    from ml_feature_builder import build_feature_matrix
    import pandas as pd

    # executed_only=False: the system triple-barrier-tracks outcomes for EVERY
    # signal, not just the handful actually filled, and the `executed` flag is
    # currently never set (so executed_only=True returned 0 rows and aborted the
    # whole nightly pipeline). Learn from all tracked outcomes. Override with
    # POST_MARKET_EXECUTED_ONLY=true if real-fill-only learning is restored.
    _exec_only = os.getenv("POST_MARKET_EXECUTED_ONLY", "false").lower() in ("true", "1", "yes")
    df = build_feature_matrix(
        days            = days,
        executed_only   = _exec_only,
        include_candles = include_candles,
        candle_days     = candle_days,
    )
    if df.empty:
        logger.error("Feature matrix is empty — aborting pipeline")
        return {"error": "empty_feature_matrix"}

    # ── Step 3: Failure autopsy ────────────────────────────────────────────────
    logger.info("[3/6] Running failure autopsy …")
    from failure_autopsy import run_autopsy, summarise_autopsy
    autopsy = run_autopsy(df)
    print(summarise_autopsy(autopsy))

    # ── Step 4: Train ML models ────────────────────────────────────────────────
    logger.info("[4/6] Training ML models …")
    from ml_trainer import train_all
    training = train_all(df)
    if "error" in training:
        logger.warning("Training failed: %s", training["error"])
        training = {}

    # MDA (leakage-free permutation) feature-importance report → prune SUGGESTIONS
    # (report-only, never auto-disable — data-gated, consistent with pruned.json).
    try:
        _cs = training.get("cross_symbol") or {}
        _mda = _cs.get("mda_importances") or []
        if _mda:
            Path("mda_feature_report.json").write_text(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "cv_method": _cs.get("cv_method"),
                "cv_auc_mean": _cs.get("cv_auc_mean"),
                "importances": _mda,
                "noise_features_suggest_prune": _cs.get("noise_features") or [],
            }, indent=2))
            logger.info("[4/6] MDA report written | %d noise feature(s) suggested for prune",
                        len(_cs.get("noise_features") or []))
    except Exception as _mda_exc:
        logger.debug("MDA report write skipped: %s", _mda_exc, exc_info=True)

    # PBO (Probability of Backtest Overfitting) of strategy selection — quantifies
    # how likely the best in-sample strategy is just luck. Report-only; data-gated
    # (needs enough distinct days, else ok:False — the honest current state).
    try:
        from signal_log import strategy_selection_pbo
        _pbo = strategy_selection_pbo()
        Path("pbo_report.json").write_text(json.dumps({
            "timestamp": datetime.now().isoformat(), **_pbo}, indent=2))
        if _pbo.get("ok"):
            logger.info("[4/6] PBO(strategy selection)=%.2f over %d days (%.0f%%+ = overfit)",
                        _pbo.get("pbo", float('nan')), _pbo.get("distinct_days", 0), 50)
        else:
            logger.info("[4/6] PBO not computable yet: %s", _pbo.get("reason", "-"))
    except Exception as _pbo_exc:
        logger.debug("PBO report write skipped: %s", _pbo_exc, exc_info=True)

    # ── Step 5: Write learned_filters.json ────────────────────────────────────
    if dry_run:
        logger.info("[5/6] DRY RUN — skipping learned_filters.json write")
        filters_path = None
    else:
        logger.info("[5/6] Writing learned_filters.json …")
        from learned_filters import generate_and_save
        filters_path = generate_and_save(autopsy, training)
        logger.info("  → %s", filters_path)

    # ── Step 6: Update strategy performance matrix ────────────────────────────
    logger.info("[6/7] Updating strategy_performance_matrix …")
    _update_strategy_matrix(df)

    # ── Step 7: Alpha decay check ─────────────────────────────────────────────
    logger.info("[7/7] Alpha decay check …")
    _decay_strategies: list = []
    try:
        from alpha_decay_tracker import run_decay_check as _rdc
        _decay_out = _rdc(save=not dry_run)
        _decay_strategies = [
            s for s, d in _decay_out.items()
            if d.get("status") in ("DECAYING", "DEGRADED")
        ]
        if _decay_strategies:
            logger.info("  Decay/degraded strategies: %s", ", ".join(_decay_strategies))
        else:
            logger.info("  No decay detected across %d strategies", len(_decay_out))
    except Exception as _de:
        logger.debug("Alpha decay check failed: %s", _de)

    # ── Step 8: Telegram summary ───────────────────────────────────────────────
    _send_telegram_summary(autopsy, training, dry_run=dry_run)

    # ── Step 9: Autonomous monitoring (dashboard push + chaos check) ──────────
    _autonomous_monitoring()

    elapsed = round(time.time() - start, 1)
    summary  = autopsy.get("__summary", {})
    result = {
        "elapsed_sec":    elapsed,
        "labels_updated": n_updated,
        "signals_used":   summary.get("n_total", 0),
        "overall_wr":     summary.get("overall_wr", 0),
        "danger_zones":   summary.get("danger_zones", 0),
        "ml_auc":         (training.get("cross_symbol") or {}).get("cv_auc_mean", 0),
        "per_symbol_models": len(training.get("per_symbol", {})),
        "filters_written": not dry_run,
        "filters_path":    str(filters_path) if filters_path else None,
        "decay_strategies": _decay_strategies,
        "edge_conclusion":  _edge.get("conclusion"),
        "edge_candidates":  list((_edge.get("candidates") or {}).keys()),
        "dry_run":         dry_run,
    }

    # Save pipeline summary for dashboard / debugging
    Path("ml_pipeline_last_run.json").write_text(
        json.dumps({**result, "timestamp": datetime.now().isoformat()}, indent=2)
    )

    logger.info("═" * 60)
    logger.info(
        "PIPELINE COMPLETE  %.1fs | signals=%d | WR=%.1f%% | AUC=%.3f | "
        "danger_zones=%d | filters_written=%s",
        elapsed,
        result["signals_used"],
        result["overall_wr"] * 100,
        result["ml_auc"],
        result["danger_zones"],
        result["filters_written"],
    )
    logger.info("═" * 60)
    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Post-market ML training and auto-upgrade pipeline"
    )
    ap.add_argument("--days",        type=int,  default=DEFAULT_ML_TRAINING_DAYS,
                    help=f"Lookback days for signal_log (default {DEFAULT_ML_TRAINING_DAYS})")
    ap.add_argument("--candle-days", type=int,  default=DEFAULT_ML_CANDLE_DAYS,
                    help=f"Days of candle-augmented features (default {DEFAULT_ML_CANDLE_DAYS})")
    ap.add_argument("--no-candles",  action="store_true",
                    help="Skip candle API calls (faster, DB features only)")
    ap.add_argument("--dry-run",     action="store_true",
                    help="Analyse only — do not write learned_filters.json")
    ap.add_argument("--force",       action="store_true",
                    help="Run even if market is still open")
    args = ap.parse_args()

    # Benign reasons the pipeline legitimately produces no output — do NOT alert
    # on these (avoids nightly false alarms, e.g. the expected data-gated state).
    _BENIGN_SKIPS = {"market_open"}
    try:
        result = run_pipeline(
            days            = args.days,
            include_candles = not args.no_candles,
            candle_days     = args.candle_days,
            dry_run         = args.dry_run,
            force           = args.force,
        )
    except Exception as exc:
        logger.exception("post_market_ml pipeline crashed")
        _alert_pipeline_failure(f"crashed: {exc}")
        return 1
    _err = result.get("error")
    if _err and _err not in _BENIGN_SKIPS:
        _alert_pipeline_failure(f"error: {_err}")
    return 0 if not _err else 1


if __name__ == "__main__":
    sys.exit(main())
