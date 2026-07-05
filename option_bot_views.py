"""Compact generated-signal and EOD views for the dedicated option bot."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List


DB_PATH = "option_chain_snapshots.db"


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def generated_signals_text(day: str | None = None, limit: int = 15) -> str:
    report_day = day or date.today().isoformat()
    if not Path(DB_PATH).exists():
        return "No generated option-signal database yet."
    with sqlite3.connect(DB_PATH) as conn:
        from option_multistrike_signals import ensure_multistrike_schema
        ensure_multistrike_schema(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT s.* FROM option_strike_signals s
                 JOIN (SELECT underlying,strike,option_type,MAX(snapshot_time) latest
                         FROM option_strike_signals WHERE snapshot_time LIKE ? AND signal LIKE 'BUY_%'
                        GROUP BY underlying,strike,option_type) x
                   ON s.underlying=x.underlying AND s.strike=x.strike
                  AND s.option_type=x.option_type AND s.snapshot_time=x.latest
                ORDER BY s.tradable DESC,s.score DESC LIMIT ?""",
            (report_day + "%", max(1, min(int(limit), 30))),
        ).fetchall()
        conn.commit()
    if not rows:
        return f"No generated option signals for {report_day}."
    out = [f"🎯 <b>GENERATED OPTION SIGNALS — {report_day}</b>"]
    for row in rows:
        state = str(row["lifecycle_status"] or ("OPEN" if row["tradable"] else "WATCH"))
        flag = "🟢" if row["tradable"] else "🟡"
        out.append(
            f"{flag} <b>{row['underlying']} {row['strike']:.0f}{row['option_type']}</b> "
            f"{row['signal']} · {state} · score {row['score']:.1f}\n"
            f"   Entry ₹{_f(row['entry_price']):.2f} | SL ₹{_f(row['stop_loss']):.2f} | "
            f"T1 ₹{_f(row['target_1']):.2f} | T2 ₹{_f(row['target_2']):.2f}\n"
            f"   Edge {row['edge_policy']} · n={int(row['edge_outcomes'] or 0)} · "
            f"PF {_f(row['edge_profit_factor']):.2f}"
        )
    out.append("🟢 actionable · 🟡 watch/rejected by final gates")
    return "\n".join(out)


def anytime_report_table(day: str | None = None, limit: int = 20) -> str:
    """Anytime option report with levels and latest lifecycle state."""
    report_day = day or date.today().isoformat()
    if not Path(DB_PATH).exists():
        return "No generated option-signal database yet."
    with sqlite3.connect(DB_PATH) as conn:
        from option_multistrike_signals import ensure_multistrike_schema
        ensure_multistrike_schema(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """WITH chosen AS (
                   SELECT s.*,ROW_NUMBER() OVER (
                     PARTITION BY underlying,strike,option_type
                     ORDER BY tradable DESC,ts DESC) rn
                   FROM option_strike_signals s
                   WHERE snapshot_time LIKE ? AND signal LIKE 'BUY_%'
                 ), latest AS (
                   SELECT p.*,ROW_NUMBER() OVER (
                     PARTITION BY underlying,expiry,strike,option_type ORDER BY ts DESC) rn
                   FROM option_strike_signals p WHERE snapshot_time LIKE ? AND price>0
                 )
                 SELECT c.*,COALESCE(l.price,c.price) current_price
                   FROM chosen c LEFT JOIN latest l
                     ON l.underlying=c.underlying AND l.expiry=c.expiry
                    AND l.strike=c.strike AND l.option_type=c.option_type AND l.rn=1
                  WHERE c.rn=1
                  ORDER BY c.tradable DESC,c.score DESC LIMIT ?""",
            (report_day + "%", report_day + "%", max(1, min(int(limit), 30))),
        ).fetchall()
        conn.commit()
    if not rows:
        return f"No generated option signals for {report_day}."

    actionable = sum(bool(row["tradable"]) for row in rows)
    out = [
        f"📋 <b>OPTION REPORT — {report_day}</b>",
        f"Showing {len(rows)} latest contracts · Actionable {actionable} · updates anytime",
        "<pre>",
        "MODE CONTRACT         ENTRY     SL     T1     T2    LTP STATUS",
        "---- ---------------- ----- ------ ------ ------ ------ -------------",
    ]
    for row in rows:
        mode = "ACT" if row["tradable"] else "WATCH"
        contract = f"{str(row['underlying'])[:8]} {float(row['strike']):.0f}{row['option_type']}"[:16]
        state = str(row["lifecycle_status"] or ("OPEN" if row["tradable"] else "WATCH"))[:13]
        out.append(
            f"{mode:<4} {contract:<16} {_f(row['entry_price']):>5.1f} "
            f"{_f(row['stop_loss']):>6.1f} {_f(row['target_1']):>6.1f} "
            f"{_f(row['target_2']):>6.1f} {_f(row['current_price']):>6.1f} {state:<13}"
        )
    out.extend(["</pre>", "ACT = actionable setup · WATCH = generated but blocked/watchlist"])
    return "\n".join(out)[:4000]


