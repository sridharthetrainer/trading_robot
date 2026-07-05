"""Telegram views for all generated autonomous signals and EOD outcomes."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path


def generated_signals_text(day: str | None = None, limit: int = 12) -> str:
    report_day = day or date.today().isoformat()
    if not Path("signal_log.db").exists():
        return "No autonomous signal log yet."
    from autonomous_signal_lifecycle import update_generated_signal_lifecycle
    update_generated_signal_lifecycle(session_date=report_day)
    conn = sqlite3.connect("signal_log.db"); conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT symbol,side,strategy,score,entry_price,stop_loss,target,rr,
                  rejection_reason,executed,lifecycle_status,lifecycle_price,signal_time
             FROM signal_log WHERE signal_date=?
             ORDER BY (executed=1 OR COALESCE(rejection_reason,'')='') DESC,score DESC,id DESC LIMIT ?""",
        (report_day, max(1, min(int(limit), 25))),
    ).fetchall(); conn.close()
    if not rows:
        return f"No generated signals for {report_day}."
    out = [f"📡 <b>AUTONOMOUS GENERATED SIGNALS — {report_day}</b>"]
    for row in rows:
        qualified = bool(row["executed"] or not str(row["rejection_reason"] or ""))
        flag = "🟢" if qualified else "🟡"
        state = str(row["lifecycle_status"] or "OPEN") if qualified else "REJECTED/WATCH"
        try:
            from autonomous_edge_policy import strategy_policy
            evidence = strategy_policy(str(row["strategy"])).get("status", "VALIDATING")
        except Exception:
            evidence = "VALIDATING"
        out.append(
            f"{flag} <b>{row['symbol']} {row['side']}</b> · {row['strategy']} · score {float(row['score'] or 0):.1f} · {state} · {evidence}\n"
            f"   Entry ₹{float(row['entry_price'] or 0):.2f} | SL ₹{float(row['stop_loss'] or 0):.2f} | "
            f"Target ₹{float(row['target'] or 0):.2f} | RR {float(row['rr'] or 0):.2f}"
        )
    out.append("🟢 qualified/tracked · 🟡 generated but rejected/watch")
    return "\n".join(out)[:4000]


def all_eod_text(day: str | None = None) -> str:
    report_day = day or date.today().isoformat()
    conn = sqlite3.connect("signal_log.db"); conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        """SELECT strategy,side,executed,rejection_reason,tb_label,tb_r_multiple_net,
                  lifecycle_status FROM signal_log WHERE signal_date=?""", (report_day,),
    )]
    conn.close()
    labelled = [row for row in rows if row["tb_label"] in (-1, 0, 1)]
    wins = sum(row["tb_label"] == 1 for row in labelled)
    net_r = sum(float(row["tb_r_multiple_net"] or 0) for row in labelled)
    categories = defaultdict(lambda: {"n": 0, "labelled": 0, "wins": 0, "r": 0.0})
    for row in rows:
        bucket = categories[str(row["strategy"] or "unknown")]
        bucket["n"] += 1
        if row["tb_label"] in (-1, 0, 1):
            bucket["labelled"] += 1; bucket["wins"] += int(row["tb_label"] == 1)
            bucket["r"] += float(row["tb_r_multiple_net"] or 0)
    try:
        trades = sqlite3.connect("trades.db"); traded = trades.execute(
            """SELECT COUNT(*),COALESCE(SUM(realized_pnl),0),COALESCE(SUM(total_charges),0)
                 FROM trades WHERE date(entry_time,'unixepoch','+5 hours','30 minutes')=?""", (report_day,),
        ).fetchone(); trades.close()
    except Exception:
        traded = (0, 0, 0)
    lifecycle = defaultdict(int)
    for row in rows: lifecycle[str(row.get("lifecycle_status") or "UNSET")] += 1
    out = [
        f"📊 <b>AUTONOMOUS ALL-IN-ONE — {report_day}</b>",
        f"Generated {len(rows)} | Qualified {sum(not str(r['rejection_reason'] or '') for r in rows)} | Executed {sum(int(r['executed'] or 0) for r in rows)}",
        f"Labelled {len(labelled)} | Pending {sum(r['tb_label']==-99 for r in rows)} | Wins {wins} | Ideal net R {net_r:+.2f}",
        f"Trades taken {int(traded[0] or 0)} | Traded net ₹{float(traded[1] or 0):+,.0f} | Charges ₹{float(traded[2] or 0):,.0f}",
        "Lifecycle: " + " · ".join(f"{k} {v}" for k, v in sorted(lifecycle.items())),
        "\n<b>Strategy categories</b>",
    ]
    for strategy, bucket in sorted(categories.items(), key=lambda item: (-item[1]["n"], item[0]))[:20]:
        wr = 100 * bucket["wins"] / bucket["labelled"] if bucket["labelled"] else 0
        out.append(f"• {strategy}: n={bucket['n']}, labelled={bucket['labelled']}, WR={wr:.0f}%, net R {bucket['r']:+.2f}")
    return "\n".join(out)[:4000]
