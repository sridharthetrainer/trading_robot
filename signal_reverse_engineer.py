#!/usr/bin/env python3
"""
signal_reverse_engineer.py

Turn the full shadow signal log into a reverse-engineering report:
which strategies, filters, and logged indicators are actually associated with
winning/losing triple-barrier outcomes.

Run:
    .venv/bin/python signal_reverse_engineer.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


REPORT_JSON = "signal_reverse_engineer_report.json"
REPORT_MD = "SIGNAL_REVERSE_ENGINEER_REPORT.md"
REVERSE_POLICY_JSON = "reverse_shadow_candidates.json"

FEATURE_COLS = [
    "bhav_delivery",
    "cross_asset_mod",
    "participant_mod",
    "expiry_mod",
    "sip_boost",
    "bulk_deal_mod",
    "theta_mod",
    "rebal_mod",
    "news_mod",
    "mtf_pivot_mod",
    "gex_mod",
    "skew_mod",
    "whale_mod",
    "sr_level_mod",
    "pivot_boss_mod",
    "oi_mod",
    "structure_mod",
    "market_quality_mod",
    "candidate_quality_mod",
    "ai_score",
    "rl_bias",
    "weinstein_mod",
    "volume_ratio",
    "indicator_coverage",
    "candidate_confirmations",
]

CONTEXT_COLS = [
    "strategy",
    "side",
    "regime",
    "htf_bias",
    "confluence",
    "structure_label",
    "structure_direction",
    "cross_asset_bias",
    "expiry_regime",
    "symbol_type",
    "hour_of_day",
    "day_of_week",
    "option_type",
    "option_style",
]


def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_cols(conn) -> set:
    return {str(r[1]) for r in conn.execute("PRAGMA table_info(signal_log)").fetchall()}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _label_value(row: Dict[str, Any]) -> int:
    """Preserve timeout label 0 (``0 or -99`` incorrectly made it pending)."""
    value = row.get("tb_label", -99)
    try:
        return -99 if value is None else int(value)
    except (TypeError, ValueError):
        return -99


def _load_rows(db_path: str, days: int) -> Tuple[List[Dict[str, Any]], set]:
    if not Path(db_path).exists():
        return [], set()
    conn = _connect(db_path)
    cols = _table_cols(conn)
    rows = conn.execute(
        """
        SELECT *
          FROM signal_log
         WHERE signal_date >= date('now','localtime', ?)
         ORDER BY log_time ASC
        """,
        (f"-{int(days)} day",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], cols


def _return_pct(row: Dict[str, Any]) -> float:
    entry = _safe_float(row.get("entry_price"))
    outcome = _safe_float(row.get("outcome_price"))
    if entry <= 0 or outcome <= 0:
        return 0.0
    side = str(row.get("side", "") or "").upper()
    sign = 1.0 if side == "BUY" else -1.0 if side == "SELL" else 0.0
    return sign * (outcome - entry) / entry * 100.0


def _bucket_feature(value: Any) -> str:
    v = _safe_float(value)
    if v > 0.05:
        return "positive"
    if v < -0.05:
        return "negative"
    return "silent"


def _bucket_score(row: Dict[str, Any]) -> str:
    score = _safe_float(row.get("score"))
    if score >= 8:
        return "score>=8"
    if score >= 6:
        return "score 6-8"
    if score >= 4:
        return "score 4-6"
    return "score<4"


def _bucket_vix(row: Dict[str, Any]) -> str:
    vix = _safe_float(row.get("india_vix"))
    if vix >= 22:
        return "vix>=22"
    if vix >= 18:
        return "vix 18-22"
    if vix > 0:
        return "vix<18"
    return "vix_unknown"


def _summarise_group(rows: List[Dict[str, Any]], min_samples: int) -> Dict[str, Any]:
    n = len(rows)
    wins = sum(1 for r in rows if int(r.get("tb_label", 0) or 0) == 1)
    losses = sum(1 for r in rows if int(r.get("tb_label", 0) or 0) == -1)
    timeouts = sum(1 for r in rows if int(r.get("tb_label", 0) or 0) == 0)
    rets = [_return_pct(r) for r in rows]
    avg_ret = sum(rets) / max(len(rets), 1)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate": round(wins / max(wins + losses, 1), 4),
        "target_rate": round(wins / max(n, 1), 4),
        "avg_return_pct": round(avg_ret, 4),
        "usable": n >= min_samples,
    }


def _ranked_groups(
    rows: List[Dict[str, Any]],
    key_fn,
    *,
    min_samples: int,
    limit: int,
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(key_fn(row) or "").strip() or "blank"
        buckets[key].append(row)
    out = []
    for key, items in buckets.items():
        block = _summarise_group(items, min_samples)
        block["key"] = key
        out.append(block)
    out.sort(key=lambda x: (x["usable"], x["avg_return_pct"], x["target_rate"], x["n"]), reverse=True)
    return out[:limit]


def _chronological_reverse_validation(
    rows: List[Dict[str, Any]],
    *,
    min_samples: int = 100,
    min_distinct_days: int = 20,
    train_fraction: float = 0.70,
    min_oos_return_pct: float = 0.03,
    limit: int = 20,
) -> Dict[str, Any]:
    """Test opposite-direction shadows without touching execution.

    Every labelled generated signal is used, including rejected/unexecuted
    candidates.  The split is chronological by session (never random), and a
    reverse is merely the negative of the observed underlying return.  That is
    useful for discovering directional anti-edge, but it is *not* an option P&L
    estimate, so this function can never authorize a live reversal.
    """
    usable = [
        r for r in rows
        if str(r.get("signal_date") or "").strip()
        and _safe_float(r.get("entry_price")) > 0
        and _safe_float(r.get("outcome_price")) > 0
        and str(r.get("side") or "").upper() in {"BUY", "SELL"}
    ]
    days = sorted({str(r["signal_date"]) for r in usable})
    if len(days) < 2:
        return {
            "status": "COLLECTING", "all_signals": len(usable),
            "distinct_days": len(days), "candidates": [],
            "live_reversal_allowed": False,
            "reason": "need_at_least_2_sessions_for_chronological_split",
        }

    split_idx = max(1, min(len(days) - 1, int(len(days) * train_fraction)))
    train_days, test_days = set(days[:split_idx]), set(days[split_idx:])
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in usable:
        grouped[str(row.get("strategy") or "unknown")].append(row)

    candidates = []
    for strategy, items in grouped.items():
        train = [r for r in items if str(r["signal_date"]) in train_days]
        test = [r for r in items if str(r["signal_date"]) in test_days]
        if len(items) < min_samples or len(train) < max(20, min_samples // 2) or len(test) < 20:
            continue
        train_returns = [_return_pct(r) for r in train]
        test_returns = [_return_pct(r) for r in test]
        original_train = sum(train_returns) / len(train_returns)
        original_test = sum(test_returns) / len(test_returns)
        if original_train >= 0 or original_test >= 0:
            continue

        by_test_day: Dict[str, List[float]] = defaultdict(list)
        for row in test:
            by_test_day[str(row["signal_date"])].append(-_return_pct(row))
        positive_days = sum(1 for vals in by_test_day.values()
                            if vals and sum(vals) / len(vals) > 0)
        day_consistency = positive_days / max(len(by_test_day), 1)
        reverse_test = -original_test
        evidence_ready = (
            len(days) >= min_distinct_days
            and reverse_test >= min_oos_return_pct
            and day_consistency >= 0.60
        )
        candidates.append({
            "strategy": strategy,
            "status": "SHADOW_VALIDATED" if evidence_ready else "SHADOW_COLLECTING",
            "train_samples": len(train), "test_samples": len(test),
            "train_days": len({str(r["signal_date"]) for r in train}),
            "test_days": len(by_test_day),
            "original_train_avg_return_pct": round(original_train, 4),
            "original_test_avg_return_pct": round(original_test, 4),
            "reverse_oos_avg_return_pct": round(reverse_test, 4),
            "reverse_positive_test_day_rate": round(day_consistency, 4),
            "meets_research_thresholds": evidence_ready,
            "live_allowed": False,
            "live_block_reason": "underlying_reverse_is_not_verified_option_pnl",
        })

    candidates.sort(
        key=lambda r: (
            bool(r["meets_research_thresholds"]),
            r["reverse_oos_avg_return_pct"], r["test_samples"],
        ),
        reverse=True,
    )
    return {
        "status": "SHADOW_ONLY",
        "scope": "all_generated_labelled_signals",
        "all_signals": len(usable),
        "distinct_days": len(days),
        "train_cutoff": days[split_idx - 1],
        "train_days": len(train_days), "test_days": len(test_days),
        "minimum_distinct_days": min_distinct_days,
        "minimum_samples": min_samples,
        "minimum_oos_return_pct": min_oos_return_pct,
        "candidates": candidates[:limit],
        "live_reversal_allowed": False,
        "reason": "shadow_A_B_only_until_verified_option_outcomes_and_costs",
    }


def _pending_profile(rows: List[Dict[str, Any]], limit: int = 20) -> Dict[str, Any]:
    pending = [r for r in rows if _label_value(r) == -99]
    by_reason: Dict[str, int] = defaultdict(int)
    by_strategy: Dict[str, int] = defaultdict(int)
    by_symbol: Dict[str, int] = defaultdict(int)
    for row in pending:
        by_reason[str(row.get("rejection_reason", "") or "blank")] += 1
        by_strategy[str(row.get("strategy", "") or "blank")] += 1
        by_symbol[str(row.get("symbol", "") or "blank")] += 1
    top = lambda d: [{"key": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:limit]]
    return {
        "pending_rows": len(pending),
        "top_rejection_reasons": top(by_reason),
        "top_pending_strategies": top(by_strategy),
        "top_pending_symbols": top(by_symbol),
    }


def build_reverse_engineering_report(
    *,
    db_path: str = "signal_log.db",
    days: int = 90,
    min_samples: int = 10,
    limit: int = 20,
) -> Dict[str, Any]:
    rows, cols = _load_rows(db_path, days)
    labelled = [r for r in rows if _label_value(r) in (-1, 0, 1)]
    pending = [r for r in rows if _label_value(r) == -99]

    context_report = {}
    for col in CONTEXT_COLS:
        if col in cols:
            context_report[col] = _ranked_groups(
                labelled,
                lambda r, c=col: r.get(c),
                min_samples=min_samples,
                limit=limit,
            )

    feature_report = {}
    for col in FEATURE_COLS:
        if col in cols:
            feature_report[col] = _ranked_groups(
                labelled,
                lambda r, c=col: _bucket_feature(r.get(c)),
                min_samples=min_samples,
                limit=3,
            )

    composite_report = {
        "score_bucket": _ranked_groups(labelled, _bucket_score, min_samples=min_samples, limit=limit),
        "vix_bucket": _ranked_groups(labelled, _bucket_vix, min_samples=min_samples, limit=limit),
        "strategy_side": _ranked_groups(
            labelled,
            lambda r: f"{r.get('strategy') or 'blank'}|{r.get('side') or 'blank'}",
            min_samples=min_samples,
            limit=limit,
        ),
        "strategy_regime": _ranked_groups(
            labelled,
            lambda r: f"{r.get('strategy') or 'blank'}|{r.get('regime') or 'blank'}",
            min_samples=min_samples,
            limit=limit,
        ),
    }

    usable_context = [
        {"dimension": dim, **row}
        for dim, groups in context_report.items()
        for row in groups
        if row.get("usable")
    ]
    usable_context.sort(
        key=lambda x: (x["avg_return_pct"], x["target_rate"], x["n"]),
        reverse=True,
    )

    feature_edges = []
    for feature, groups in feature_report.items():
        positive = next((g for g in groups if g["key"] == "positive" and g.get("usable")), None)
        silent = next((g for g in groups if g["key"] == "silent" and g.get("usable")), None)
        negative = next((g for g in groups if g["key"] == "negative" and g.get("usable")), None)
        if positive and silent:
            feature_edges.append({
                "feature": feature,
                "comparison": "positive_vs_silent",
                "lift_return_pct": round(positive["avg_return_pct"] - silent["avg_return_pct"], 4),
                "positive": positive,
                "silent": silent,
            })
        if negative and silent:
            feature_edges.append({
                "feature": feature,
                "comparison": "negative_vs_silent",
                "lift_return_pct": round(negative["avg_return_pct"] - silent["avg_return_pct"], 4),
                "negative": negative,
                "silent": silent,
            })
    feature_edges.sort(key=lambda x: x["lift_return_pct"], reverse=True)

    labelled_summary = _summarise_group(labelled, min_samples) if labelled else {
        "n": 0,
        "wins": 0,
        "losses": 0,
        "timeouts": 0,
        "win_rate": 0,
        "target_rate": 0,
        "avg_return_pct": 0,
        "usable": False,
    }
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_path": db_path,
        "days": days,
        "min_samples": min_samples,
        "totals": {
            "rows": len(rows),
            "labelled_rows": len(labelled),
            "pending_rows": len(pending),
            "labelled_pct": round(len(labelled) / max(len(rows), 1), 4),
        },
        "labelled_summary": labelled_summary,
        "pending_profile": _pending_profile(rows, limit=limit),
        "top_context_edges": usable_context[:limit],
        "feature_edges": feature_edges[:limit],
        "context": context_report,
        "features": feature_report,
        "composites": composite_report,
        "reverse_shadow": _chronological_reverse_validation(
            labelled,
            min_samples=max(100, min_samples),
            limit=limit,
        ),
        "ready": len(labelled) >= min_samples,
    }
    if not report["ready"]:
        report["next_action"] = (
            "Run post-market triple-barrier labelling after market close so pending "
            "signals become labelled training rows."
        )
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    totals = report.get("totals", {})
    summary = report.get("labelled_summary", {})
    lines = [
        "# Signal Reverse Engineering Report",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Status: `{'READY' if report.get('ready') else 'WAITING_FOR_LABELS'}`",
        f"- Rows: `{totals.get('rows', 0)}`",
        f"- Labelled rows: `{totals.get('labelled_rows', 0)}`",
        f"- Pending rows: `{totals.get('pending_rows', 0)}`",
        f"- Labelled pct: `{totals.get('labelled_pct', 0)}`",
        f"- Overall target/loss/timeout: `{summary.get('wins', 0)}` / `{summary.get('losses', 0)}` / `{summary.get('timeouts', 0)}`",
        f"- Overall average return pct: `{summary.get('avg_return_pct', 0)}`",
        "",
    ]
    if report.get("next_action"):
        lines.extend(["## Next Action", "", f"- {report['next_action']}", ""])

    lines.extend(["## Best Context Edges", ""])
    for row in report.get("top_context_edges", [])[:15]:
        lines.append(
            f"- `{row.get('dimension')}={row.get('key')}` n `{row.get('n')}` "
            f"target_rate `{row.get('target_rate')}` avg_return `{row.get('avg_return_pct')}`"
        )
    if not report.get("top_context_edges"):
        lines.append("- none yet")

    lines.extend(["", "## Feature Lifts", ""])
    for row in report.get("feature_edges", [])[:15]:
        lines.append(
            f"- `{row.get('feature')}` {row.get('comparison')} "
            f"lift `{row.get('lift_return_pct')}`"
        )
    if not report.get("feature_edges"):
        lines.append("- none yet")

    reverse = report.get("reverse_shadow", {})
    lines.extend(["", "## Reverse Shadow A/B (All Signals)", ""])
    lines.append(
        f"- Scope `{reverse.get('scope', 'all_generated_labelled_signals')}`; "
        f"signals `{reverse.get('all_signals', 0)}`; days `{reverse.get('distinct_days', 0)}`; "
        "live reversal `BLOCKED`"
    )
    for row in reverse.get("candidates", [])[:10]:
        lines.append(
            f"- `{row.get('strategy')}` {row.get('status')} train/test "
            f"`{row.get('train_samples')}/{row.get('test_samples')}` reverse OOS "
            f"`{row.get('reverse_oos_avg_return_pct')}%` positive test days "
            f"`{row.get('reverse_positive_test_day_rate')}`"
        )
    if not reverse.get("candidates"):
        lines.append("- no chronologically stable reverse candidates yet")

    pending = report.get("pending_profile", {})
    lines.extend(["", "## Pending Signal Profile", ""])
    for row in pending.get("top_rejection_reasons", [])[:10]:
        lines.append(f"- `{row.get('key')}` count `{row.get('count')}`")
    if not pending.get("top_rejection_reasons"):
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="signal_log.db")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = build_reverse_engineering_report(
        db_path=args.db_path,
        days=args.days,
        min_samples=args.min_samples,
        limit=args.limit,
    )
    text = render_markdown(report)
    if not args.no_write:
        Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        Path(REPORT_MD).write_text(text, encoding="utf-8")
        Path(REVERSE_POLICY_JSON).write_text(
            json.dumps(report.get("reverse_shadow", {}), indent=2, default=str),
            encoding="utf-8",
        )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
