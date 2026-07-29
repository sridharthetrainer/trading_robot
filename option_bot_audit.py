#!/usr/bin/env python3
"""
option_bot_audit.py

Fast health check for the live option bot data loop:
- option-chain snapshot attempts,
- option decision journal coverage,
- strike autotune sample count,
- historical options database availability,
- live signal-log option metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

REPORT_FILE = "option_bot_audit_report.json"

from trading_calendar import is_trading_day, session_lag


def _is_market_day(now: datetime | None = None) -> bool:
    return is_trading_day(now or datetime.now())


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


def _sqlite_scalar(path: Path, sql: str, default: Any = 0) -> Any:
    if not path.exists():
        return default
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(sql).fetchone()
            return row[0] if row else default
    except Exception:
        return default


def _snapshot_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "rows": 0, "ok_rows": 0, "latest": None}
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(option_chain_snapshots)").fetchall()}
        rows = conn.execute("SELECT COUNT(*) FROM option_chain_snapshots").fetchone()[0]
        latest = conn.execute("SELECT MAX(snapshot_time) FROM option_chain_snapshots").fetchone()[0]
        if "ok" in cols:
            ok_rows = conn.execute("SELECT COUNT(*) FROM option_chain_snapshots WHERE ok=1").fetchone()[0]
            latest_ok = conn.execute(
                "SELECT MAX(snapshot_time) FROM option_chain_snapshots WHERE ok=1"
            ).fetchone()[0]
            recent_failures = conn.execute(
                """
                SELECT underlying, reason, COUNT(*)
                FROM option_chain_snapshots
                WHERE ok=0
                GROUP BY underlying, reason
                ORDER BY COUNT(*) DESC
                LIMIT 5
                """
            ).fetchall()
        else:
            ok_rows = rows
            latest_ok = latest
            recent_failures = []
        if {"source", "is_live"} <= cols:
            verified_live_rows = conn.execute(
                "SELECT COUNT(*) FROM option_chain_snapshots "
                "WHERE ok=1 AND is_live=1 AND COALESCE(source,'')<>''"
            ).fetchone()[0]
            latest_verified_live = conn.execute(
                "SELECT MAX(snapshot_time) FROM option_chain_snapshots "
                "WHERE ok=1 AND is_live=1 AND COALESCE(source,'')<>''"
            ).fetchone()[0]
        else:
            verified_live_rows = 0
            latest_verified_live = None
        strike_rows = 0
        strike_labelled = 0
        verified_strike_outcomes = 0
        today_strike_rows = 0
        today_strike_tradable = 0
        today_strike_labelled = 0
        if conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='option_strike_signals'"
        ).fetchone()[0]:
            strike_cols = {r[1] for r in conn.execute("PRAGMA table_info(option_strike_signals)")}
            strike_rows = conn.execute("SELECT COUNT(*) FROM option_strike_signals").fetchone()[0]
            today_prefix = datetime.now().strftime("%Y-%m-%d") + "%"
            today_strike_rows, today_strike_tradable = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(tradable),0) FROM option_strike_signals "
                "WHERE snapshot_time LIKE ?", (today_prefix,)
            ).fetchone()
            if "outcome_label" in strike_cols:
                strike_labelled = conn.execute(
                    "SELECT COUNT(*) FROM option_strike_signals WHERE outcome_label IN (-1,0,1)"
                ).fetchone()[0]
                verified_strike_outcomes = conn.execute(
                    "SELECT COUNT(*) FROM option_strike_signals WHERE outcome_label IN (-1,0,1) "
                    "AND lower(COALESCE(source,'')) IN "
                    "('nse_live','resilience_nse','angel','angel_fallback','sensibull','bse','bse_oc')"
                ).fetchone()[0]
                today_strike_labelled = conn.execute(
                    "SELECT COUNT(*) FROM option_strike_signals "
                    "WHERE snapshot_time LIKE ? AND outcome_label IN (-1,0,1)",
                    (today_prefix,),
                ).fetchone()[0]
    latest_ok_dt = _parse_dt(latest_ok)
    latest_ok_age_hours = None
    if latest_ok_dt is not None:
        now = datetime.now(tz=latest_ok_dt.tzinfo) if latest_ok_dt.tzinfo else datetime.now()
        latest_ok_age_hours = round(max(0.0, (now - latest_ok_dt).total_seconds() / 3600.0), 2)
    return {
        "exists": True,
        "rows": int(rows or 0),
        "ok_rows": int(ok_rows or 0),
        "verified_live_rows": int(verified_live_rows or 0),
        "latest_verified_live": latest_verified_live,
        "latest": latest,
        "latest_ok": latest_ok,
        "latest_ok_age_hours": latest_ok_age_hours,
        "recent_failures": [
            {"underlying": r[0], "reason": r[1], "count": r[2]} for r in recent_failures
        ],
        "strike_signal_rows": int(strike_rows or 0),
        "strike_signal_labelled": int(strike_labelled or 0),
        "verified_strike_outcomes": int(verified_strike_outcomes or 0),
        "today_strike_rows": int(today_strike_rows or 0),
        "today_strike_tradable": int(today_strike_tradable or 0),
        "today_strike_labelled": int(today_strike_labelled or 0),
    }


def _journal_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "rows": 0, "decisions": {}, "today_rows": 0, "today_decisions": {}}
    today = datetime.now().strftime("%Y-%m-%d")
    decisions: Dict[str, int] = {}
    today_decisions: Dict[str, int] = {}
    rows = 0
    today_rows = 0
    selected_with_shadow = 0
    verified_selected = 0
    executed_selected = 0
    synthetic_rows = 0
    research_rows = 0
    verified_outcomes = 0
    verified_shadow_outcomes = 0
    today_selected = 0
    today_blocked = 0
    today_chain_signal = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        rows += 1
        decision = str(row.get("decision", "") or "")
        decisions[decision] = decisions.get(decision, 0) + 1
        row_day = str(row.get("time", "") or "")[:10]
        if row_day == today:
            today_rows += 1
            today_decisions[decision] = today_decisions.get(decision, 0) + 1
            if decision.startswith("selected"):
                today_selected += 1
            elif decision.startswith("blocked"):
                today_blocked += 1
            elif decision.startswith("chain_"):
                today_chain_signal += 1
        if decision.startswith("selected") and isinstance(row.get("strikes"), list) and row["strikes"]:
            selected_with_shadow += 1
        evidence_class = str(row.get("evidence_class") or "")
        synthetic = evidence_class == "RESEARCH_SYNTHETIC" or bool(row.get("selected_synthetic"))
        if synthetic:
            synthetic_rows += 1
        research = (
            evidence_class == "RESEARCH_SYNTHETIC"
            or "backfill" in str(row.get("reason") or "").lower()
            or "replay" in str(row.get("strategy") or "").lower()
            or "historical" in str(row.get("strategy") or "").lower()
        )
        if research:
            research_rows += 1
        if decision.startswith("selected") and bool(row.get("is_live_data")) and not synthetic:
            verified_selected += 1
            if row.get("trade_id"):
                executed_selected += 1
            if row.get("outcome_label") in (-1, 0, 1) or isinstance(row.get("outcome"), dict):
                verified_outcomes += 1
            verified_shadow_outcomes += sum(
                1 for item in (row.get("strikes") or [])
                if isinstance(item, dict)
                and not item.get("synthetic_shadow")
                and isinstance(item.get("shadow_outcome"), dict)
            )
    return {
        "exists": True,
        "rows": rows,
        "decisions": decisions,
        "today_rows": today_rows,
        "today_decisions": today_decisions,
        "today_selected": today_selected,
        "today_blocked": today_blocked,
        "today_chain_signal": today_chain_signal,
        "selected_with_shadow": selected_with_shadow,
        "verified_selected": verified_selected,
        "executed_selected": executed_selected,
        "synthetic_rows": synthetic_rows,
        "research_rows": research_rows,
        "verified_outcomes": verified_outcomes,
        "verified_shadow_outcomes": verified_shadow_outcomes,
        "verified_generated_outcomes": verified_outcomes + verified_shadow_outcomes,
    }


def _autotune_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "labelled_selected": 0, "labelled_shadow": 0, "weights": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        data = {}
    weights = data.get("feature_weights", {}) if isinstance(data.get("feature_weights"), dict) else {}
    return {
        "exists": True,
        "labelled_selected": int(data.get("labelled_selected", 0) or 0),
        "labelled_shadow": int(data.get("labelled_shadow", 0) or 0),
        "weights": len(weights),
        "verified_generated_outcomes": int(data.get("verified_generated_outcomes", 0) or 0),
    }


def _structure_mining_stats(path: Path) -> Dict[str, Any]:
    db_path = Path("option_structure_training.db")
    stats: Dict[str, Any] = {
        "report_exists": path.exists(),
        "db_exists": db_path.exists(),
        "legs": 0,
        "symbols": 0,
        "latest_session": None,
        "top_edges": 0,
    }
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            stats["report_legs"] = int(data.get("legs", 0) or 0)
            stats["top_edges"] = len(data.get("top_edges", []) or [])
        except Exception:
            pass
    if db_path.exists():
        stats["legs"] = _sqlite_scalar(db_path, "SELECT COUNT(*) FROM option_structure_legs", 0)
        stats["symbols"] = _sqlite_scalar(db_path, "SELECT COUNT(DISTINCT symbol) FROM option_structure_legs", 0)
        stats["latest_session"] = _sqlite_scalar(db_path, "SELECT MAX(session_date) FROM option_structure_legs", None)
    return stats


def _hero_zero_stats(path: Path) -> Dict[str, Any]:
    rows = 0
    selected = 0
    blocked = 0
    live_micro = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("strategy", "")) != "hero_zero":
                continue
            rows += 1
            decision = str(row.get("decision", ""))
            if decision.startswith("selected"):
                selected += 1
            if decision.startswith("blocked"):
                blocked += 1
            meta = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
            if meta.get("live_micro_ok"):
                live_micro += 1
    return {
        "module_exists": Path("hero_zero_strategy.py").exists(),
        "mode": os.getenv("HERO_ZERO_MODE", "shadow"),
        "live_vote": os.getenv("HERO_ZERO_LIVE_VOTE", "false").lower() == "true",
        "probation_block": os.getenv("LIVE_PROBATION_BLOCK_HERO_ZERO", "true").lower() != "false",
        "journal_rows": rows,
        "selected": selected,
        "blocked": blocked,
        "live_micro_ok_rows": live_micro,
    }


def _option_scalping_stats() -> Dict[str, Any]:
    try:
        import config as cfg
        enabled = bool(getattr(cfg, "ENABLE_PIVOT_SCALPING_STRATEGY", True))
        underlyings = list(getattr(cfg, "PIVOT_SCALPING_UNDERLYINGS", []) or [])
        max_lots = int(getattr(cfg, "PIVOT_SCALPING_MAX_LOTS", 0) or 0)
        stop_0dte = float(getattr(cfg, "PIVOT_SCALPING_OPTION_STOP_0DTE", 0.0) or 0.0)
        target_rr = float(getattr(cfg, "PIVOT_SCALPING_OPTION_TARGET_RR", 0.0) or 0.0)
        max_hold = int(getattr(cfg, "PIVOT_SCALPING_MAX_HOLD_MINUTES", 0) or 0)
    except Exception:
        enabled = False
        underlyings = []
        max_lots = 0
        stop_0dte = 0.0
        target_rr = 0.0
        max_hold = 0
    live_text = Path("live_signal_engine.py").read_text(encoding="utf-8", errors="replace") if Path("live_signal_engine.py").exists() else ""
    trade_text = Path("trade_manager.py").read_text(encoding="utf-8", errors="replace") if Path("trade_manager.py").exists() else ""
    return {
        "module_exists": Path("pivot_scalping_strategy.py").exists(),
        "enabled": enabled,
        "underlyings": underlyings,
        "underlying_count": len(underlyings),
        "fetch_1m": bool(getattr(__import__("config"), "PIVOT_SCALPING_FETCH_1M", True)) if Path("config.py").exists() else False,
        "max_lots": max_lots,
        "stop_0dte_pct": stop_0dte,
        "target_rr": target_rr,
        "max_hold_minutes": max_hold,
        "live_engine_style_hook": "is_pivot_scalp" in live_text and "style = \"scalping\"" in live_text,
        "trade_manager_time_exit": "PIVOT_SCALPING_MAX_HOLD_MINUTES" in trade_text,
    }


def _autonomous_policy_stats() -> Dict[str, Any]:
    try:
        import config as cfg
        return {
            "option_first": bool(getattr(cfg, "AUTONOMOUS_OPTION_FIRST", True)),
            "cash_stock_last_resort": bool(getattr(cfg, "ENABLE_CASH_STOCK_LAST_RESORT", True)),
            "cash_last_resort_min_score": float(getattr(cfg, "CASH_LAST_RESORT_MIN_SCORE", 7.0) or 7.0),
            "allowed_styles": list(getattr(cfg, "AUTONOMOUS_ALLOWED_STYLES", []) or []),
            "parallel_styles": bool(getattr(cfg, "ENABLE_PARALLEL_OPTION_STYLES", True)),
            "parallel_style_order": list(getattr(cfg, "OPTION_PARALLEL_STYLE_ORDER", []) or []),
            "max_signals_per_cycle": int(getattr(cfg, "MAX_SIGNALS_PER_CYCLE", 2) or 2),
            "max_new_trades_per_style_per_cycle": int(getattr(cfg, "MAX_NEW_TRADES_PER_STYLE_PER_CYCLE", 1) or 1),
            "max_new_trades_per_underlying_per_cycle": int(getattr(cfg, "MAX_NEW_TRADES_PER_UNDERLYING_PER_CYCLE", 1) or 1),
            "qty_multipliers": dict(getattr(cfg, "OPTION_STYLE_QTY_MULTIPLIERS", {}) or {}),
            "cash_qty_multiplier": float(getattr(cfg, "CASH_LAST_RESORT_QTY_MULTIPLIER", 0.5) or 0.5),
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def _historical_replay_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "written": 0, "shadow_outcomes": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"exists": True, "ok": False, "reason": str(exc), "written": 0, "shadow_outcomes": 0}
    return {
        "exists": True,
        "ok": bool(data.get("ok", False)),
        "generated_at": data.get("generated_at"),
        "written": int(data.get("written", 0) or 0),
        "shadow_outcomes": int(data.get("shadow_outcomes", 0) or 0),
        "type_counts": data.get("type_counts", {}),
        "style_counts": data.get("style_counts", {}),
    }


def _quality_layer_stats() -> Dict[str, Any]:
    try:
        import config as cfg
        return {
            "selected_option_execution_quality": Path("option_execution_quality.py").exists()
            and bool(getattr(cfg, "ENABLE_SELECTED_OPTION_EXECUTION_QUALITY", True)),
            "post_trade_autopsy": Path("trade_autopsy.py").exists(),
            "min_selected_option_oi": float(getattr(cfg, "MIN_SELECTED_OPTION_OI", 100.0) or 100.0),
            "min_selected_option_volume": float(getattr(cfg, "MIN_SELECTED_OPTION_VOLUME", 100.0) or 100.0),
            "max_selected_option_spread_pct": float(getattr(cfg, "MAX_SELECTED_OPTION_SPREAD_PCT", 0.20) or 0.20),
            "require_selected_liquidity_fields": bool(getattr(cfg, "REQUIRE_SELECTED_OPTION_LIQUIDITY_FIELDS", False)),
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def _score_option_bot(audit: Dict[str, Any]) -> Dict[str, Any]:
    weights = {
        "snapshot_capture": 25,
        "decision_journal": 15,
        "strike_autotune": 20,
        "historical_options": 15,
        "signal_activity": 10,
        "telegram_oi_tools": 10,
        "automation": 5,
    }
    parts: Dict[str, Dict[str, Any]] = {}
    improvements = []

    snaps = audit.get("option_chain_snapshots", {}) or {}
    rows = int(snaps.get("rows", 0) or 0)
    ok_rows = int(snaps.get("ok_rows", 0) or 0)
    verified_live_rows = int(snaps.get("verified_live_rows", 0) or 0)
    ok_ratio = ok_rows / max(rows, 1)
    latest_ok_age_hours = snaps.get("latest_ok_age_hours")
    fresh_ok = latest_ok_age_hours is not None and float(latest_ok_age_hours) <= 96.0
    snap_score = 0.0
    if verified_live_rows >= 60:
        snap_score = weights["snapshot_capture"]
    elif verified_live_rows >= 20:
        snap_score = 18.0
    elif verified_live_rows >= 5:
        snap_score = 10.0
    elif rows > 0:
        snap_score = min(3.0, 3.0 * ok_ratio)
        improvements.append("Capture source-attributed live option-chain snapshots; historical ok rows are unverified.")
    else:
        improvements.append("Start market-hour option-chain snapshot collection.")
    if ok_rows > 0 and not fresh_ok:
        snap_score = min(snap_score, 18.0)
        improvements.append("Refresh successful option-chain snapshots; latest ok snapshot is stale.")
    parts["snapshot_capture"] = {
        "score": round(snap_score, 1),
        "max": weights["snapshot_capture"],
        "detail": f"verified_live={verified_live_rows}, ok_rows={ok_rows}, rows={rows}, latest={snaps.get('latest_verified_live')}",
    }

    journal = audit.get("decision_journal", {}) or {}
    journal_rows = int(journal.get("rows", 0) or 0)
    decisions = journal.get("decisions", {}) or {}
    selected = sum(int(v or 0) for k, v in decisions.items() if str(k).startswith("selected"))
    with_shadow = int(journal.get("selected_with_shadow", 0) or 0)
    verified_selected = int(journal.get("verified_selected", 0) or 0)
    executed_selected = int(journal.get("executed_selected", 0) or 0)
    verified_generated = max(
        int(journal.get("verified_generated_outcomes", 0) or 0),
        int(snaps.get("verified_strike_outcomes", 0) or 0),
    )
    journal_score = 0.0
    journal_score += 4.0 if journal.get("exists") else 0.0
    journal_score += 4.0 if journal_rows >= 20 else 2.0 if journal_rows > 0 else 0.0
    journal_score += 3.0 if verified_selected >= 10 else 1.5 if verified_selected > 0 else 0.0
    journal_score += 4.0 if verified_generated >= 100 else 2.0 if verified_generated >= 20 else 0.0
    if verified_selected == 0:
        improvements.append("Collect source-attributed generated option signals and EOD outcomes; synthetic research does not qualify.")
    parts["decision_journal"] = {
        "score": round(min(weights["decision_journal"], journal_score), 1),
        "max": weights["decision_journal"],
        "detail": f"rows={journal_rows}, selected={selected}, verified={verified_selected}, generated_outcomes={verified_generated}, executed={executed_selected}, research={journal.get('research_rows', 0)}",
    }

    tune = audit.get("strike_autotune", {}) or {}
    labelled_selected = int(tune.get("labelled_selected", 0) or 0)
    labelled_shadow = int(tune.get("labelled_shadow", 0) or 0)
    weights_count = int(tune.get("weights", 0) or 0)
    verified_tune_outcomes = int(tune.get("verified_generated_outcomes", 0) or 0)
    tune_score = 0.0
    tune_score += 3.0 if tune.get("exists") else 0.0
    tune_score += min(7.0, labelled_selected / 30.0 * 7.0)
    tune_score += min(7.0, labelled_shadow / 30.0 * 7.0)
    tune_score += 3.0 if weights_count >= 8 else 1.5 if weights_count > 0 else 0.0
    if labelled_selected < 30:
        improvements.append("Collect at least 30 labelled selected option trades for stable strike autotune.")
    if labelled_shadow < 30:
        improvements.append("Collect at least 30 labelled shadow strike outcomes.")
    structure = audit.get("option_structure_mining", {}) or {}
    if int(structure.get("legs", 0) or structure.get("report_legs", 0) or 0) == 0:
        improvements.append("Run EOD option structure mining to learn HH/HL, LH/LL, VWAP and OI-backed legs.")
    parts["strike_autotune"] = {
        "score": round(min(weights["strike_autotune"], tune_score), 1),
        "max": weights["strike_autotune"],
        "detail": f"research_selected={labelled_selected}, research_shadow={labelled_shadow}, verified_generated={verified_tune_outcomes}, weights={weights_count}",
    }

    hist = audit.get("historical_options", {}) or {}
    hist_rows = int(hist.get("rows", 0) or 0)
    hist_score = weights["historical_options"] if hist_rows >= 100000 else 8.0 if hist_rows >= 10000 else 0.0
    if hist_rows < 100000:
        improvements.append("Backfill more historical option EOD rows.")
    parts["historical_options"] = {
        "score": round(hist_score, 1),
        "max": weights["historical_options"],
        "detail": f"rows={hist_rows}, range={hist.get('first_date')}..{hist.get('last_date')}",
    }

    sig = audit.get("signal_log", {}) or {}
    journal_today_rows = int((audit.get("decision_journal", {}) or {}).get("today_rows", 0) or 0)
    journal_today_chain = int((audit.get("decision_journal", {}) or {}).get("today_chain_signal", 0) or 0)
    today_strike_rows = int(snaps.get("today_strike_rows", 0) or 0)
    today_rows = max(
        int(sig.get("today_option_rows", 0) or 0),
        journal_today_rows,
        today_strike_rows,
    )
    executed_rows = int(sig.get("today_executed_option_rows", 0) or 0)
    signal_score = 0.0
    signal_score += 4.0 if sig.get("exists") else 0.0
    if not _is_market_day():
        signal_score += 4.0
        signal_detail = f"market_closed_today, today_option_rows={today_rows}, executed={executed_rows}"
    else:
        signal_score += 4.0 if today_rows >= 5 else 2.0 if today_rows > 0 else 0.0
        signal_score += 2.0 if executed_rows > 0 else 0.0
        signal_detail = (
            f"today_option_rows={today_rows}, executed={executed_rows}, "
            f"journal_rows={journal_today_rows}, chain_rows={journal_today_chain}, "
            f"strike_rows={today_strike_rows}, "
            f"signal_log_option_rows={sig.get('signal_log_option_rows', 0)}"
        )
    if _is_market_day() and today_rows == 0:
        improvements.append("No option signal rows today yet; confirm market-hour scan and option-chain availability.")
    parts["signal_activity"] = {
        "score": round(min(weights["signal_activity"], signal_score), 1),
        "max": weights["signal_activity"],
        "detail": signal_detail,
    }

    tools = audit.get("telegram_oi_tools", {}) or {}
    tool_score = 0.0
    tool_score += 2.0 if tools.get("telegram_commands") else 0.0
    tool_score += 2.0 if tools.get("oi_chart") else 0.0
    tool_score += 2.0 if tools.get("strikeflow") else 0.0
    tool_score += 2.0 if tools.get("multi_strike_chart") else 0.0
    tool_score += 2.0 if tools.get("send_photo") else 0.0
    parts["telegram_oi_tools"] = {
        "score": round(tool_score, 1),
        "max": weights["telegram_oi_tools"],
        "detail": ", ".join(k for k, v in tools.items() if v) or "none",
    }

    automation = audit.get("automation", {}) or {}
    auto_score = 0.0
    auto_score += 2.0 if automation.get("live_engine_snapshot_hook") else 0.0
    auto_score += 1.5 if automation.get("recorder_loop") else 0.0
    auto_score += 1.5 if automation.get("eod_shadow_labeller") else 0.0
    parts["automation"] = {
        "score": round(auto_score, 1),
        "max": weights["automation"],
        "detail": ", ".join(k for k, v in automation.items() if v) or "none",
    }

    raw_total = round(sum(p["score"] for p in parts.values()), 1)
    evidence_blocks = []
    if verified_live_rows < 20:
        evidence_blocks.append("insufficient_verified_live_snapshots")
    if verified_generated < 100:   # spec: test_data_quality_upgrades (150 must pass); 37358f66 reverted this
        evidence_blocks.append("insufficient_verified_option_signal_outcomes")
    if verified_selected == 0:
        evidence_blocks.append("no_source_attributed_selected_option_outcomes")
    if executed_selected == 0:
        evidence_blocks.append("no_executed_option_fill_evidence")
    evidence_score = min(raw_total, 59.0) if evidence_blocks else raw_total
    grade = "A" if evidence_score >= 90 else "B" if evidence_score >= 80 else "C" if evidence_score >= 70 else "D" if evidence_score >= 60 else "F"
    readiness = (
        "EVIDENCE_READY"
        if evidence_score >= 85 and not evidence_blocks
        else "CAPABILITY_READY_EVIDENCE_PENDING"
        if raw_total >= 85
        else "PAPER_OR_SHADOW"
        if raw_total >= 60
        else "FIX_BEFORE_LIVE"
    )
    dedup = []
    seen = set()
    for item in improvements:
        if item not in seen:
            dedup.append(item)
            seen.add(item)
    return {
        "total": evidence_score,
        "capability_score": raw_total,
        "evidence_score": evidence_score,
        "max": sum(weights.values()),
        "grade": grade,
        "readiness": readiness,
        "autonomous_score": _score_option_bot_autonomy(audit),
        "raw_capability_score": raw_total,
        "minimum_verified_outcomes": 500,
        "evidence_blocks": evidence_blocks,
        "parts": parts,
        "top_improvements": dedup[:8],
    }


def _score_option_bot_autonomy(audit: Dict[str, Any]) -> Dict[str, Any]:
    weights = {
        "automation": 25,
        "telegram_oi_tools": 20,
        "historical_options": 20,
        "source_modules": 20,
        "scheduled_audits": 15,
    }
    parts: Dict[str, Dict[str, Any]] = {}

    automation = audit.get("automation", {}) or {}
    auto_keys = ("live_engine_snapshot_hook", "recorder_loop", "eod_shadow_labeller", "eod_structure_miner")
    auto_hits = sum(1 for key in auto_keys if automation.get(key))
    parts["automation"] = {
        "score": round(weights["automation"] * auto_hits / len(auto_keys), 1),
        "max": weights["automation"],
        "detail": f"{auto_hits}/{len(auto_keys)} autonomous option loops wired",
    }

    tools = audit.get("telegram_oi_tools", {}) or {}
    tool_hits = sum(1 for v in tools.values() if v)
    parts["telegram_oi_tools"] = {
        "score": round(weights["telegram_oi_tools"] * tool_hits / max(1, len(tools)), 1),
        "max": weights["telegram_oi_tools"],
        "detail": f"{tool_hits}/{len(tools)} command/chart tools wired",
    }

    hist_rows = int((audit.get("historical_options", {}) or {}).get("rows", 0) or 0)
    hist_score = weights["historical_options"] if hist_rows >= 100000 else weights["historical_options"] * 0.5 if hist_rows >= 10000 else 0.0
    parts["historical_options"] = {
        "score": round(hist_score, 1),
        "max": weights["historical_options"],
        "detail": f"{hist_rows} historical option rows",
    }

    source_modules = audit.get("source_modules", {}) or {}
    source_hits = sum(1 for v in source_modules.values() if v)
    parts["source_modules"] = {
        "score": round(weights["source_modules"] * source_hits / max(1, len(source_modules)), 1),
        "max": weights["source_modules"],
        "detail": f"{source_hits}/{len(source_modules)} option source modules present",
    }

    scheduled = audit.get("scheduled_audits", {}) or {}
    scheduled_hits = sum(1 for v in scheduled.values() if v)
    parts["scheduled_audits"] = {
        "score": round(weights["scheduled_audits"] * scheduled_hits / max(1, len(scheduled)), 1),
        "max": weights["scheduled_audits"],
        "detail": f"{scheduled_hits}/{len(scheduled)} audit/report schedules wired",
    }

    raw_total = round(sum(p["score"] for p in parts.values()), 1)
    snaps = audit.get("option_chain_snapshots", {}) or {}
    journal = audit.get("decision_journal", {}) or {}
    evidence_blocks = []
    if int(snaps.get("verified_live_rows", 0) or 0) < 20:
        evidence_blocks.append("insufficient_verified_live_snapshots")
    verified_generated = max(
        int(journal.get("verified_generated_outcomes", 0) or 0),
        int(snaps.get("verified_strike_outcomes", 0) or 0),
    )
    if verified_generated < 100:   # spec: test_data_quality_upgrades (150 must pass); 37358f66 reverted this
        evidence_blocks.append("insufficient_verified_option_signal_outcomes")
    total = min(raw_total, 59.0) if evidence_blocks else raw_total
    grade = "A" if total >= 90 else "B" if total >= 80 else "C" if total >= 70 else "D" if total >= 60 else "F"
    return {
        "total": total,
        "max": sum(weights.values()),
        "grade": grade,
        "parts": parts,
        "raw_capability_score": raw_total,
        "evidence_blocks": evidence_blocks,
    }


def _recorder_cadence_ok(db: Path, min_rows_per_underlying: int = 30) -> bool:
    """True when the most recent snapshot day shows a real 5-min-ish cadence.

    A 5-min loop yields ~75 rows/underlying/session; the live-engine hook
    alone (scan-paced, ~26 min) yields ~14. The threshold separates 'the
    recorder actually ran' from 'only the fallback hook fired'.
    """
    if not db.exists():
        return False
    try:
        with sqlite3.connect(db) as conn:
            last_day = conn.execute(
                "SELECT MAX(substr(snapshot_time,1,10)) FROM option_chain_snapshots WHERE ok=1"
            ).fetchone()[0]
            if not last_day:
                return False
            per_underlying = conn.execute(
                "SELECT MAX(cnt) FROM (SELECT COUNT(*) cnt FROM option_chain_snapshots "
                "WHERE ok=1 AND snapshot_time LIKE ? GROUP BY underlying)",
                (last_day + "%",),
            ).fetchone()[0]
            return int(per_underlying or 0) >= min_rows_per_underlying
    except Exception:
        return False


def build_audit() -> Dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    signal_db = Path("signal_log.db")
    hist_db = Path("options_nifty.db")
    try:
        from option_decision_journal import ensure_option_journal
        ensure_option_journal()
    except Exception:
        pass
    audit = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "option_chain_snapshots": _snapshot_stats(Path("option_chain_snapshots.db")),
        "decision_journal": _journal_stats(Path("option_decision_journal.jsonl")),
        "strike_autotune": _autotune_stats(Path("option_strike_autotune.json")),
        "option_structure_mining": _structure_mining_stats(Path("eod_option_structure_report.json")),
        "hero_zero": _hero_zero_stats(Path("option_decision_journal.jsonl")),
        "option_scalping": _option_scalping_stats(),
        "autonomous_policy": _autonomous_policy_stats(),
        "historical_replay": _historical_replay_stats(Path("option_historical_replay_report.json")),
        "quality_layer": _quality_layer_stats(),
        "signal_log": {
            "exists": signal_db.exists(),
            "signal_log_option_rows": _sqlite_scalar(
                signal_db,
                f"SELECT COUNT(*) FROM signal_log WHERE signal_date='{today}' AND option_strike > 0",
                0,
            ),
            "today_option_rows": max(
                _sqlite_scalar(
                    signal_db,
                    f"SELECT COUNT(*) FROM signal_log WHERE signal_date='{today}' AND option_strike > 0",
                    0,
                ),
                int((_journal_stats(Path("option_decision_journal.jsonl")) or {}).get("today_rows", 0) or 0),
            ),
            "today_executed_option_rows": _sqlite_scalar(
                signal_db,
                f"SELECT COUNT(*) FROM signal_log WHERE signal_date='{today}' AND executed=1 AND option_strike > 0",
                0,
            ),
        },
        "historical_options": {
            "exists": hist_db.exists(),
            "rows": _sqlite_scalar(hist_db, "SELECT COUNT(*) FROM options_eod", 0),
            "first_date": _sqlite_scalar(hist_db, "SELECT MIN(date) FROM options_eod", None),
            "last_date": _sqlite_scalar(hist_db, "SELECT MAX(date) FROM options_eod", None),
        },
        "telegram_oi_tools": {
            "telegram_commands": Path("telegram_commands.py").exists(),
            "oi_chart": Path("option_oi_chart.py").exists(),
            "strikeflow": Path("option_strike_activity.py").exists(),
            "multi_strike_chart": "compare_top" in Path("option_oi_chart.py").read_text(encoding="utf-8", errors="replace")
            if Path("option_oi_chart.py").exists() else False,
            "send_photo": "def send_photo" in Path("telegram_commands.py").read_text(encoding="utf-8", errors="replace")
            if Path("telegram_commands.py").exists() else False,
        },
        "automation": {
            "live_engine_snapshot_hook": "_record_learning_snapshots" in Path("live_signal_engine.py").read_text(encoding="utf-8", errors="replace")
            if Path("live_signal_engine.py").exists() else False,
            # Runtime evidence, not code presence: the loop existed for weeks
            # while NOTHING ran it (no unit, no cron) and this flag stayed
            # green. Require an actual 5-min-ish cadence on the most recent
            # snapshot day (>=30 rows; engine-paced fallback gives ~14/underlying).
            "recorder_loop": _recorder_cadence_ok(Path("option_chain_snapshots.db")),
            "eod_shadow_labeller": Path("option_shadow_labeller.py").exists()
            and "option_shadow_labels" in Path("autonomous_learning_cycle.py").read_text(encoding="utf-8", errors="replace"),
            "eod_structure_miner": Path("eod_option_structure_miner.py").exists()
            and "option_structure_mining" in Path("autonomous_learning_cycle.py").read_text(encoding="utf-8", errors="replace"),
        },
        "source_modules": {
            "option_chain_fetcher": Path("option_chain_fetcher.py").exists(),
            "option_chain_engine": Path("option_chain_engine.py").exists(),
            "option_chain_intelligence": Path("option_chain_intelligence.py").exists(),
            "option_chain_recorder": Path("option_chain_recorder.py").exists(),
            "option_strike_activity": Path("option_strike_activity.py").exists(),
            "option_oi_chart": Path("option_oi_chart.py").exists(),
            "option_shadow_labeller": Path("option_shadow_labeller.py").exists(),
            "option_strike_autotune": Path("option_strike_autotune.py").exists(),
            "option_historical_replay_labeller": Path("option_historical_replay_labeller.py").exists(),
            "option_execution_quality": Path("option_execution_quality.py").exists(),
            "trade_autopsy": Path("trade_autopsy.py").exists(),
            "eod_option_structure_miner": Path("eod_option_structure_miner.py").exists(),
            "pivot_scalping_strategy": Path("pivot_scalping_strategy.py").exists(),
        },
        "scheduled_audits": {
            "idle_engine": Path("idle_engine.py").exists(),
            "option_bot_audit_task": "option_bot_audit" in Path("idle_engine.py").read_text(encoding="utf-8", errors="replace")
            if Path("idle_engine.py").exists() else False,
            "data_pipeline_audit_task": "data_pipeline_audit" in Path("idle_engine.py").read_text(encoding="utf-8", errors="replace")
            if Path("idle_engine.py").exists() else False,
            "autonomous_learning_task": "autolearn" in Path("idle_engine.py").read_text(encoding="utf-8", errors="replace")
            if Path("idle_engine.py").exists() else False,
        },
    }
    audit["score"] = _score_option_bot(audit)
    journal = audit.get("decision_journal", {}) or {}
    from audit_artifacts import evidence_scorecard
    audit["score_dimensions"] = evidence_scorecard(
        capability=float(audit["score"].get("capability_score", 0) or 0),
        live_ready=1 if int(journal.get("verified_outcomes", 0) or 0) > 0 else 0,
        total_strategies=1,
        paired_fills=int(journal.get("executed_selected", 0) or 0),
        target_paired_fills=100,
        net_pnl=0.0,
    )
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    parser.add_argument("--no-write", action="store_true", help="do not refresh option_bot_audit_report.json")
    args = parser.parse_args()
    audit = build_audit()
    if not args.no_write:
        from audit_artifacts import write_report_with_snapshot
        write_report_with_snapshot(REPORT_FILE, audit)
    if args.json:
        print(json.dumps(audit, indent=2, default=str))
        return 0
    score = audit.get("score", {})
    auto = score.get("autonomous_score", {}) or {}
    print("OPTION BOT AUDIT")
    print(f"Score: {score.get('total', 0)}/{score.get('max', 100)} grade={score.get('grade')} readiness={score.get('readiness')}")
    print(f"Autonomous score: {auto.get('total', 0)}/{auto.get('max', 100)} grade={auto.get('grade')}")
    for name, part in (score.get("parts", {}) or {}).items():
        print(f"- {name}: {part.get('score')}/{part.get('max')} | {part.get('detail')}")
    improvements = score.get("top_improvements", []) or []
    if improvements:
        print("\nImprovement priorities:")
        for i, item in enumerate(improvements, 1):
            print(f"{i}. {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
