"""
daily_pipeline.py

Post-market analysis pipeline. Runs all end-of-day analytics in sequence.
Designed to be triggered by a systemd timer at 15:35 IST.

Steps (in order):
  1. strategy_validator    — update WR report from labeled signal_log data
  2. alpha_decay_tracker   — update decay state for all strategies
  3. daily_performance_report — 5-day rolling table (+ Telegram if --telegram)
  4. candle_coverage_plan  — plan missing 1m/1d candle coverage for backfill
  5. derive_daily_candles  — derive 1d candles from valid intraday cache
  6. autonomous_param_training — guarded strategy parameter validation/promote
  7. system_readiness_report — unified readiness/blocker report
  8. post_market_ml        — retrain ML filters on today's outcomes
  9. walk_forward_backtest — OOS validation (skipped unless --run-wf flag set;
                              takes 5–10 min due to Angel API calls)

Usage:
  python3 daily_pipeline.py                    # steps 1-4 only
  python3 daily_pipeline.py --telegram         # + push Telegram summary
  python3 daily_pipeline.py --run-wf           # + run walk-forward (slow)
  python3 daily_pipeline.py --dry-run          # skip ML training + WF, log only
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

PIPELINE_LOG = "pipeline_run.json"


def _run_step(name: str, fn, *args, **kwargs) -> Dict[str, Any]:
    """Run a pipeline step, catch exceptions, return timing + status dict."""
    t0 = time.time()
    logger.info("── [%s] starting", name)
    status = "ok"
    error  = None
    result = None
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        status = "error"
        error  = str(exc)
        logger.error("[%s] FAILED: %s", name, exc)
    elapsed = round(time.time() - t0, 2)
    logger.info("── [%s] done in %.1fs  status=%s", name, elapsed, status)
    return {
        "step":    name,
        "status":  status,
        "elapsed": elapsed,
        "error":   error,
    }


def run_pipeline(
    telegram:  bool = False,
    run_wf:    bool = False,
    dry_run:   bool = False,
) -> Dict[str, Any]:
    """
    Execute post-market pipeline and return summary dict.
    All steps run regardless of individual failures — later steps do not
    depend on earlier ones (they each read from on-disk DB / JSON files).
    """
    started_at = datetime.now().isoformat()
    steps: List[Dict[str, Any]] = []
    logger.info("=" * 60)
    logger.info("  DAILY PIPELINE  %s", started_at[:16])
    logger.info("  dry_run=%s  telegram=%s  run_wf=%s", dry_run, telegram, run_wf)
    logger.info("=" * 60)

    # ── Step 1: Strategy validator ───────────────────────────────────────────
    def _step_validator():
        from strategy_validator import validate_all_strategies
        report = validate_all_strategies(save=not dry_run)
        confirmed = report.get("edge_confirmed", [])
        negative  = report.get("negative_edge",  [])
        logger.info("  validator: %d EDGE_CONFIRMED, %d NEGATIVE_EDGE",
                    len(confirmed), len(negative))
        return {"edge_confirmed": confirmed, "negative_edge": negative}

    steps.append(_run_step("strategy_validator", _step_validator))

    # ── Step 2: Alpha decay tracker ──────────────────────────────────────────
    def _step_decay():
        from alpha_decay_tracker import run_decay_check
        state    = run_decay_check(save=not dry_run)
        decaying = [s for s, d in state.items() if d.get("status") in ("DECAYING", "DEGRADED")]
        logger.info("  decay: %d strategies DECAYING/DEGRADED: %s",
                    len(decaying), ", ".join(decaying[:5]) or "none")
        return {"decaying": decaying}

    steps.append(_run_step("alpha_decay_tracker", _step_decay))

    # ── Step 3: Daily performance report ────────────────────────────────────
    def _step_daily_report():
        from daily_performance_report import run_daily_report
        report = run_daily_report(telegram=telegram, save=not dry_run)
        if "error" in report:
            raise RuntimeError(report["error"])
        pause_cands = report.get("pause_candidates", [])
        if pause_cands:
            logger.info("  daily_report: PAUSE candidates: %s", ", ".join(pause_cands))
        return {"pause_candidates": pause_cands,
                "promote_candidates": report.get("promote_candidates", [])}

    steps.append(_run_step("daily_performance_report", _step_daily_report))

    def _step_executive_report():
        from executive_reporting import run
        report = run(send_telegram=bool(telegram and not dry_run))
        return {"session_date": report.get("session_date"),
                "signals_400d": (report.get("windows") or {}).get("400", {}).get("signals", 0),
                "chart_path": report.get("chart_path")}
    steps.append(_run_step("executive_eod_report", _step_executive_report))

    # Publish the dedicated option dashboard early in the 15:45 post-market
    # run, before slower training/backfill steps.  The sender is date-deduped;
    # post_market_ml safely retries it later if this upload fails.
    if telegram and not dry_run:
        def _step_option_report():
            from option_telegram_report import send_post_market_option_report
            result = send_post_market_option_report()
            if not result.get("ok"):
                raise RuntimeError(result.get("skipped") or "option report send failed")
            logger.info("  option_report: %s", result.get("skipped") or result.get("path"))
            return result
        steps.append(_run_step("option_telegram_report", _step_option_report))

    # ── Step 4: Candle coverage plan (offline/read-only) ─────────────────────
    def _step_candle_coverage_plan():
        from candle_coverage_backfill import build_candle_coverage_plan
        report = build_candle_coverage_plan(write=not dry_run)
        interval_plans = report.get("interval_plans", []) or []
        for row in interval_plans:
            logger.info(
                "  candle_coverage: %s coverage=%.2f%% missing=%d",
                row.get("interval"),
                float(row.get("coverage_pct", 0.0) or 0.0),
                int(row.get("missing_symbols", 0) or 0),
            )
        return {
            "intervals": report.get("intervals", []),
            "next_actions": report.get("next_actions", [])[:5],
        }

    steps.append(_run_step("candle_coverage_plan", _step_candle_coverage_plan))

    # ── Step: Correlation matrix update ──────────────────────────────────────
    # 2026-08-19: run_correlation_update() (idle_engine.py) had zero callers
    # anywhere -- correlation_matrix.json never existed, so portfolio_heat.py's
    # real-correlation lookup silently had nothing to load. Scheduling it here
    # is what actually starts producing the data the live entry-gate now
    # consumes (see portfolio_heat.py / signal_engine.py GATE: Portfolio heat).
    def _step_correlation_update():
        from idle_engine import run_correlation_update
        result = run_correlation_update() if not dry_run else {}
        high_corr = result.get("high_corr", []) if isinstance(result, dict) else []
        logger.info("  correlation_update: %d symbols, %d pairs > 0.80",
                    len(result.get("symbols", []) if isinstance(result, dict) else []),
                    len(high_corr))
        return {"symbol_count": len(result.get("symbols", []) if isinstance(result, dict) else []),
                "high_corr_pairs": len(high_corr)}

    steps.append(_run_step("correlation_matrix_update", _step_correlation_update))

    # ── Step 5: Derive daily candles from intraday cache ─────────────────────
    def _step_derive_daily():
        from derive_daily_candles import derive_daily_candles
        report = derive_daily_candles(source_interval="1m", write=not dry_run)
        logger.info(
            "  derive_daily: source=%s symbols_ok=%d inserted=%d",
            report.get("source_interval", "1m"),
            int(report.get("symbols_ok", 0) or 0),
            int(report.get("inserted_rows", 0) or 0),
        )
        return {
            "source_interval": report.get("source_interval", "1m"),
            "symbols_ok": report.get("symbols_ok", 0),
            "symbols_skipped": report.get("symbols_skipped", 0),
            "inserted_rows": report.get("inserted_rows", 0),
        }

    steps.append(_run_step("derive_daily_candles", _step_derive_daily))

    # ── Step 6: Guarded strategy parameter trainer ───────────────────────────
    def _step_param_training():
        from autonomous_param_trainer import run_autonomous_param_training
        report = run_autonomous_param_training(dry_run=dry_run, write=not dry_run)
        logger.info(
            "  param_training: planned=%d promoted=%d paper_only=%d skipped=%d",
            len(report.get("planned", []) or []),
            len(report.get("promoted", []) or []),
            len(report.get("paper_only", []) or []),
            len(report.get("skipped", []) or []),
        )
        return {
            "planned": len(report.get("planned", []) or []),
            "promoted": len(report.get("promoted", []) or []),
            "paper_only": len(report.get("paper_only", []) or []),
            "skipped": len(report.get("skipped", []) or []),
        }

    steps.append(_run_step("autonomous_param_training", _step_param_training))

    # ── Step 7: Unified readiness report ────────────────────────────────────
    def _step_readiness():
        from system_readiness_report import build_system_readiness_report
        report = build_system_readiness_report(write=not dry_run)
        logger.info(
            "  readiness: data=%s institutional=%s live_ready=%s/%s blocks=%s",
            (report.get("scores") or {}).get("data_pipeline"),
            (report.get("scores") or {}).get("institutional"),
            (report.get("mode") or {}).get("live_ready_count"),
            (report.get("mode") or {}).get("total_strategies"),
            ",".join(report.get("blocks", []) or []) or "none",
        )
        return {
            "data_pipeline": (report.get("scores") or {}).get("data_pipeline"),
            "institutional": (report.get("scores") or {}).get("institutional"),
            "option_bot": (report.get("scores") or {}).get("option_bot"),
            "blocks": report.get("blocks", []),
            "warnings": report.get("warnings", []),
        }

    steps.append(_run_step("system_readiness_report", _step_readiness))

    # ── Step 8: Post-market ML training ─────────────────────────────────────
    if not dry_run:
        def _step_ml():
            from post_market_ml import run_pipeline
            result = run_pipeline(dry_run=False)   # was run_post_market (undefined → ImportError); run_pipeline sends telegram internally
            auc = (result.get("training") or {}).get("cross_symbol", {}).get("cv_auc_mean", 0)
            logger.info("  post_market_ml: CV AUC=%.3f", auc)
            return {"cv_auc": auc}
        steps.append(_run_step("post_market_ml", _step_ml))
    else:
        logger.info("── [post_market_ml] SKIPPED (dry_run)")
        steps.append({"step": "post_market_ml", "status": "skipped", "elapsed": 0, "error": None})

    # ── Step 9: Walk-forward backtest (optional, slow) ───────────────────────
    if run_wf and not dry_run:
        def _step_wf():
            from walk_forward_backtest import run_walk_forward_all, _get_angel_data_fetcher
            try:
                df = _get_angel_data_fetcher()
            except Exception as exc:
                logger.warning("  WF: DataFetcher init failed: %s", exc)
                df = None
            results = run_walk_forward_all(data_fetcher=df)
            logger.info("  walk_forward: %d strategies run", len(results))
            return {"strategies_run": list(results.keys())}
        steps.append(_run_step("walk_forward_backtest", _step_wf))
    else:
        reason = "dry_run" if dry_run else "not requested (use --run-wf)"
        logger.info("── [walk_forward_backtest] SKIPPED (%s)", reason)
        steps.append({"step": "walk_forward_backtest", "status": "skipped", "elapsed": 0, "error": None})

    # ── Summary ──────────────────────────────────────────────────────────────
    ok     = [s for s in steps if s["status"] == "ok"]
    errors = [s for s in steps if s["status"] == "error"]
    total  = round(sum(s["elapsed"] for s in steps), 1)

    summary = {
        "started_at":    started_at,
        "finished_at":   datetime.now().isoformat(),
        "total_elapsed": total,
        "steps_ok":      len(ok),
        "steps_error":   len(errors),
        "errors":        [{"step": s["step"], "error": s["error"]} for s in errors],
        "steps":         steps,
    }

    if not dry_run:
        Path(PIPELINE_LOG).write_text(json.dumps(summary, indent=2))

    logger.info("=" * 60)
    logger.info("  PIPELINE DONE  %.1fs  ok=%d  errors=%d", total, len(ok), len(errors))
    if errors:
        for e in errors:
            logger.error("  FAILED: %s — %s", e["step"], e["error"])
    logger.info("=" * 60)

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s | %(levelname)s | %(message)s",
        datefmt = "%H:%M:%S",
    )

    args     = sys.argv[1:]
    telegram = "--telegram" in args
    run_wf   = "--run-wf"   in args
    dry_run  = "--dry-run"  in args

    summary = run_pipeline(telegram=telegram, run_wf=run_wf, dry_run=dry_run)
    sys.exit(0 if summary["steps_error"] == 0 else 1)
