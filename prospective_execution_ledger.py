#!/usr/bin/env python3
"""Append-only prospective execution evidence for frozen strategy cohorts.

The ledger never places orders and never changes signal selection.  A manifest
is frozen before observations begin; only signal rows strictly after its
boundary may be captured.  CAPTURED and OUTCOME_FINALIZED are separate,
append-only events so later labels cannot rewrite signal-time evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, Iterable


LEDGER_DB = "prospective_execution_ledger.db"
MANIFEST_FILE = "prospective_execution_manifest.json"
REPORT_FILE = "prospective_execution_report.json"
DEFAULT_STRATEGIES = (
    "mean_reversion",
    "breakout",
    "elder_triple_screen",
    "gap_fill",
    "liquidity_sweep",
)
MIN_OUTCOMES = 100
MIN_ACTIVE_DAYS = 30
EVALUATION_SESSIONS = 60
MIN_QUOTE_COVERAGE = 0.80
MAX_SIGNALS_PER_DAY = 5


def _canonical_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _future_sessions(start_after: str, count: int) -> list[str]:
    from trading_calendar import is_trading_day

    cursor = date.fromisoformat(start_after)
    sessions: list[str] = []
    while len(sessions) < count:
        cursor += timedelta(days=1)
        if is_trading_day(cursor):
            sessions.append(cursor.isoformat())
    return sessions


def initialise_experiment(
    *,
    manifest_path: str = MANIFEST_FILE,
    start_after_date: str | None = None,
    strategies: Iterable[str] = DEFAULT_STRATEGIES,
) -> Dict[str, Any]:
    """Create the immutable policy once; an existing manifest is never replaced."""
    path = Path(manifest_path)
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        expected = manifest.get("manifest_sha256")
        body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        if expected != _canonical_hash(body):
            raise ValueError("prospective manifest integrity check failed")
        return manifest

    boundary = start_after_date or date.today().isoformat()
    strategy_list = sorted({str(s).strip() for s in strategies if str(s).strip()})
    if not strategy_list:
        raise ValueError("at least one frozen strategy is required")
    sessions = _future_sessions(boundary, EVALUATION_SESSIONS)
    body: Dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": f"execution-{boundary}",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "start_boundary": f"signal_date > {boundary}",
        "start_after_date": boundary,
        "evaluation_sessions": sessions,
        "evaluation_end_date": sessions[-1],
        "strategies": strategy_list,
        "selection_policy": "first_signal_per_strategy_symbol_then_first_5_per_day",
        "max_signals_per_day": MAX_SIGNALS_PER_DAY,
        "outcome_field": "tb_r_multiple_net",
        "minimum_outcomes": MIN_OUTCOMES,
        "minimum_active_days": MIN_ACTIVE_DAYS,
        "minimum_quote_coverage": MIN_QUOTE_COVERAGE,
        "pass_rule": (
            "n>=100 AND active_days>=30 AND executable_spread_coverage>=80% "
            "AND signal_weighted_mean_net_r>0 AND equal_day_net_r_lcb95>0"
        ),
        "production_impact": "NONE_RESEARCH_ONLY",
    }
    manifest = {**body, "manifest_sha256": _canonical_hash(body)}
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ledger_events (
               event_id INTEGER PRIMARY KEY AUTOINCREMENT,
               experiment_id TEXT NOT NULL,
               signal_id INTEGER NOT NULL,
               event_type TEXT NOT NULL CHECK(event_type IN ('CAPTURED','OUTCOME_FINALIZED')),
               recorded_at REAL NOT NULL,
               signal_date TEXT NOT NULL,
               strategy TEXT NOT NULL,
               symbol TEXT NOT NULL,
               side TEXT NOT NULL,
               payload_json TEXT NOT NULL,
               source_sha256 TEXT NOT NULL,
               UNIQUE(experiment_id, signal_id, event_type)
           )"""
    )
    return conn


def _signal_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(signal_log)")}