def consolidated_eod_text(day: str | None = None) -> str:
    from option_telegram_report import collect_report_data

    data = collect_report_data(day)
    edge_gate = {}
    try:
        from option_multistrike_signals import _execution_edge_gate
        with sqlite3.connect(DB_PATH) as gate_conn:
            edge_gate = _execution_edge_gate(gate_conn)
    except Exception:
        edge_gate = {}
    rows: List[Dict[str, Any]] = data["all_signals"]
    grouped: Dict[str, Dict[str, float]] = defaultdict(lambda: {"n": 0, "labelled": 0, "net": 0, "wins": 0})
    lifecycle: Dict[str, int] = defaultdict(int)
    for row in rows:
        key = f"{row.get('underlying')} {row.get('signal')}"
        grouped[key]["n"] += 1
        label = int(row.get("outcome_label", -99))
        if label in (-1, 0, 1):
            grouped[key]["labelled"] += 1
            grouped[key]["net"] += _f(row.get("net_pnl"))
            grouped[key]["wins"] += int(label == 1)
        lifecycle[str(row.get("lifecycle_status") or "UNSET")] += 1
    labelled = len(data["labelled_signals"])
    wr = 100 * data["all_signal_wins"] / labelled if labelled else 0
    out = [
        f"📊 <b>OPTION ALL-IN-ONE — {data['day']}</b>",
        f"Generated {len(rows)} | Labelled {labelled} | Pending {len(data['pending_signals'])} | Unfilled {len(data['unfilled_signals'])}",
        f"Ideal signal net ₹{data['all_signal_net_pnl']:+,.0f} | WR {wr:.1f}% | Avg ₹{data['ideal_avg_net_pnl']:+,.0f}",
        f"Actionable-only ideal net ₹{data['actionable_ideal_net_pnl']:+,.0f}",
        f"Trades taken {len(data['trades'])} | Closed {len(data['closed'])} | Open {len(data['open_trades'])}",
        f"Traded gross ₹{data['traded_gross_pnl']:+,.0f} | Charges ₹{data['charges']:,.0f} | Net ₹{data['traded_net_pnl']:+,.0f}",
        ("🛑 New actionable signals BLOCKED — negative verified edge"
         if edge_gate and not edge_gate.get("allow_actionable")
         else "✅ Actionable edge circuit breaker clear"),
        "\n<b>By underlying and category</b>",
    ]
    for key, bucket in sorted(grouped.items()):
        cat_wr = 100 * bucket["wins"] / bucket["labelled"] if bucket["labelled"] else 0
        out.append(f"• {key}: n={int(bucket['n'])}, labelled={int(bucket['labelled'])}, WR={cat_wr:.0f}%, net ₹{bucket['net']:+,.0f}")
    if lifecycle:
        out.append("\n<b>Lifecycle</b>: " + " · ".join(f"{k} {v}" for k, v in sorted(lifecycle.items())))
    return "\n".join(out)[:4000]
