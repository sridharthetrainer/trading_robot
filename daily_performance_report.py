"""
daily_performance_report.py

End-of-day strategy performance summary:
  - Today's win rate per strategy
  - Rolling 5-day win rate
  - Trend direction  (↑ improving / ↓ declining / → flat)
  - Auto-pause candidates  (5d-WR < 40%, n >= 30)
  - Promoting strategies   (5d-WR >= 58%, n >= 30)

Run standalone at market close (15:35 IST) or import for cron/post-market use.
Optional --telegram flag pushes a summary card to Telegram.

Usage:
  python3 daily_performance_report.py
  python3 daily_performance_report.py --telegram
  from daily_performance_report import run_daily_report
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

DB_PATH       = "signal_log.db"
PAUSED_FILE   = "paused_strategies.json"
REPORT_FILE   = "daily_report.json"
BREAKEVEN_WR  = 0.42
PAUSE_WR      = 0.40   # recommend pause if 5d-WR falls below this
PAUSE_MIN_N   = 30     # require at least this many signals before recommending pause
PROMOTE_WR    = 0.58   # flag as PROMOTING if rolling WR is above this
LOOKBACK_DAYS = 5


def _load_paused() -> List[str]:
    try:
        raw = json.loads(Path(PAUSED_FILE).read_text())
        items = raw if isinstance(raw, list) else []
        return [s.lower() for s in items]   # normalise to lowercase to match signal_log
    except Exception:
        return []


def _load_signals(n_days: int = LOOKBACK_DAYS) -> List[Dict[str, Any]]:
    """
    Load labeled signals from the n most recent distinct signal_dates.
    Returns a flat list of {strategy, signal_date, outcome} dicts.
    """
    rows: List[Dict[str, Any]] = []
    try:
        conn = sqlite3.connect(DB_PATH)

        # Resolve the n most recent distinct dates that have labeled data
        date_rows = conn.execute("""
            SELECT DISTINCT signal_date
              FROM signal_log
             WHERE tb_label IN (1, -1)
               AND signal_date IS NOT NULL
             ORDER BY signal_date DESC
             LIMIT ?
        """, (n_days,)).fetchall()

        if not date_rows:
            conn.close()
            return []

        oldest      = date_rows[-1][0]
        most_recent = date_rows[0][0]

        data_rows = conn.execute("""
            SELECT strategy, signal_date, tb_label
              FROM signal_log
             WHERE tb_label IN (1, -1)
               AND signal_date >= ?
               AND signal_date <= ?
             ORDER BY signal_date, id
        """, (oldest, most_recent)).fetchall()
        conn.close()

        for strategy, signal_date, tb_label in data_rows:
            rows.append({
                "strategy":    strategy or "unknown",
                "signal_date": signal_date,
                "outcome":     1 if tb_label == 1 else 0,
            })
    except Exception as exc:
        logger.warning("daily_report: signal load error: %s", exc)
    return rows


def _compute_stats(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Compute per-strategy stats from the loaded signal rows.

    Returns dict keyed by strategy with:
      n_total, n_today, today_wr, roll_wr, trend, day_wrs, n_days
    """
    all_dates = sorted({r["signal_date"] for r in rows})
    today     = all_dates[-1] if all_dates else None

    # Build strategy → date → outcome list
    by_strat: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_strat[r["strategy"]][r["signal_date"]].append(r["outcome"])

    stats: Dict[str, Dict[str, Any]] = {}
    for strat, by_date in by_strat.items():
        day_wrs: List[Tuple[str, float, int]] = []
        for d in sorted(by_date):
            outs = by_date[d]
            day_wrs.append((d, sum(outs) / len(outs), len(outs)))

        all_outs = [o for outs in by_date.values() for o in outs]
        n_total  = len(all_outs)
        roll_wr  = sum(all_outs) / n_total if n_total else 0.0

        today_outs = by_date.get(today, []) if today else []
        today_wr   = (sum(today_outs) / len(today_outs)) if today_outs else None
        n_today    = len(today_outs)

        # Trend: last day vs average of all preceding days
        trend = "→"
        if len(day_wrs) >= 2:
            preceding_avg = sum(w for _, w, _ in day_wrs[:-1]) / (len(day_wrs) - 1)
            last_wr       = day_wrs[-1][1]
            diff          = last_wr - preceding_avg
            if diff >= 0.05:
                trend = "↑"
            elif diff <= -0.05:
                trend = "↓"

        stats[strat] = {
            "strategy": strat,
            "n_total":  n_total,
            "n_today":  n_today,
            "today_wr": round(today_wr, 4) if today_wr is not None else None,
            "roll_wr":  round(roll_wr, 4),
            "trend":    trend,
            "day_wrs":  [(d, round(w, 4), n) for d, w, n in day_wrs],
            "n_days":   len(day_wrs),
        }

    return stats


def _classify(s: Dict[str, Any], paused: List[str]) -> str:
    if s["strategy"] in paused:
        return "PAUSED"
    n  = s["n_total"]
    wr = s["roll_wr"]
    if n < 15:
        return "INSUFFICIENT"
    if wr >= PROMOTE_WR and n >= 30:
        return "PROMOTING"
    if wr < BREAKEVEN_WR:
        return "NEGATIVE"
    return "ACTIVE"


