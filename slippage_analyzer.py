"""
slippage_analyzer.py

Measures implementation shortfall: the gap between expected prices from
signal_log.db and actual fills in trades.db.

Joins on trade_id. Reports entry slippage, stop-loss gap-through, and
per-strategy averages.

Writes slippage_report.json. Accumulates meaningful data as more trades close.

Usage:
  python3 slippage_analyzer.py
  from slippage_analyzer import run_slippage_analysis
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SIGNAL_DB   = "signal_log.db"
TRADES_DB   = "trades.db"
REPORT_FILE = "slippage_report.json"


def _load_signal_entries() -> Dict[str, Dict[str, Any]]:
    """Load expected entry prices from signal_log.db, keyed by trade_id."""
    result: Dict[str, Dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(SIGNAL_DB)
        rows = conn.execute("""
            SELECT trade_id, symbol, strategy, side, entry_price, signal_time
              FROM signal_log
             WHERE trade_id IS NOT NULL AND trade_id != ''
               AND entry_price > 0
        """).fetchall()
        conn.close()
        for tid, sym, strat, side, ep, stime in rows:
            result[str(tid)] = {
                "symbol":         sym,
                "strategy":       strat,
                "side":           side,
                "expected_entry": float(ep),
                "signal_time":    stime,
            }
    except Exception as exc:
        logger.warning("slippage_analyzer: signal load error: %s", exc)
    return result


def _load_trade_fills() -> List[Dict[str, Any]]:
    """Load actual fill prices from trades.db."""
    result: List[Dict[str, Any]] = []
    try:
        conn = sqlite3.connect(TRADES_DB)
        rows = conn.execute("""
            SELECT trade_id, symbol, strategy, side,
                   entry_price, exit_price, stop_loss, target_price,
                   realized_pnl, exit_reason, holding_minutes, mode
              FROM trades
             WHERE entry_price > 0
        """).fetchall()
        conn.close()
        for row in rows:
            result.append({
                "trade_id":     str(row[0]),
                "symbol":       row[1],
                "strategy":     row[2],
                "side":         row[3],
                "actual_entry": float(row[4] or 0),
                "actual_exit":  float(row[5] or 0),
                "stop_loss":    float(row[6] or 0),
                "target_price": float(row[7] or 0),
                "realized_pnl": float(row[8] or 0),
                "exit_reason":  row[9],
                "hold_min":     row[10],
                "mode":         row[11],
            })
    except Exception as exc:
        logger.warning("slippage_analyzer: trades load error: %s", exc)
    return result


def _slip_pct(expected: float, actual: float, side: str) -> Optional[float]:
    """
    Signed slippage as % of expected price.
    Positive = paid more (BUY) or received less (SELL) than expected — bad.
    """
    if not (expected > 0 and actual > 0):
        return None
    raw = (actual - expected) / expected * 100
    if side and side.upper() == "SELL":
        raw = -raw
    return round(raw, 4)


def run_slippage_analysis(save: bool = True) -> Dict[str, Any]:
    """Join signal and trade records, compute slippage per trade and per strategy."""
    signal_map = _load_signal_entries()
    trades     = _load_trade_fills()

    joined: List[Dict[str, Any]] = []
    for trade in trades:
        tid  = trade["trade_id"]
        sig  = signal_map.get(tid, {})
        side = trade["side"] or sig.get("side", "")

        entry_slip = _slip_pct(sig.get("expected_entry", 0), trade["actual_entry"], side)

        # Exit slippage: measure gap-through on stop exits
        exit_slip: Optional[float] = None
        rsn = (trade["exit_reason"] or "").lower()
        if trade["actual_exit"] > 0:
            if "stop" in rsn and trade["stop_loss"] > 0:
                exit_slip = _slip_pct(trade["stop_loss"], trade["actual_exit"], side)
            elif "target" in rsn and trade["target_price"] > 0:
                exit_slip = _slip_pct(trade["target_price"], trade["actual_exit"], side)

        joined.append({
            "trade_id":    tid,
            "symbol":      trade["symbol"],
            "strategy":    trade["strategy"],
            "side":        side,
            "exp_entry":   sig.get("expected_entry"),
            "act_entry":   trade["actual_entry"],
            "entry_slip":  entry_slip,
            "act_exit":    trade["actual_exit"] or None,
            "exit_slip":   exit_slip,
            "hold_min":    trade["hold_min"],
            "exit_reason": trade["exit_reason"],
            "mode":        trade["mode"],
        })

    # Per-strategy aggregates
    by_strat: Dict[str, Any] = {}
    for j in joined:
        strat = j["strategy"] or "unknown"
        if strat not in by_strat:
            by_strat[strat] = {"n": 0, "entry_slips": [], "exit_slips": []}
        by_strat[strat]["n"] += 1
        if j["entry_slip"] is not None:
            by_strat[strat]["entry_slips"].append(j["entry_slip"])
        if j["exit_slip"] is not None:
            by_strat[strat]["exit_slips"].append(j["exit_slip"])

    strategy_summary: Dict[str, Any] = {}
    for strat, d in by_strat.items():
        es = d["entry_slips"]
        xs = d["exit_slips"]
        strategy_summary[strat] = {
            "trades":        d["n"],
            "avg_entry_slip": round(sum(es) / len(es), 4) if es else None,
            "max_entry_slip": round(max(es), 4)            if es else None,
            "avg_exit_slip":  round(sum(xs) / len(xs), 4) if xs else None,
        }

    all_entry = [j["entry_slip"] for j in joined if j["entry_slip"] is not None]
    n_matched  = len(all_entry)
    avg_entry  = round(sum(all_entry) / n_matched, 4) if n_matched else None

    report: Dict[str, Any] = {
        "generated_at":       datetime.now().isoformat(),
        "total_trades":       len(trades),
        "matched_pairs":      n_matched,
        "avg_entry_slip_pct": avg_entry,
        "note":               (
            f"{n_matched} matched signal→trade pairs available. "
            "Analysis grows as more live trades close."
        ),
        "by_strategy":        strategy_summary,
        "trades":             joined,
    }

    if save:
        Path(REPORT_FILE).write_text(json.dumps(report, indent=2))
        logger.info("slippage_report.json: %d trades, %d matched pairs", len(trades), n_matched)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rpt = run_slippage_analysis()
    print(f"\nSlippage Analysis — {rpt['total_trades']} trades, "
          f"{rpt['matched_pairs']} matched pairs")
    if rpt["avg_entry_slip_pct"] is not None:
        print(f"Average entry slippage: {rpt['avg_entry_slip_pct']:.3f}%")
    print(f"\nBy strategy:")
    for strat, s in rpt["by_strategy"].items():
        slip = f"{s['avg_entry_slip']:.3f}%" if s["avg_entry_slip"] is not None else "N/A"
        print(f"  {strat:<35} n={s['trades']:<4} entry_slip={slip}")
    print(f"\nFull report: {REPORT_FILE}")
