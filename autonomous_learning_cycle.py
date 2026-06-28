#!/usr/bin/env python3
"""
autonomous_learning_cycle.py

One-command post-market learning and safety refresh.

Run:
    python autonomous_learning_cycle.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable


REPORT_FILE = "autonomous_learning_report.json"
AUTOTUNE_FILE = "option_strike_autotune.json"


def _step(name: str, fn) -> Dict[str, Any]:
    started = time.time()
    try:
        result = fn()
        return {
            "ok": True,
            "duration_sec": round(time.time() - started, 3),
            "result": result if isinstance(result, dict) else {"value": result},
        }
    except Exception as exc:
        return {
            "ok": False,
            "duration_sec": round(time.time() - started, 3),
            "error": str(exc),
        }


def _read_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _top_weight_changes(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    limit: int = 5,
) -> Dict[str, Any]:
    before_w = before.get("feature_weights", {}) if isinstance(before, dict) else {}
    after_w = after.get("feature_weights", {}) if isinstance(after, dict) else {}
    if not isinstance(before_w, dict):
        before_w = {}
    if not isinstance(after_w, dict):
        after_w = {}

    changes = []
    for feature in sorted(set(before_w) | set(after_w)):
        old = float(before_w.get(feature, 1.0) or 1.0)
        new = float(after_w.get(feature, 1.0) or 1.0)
        delta = round(new - old, 4)
        if abs(delta) < 0.0001:
            continue
        changes.append({
            "feature": feature,
            "before": round(old, 4),
            "after": round(new, 4),
            "delta": delta,
        })
    promoted = sorted(changes, key=lambda x: x["delta"], reverse=True)[:limit]
    demoted = sorted(changes, key=lambda x: x["delta"])[:limit]
    return {"promoted": promoted, "demoted": demoted}


def _build_learning_delta(
    before_model: Dict[str, Any],
    after_model: Dict[str, Any],
    previous_report: Dict[str, Any],
    current_report: Dict[str, Any],
) -> Dict[str, Any]:
    prev_steps = previous_report.get("steps", {}) if isinstance(previous_report, dict) else {}
    prev_elig = prev_steps.get("live_eligibility", {}).get("result", {})
    cur_steps = current_report.get("steps", {}) if isinstance(current_report, dict) else {}
    cur_elig = cur_steps.get("live_eligibility", {}).get("result", {})
    before_selected = int(before_model.get("labelled_selected", 0) or 0)
    before_shadow = int(before_model.get("labelled_shadow", 0) or 0)
    after_selected = int(after_model.get("labelled_selected", 0) or 0)
    after_shadow = int(after_model.get("labelled_shadow", 0) or 0)
    before_features = before_model.get("feature_weights", {})
    after_features = after_model.get("feature_weights", {})
    if not isinstance(before_features, dict):
        before_features = {}
    if not isinstance(after_features, dict):
        after_features = {}
    return {
        "new_selected_samples": after_selected - before_selected,
        "new_shadow_samples": after_shadow - before_shadow,
        "selected_total": after_selected,
        "shadow_total": after_shadow,
        "feature_count_before": len(before_features),
        "feature_count_after": len(after_features),
        "feature_count_delta": len(after_features) - len(before_features),
        "weight_changes": _top_weight_changes(before_model, after_model),
        "live_ready_before": int(prev_elig.get("live_ready_count", 0) or 0),
        "live_ready_after": int(cur_elig.get("live_ready_count", 0) or 0),
        "live_block_reason": cur_elig.get("live_block_reason", ""),
    }


def run_autonomous_learning_cycle(
    *,
    report_file: str = REPORT_FILE,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Run the post-market autonomous learning sequence.

    This intentionally uses local files/DBs only. It does not place orders and
    it does not override live eligibility; it refreshes the evidence and gates.
    """
    report: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": bool(dry_run),
        "steps": {},
    }
    previous_report = _read_json(report_file)
    before_autotune = _read_json(AUTOTUNE_FILE)

    def _backfill() -> Dict[str, Any]:
        from option_autotune_backfill import backfill_from_signal_log, backfill_from_trades_db
        from option_decision_journal import repair_missing_shadow_candidates

        snapshot_labels = {"skipped": True, "reason": "dry_run"}
        if not dry_run:
            try:
                from option_signal_snapshot_labeller import label_pending_option_signals_from_snapshots
                snapshot_labels = label_pending_option_signals_from_snapshots()
            except Exception as exc:
                snapshot_labels = {"ok": False, "reason": str(exc), "updated": 0}
        replay = {"skipped": True, "reason": "dry_run"}
        if not dry_run:
            try:
                from option_historical_replay_labeller import run_historical_option_replay
                replay = run_historical_option_replay()
            except Exception as exc:
                replay = {"ok": False, "reason": str(exc), "written": 0, "shadow_outcomes": 0}
        trades = backfill_from_trades_db(dry_run=dry_run)
        signals = backfill_from_signal_log(dry_run=dry_run)
        repair = (
            {"skipped": True, "reason": "dry_run"}
            if dry_run
            else repair_missing_shadow_candidates()
        )
        return {
            "trades_db": trades,
            "signal_log": signals,
            "snapshot_option_labels": snapshot_labels.get("updated", 0),
            "historical_replay": replay.get("written", 0),
            "historical_replay_shadow": replay.get("shadow_outcomes", 0),
            "shadow_ladders_repaired": repair.get("updated", 0),
            "selected_seen": repair.get("selected_seen", 0),
        }

    report["steps"]["option_backfill"] = _step("option_backfill", _backfill)

    def _signal_trade_reconcile() -> Dict[str, Any]:
        from signal_trade_reconciler import reconcile_signal_trades

        result = reconcile_signal_trades(dry_run=dry_run)
        return {
            "trades_seen": result.get("trades_seen", 0),
            "already_linked": result.get("already_linked", 0),
            "matched": result.get("matched", 0),
            "updated": result.get("updated", 0),
            "unmatched": len(result.get("unmatched", []) or []),
        }

    report["steps"]["signal_trade_reconcile"] = _step("signal_trade_reconcile", _signal_trade_reconcile)

    def _signal_reverse_engineering() -> Dict[str, Any]:
        from signal_reverse_engineer import build_reverse_engineering_report

        result = build_reverse_engineering_report()
        totals = result.get("totals", {})
        return {
            "ready": bool(result.get("ready")),
            "rows": totals.get("rows", 0),
            "labelled_rows": totals.get("labelled_rows", 0),
            "pending_rows": totals.get("pending_rows", 0),
            "top_context_edges": len(result.get("top_context_edges", []) or []),
            "feature_edges": len(result.get("feature_edges", []) or []),
            "next_action": result.get("next_action", ""),
        }

    report["steps"]["signal_reverse_engineering"] = _step(
        "signal_reverse_engineering",
        _signal_reverse_engineering,
    )

    def _eod_signal_mining() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from eod_signal_miner import run_miner
        from data_fetcher import DataFetcher

        try:
            import config as cfg
            symbols_csv = "nifty200.csv" if Path("nifty200.csv").exists() else None
            fetcher = DataFetcher(symbols_csv=symbols_csv)
            symbols = fetcher.get_ordered_symbols(include_full_universe=True)
            max_symbols = int(getattr(cfg, "EOD_SIGNAL_MINER_MAX_SYMBOLS", 0) or 0)
            if max_symbols <= 0:
                max_symbols = int(getattr(cfg, "FULL_UNIVERSE_SCAN_MAX_SYMBOLS", len(symbols)) or len(symbols))
        except Exception:
            from universe_manager import build_learning_universe
            symbols = build_learning_universe()
            max_symbols = len(symbols)
        result = run_miner(symbols=symbols, days=5, max_symbols=max_symbols, write=True)
        summary = result.get("summary", {})
        return {
            "symbols_ok": result.get("symbols_ok", 0),
            "symbols_seen": result.get("symbols_seen", 0),
            "symbols_requested": min(len(symbols), max_symbols),
            "candidates": summary.get("n", 0),
            "wins": summary.get("wins", 0),
            "losses": summary.get("losses", 0),
            "timeouts": summary.get("timeouts", 0),
            "avg_return_pct": summary.get("avg_return_pct", 0),
            "setup_count": len(result.get("by_setup", []) or []),
            "factor_count": len(result.get("by_factor", []) or []),
        }

    report["steps"]["eod_signal_mining"] = _step("eod_signal_mining", _eod_signal_mining)

    def _confluence_feature_store() -> Dict[str, Any]:
        from confluence_feature_store import refresh_confluence_features

        return refresh_confluence_features()

    report["steps"]["confluence_feature_store"] = _step(
        "confluence_feature_store",
        _confluence_feature_store,
    )

    def _alternative_representation_backfill() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from alternative_representation_backfill import backfill_representation_features

        return backfill_representation_features(limit=2000)

    report["steps"]["alternative_representation_backfill"] = _step(
        "alternative_representation_backfill", _alternative_representation_backfill
    )

    def _candle_coverage_backfill() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from candle_coverage_backfill import run_candle_coverage_backfill

        result = run_candle_coverage_backfill()
        after = result.get("after", {}) if isinstance(result.get("after"), dict) else {}
        intervals = after.get("intervals", {}) if isinstance(after.get("intervals"), dict) else {}
        return {
            "symbols_requested": result.get("symbols_requested", 0),
            "intervals": result.get("intervals", []),
            "total_rows": after.get("total", 0),
            "interval_symbols": {
                k: v.get("symbols", 0)
                for k, v in intervals.items()
                if isinstance(v, dict)
            },
            "interval_rows": {
                k: v.get("rows", 0)
                for k, v in intervals.items()
                if isinstance(v, dict)
            },
        }

    report["steps"]["candle_coverage_backfill"] = _step(
        "candle_coverage_backfill",
        _candle_coverage_backfill,
    )

    def _derive_daily_candles() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from derive_daily_candles import derive_daily_candles

        result = derive_daily_candles(source_interval="1m")
        return {
            "source_interval": result.get("source_interval", "1m"),
            "symbols_ok": result.get("symbols_ok", 0),
            "symbols_skipped": result.get("symbols_skipped", 0),
            "inserted_rows": result.get("inserted_rows", 0),
        }

    report["steps"]["derive_daily_candles"] = _step(
        "derive_daily_candles",
        _derive_daily_candles,
    )

    def _data_quality_watchdog() -> Dict[str, Any]:
        from data_quality_watchdog import audit_candle_cache

        audit = audit_candle_cache()
        return {
            "ok": bool(audit.get("ok")),
            "groups": audit.get("total_groups", 0),
            "bad_groups": audit.get("bad_groups", 0),
            "stale_groups": audit.get("stale_groups", 0),
            "max_intraday_age_days": audit.get("max_intraday_age_days", 0),
            "bars": audit.get("total_bars", 0),
        }

    report["steps"]["data_quality_watchdog"] = _step("data_quality_watchdog", _data_quality_watchdog)

    def _research_bias_audit() -> Dict[str, Any]:
        from research_bias_audit import run_bias_audit

        result = run_bias_audit(write=not dry_run)
        return {
            "ok": bool(result.get("ok")),
            "checks": len(result.get("checks", []) or []),
            "failed": result.get("failed", []),
            "sample": result.get("sample", {}),
        }

    report["steps"]["research_bias_audit"] = _step(
        "research_bias_audit", _research_bias_audit
    )

    def _training_contract_audit() -> Dict[str, Any]:
        from training_contract_audit import build_training_contract_audit

        result = build_training_contract_audit(write=not dry_run)
        return {
            "ok": bool(result.get("ok")),
            "checks": result.get("checks", {}),
            "clean_evidence": result.get("clean_evidence", {}),
        }

    report["steps"]["training_contract_audit"] = _step(
        "training_contract_audit", _training_contract_audit
    )

    def _data_evidence_catalog() -> Dict[str, Any]:
        from data_evidence_catalog import build_evidence_catalog

        result = build_evidence_catalog(write=not dry_run)
        return {
            "ok": bool(result.get("ok")),
            "databases": result.get("database_count", 0),
            "tables": result.get("table_count", 0),
            "rows": result.get("total_rows", 0),
            "issues": result.get("issues", []),
        }

    report["steps"]["data_evidence_catalog"] = _step(
        "data_evidence_catalog", _data_evidence_catalog
    )

    def _execution_audit_chain() -> Dict[str, Any]:
        from execution_compliance import record_assurance_event, verify_audit_chain

        if not dry_run:
            record_assurance_event("autonomous_learning_cycle")
        return verify_audit_chain()

    report["steps"]["execution_audit_chain"] = _step(
        "execution_audit_chain", _execution_audit_chain
    )

    def _shadow_portfolio() -> Dict[str, Any]:
        from shadow_portfolio_simulator import simulate_shadow_portfolio

        result = simulate_shadow_portfolio()
        return {
            "labelled_seen": result.get("labelled_seen", 0),
            "shadow_trades": result.get("shadow_trades", 0),
            "wins": result.get("wins", 0),
            "losses": result.get("losses", 0),
            "timeouts": result.get("timeouts", 0),
            "target_rate": result.get("target_rate", 0),
            "rejected_wins": result.get("rejected_wins", 0),
        }

    report["steps"]["shadow_portfolio"] = _step("shadow_portfolio", _shadow_portfolio)

    def _market_snapshot() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from market_snapshot_recorder import record_market_snapshot

        return record_market_snapshot()

    report["steps"]["market_snapshot"] = _step("market_snapshot", _market_snapshot)

    def _option_chain_snapshot() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from option_chain_recorder import record_option_chains
        from option_chain_recorder import _in_market_hours

        try:
            import config as cfg
            underlyings = list(getattr(cfg, "SNAPSHOT_OPTION_UNDERLYINGS", []) or [])
        except Exception:
            underlyings = []
        if not _in_market_hours():
            return {
                "requested": len(underlyings or ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]),
                "ok_count": 0,
                "skipped": True,
                "reason": "outside_market_hours",
            }
        result = record_option_chains(underlyings or ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"])
        return {
            "requested": result.get("requested", 0),
            "ok_count": result.get("ok_count", 0),
        }

    report["steps"]["option_chain_snapshot"] = _step("option_chain_snapshot", _option_chain_snapshot)

    def _option_signal_evidence() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from option_signal_evidence import backfill_option_signal_evidence

        result = backfill_option_signal_evidence()
        return {
            "snapshots_seen": result.get("snapshots_seen", 0),
            "inserted": result.get("inserted", 0),
            "skipped_existing": result.get("skipped_existing", 0),
        }

    report["steps"]["option_signal_evidence"] = _step(
        "option_signal_evidence",
        _option_signal_evidence,
    )

    def _option_structure_mining() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from eod_option_structure_miner import run_structure_miner

        try:
            import config as cfg
            symbols = list(getattr(cfg, "SNAPSHOT_OPTION_UNDERLYINGS", []) or [])
        except Exception:
            symbols = []
        result = run_structure_miner(
            symbols=symbols or ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"],
            days=5,
            top_n=8,
            persist=True,
        )
        return {
            "symbols_ok": result.get("symbols_ok", 0),
            "symbols_seen": result.get("symbols_seen", 0),
            "legs": result.get("legs", 0),
            "stored": result.get("stored", 0),
            "top_edges": len(result.get("top_edges", []) or []),
        }

    report["steps"]["option_structure_mining"] = _step("option_structure_mining", _option_structure_mining)

    def _option_shadow_labels() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from option_shadow_labeller import label_shadow_candidates_from_eod

        result = label_shadow_candidates_from_eod()
        return {
            "eligible_rows": result.get("eligible_rows", 0),
            "labelled_shadow": result.get("labelled_shadow", 0),
            "skipped": result.get("skipped", 0),
            "option_db": result.get("option_db", ""),
        }

    report["steps"]["option_shadow_labels"] = _step("option_shadow_labels", _option_shadow_labels)

    def _multistrike_labels() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from option_multistrike_signals import label_multistrike_outcomes
        return label_multistrike_outcomes()

    report["steps"]["option_multistrike_labels"] = _step(
        "option_multistrike_labels", _multistrike_labels
    )

    def _multistrike_edge() -> Dict[str, Any]:
        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        from option_multistrike_signals import build_multistrike_edge_model
        return build_multistrike_edge_model()

    report["steps"]["option_multistrike_edge"] = _step(
        "option_multistrike_edge", _multistrike_edge
    )

    def _option_autotune() -> Dict[str, Any]:
        from option_strike_autotune import build_strike_autotune

        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        model = build_strike_autotune()
        return {
            "labelled_selected": model.get("labelled_selected", 0),
            "labelled_shadow": model.get("labelled_shadow", 0),
            "features": len(model.get("feature_weights", {}) or {}),
            "min_samples": model.get("min_samples", 0),
        }

    report["steps"]["option_autotune"] = _step("option_autotune", _option_autotune)

    def _live_eligibility() -> Dict[str, Any]:
        from live_eligibility import build_manifest

        if dry_run:
            return {"skipped": True, "reason": "dry_run"}
        manifest = build_manifest()
        return {
            "live_ready_count": manifest.get("live_ready_count", 0),
            "total_strategies": manifest.get("total_strategies", 0),
            "live_block_reason": manifest.get("live_block_reason", ""),
            "warnings": manifest.get("warnings", []),
        }

    report["steps"]["live_eligibility"] = _step("live_eligibility", _live_eligibility)

    def _live_probation() -> Dict[str, Any]:
        from live_probation import get_probation_status

        return get_probation_status()

    report["steps"]["live_probation"] = _step("live_probation", _live_probation)

    def _universe() -> Dict[str, Any]:
        from universe_manager import describe_universe

        return describe_universe()

    report["steps"]["universe"] = _step("universe", _universe)

    def _data_pipeline() -> Dict[str, Any]:
        from data_pipeline_audit import run_audit

        audit = run_audit(fetch_sample=0)
        return {
            "ok": bool(audit.get("ok")),
            "checks": len(audit.get("checks", []) or []),
            "warnings": [
                c.get("name", "unknown")
                for c in audit.get("checks", []) or []
                if not c.get("ok")
            ],
        }

    report["steps"]["data_pipeline"] = _step("data_pipeline", _data_pipeline)

    def _learning_coverage() -> Dict[str, Any]:
        from learning_coverage_report import build_learning_coverage_report

        coverage = build_learning_coverage_report()
        return {
            "ok": bool(coverage.get("ok")),
            "totals": coverage.get("totals", {}),
            "coverage": coverage.get("coverage", {}),
        }

    report["steps"]["learning_coverage"] = _step("learning_coverage", _learning_coverage)

    def _quality_gate() -> Dict[str, Any]:
        from quality_gate import compile_critical

        failures = compile_critical()
        return {
            "critical_failures": failures,
            "critical_ok": not failures,
        }

    report["steps"]["quality_gate"] = _step("quality_gate", _quality_gate)

    after_autotune = before_autotune if dry_run else _read_json(AUTOTUNE_FILE)
    report["learning_delta"] = _build_learning_delta(
        before_autotune,
        after_autotune,
        previous_report,
        report,
    )

    steps = report["steps"]
    report["ok"] = all(step.get("ok") for step in steps.values()) and not (
        steps.get("quality_gate", {})
        .get("result", {})
        .get("critical_failures", [])
    )

    if not dry_run:
        Path(report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def render_summary(report: Dict[str, Any]) -> str:
    steps = report.get("steps", {}) if isinstance(report, dict) else {}
    backfill = steps.get("option_backfill", {}).get("result", {})
    reconcile = steps.get("signal_trade_reconcile", {}).get("result", {})
    reverse = steps.get("signal_reverse_engineering", {}).get("result", {})
    eod_miner = steps.get("eod_signal_mining", {}).get("result", {})
    confluence_store = steps.get("confluence_feature_store", {}).get("result", {})
    candle_cov = steps.get("candle_coverage_backfill", {}).get("result", {})
    derived_daily = steps.get("derive_daily_candles", {}).get("result", {})
    dq = steps.get("data_quality_watchdog", {}).get("result", {})
    shadow = steps.get("shadow_portfolio", {}).get("result", {})
    market_snap = steps.get("market_snapshot", {}).get("result", {})
    option_snap = steps.get("option_chain_snapshot", {}).get("result", {})
    option_evidence = steps.get("option_signal_evidence", {}).get("result", {})
    option_structure = steps.get("option_structure_mining", {}).get("result", {})
    tune = steps.get("option_autotune", {}).get("result", {})
    elig = steps.get("live_eligibility", {}).get("result", {})
    probation = steps.get("live_probation", {}).get("result", {})
    universe = steps.get("universe", {}).get("result", {})
    data_pipe = steps.get("data_pipeline", {}).get("result", {})
    learning_cov = steps.get("learning_coverage", {}).get("result", {})
    learning_totals = learning_cov.get("totals", {}) if isinstance(learning_cov.get("totals"), dict) else {}
    learning_counts = learning_cov.get("coverage", {}) if isinstance(learning_cov.get("coverage"), dict) else {}
    qg = steps.get("quality_gate", {}).get("result", {})
    delta = report.get("learning_delta", {}) if isinstance(report.get("learning_delta"), dict) else {}
    weights = delta.get("weight_changes", {}) if isinstance(delta.get("weight_changes"), dict) else {}
    promoted = weights.get("promoted", []) if isinstance(weights.get("promoted"), list) else []
    demoted = weights.get("demoted", []) if isinstance(weights.get("demoted"), list) else []
    failures = qg.get("critical_failures", []) or []
    top_up = ", ".join(f"{x['feature']} {x['before']}->{x['after']}" for x in promoted[:3]) or "none"
    top_down = ", ".join(f"{x['feature']} {x['before']}->{x['after']}" for x in demoted[:3]) or "none"
    return "\n".join([
        "AUTONOMOUS LEARNING CYCLE",
        f"status={'PASS' if report.get('ok') else 'WARN'} dry_run={bool(report.get('dry_run'))}",
        f"backfill trades={backfill.get('trades_db', 0)} signal_log={backfill.get('signal_log', 0)}",
        (
            "reconcile "
            f"trades={reconcile.get('trades_seen', 0)} "
            f"linked={reconcile.get('already_linked', 0)} "
            f"matched={reconcile.get('matched', 0)} "
            f"updated={reconcile.get('updated', 0)} "
            f"unmatched={reconcile.get('unmatched', 0)}"
        ),
        (
            "reverse-engineer "
            f"ready={bool(reverse.get('ready', False))} "
            f"labelled={reverse.get('labelled_rows', 0)} "
            f"pending={reverse.get('pending_rows', 0)} "
            f"context_edges={reverse.get('top_context_edges', 0)} "
            f"feature_edges={reverse.get('feature_edges', 0)}"
        ),
        (
            "eod miner "
            f"skipped={bool(eod_miner.get('skipped', False))} "
            f"symbols={eod_miner.get('symbols_ok', 0)}/{eod_miner.get('symbols_seen', 0)} "
            f"requested={eod_miner.get('symbols_requested', 0)} "
            f"candidates={eod_miner.get('candidates', 0)} "
            f"avg_return={eod_miner.get('avg_return_pct', 0)}"
        ),
        (
            "feature stores "
            f"confluence_updated={confluence_store.get('updated', 0)} "
            f"candle_backfill_skipped={bool(candle_cov.get('skipped', False))} "
            f"candle_symbols={candle_cov.get('symbols_requested', 0)} "
            f"derived_daily={derived_daily.get('symbols_ok', 0)} "
            f"data_quality_bad={dq.get('bad_groups', 0)}/{dq.get('groups', 0)} "
            f"shadow={shadow.get('shadow_trades', 0)} "
            f"shadow_target={shadow.get('target_rate', 0)}"
        ),
        (
            "snapshots "
            f"market_skipped={bool(market_snap.get('skipped', False))} "
            f"option_skipped={bool(option_snap.get('skipped', False))} "
            f"option_ok={option_snap.get('ok_count', 0)}/{option_snap.get('requested', 0)} "
            f"evidence_inserted={option_evidence.get('inserted', 0)}"
        ),
        (
            "option structure "
            f"skipped={bool(option_structure.get('skipped', False))} "
            f"symbols={option_structure.get('symbols_ok', 0)}/{option_structure.get('symbols_seen', 0)} "
            f"legs={option_structure.get('legs', 0)} "
            f"edges={option_structure.get('top_edges', 0)}"
        ),
        (
            "autotune "
            f"selected={tune.get('labelled_selected', 0)} "
            f"shadow={tune.get('labelled_shadow', 0)} "
            f"features={tune.get('features', 0)}"
        ),
        (
            "delta "
            f"selected=+{delta.get('new_selected_samples', 0)} "
            f"shadow=+{delta.get('new_shadow_samples', 0)} "
            f"features={delta.get('feature_count_before', 0)}->{delta.get('feature_count_after', 0)}"
        ),
        f"promoted {top_up}",
        f"demoted {top_down}",
        (
            "live eligibility "
            f"{elig.get('live_ready_count', 0)}/{elig.get('total_strategies', 0)} "
            f"reason={elig.get('live_block_reason', '') or 'ok'}"
        ),
        (
            "probation "
            f"enabled={bool(probation.get('enabled', False))} "
            f"trades={probation.get('trades_today', 0)}/{probation.get('max_trades_per_day', 0)} "
            f"pnl={probation.get('daily_pnl', 0)} "
            f"locked={bool(probation.get('loss_locked', False))}"
        ),
        (
            "universe "
            f"learning={universe.get('learning_count', 0)} "
            f"mode={universe.get('learning_mode', '')} "
            f"probation={','.join(universe.get('probation_universe', []) or [])}"
        ),
        (
            "data pipeline "
            f"ok={bool(data_pipe.get('ok', False))} "
            f"checks={data_pipe.get('checks', 0)} "
            f"warnings={','.join(data_pipe.get('warnings', []) or []) or 'none'}"
        ),
        (
            "learning coverage "
            f"rows={learning_totals.get('signal_rows', 0)} "
            f"labelled={learning_totals.get('labelled_rows', 0)} "
            f"logged_symbols={learning_counts.get('logged_universe_count', 0)}/"
            f"{learning_counts.get('universe_count', 0)}"
        ),
        f"quality critical_ok={not failures}",
    ])


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default=REPORT_FILE)
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = run_autonomous_learning_cycle(report_file=args.report, dry_run=args.dry_run)
    print(render_summary(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