def run_daily_report(telegram: bool = False, save: bool = True) -> Dict[str, Any]:
    """
    Main entry point.  Loads signals, computes stats, prints table, optionally
    pushes to Telegram, and saves daily_report.json.
    """
    paused = _load_paused()
    rows   = _load_signals(LOOKBACK_DAYS)

    if not rows:
        logger.warning("daily_report: no labeled signals found in signal_log.db")
        return {"error": "no_data"}

    stats     = _compute_stats(rows)
    all_dates = sorted({r["signal_date"] for r in rows})
    today     = all_dates[-1] if all_dates else "unknown"

    pause_candidates = [
        s for s, st in stats.items()
        if st["roll_wr"] < PAUSE_WR and st["n_total"] >= PAUSE_MIN_N and s not in paused
    ]
    promote_candidates = [
        s for s, st in stats.items()
        if st["roll_wr"] >= PROMOTE_WR and st["n_total"] >= 30
    ]

    report = {
        "report_date":       today,
        "generated_at":      datetime.now().isoformat(),
        "n_days_in_window":  len(all_dates),
        "lookback_days":     LOOKBACK_DAYS,
        "total_signals":     len(rows),
        "active_strategies": len([s for s in stats if s not in paused]),
        "pause_candidates":  pause_candidates,
        "promote_candidates":promote_candidates,
        "strategies":        stats,
    }

    if save:
        Path(REPORT_FILE).write_text(json.dumps(report, indent=2))

    text = _format_report(report, paused)
    print(text)

    if telegram:
        ok = _send_telegram(_format_telegram(report, paused))
        if not ok:
            logger.warning("daily_report: Telegram send failed")

    return report


# ── Formatting helpers ────────────────────────────────────────────────────────

def _format_report(report: Dict[str, Any], paused: List[str]) -> str:
    w = 72
    lines = [
        f"\n{'═'*w}",
        f"  DAILY STRATEGY REPORT  {report['report_date']}  "
        f"({report['n_days_in_window']} day(s) of labeled data)",
        f"  Total signals: {report['total_signals']:,}  |  "
        f"Active strategies: {report['active_strategies']}",
        f"{'═'*w}",
        f"{'Strategy':<36} {'Today':>6} {'5d-WR':>6} {'N':>5}  Trend  Status",
        f"{'─'*w}",
    ]

    strats = sorted(
        report["strategies"].values(),
        key=lambda s: (s["strategy"] not in paused, s["roll_wr"]),
        reverse=True,
    )

    for s in strats:
        name      = s["strategy"][:35]
        today_str = f"{s['today_wr']:.0%}" if s["today_wr"] is not None else "  --"
        roll      = f"{s['roll_wr']:.0%}"
        n         = s["n_total"]
        trend     = s["trend"]
        status    = _classify(s, paused)
        marker    = "  ←" if status == "PROMOTING" else ("  ⚠" if status == "NEGATIVE" else "")
        lines.append(
            f"{name:<36} {today_str:>6} {roll:>6} {n:>5}  {trend:^5}  {status}{marker}"
        )

    if report["pause_candidates"]:
        lines.append(f"\n{'─'*w}")
        lines.append("  AUTO-PAUSE CANDIDATES (5d-WR < 40%, n >= 30):")
        for s in report["pause_candidates"]:
            st = report["strategies"][s]
            lines.append(
                f"    • {s}:  5d-WR={st['roll_wr']:.0%}  n={st['n_total']}"
                f"  → add to paused_strategies.json"
            )

    if report["promote_candidates"]:
        lines.append(f"\n{'─'*w}")
        lines.append("  PROMOTING (5d-WR >= 58%, n >= 30):")
        for s in report["promote_candidates"]:
            st = report["strategies"][s]
            lines.append(f"    • {s}:  5d-WR={st['roll_wr']:.0%}  n={st['n_total']}")

    lines.append(f"{'═'*w}\n")
    return "\n".join(lines)


def _format_telegram(report: Dict[str, Any], paused: List[str]) -> str:
    lines = [
        f"<b>Daily Strategy Report — {report['report_date']}</b>",
        f"Signals: {report['total_signals']:,} | Window: {report['n_days_in_window']} day(s)",
        "",
    ]

    # Top 12 active strategies only (keep message readable)
    active = [
        s for s in report["strategies"].values() if s["strategy"] not in paused
    ]
    active.sort(key=lambda s: s["roll_wr"], reverse=True)

    for s in active[:12]:
        roll   = f"{s['roll_wr']:.0%}"
        trend  = s["trend"]
        n      = s["n_total"]
        status = _classify(s, paused)
        emoji  = "🟢" if status == "PROMOTING" else ("🔴" if status == "NEGATIVE" else "🟡")
        lines.append(f"{emoji} <b>{s['strategy']}</b>: {roll} (n={n}) {trend}")

    if report["pause_candidates"]:
        lines.append("")
        lines.append("⚠️ <b>Pause candidates:</b>")
        for s in report["pause_candidates"]:
            st = report["strategies"][s]
            lines.append(f"  • {s}: {st['roll_wr']:.0%} ({st['n_total']} signals)")

    if report["promote_candidates"]:
        lines.append("")
        lines.append("🌟 <b>Promoting:</b>")
        for s in report["promote_candidates"]:
            st = report["strategies"][s]
            lines.append(f"  • {s}: {st['roll_wr']:.0%} ({st['n_total']} signals)")

    return "\n".join(lines)


def _send_telegram(message: str) -> bool:
    """Send via Telegram Bot API. Reads BOT_TOKEN + CHAT_ID from environment."""
    import os
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed — cannot push to Telegram")
        return False

    token   = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")  or os.getenv("CHAT_ID")

    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN / CHAT_ID not set in env — skipping Telegram")
        return False

    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        if not resp.ok:
            logger.warning("Telegram send failed (%s): %s", resp.status_code, resp.text[:80])
        return resp.ok
    except Exception as exc:
        logger.warning("Telegram send error: %s", exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_daily_report(telegram="--telegram" in sys.argv)