def sync_from_signal_log(
    *,
    signal_db: str = "signal_log.db",
    ledger_db: str = LEDGER_DB,
    manifest_path: str = MANIFEST_FILE,
) -> Dict[str, Any]:
    manifest = initialise_experiment(manifest_path=manifest_path)
    if not Path(signal_db).exists():
        return {"ok": False, "reason": "signal_log_missing"}
    strategies = manifest["strategies"]
    placeholders = ",".join("?" for _ in strategies)
    with sqlite3.connect(signal_db) as source:
        source.row_factory = sqlite3.Row
        columns = _signal_columns(source)
        spread_expr = "signal_spread_pct" if "signal_spread_pct" in columns else "0.0"
        rows = source.execute(
            f"""SELECT id, log_time, signal_date, signal_time, symbol, side,
                       strategy, score, entry_price, {spread_expr} AS signal_spread_pct,
                       tb_label, tb_r_multiple_net, outcome_price, outcome_time
                  FROM signal_log
                 WHERE signal_date > ? AND strategy IN ({placeholders})
                 ORDER BY signal_date, log_time, id""",
            [manifest["start_after_date"], *strategies],
        ).fetchall()

    selected: list[sqlite3.Row] = []
    seen_keys: set[tuple[str, str, str]] = set()
    day_counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        day = str(row["signal_date"])
        key = (day, str(row["strategy"]), str(row["symbol"]))
        if key in seen_keys or day_counts[day] >= int(manifest["max_signals_per_day"]):
            continue
        seen_keys.add(key)
        day_counts[day] += 1
        selected.append(row)

    captured = finalized = 0
    with _connect(ledger_db) as ledger:
        for row in selected:
            base = {
                "log_time": row["log_time"], "signal_time": row["signal_time"],
                "score": row["score"], "decision_price": row["entry_price"],
                "signal_spread_pct": row["signal_spread_pct"],
                "executable_quote_observed": bool(float(row["signal_spread_pct"] or 0) > 0),
            }
            source_hash = _canonical_hash({"signal_id": row["id"], **base})
            before = ledger.total_changes
            ledger.execute(
                "INSERT OR IGNORE INTO ledger_events VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                (manifest["experiment_id"], row["id"], "CAPTURED", time.time(),
                 row["signal_date"], row["strategy"], row["symbol"], row["side"],
                 json.dumps(base, sort_keys=True, default=str), source_hash),
            )
            captured += ledger.total_changes - before
            if int(row["tb_label"] or -99) in (-1, 0, 1):
                outcome = {
                    "tb_label": int(row["tb_label"]),
                    "net_r": float(row["tb_r_multiple_net"] or 0.0),
                    "outcome_price": float(row["outcome_price"] or 0.0),
                    # signal_log.outcome_time is a timestamp STRING
                    # ("2026-09-02 10:20:00+05:30"), not a numeric epoch --
                    # forcing float() here crashed sync_from_signal_log() on
                    # every real row (found by actually running this against
                    # signal_log.db, not caught by static review). Purely
                    # informational metadata, not used in any calculation
                    # below -- store it as-is.
                    "outcome_time": str(row["outcome_time"] or ""),
                }
                before = ledger.total_changes
                ledger.execute(
                    "INSERT OR IGNORE INTO ledger_events VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                    (manifest["experiment_id"], row["id"], "OUTCOME_FINALIZED", time.time(),
                     row["signal_date"], row["strategy"], row["symbol"], row["side"],
                     json.dumps(outcome, sort_keys=True), _canonical_hash(outcome)),
                )
                finalized += ledger.total_changes - before
    return {"ok": True, "eligible_source_rows": len(rows), "selected": len(selected),
            "captured_added": captured, "outcomes_added": finalized}


def _stats(values: list[float]) -> Dict[str, Any]:
    n = len(values)
    if not n:
        return {"n": 0, "mean": None, "lcb95": None}
    mean = sum(values) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if sd > 0 else 0.0
    return {"n": n, "mean": round(mean, 6), "lcb95": round(mean - 1.645 * se, 6)}


def build_report(
    *, ledger_db: str = LEDGER_DB, manifest_path: str = MANIFEST_FILE,
    report_file: str = REPORT_FILE, write: bool = True,
) -> Dict[str, Any]:
    manifest = initialise_experiment(manifest_path=manifest_path)
    with _connect(ledger_db) as conn:
        rows = conn.execute(
            "SELECT * FROM ledger_events WHERE experiment_id=? ORDER BY event_id",
            (manifest["experiment_id"],),
        ).fetchall()
    captures: Dict[int, Dict[str, Any]] = {}
    outcomes: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        target = captures if row["event_type"] == "CAPTURED" else outcomes
        target[int(row["signal_id"])] = {**dict(row), **payload}
    joined = [{**capture, **outcomes[sid]} for sid, capture in captures.items() if sid in outcomes]
    net_r = [float(row["net_r"]) for row in joined]
    by_day: Dict[str, list[float]] = defaultdict(list)
    for row in joined:
        by_day[str(row["signal_date"])].append(float(row["net_r"]))
    day_means = [sum(values) / len(values) for values in by_day.values()]
    quote_count = sum(bool(row.get("executable_quote_observed")) for row in captures.values())
    quote_coverage = quote_count / max(1, len(captures))
    signal_stats, day_stats = _stats(net_r), _stats(day_means)
    gates = {
        "minimum_outcomes": len(joined) >= int(manifest["minimum_outcomes"]),
        "minimum_active_days": len(by_day) >= int(manifest["minimum_active_days"]),
        "quote_coverage": quote_coverage >= float(manifest["minimum_quote_coverage"]),
        "positive_signal_mean": bool(signal_stats["mean"] is not None and signal_stats["mean"] > 0),
        "positive_equal_day_lcb95": bool(day_stats["lcb95"] is not None and day_stats["lcb95"] > 0),
    }
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "experiment_id": manifest["experiment_id"], "manifest_sha256": manifest["manifest_sha256"],
        "research_only": True, "captured": len(captures), "outcomes": len(joined),
        "pending": len(captures) - len(joined), "active_days": len(by_day),
        "executable_quote_coverage": round(quote_coverage, 4),
        "signal_weighted_net_r": signal_stats, "equal_day_net_r": day_stats,
        "gates": gates, "passed": all(gates.values()),
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    output: Dict[str, Any] = {}
    if args.init:
        output["manifest"] = initialise_experiment()
    if args.sync:
        output["sync"] = sync_from_signal_log()
    if args.report or not output:
        output["report"] = build_report(write=not args.no_write)
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
