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
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


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
            recent_failures = []
    return {
        "exists": True,
        "rows": int(rows or 0),
        "ok_rows": int(ok_rows or 0),
        "latest": latest,
        "recent_failures": [
            {"underlying": r[0], "reason": r[1], "count": r[2]} for r in recent_failures
        ],
    }


def _journal_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "rows": 0, "decisions": {}}
    decisions: Dict[str, int] = {}
    rows = 0
    selected_with_shadow = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        rows += 1
        decision = str(row.get("decision", "") or "")
        decisions[decision] = decisions.get(decision, 0) + 1
        if decision.startswith("selected") and isinstance(row.get("strikes"), list) and row["strikes"]:
            selected_with_shadow += 1
    return {
        "exists": True,
        "rows": rows,
        "decisions": decisions,
        "selected_with_shadow": selected_with_shadow,
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
    }


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
    ok_ratio = ok_rows / max(rows, 1)
    snap_score = 0.0
    if ok_rows >= 60:
        snap_score = weights["snapshot_capture"]
    elif ok_rows >= 20:
        snap_score = 18.0
    elif ok_rows >= 5:
        snap_score = 10.0
    elif rows > 0:
        snap_score = 3.0 * ok_ratio
        improvements.append("Capture successful option-chain snapshots during market hours; current ok_rows is too low.")
    else:
        improvements.append("Start market-hour option-chain snapshot collection.")
    parts["snapshot_capture"] = {
        "score": round(snap_score, 1),
        "max": weights["snapshot_capture"],
        "detail": f"ok_rows={ok_rows}, rows={rows}",
    }

    journal = audit.get("decision_journal", {}) or {}
    journal_rows = int(journal.get("rows", 0) or 0)
    selected = int((journal.get("decisions", {}) or {}).get("selected", 0) or 0)
    with_shadow = int(journal.get("selected_with_shadow", 0) or 0)
    journal_score = 0.0
    journal_score += 4.0 if journal.get("exists") else 0.0
    journal_score += 4.0 if journal_rows >= 20 else 2.0 if journal_rows > 0 else 0.0
    journal_score += 3.0 if selected >= 10 else 1.5 if selected > 0 else 0.0
    journal_score += 4.0 if with_shadow >= 10 else 1.0 if with_shadow > 0 else 0.0
    if with_shadow == 0:
        improvements.append("Generate selected option decisions with shadow strike candidates for comparison learning.")
    parts["decision_journal"] = {
        "score": round(min(weights["decision_journal"], journal_score), 1),
        "max": weights["decision_journal"],
        "detail": f"rows={journal_rows}, selected={selected}, selected_with_shadow={with_shadow}",
    }

    tune = audit.get("strike_autotune", {}) or {}
    labelled_selected = int(tune.get("labelled_selected", 0) or 0)
    labelled_shadow = int(tune.get("labelled_shadow", 0) or 0)
    weights_count = int(tune.get("weights", 0) or 0)
    tune_score = 0.0
    tune_score += 3.0 if tune.get("exists") else 0.0
    tune_score += min(7.0, labelled_selected / 30.0 * 7.0)
    tune_score += min(7.0, labelled_shadow / 30.0 * 7.0)
    tune_score += 3.0 if weights_count >= 8 else 1.5 if weights_count > 0 else 0.0
    if labelled_selected < 30:
        improvements.append("Collect at least 30 labelled selected option trades for stable strike autotune.")
    if labelled_shadow < 30:
        improvements.append("Collect at least 30 labelled shadow strike outcomes.")
    parts["strike_autotune"] = {
        "score": round(min(weights["strike_autotune"], tune_score), 1),
        "max": weights["strike_autotune"],
        "detail": f"selected={labelled_selected}, shadow={labelled_shadow}, weights={weights_count}",
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
    today_rows = int(sig.get("today_option_rows", 0) or 0)
    executed_rows = int(sig.get("today_executed_option_rows", 0) or 0)
    signal_score = 0.0
    signal_score += 4.0 if sig.get("exists") else 0.0
    signal_score += 4.0 if today_rows >= 5 else 2.0 if today_rows > 0 else 0.0
    signal_score += 2.0 if executed_rows > 0 else 0.0
    if today_rows == 0:
        improvements.append("No option signal rows today yet; confirm market-hour scan and option-chain availability.")
    parts["signal_activity"] = {
        "score": round(min(weights["signal_activity"], signal_score), 1),
        "max": weights["signal_activity"],
        "detail": f"today_option_rows={today_rows}, executed={executed_rows}",
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

    total = round(sum(p["score"] for p in parts.values()), 1)
    grade = "A" if total >= 90 else "B" if total >= 80 else "C" if total >= 70 else "D" if total >= 60 else "F"
    readiness = (
        "LIVE_READY"
        if total >= 85 and ok_rows >= 20 and labelled_selected >= 30 and labelled_shadow >= 10
        else "PAPER_OR_SHADOW"
        if total >= 60
        else "FIX_BEFORE_LIVE"
    )
    dedup = []
    seen = set()
    for item in improvements:
        if item not in seen:
            dedup.append(item)
            seen.add(item)
    return {
        "total": total,
        "max": sum(weights.values()),
        "grade": grade,
        "readiness": readiness,
        "autonomous_score": _score_option_bot_autonomy(audit),
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
    auto_hits = sum(1 for key in ("live_engine_snapshot_hook", "recorder_loop", "eod_shadow_labeller") if automation.get(key))
    parts["automation"] = {
        "score": round(weights["automation"] * auto_hits / 3.0, 1),
        "max": weights["automation"],
        "detail": f"{auto_hits}/3 autonomous option loops wired",
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

    total = round(sum(p["score"] for p in parts.values()), 1)
    grade = "A" if total >= 90 else "B" if total >= 80 else "C" if total >= 70 else "D" if total >= 60 else "F"
    return {
        "total": total,
        "max": sum(weights.values()),
        "grade": grade,
        "parts": parts,
    }


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
        "signal_log": {
            "exists": signal_db.exists(),
            "today_option_rows": _sqlite_scalar(
                signal_db,
                f"SELECT COUNT(*) FROM signal_log WHERE signal_date='{today}' AND option_strike > 0",
                0,
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
            "recorder_loop": "def run_snapshot_loop" in Path("option_chain_recorder.py").read_text(encoding="utf-8", errors="replace")
            if Path("option_chain_recorder.py").exists() else False,
            "eod_shadow_labeller": Path("option_shadow_labeller.py").exists()
            and "option_shadow_labels" in Path("autonomous_learning_cycle.py").read_text(encoding="utf-8", errors="replace"),
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
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args()
    audit = build_audit()
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
