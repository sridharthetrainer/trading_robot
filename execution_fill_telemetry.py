#!/usr/bin/env python3
"""Build execution/fill telemetry from the local trade journal.

This is intentionally broker-agnostic. Live runs will enrich the same report
with real broker order IDs and partial-fill fields as those become available,
while paper runs still provide useful coverage for order lifecycle auditing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPORT_FILE = "execution_fill_telemetry.json"
TRADES_DB = "trades.db"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _load_trades(db_path: str = TRADES_DB) -> List[Dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT trade_id, symbol, side, qty, strategy, broker_name, order_id,
                   entry_price, entry_time, exit_price, exit_time, exit_reason,
                   realized_pnl, mode, trade_type, total_charges, holding_minutes,
                   r_multiple, sl_order_id, status, metadata, signal_metadata
              FROM trades
             ORDER BY entry_time DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _enrich(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = _json_dict(row.get("metadata"))
    sig_meta = _json_dict(row.get("signal_metadata"))
    fill = meta.get("fill") if isinstance(meta.get("fill"), dict) else {}
    intended_price = _safe_float(
        _first_present(
            sig_meta.get("expected_entry"),
            sig_meta.get("entry_price"),
            (meta.get("signal_data") or {}).get("price") if isinstance(meta.get("signal_data"), dict) else None,
            row.get("entry_price"),
        )
    )
    fill_avg_price = _safe_float(
        _first_present(
            fill.get("fill_avg_price"),
            fill.get("avg_price"),
            fill.get("averageprice"),
            row.get("entry_price"),
        )
    )
    entry_slippage_pct = None
    if intended_price > 0 and fill_avg_price > 0:
        raw = (fill_avg_price - intended_price) / intended_price * 100.0
        if str(row.get("side") or "").upper() == "SELL":
            raw = -raw
        entry_slippage_pct = round(raw, 4)
    return {
        **row,
        "_metadata": meta,
        "_signal_metadata": sig_meta,
        "_fill": fill,
        "paper_order_id": meta.get("paper_order_id", ""),
        "live_order_id": meta.get("live_order_id", ""),
        "paper_exit_order_id": meta.get("paper_exit_order_id", ""),
        "live_exit_order_id": meta.get("live_exit_order_id", ""),
        "fill_confirmed": bool(fill.get("fill_confirmed", False)),
        "fill_status": str(fill.get("fill_status", "")),
        "fill_qty": int(_safe_float(fill.get("fill_qty"), 0)),
        "fill_avg_price": fill_avg_price,
        "fill_latency_sec": (
            None if fill.get("fill_latency_sec") is None
            else round(_safe_float(fill.get("fill_latency_sec")), 3)
        ),
        "fill_rejection_reason": str(fill.get("fill_rejection_reason", "")),
        "intended_entry_price": intended_price,
        "entry_slippage_pct": entry_slippage_pct,
    }


def _summarise_by(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = str(row.get(key) or "UNKNOWN")
        bucket = out.setdefault(
            name,
            {
                "trades": 0,
                "closed": 0,
                "paper": 0,
                "live": 0,
                "with_order_id": 0,
                "with_sl_order_id": 0,
                "gross_pnl": 0.0,
                "charges": 0.0,
                "avg_hold_min": 0.0,
            },
        )
        bucket["trades"] += 1
        if str(row.get("status") or "").upper() == "CLOSED":
            bucket["closed"] += 1
        if str(row.get("mode") or "").upper() == "LIVE":
            bucket["live"] += 1
        else:
            bucket["paper"] += 1
        if row.get("order_id"):
            bucket["with_order_id"] += 1
        if row.get("sl_order_id"):
            bucket["with_sl_order_id"] += 1
        bucket["gross_pnl"] += _safe_float(row.get("realized_pnl"))
        bucket["charges"] += _safe_float(row.get("total_charges"))
        bucket["avg_hold_min"] += _safe_float(row.get("holding_minutes"))
    for bucket in out.values():
        n = max(1, int(bucket["trades"]))
        bucket["gross_pnl"] = round(float(bucket["gross_pnl"]), 2)
        bucket["charges"] = round(float(bucket["charges"]), 2)
        bucket["avg_hold_min"] = round(float(bucket["avg_hold_min"]) / n, 2)
        bucket["order_id_coverage_pct"] = round(100.0 * bucket["with_order_id"] / n, 2)
        bucket["sl_order_id_coverage_pct"] = round(100.0 * bucket["with_sl_order_id"] / n, 2)
    return out


def _paired_fill_comparison(rows: Iterable[Dict[str, Any]], target: int = 100) -> Dict[str, Any]:
    """Measure paper-vs-live fill drift only when both fills are recorded."""
    pairs = []
    for row in rows:
        meta = row.get("_metadata", {}) or {}
        paper_price = _safe_float(meta.get("paper_fill_price"))
        live_price = _safe_float(meta.get("live_fill_price"))
        if paper_price > 0 and live_price > 0:
            pairs.append(abs(live_price - paper_price) / paper_price * 100.0)
    return {
        "paired_fills": len(pairs),
        "target_paired_fills": int(target),
        "ready": len(pairs) >= int(target),
        "avg_absolute_deviation_pct": round(sum(pairs) / len(pairs), 4) if pairs else None,
        "max_absolute_deviation_pct": round(max(pairs), 4) if pairs else None,
    }


def build_execution_fill_telemetry(
    *,
    db_path: str = TRADES_DB,
    report_file: str = REPORT_FILE,
    write: bool = True,
) -> Dict[str, Any]:
    trades = [_enrich(t) for t in _load_trades(db_path)]
    closed = [t for t in trades if str(t.get("status") or "").upper() == "CLOSED"]
    live = [t for t in trades if str(t.get("mode") or "").upper() == "LIVE"]
    paper = [t for t in trades if str(t.get("mode") or "").upper() != "LIVE"]
    with_order = [t for t in trades if t.get("order_id")]
    with_sl = [t for t in trades if t.get("sl_order_id")]
    with_fill_status = [t for t in trades if t.get("fill_status")]
    with_fill_latency = [t for t in trades if t.get("fill_latency_sec") is not None]
    with_fill_avg = [t for t in trades if _safe_float(t.get("fill_avg_price")) > 0]
    with_slippage = [t for t in trades if t.get("entry_slippage_pct") is not None]
    partial_like = [
        t for t in trades
        if "partial" in str(t.get("exit_reason") or "").lower()
        or "partial" in str(t.get("metadata") or "").lower()
        or "partial" in str(t.get("signal_metadata") or "").lower()
    ]
    rejected_like = [
        t for t in trades
        if "reject" in str(t.get("exit_reason") or "").lower()
        or str(t.get("status") or "").upper() == "REJECTED"
    ]
    paired = _paired_fill_comparison(trades)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_path": db_path,
        "trades": len(trades),
        "closed": len(closed),
        "paper": len(paper),
        "live": len(live),
        "with_order_id": len(with_order),
        "with_sl_order_id": len(with_sl),
        "with_fill_status": len(with_fill_status),
        "with_fill_latency": len(with_fill_latency),
        "with_fill_avg_price": len(with_fill_avg),
        "with_entry_slippage": len(with_slippage),
        "partial_fill_like": len(partial_like),
        "rejected_like": len(rejected_like),
        "order_id_coverage_pct": round(100.0 * len(with_order) / max(1, len(trades)), 2),
        "sl_order_id_coverage_pct": round(100.0 * len(with_sl) / max(1, len(trades)), 2),
        "fill_status_coverage_pct": round(100.0 * len(with_fill_status) / max(1, len(trades)), 2),
        "fill_latency_coverage_pct": round(100.0 * len(with_fill_latency) / max(1, len(trades)), 2),
        "fill_avg_price_coverage_pct": round(100.0 * len(with_fill_avg) / max(1, len(trades)), 2),
        "entry_slippage_coverage_pct": round(100.0 * len(with_slippage) / max(1, len(trades)), 2),
        "paired_fill_comparison": paired,
        "avg_entry_slippage_pct": (
            round(sum(float(t["entry_slippage_pct"]) for t in with_slippage) / len(with_slippage), 4)
            if with_slippage else None
        ),
        "avg_fill_latency_sec": (
            round(sum(float(t["fill_latency_sec"]) for t in with_fill_latency) / len(with_fill_latency), 3)
            if with_fill_latency else None
        ),
        "by_strategy": _summarise_by(trades, "strategy"),
        "by_mode": _summarise_by(trades, "mode"),
        "latest_trades": [
            {
                "trade_id": t.get("trade_id"),
                "symbol": t.get("symbol"),
                "strategy": t.get("strategy"),
                "mode": t.get("mode"),
                "status": t.get("status"),
                "order_id": t.get("order_id"),
                "sl_order_id": t.get("sl_order_id"),
                "fill_status": t.get("fill_status"),
                "fill_qty": t.get("fill_qty"),
                "fill_avg_price": t.get("fill_avg_price"),
                "fill_latency_sec": t.get("fill_latency_sec"),
                "entry_slippage_pct": t.get("entry_slippage_pct"),
                "pnl": _safe_float(t.get("realized_pnl")),
            }
            for t in trades[:25]
        ],
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=TRADES_DB)
    parser.add_argument("--report", default=REPORT_FILE)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_execution_fill_telemetry(
        db_path=args.db,
        report_file=args.report,
        write=not args.no_write,
    )
    print(json.dumps({
        "trades": report["trades"],
        "closed": report["closed"],
        "paper": report["paper"],
        "live": report["live"],
        "order_id_coverage_pct": report["order_id_coverage_pct"],
        "sl_order_id_coverage_pct": report["sl_order_id_coverage_pct"],
        "fill_status_coverage_pct": report["fill_status_coverage_pct"],
        "entry_slippage_coverage_pct": report["entry_slippage_coverage_pct"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
