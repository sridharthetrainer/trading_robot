#!/usr/bin/env python3
"""Unified readiness report for autonomous trading operations."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable


REPORT_FILE = "system_readiness_report.json"


def _read_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _sqlite_scalar(db_path: str, sql: str, default: Any = 0) -> Any:
    if not Path(db_path).exists():
        return default
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(sql).fetchone()
            return row[0] if row else default
    except Exception:
        return default


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _age_hours(value: Any) -> float | None:
    dt = _parse_dt(value)
    if dt is None:
        return None
    now = datetime.now(tz=dt.tzinfo) if dt.tzinfo else datetime.now()
    return round(max(0.0, (now - dt).total_seconds() / 3600.0), 2)


def build_system_readiness_report(
    *,
    report_file: str = REPORT_FILE,
    write: bool = True,
) -> Dict[str, Any]:
    # Readiness must be built from current state. Stale JSON reports previously
    # allowed an old 98/A score to survive after the underlying evidence failed.
    try:
        from data_pipeline_audit import run_audit
        data_audit = run_audit(fetch_sample=0, internet=False)
    except Exception:
        data_audit = _read_json("data_pipeline_audit.json")
    try:
        from option_bot_audit import build_audit
        option_audit = build_audit()
    except Exception:
        option_audit = _read_json("option_bot_audit_report.json")
    option_score = option_audit.get("score") if isinstance(option_audit.get("score"), dict) else {}
    live_elig = _read_json("live_eligibility.json")
    try:
        from execution_fill_telemetry import build_execution_fill_telemetry
        fill = build_execution_fill_telemetry(write=False)
    except Exception:
        fill = _read_json("execution_fill_telemetry.json")
    try:
        from data_quality_watchdog import audit_candle_cache
        quality = audit_candle_cache()
    except Exception:
        quality = _read_json("data_quality_watchdog_report.json")
    derived = _read_json("derived_daily_candles_report.json")
    edge = _read_json("edge_analysis_last_run.json")
    health = _read_json("health_snapshot.json")
    try:
        from shadow_portfolio_simulator import simulate_shadow_portfolio
        shadow_portfolio = simulate_shadow_portfolio()
    except Exception:
        shadow_portfolio = _read_json("shadow_portfolio_report.json")
    try:
        from release_integrity import verify_manifest
        release_integrity = verify_manifest()
    except Exception as exc:
        release_integrity = {"ok": False, "reason": str(exc)}
    try:
        from research_bias_audit import run_bias_audit
        bias_audit = run_bias_audit(write=False)
    except Exception as exc:
        bias_audit = {"ok": False, "error": str(exc)}
    try:
        from data_evidence_catalog import build_evidence_catalog
        evidence_catalog = build_evidence_catalog(write=False)
    except Exception as exc:
        evidence_catalog = {"ok": False, "error": str(exc)}
    try:
        from training_contract_audit import build_training_contract_audit
        training_contract = build_training_contract_audit(write=False)
    except Exception as exc:
        training_contract = {"ok": False, "error": str(exc)}
    try:
        from execution_compliance import verify_audit_chain
        execution_chain = verify_audit_chain()
    except Exception as exc:
        execution_chain = {"ok": False, "error": str(exc)}

    latest_option_ok = _sqlite_scalar(
        "option_chain_snapshots.db",
        "SELECT MAX(snapshot_time) FROM option_chain_snapshots "
        "WHERE ok=1 AND is_live=1 AND COALESCE(source,'')<>''",
        None,
    )
    experiments = _sqlite_scalar("experiments.db", "SELECT COUNT(*) FROM experiments", 0)
    candles_1m = _sqlite_scalar(
        "candle_cache.db",
        "SELECT COUNT(DISTINCT symbol) FROM candles WHERE interval='1m'",
        0,
    )
    candles_1d = _sqlite_scalar(
        "candle_cache.db",
        "SELECT COUNT(DISTINCT symbol) FROM candles WHERE interval='1d'",
        0,
    )

    blocks = []
    warnings = []
    if int(live_elig.get("live_ready_count", 0) or 0) <= 0:
        blocks.append("no_live_ready_strategy")
    option_blocks = (option_score.get("evidence_blocks") or []) if isinstance(option_score, dict) else []
    blocks.extend(str(item) for item in option_blocks)
    if str(edge.get("overall", {}).get("verdict", "")).upper() != "EDGE" and "NO significant edge" in str(edge.get("conclusion", "")):
        blocks.append("no_after_cost_statistical_edge")
    gross_pnl = _sqlite_scalar("trades.db", "SELECT COALESCE(SUM(gross_pnl),0) FROM trades WHERE status='CLOSED'", 0)
    net_pnl = _sqlite_scalar("trades.db", "SELECT COALESCE(SUM(realized_pnl),0) FROM trades WHERE status='CLOSED'", 0)
    profitable = _sqlite_scalar("trades.db", "SELECT COUNT(*) FROM trades WHERE status='CLOSED' AND realized_pnl>0", 0)
    # Generated signals, not rare executions, are the strategy-learning sample.
    # Execution count remains infrastructure telemetry and never trains edge.
    if int(fill.get("paper", 0) or 0) < 10:
        warnings.append("paper_execution_telemetry_below_10")
    paired_fills = fill.get("paired_fill_comparison", {}) if isinstance(fill, dict) else {}
    if int(paired_fills.get("paired_fills", 0) or 0) < int(paired_fills.get("target_paired_fills", 100) or 100):
        blocks.append("paper_live_fill_comparisons_below_100")
    broker_status = health.get("broker_status", []) if isinstance(health.get("broker_status"), list) else []
    broker_connected = any(bool(row.get("connected")) for row in broker_status if isinstance(row, dict))
    if not broker_connected:
        blocks.append("broker_connectivity_unverified")
    custom_barriers = _sqlite_scalar(
        "signal_log.db", "SELECT COUNT(*) FROM signal_log WHERE tb_used_custom_barrier=1", 0
    )
    if int(custom_barriers or 0) <= 0:
        blocks.append("no_setup_specific_barrier_labels")
    if not release_integrity.get("ok"):
        blocks.append("release_integrity_unverified")
    elif not release_integrity.get("content_digest_matches", False):
        blocks.append("release_content_digest_unverified")
    label_detail = (
        data_audit.get("checks", [{}]) if isinstance(data_audit.get("checks"), list) else []
    )
    labelled_check = next((c for c in label_detail if c.get("name") == "labelled_dataset"), {})
    if int(labelled_check.get("distinct_days", 0) or 0) < int(labelled_check.get("target_days", 15) or 15):
        blocks.append("labelled_days_below_target")
    if int(labelled_check.get("labelled", 0) or 0) < int(labelled_check.get("target_labelled", 5000) or 5000):
        blocks.append("clean_generated_signal_outcomes_below_target")
    if (
        int(labelled_check.get("labelled", 0) or 0) >= int(labelled_check.get("target_labelled", 5000) or 5000)
        and not bool(shadow_portfolio.get("after_cost_positive"))
    ):
        blocks.append("generated_signal_after_cost_edge_not_positive")
    if latest_option_ok and (_age_hours(latest_option_ok) or 999) > 24 and datetime.now().weekday() < 5:
        warnings.append("option_chain_snapshot_stale_market_day")
    if float(fill.get("fill_latency_coverage_pct", 0.0) or 0.0) < 80 and int(fill.get("live", 0) or 0) > 0:
        warnings.append("live_fill_latency_coverage_low")
    # Any explicit watchdog failure is a live-admission failure.  A numeric
    # threshold here previously allowed known-stale candle groups to remain a
    # warning merely because there were fewer than 20 of them.
    if not bool(quality.get("ok", False)):
        blocks.append("candle_quality_watchdog_failed")
    if int(quality.get("bad_groups", 0) or 0) > 20:
        warnings.append("candle_quality_bad_groups_high")
    if int(quality.get("stale_groups", 0) or 0) > 20:
        warnings.append("intraday_candle_cache_stale")
    if int(experiments or 0) == 0:
        warnings.append("experiment_registry_empty")
    if not bias_audit.get("ok"):
        blocks.append("indicator_lookahead_audit_failed")
    if not evidence_catalog.get("ok"):
        warnings.append("stored_data_catalog_has_integrity_issues")
    if not execution_chain.get("ok"):
        blocks.append("execution_audit_chain_invalid")
    elif int(execution_chain.get("chained_rows", 0) or 0) == 0:
        warnings.append("execution_audit_chain_awaiting_new_events")
    if not training_contract.get("ok"):
        blocks.append("ml_training_contract_audit_failed")

    raw_data_score = (data_audit.get("score") or {}).get("total")
    raw_inst_score = (data_audit.get("institutional_readiness") or {}).get("total")
    data_score = min(float(raw_data_score or 0), 79.0) if not latest_option_ok else raw_data_score
    institutional_score = min(float(raw_inst_score or 0), 59.0) if blocks else raw_inst_score
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": {
            "paper_training_only": bool(live_elig.get("paper_training_only", True)),
            "live_ready_count": int(live_elig.get("live_ready_count", 0) or 0),
            "total_strategies": int(live_elig.get("total_strategies", 0) or 0),
            "live_block_reason": live_elig.get("live_block_reason", ""),
        },
        "scores": {
            "data_pipeline": data_score,
            "data_pipeline_grade": "A" if float(data_score or 0) >= 90 else "B" if float(data_score or 0) >= 80 else "C" if float(data_score or 0) >= 70 else "D" if float(data_score or 0) >= 60 else "F",
            "data_pipeline_raw_capability": raw_data_score,
            "institutional": institutional_score,
            "institutional_grade": "A" if float(institutional_score or 0) >= 90 else "B" if float(institutional_score or 0) >= 80 else "C" if float(institutional_score or 0) >= 70 else "D" if float(institutional_score or 0) >= 60 else "F",
            "institutional_raw_capability": raw_inst_score,
            "option_bot": option_score.get("total"),
            "option_bot_grade": option_score.get("grade"),
            "option_bot_capability": option_score.get("capability_score", option_score.get("raw_capability_score")),
            "option_bot_evidence": option_score.get("evidence_score", option_score.get("total")),
        },
        "data": {
            "candle_1m_symbols": int(candles_1m or 0),
            "candle_1d_symbols": int(candles_1d or 0),
            "derived_daily_symbols": int(derived.get("symbols_ok", 0) or 0),
            "bad_candle_groups": int(quality.get("bad_groups", 0) or 0),
            "stale_candle_groups": int(quality.get("stale_groups", 0) or 0),
            "total_candle_groups": int(quality.get("total_groups", 0) or 0),
            "latest_option_snapshot_ok": latest_option_ok,
            "latest_option_snapshot_age_hours": _age_hours(latest_option_ok),
        },
        "execution": {
            "trades": int(fill.get("trades", 0) or 0),
            "live_trades": int(fill.get("live", 0) or 0),
            "order_id_coverage_pct": fill.get("order_id_coverage_pct"),
            "fill_status_coverage_pct": fill.get("fill_status_coverage_pct"),
            "fill_latency_coverage_pct": fill.get("fill_latency_coverage_pct"),
            "entry_slippage_coverage_pct": fill.get("entry_slippage_coverage_pct"),
            "avg_entry_slippage_pct": fill.get("avg_entry_slippage_pct"),
            "paired_fill_comparison": paired_fills,
            "gross_pnl": round(float(gross_pnl or 0), 2),
            "net_pnl": round(float(net_pnl or 0), 2),
            "profitable_trades": int(profitable or 0),
            "broker_connected": broker_connected,
            "role": "execution_infrastructure_validation_only",
        },
        "learning": {
            "experiments_logged": int(experiments or 0),
            "labelled_rows": int(labelled_check.get("labelled", 0) or 0),
            "labelled_days": int(labelled_check.get("distinct_days", 0) or 0),
            "target_labelled": int(labelled_check.get("target_labelled", 5000) or 5000),
            "target_days": int(labelled_check.get("target_days", 15) or 15),
            "custom_barrier_labels": int(custom_barriers or 0),
            "edge_conclusion": edge.get("conclusion", ""),
            "sample_source": "all_training_eligible_generated_signals",
            "legacy_labelled_rows": int(labelled_check.get("legacy_labelled", 0) or 0),
            "shadow_portfolio": shadow_portfolio,
        },
        "blocks": list(dict.fromkeys(blocks)),
        "warnings": warnings,
        "release_integrity": release_integrity,
        "assurance": {
            "indicator_lookahead": bias_audit,
            "stored_data_catalog": {
                "ok": evidence_catalog.get("ok"),
                "databases": evidence_catalog.get("database_count", 0),
                "tables": evidence_catalog.get("table_count", 0),
                "rows": evidence_catalog.get("total_rows", 0),
                "issues": evidence_catalog.get("issues", []),
                "empty_databases": evidence_catalog.get("empty_databases", []),
            },
            "execution_audit_chain": execution_chain,
            "ml_training_contract": training_contract,
        },
        "ready_for_scaled_live": not blocks and not warnings,
    }
    from audit_artifacts import evidence_scorecard
    report["score_dimensions"] = evidence_scorecard(
        capability=float(report["scores"].get("option_bot_capability", 0) or 0),
        live_ready=int(report["mode"].get("live_ready_count", 0) or 0),
        total_strategies=int(report["mode"].get("total_strategies", 0) or 0),
        paired_fills=int(paired_fills.get("paired_fills", 0) or 0),
        target_paired_fills=int(paired_fills.get("target_paired_fills", 100) or 100),
        net_pnl=float(report["execution"].get("net_pnl", 0) or 0),
    )
    if write:
        from audit_artifacts import write_report_with_snapshot
        write_report_with_snapshot(report_file, report)
    return report


def render_summary(report: Dict[str, Any]) -> str:
    scores = report.get("scores", {})
    mode = report.get("mode", {})
    data = report.get("data", {})
    learning = report.get("learning", {})
    return "\n".join([
        "SYSTEM READINESS",
        f"data_pipeline={scores.get('data_pipeline')}/{scores.get('data_pipeline_grade')} institutional={scores.get('institutional')}/{scores.get('institutional_grade')} option_bot={scores.get('option_bot')}/{scores.get('option_bot_grade')}",
        f"live_ready={mode.get('live_ready_count')}/{mode.get('total_strategies')} reason={mode.get('live_block_reason') or 'ok'}",
        f"candles 1m={data.get('candle_1m_symbols')} 1d={data.get('candle_1d_symbols')} bad_groups={data.get('bad_candle_groups')}/{data.get('total_candle_groups')} stale_groups={data.get('stale_candle_groups')}",
        f"latest_option_snapshot={data.get('latest_option_snapshot_ok')} age_h={data.get('latest_option_snapshot_age_hours')}",
        f"labels={learning.get('labelled_rows')}/{learning.get('target_labelled')} days={learning.get('labelled_days')}/{learning.get('target_days')} experiments={learning.get('experiments_logged')}",
        f"blocks={','.join(report.get('blocks') or []) or 'none'}",
        f"warnings={','.join(report.get('warnings') or []) or 'none'}",
    ])


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_system_readiness_report(write=not args.no_write)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
