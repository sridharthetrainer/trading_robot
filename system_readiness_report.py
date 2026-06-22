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
    data_audit = _read_json("data_pipeline_audit.json")
    option_audit = _read_json("option_bot_audit_report.json")
    option_score = option_audit.get("score") if isinstance(option_audit.get("score"), dict) else {}
    live_elig = _read_json("live_eligibility.json")
    fill = _read_json("execution_fill_telemetry.json")
    quality = _read_json("data_quality_watchdog_report.json")
    derived = _read_json("derived_daily_candles_report.json")

    latest_option_ok = _sqlite_scalar(
        "option_chain_snapshots.db",
        "SELECT MAX(snapshot_time) FROM option_chain_snapshots WHERE ok=1",
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
    label_detail = (
        data_audit.get("checks", [{}]) if isinstance(data_audit.get("checks"), list) else []
    )
    labelled_check = next((c for c in label_detail if c.get("name") == "labelled_dataset"), {})
    if int(labelled_check.get("distinct_days", 0) or 0) < int(labelled_check.get("target_days", 15) or 15):
        blocks.append("labelled_days_below_target")
    if latest_option_ok and (_age_hours(latest_option_ok) or 999) > 24 and datetime.now().weekday() < 5:
        warnings.append("option_chain_snapshot_stale_market_day")
    if float(fill.get("fill_latency_coverage_pct", 0.0) or 0.0) < 80 and int(fill.get("live", 0) or 0) > 0:
        warnings.append("live_fill_latency_coverage_low")
    if int(quality.get("bad_groups", 0) or 0) > 20:
        warnings.append("candle_quality_bad_groups_high")
    if int(quality.get("stale_groups", 0) or 0) > 20:
        warnings.append("intraday_candle_cache_stale")
    if int(experiments or 0) == 0:
        warnings.append("experiment_registry_empty")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": {
            "paper_training_only": bool(live_elig.get("paper_training_only", True)),
            "live_ready_count": int(live_elig.get("live_ready_count", 0) or 0),
            "total_strategies": int(live_elig.get("total_strategies", 0) or 0),
            "live_block_reason": live_elig.get("live_block_reason", ""),
        },
        "scores": {
            "data_pipeline": (data_audit.get("score") or {}).get("total"),
            "data_pipeline_grade": (data_audit.get("score") or {}).get("grade"),
            "institutional": (data_audit.get("institutional_readiness") or {}).get("total"),
            "institutional_grade": (data_audit.get("institutional_readiness") or {}).get("grade"),
            "option_bot": option_score.get("total"),
            "option_bot_grade": option_score.get("grade"),
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
        },
        "learning": {
            "experiments_logged": int(experiments or 0),
            "labelled_rows": int(labelled_check.get("labelled", 0) or 0),
            "labelled_days": int(labelled_check.get("distinct_days", 0) or 0),
            "target_labelled": int(labelled_check.get("target_labelled", 5000) or 5000),
            "target_days": int(labelled_check.get("target_days", 15) or 15),
        },
        "blocks": blocks,
        "warnings": warnings,
        "ready_for_scaled_live": not blocks and not warnings,
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
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
