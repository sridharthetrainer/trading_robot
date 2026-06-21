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


def _pending_profile(rows: List[Dict[str, Any]], limit: int = 20) -> Dict[str, Any]:
    pending = [r for r in rows if int(r.get("tb_label", -99) or -99) == -99]
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
    labelled = [r for r in rows if int(r.get("tb_label", -99) or -99) in (-1, 0, 1)]
    pending = [r for r in rows if int(r.get("tb_label", -99) or -99) == -99]

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
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
