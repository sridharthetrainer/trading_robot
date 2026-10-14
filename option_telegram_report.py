"""Chart-first post-market report for the dedicated option Telegram bot.

The report deliberately uses only locally captured evidence.  It can therefore
be generated after NSE closes without depending on a live option-chain API.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import chart_theme as ct

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception as exc:
    logger.debug("Could not load option bot .env: %s", exc)

JOURNAL_FILE = Path(os.getenv("OPTION_DECISION_JOURNAL", "option_decision_journal.jsonl"))
TRADES_DB = Path(os.getenv("TRADES_DB", "trades.db"))
STATE_FILE = Path(os.getenv("OPTION_REPORT_STATE", "option_telegram_report_state.json"))
REPORT_DIR = Path(os.getenv("OPTION_REPORT_DIR", "reports"))
OPTION_SYMBOL = re.compile(r"[0-9](?:CE|PE)$", re.I)
STRIKE_DB = Path(os.getenv("OPTION_STRIKE_DB", "option_chain_snapshots.db"))


def _parse_time(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata")) if parsed.tzinfo is None else parsed.astimezone(ZoneInfo("Asia/Kolkata"))
    except ValueError:
        return None


def _journal_rows(day: str) -> List[dict]:
    rows: List[dict] = []
    try:
        with JOURNAL_FILE.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if str(row.get("time", ""))[:10] == day:
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _option_trades(day: str) -> List[dict]:
    if not TRADES_DB.exists():
        return []
    out: List[dict] = []
    try:
        con = sqlite3.connect(f"file:{TRADES_DB}?mode=ro", uri=True, timeout=5)
        cols = {row[1] for row in con.execute("PRAGMA table_info(trades)")}
        gross_expr = (
            "CASE WHEN COALESCE(gross_pnl,0)!=0 THEN gross_pnl "
            "ELSE COALESCE(realized_pnl,0)+COALESCE(total_charges,0) END"
            if "gross_pnl" in cols else "COALESCE(realized_pnl,0)+COALESCE(total_charges,0)"
        )
        rows = con.execute(
            f"SELECT symbol,status,entry_time,exit_time,realized_pnl,total_charges,{gross_expr} "
            "FROM trades ORDER BY COALESCE(exit_time,entry_time)"
        ).fetchall()
        con.close()
        for symbol, status, entry_ts, exit_ts, pnl, charges, gross_pnl in rows:
            if not OPTION_SYMBOL.search(str(symbol or "")):
                continue
            ts = exit_ts or entry_ts
            try:
                dt = datetime.fromtimestamp(float(ts), ZoneInfo("Asia/Kolkata"))
            except (TypeError, ValueError, OSError):
                continue
            if dt.date().isoformat() == day:
                out.append({
                    "symbol": symbol, "status": status, "time": dt,
                    "pnl": float(pnl or 0), "gross_pnl": float(gross_pnl or 0),
                    "charges": float(charges or 0),
                })
    except (sqlite3.Error, OSError) as exc:
        logger.warning("option report trade load failed: %s", exc)
    return out


def _all_option_signals(day: str) -> List[dict]:
    """Load every generated strike signal, including WATCH and unexecuted rows."""
    if not STRIKE_DB.exists():
        return []
    try:
        con = sqlite3.connect(STRIKE_DB)
        from option_multistrike_signals import ensure_multistrike_schema
        ensure_multistrike_schema(con)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT snapshot_time,underlying,expiry,strike,option_type,signal,tradable,
                      entry_price,stop_loss,target_1,target_2,outcome_label,exit_price,
                      gross_pnl,estimated_costs,net_pnl,net_r,execution_status
                      ,lifecycle_status,status_updated_at
                 FROM option_strike_signals WHERE snapshot_time LIKE ?
                 ORDER BY ts,strike,option_type""", (day + "%",),
        ).fetchall()
        con.commit()
        con.close()
        return [dict(row) for row in rows]
    except (sqlite3.Error, OSError) as exc:
        logger.warning("all option signal load failed: %s", exc)
        return []


def _dedupe_selections(rows: Iterable[dict]) -> List[dict]:
    seen = set()
    selected = []
    for row in rows:
        if not str(row.get("decision", "")).lower().startswith("selected"):
            continue
        strategy = str(row.get("strategy", "")).lower()
        if any(x in strategy for x in ("replay", "backtest", "shadow", "snapshot")):
            continue
        leg = row.get("selected") or {}
        key = (
            str(row.get("time", ""))[:16], row.get("symbol"), row.get("side"),
            leg.get("strike"), leg.get("option_type"),
        )
        if key not in seen:
            seen.add(key)
            selected.append(row)
    return selected


def collect_report_data(day: Optional[str] = None) -> Dict[str, Any]:
    report_day = day or datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    rows = _journal_rows(report_day)
    selections = _dedupe_selections(rows)
    trades = _option_trades(report_day)
    all_signals = _all_option_signals(report_day)

    spots: Dict[str, List[tuple]] = defaultdict(list)
    for row in rows:
        dt = _parse_time(row.get("time"))
        symbol = str(row.get("symbol", "")).upper()
        try:
            spot = float(row.get("spot") or 0)
        except (TypeError, ValueError):
            spot = 0
        if dt and spot > 0 and symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}:
            spots[symbol].append((dt, spot))

    # Collapse repeated scans within one minute; they otherwise obscure the line.
    collapsed: Dict[str, List[tuple]] = {}
    latest_spots: Dict[str, float] = {}
    for symbol, values in spots.items():
        per_minute = {}
        for dt, value in values:
            per_minute[dt.replace(second=0, microsecond=0)] = value
        ordered = sorted(per_minute.items())
        if ordered:
            latest_spots[symbol] = ordered[-1][1]
        # Do not turn stale/off-hours refreshes into a fictitious market move.
        collapsed[symbol] = [item for item in ordered
                             if time(9, 15) <= item[0].time() <= time(15, 35)]

    blocked = [r for r in rows if str(r.get("decision", "")).lower().startswith("blocked")]
    option_types = Counter(str((r.get("selected") or {}).get("option_type", "")).upper()
                           for r in selections)
    option_types.pop("", None)
    closed = [t for t in trades if str(t["status"]).upper() == "CLOSED"]
    open_trades = [t for t in trades if str(t["status"]).upper() != "CLOSED"]
    labelled_signals = [r for r in all_signals if int(r.get("outcome_label", -99)) in (-1, 0, 1)]
    actionable_labelled = [r for r in labelled_signals if bool(r.get("tradable"))]
    pending_signals = [r for r in all_signals if int(r.get("outcome_label", -99)) == -99]
    unfilled_signals = [r for r in all_signals if int(r.get("outcome_label", -99)) == -2]
    return {
        "day": report_day, "spots": collapsed, "latest_spots": latest_spots,
        "selections": selections,
        "blocked": blocked, "option_types": option_types, "trades": trades,
        "closed": closed, "realized_pnl": sum(t["pnl"] for t in closed),
        "charges": sum(t["charges"] for t in trades),
        "wins": sum(1 for t in closed if t["pnl"] > 0),
        "open_trades": open_trades,
        "traded_gross_pnl": sum(t["gross_pnl"] for t in closed),
        "traded_net_pnl": sum(t["pnl"] for t in closed),
        "all_signals": all_signals,
        "labelled_signals": labelled_signals,
        "pending_signals": pending_signals,
        "unfilled_signals": unfilled_signals,
        "all_signal_gross_pnl": sum(float(r.get("gross_pnl") or 0) for r in labelled_signals),
        "all_signal_costs": sum(float(r.get("estimated_costs") or 0) for r in labelled_signals),
        "all_signal_net_pnl": sum(float(r.get("net_pnl") or 0) for r in labelled_signals),
        "all_signal_wins": sum(1 for r in labelled_signals if int(r.get("outcome_label", 0)) == 1),
        "ideal_avg_net_pnl": (
            sum(float(r.get("net_pnl") or 0) for r in labelled_signals) / len(labelled_signals)
            if labelled_signals else 0.0
        ),
        "ideal_avg_net_r": (
            sum(float(r.get("net_r") or 0) for r in labelled_signals) / len(labelled_signals)
            if labelled_signals else 0.0
        ),
        "actionable_labelled": actionable_labelled,
        "actionable_ideal_net_pnl": sum(float(r.get("net_pnl") or 0) for r in actionable_labelled),
    }


def _running_counts(rows: List[dict]) -> tuple:
    events = []
    for row in rows:
        dt = _parse_time(row.get("time"))
        if dt:
            events.append(dt)
    events.sort()
    return events, list(range(1, len(events) + 1))


def generate_option_report(day: Optional[str] = None, output_dir: Optional[str] = None) -> Dict[str, Any]:
    data = collect_report_data(day)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    panel, fg = ct.PANEL, ct.TEXT_PRIMARY
    fig, axes = plt.subplots(2, 2, figsize=(10, 12), dpi=145)
    ct.apply_theme(fig, axes.flat)
    for ax in axes.flat:
        ax.tick_params(labelsize=9)

    ax = axes[0, 0]
    palette = list(ct.CATEGORICAL)
    for color, (symbol, series) in zip(palette, sorted(data["spots"].items())):
        if not series:
            continue
        xs, raw = zip(*series)
        base = raw[0]
        pct = [(v / base - 1) * 100 for v in raw]
        ax.plot(xs, pct, color=color, linewidth=2, marker="o", markersize=3, label=f"{symbol} {raw[-1]:,.0f}")
        ax.annotate(f"{pct[-1]:+.2f}%", (xs[-1], pct[-1]), color=color, fontsize=8,
                    xytext=(5, 0), textcoords="offset points")
    ax.axhline(0, color=ct.TEXT_MUTED, linewidth=.8)
    ax.set_title("INDEX MOVE / STORED CAPTURES", color=fg, fontweight="bold", fontsize=11)
    ax.set_ylabel("Change %", color=ct.TEXT_MUTED)
    has_spot_lines = any(len(series) >= 2 for series in data["spots"].values())
    if has_spot_lines:
        ax.legend(facecolor=panel, labelcolor=fg, fontsize=7, loc="best")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=ZoneInfo("Asia/Kolkata")))
    else:
        latest = data.get("latest_spots") or {}
        detail = "\n".join(f"{symbol}: {value:,.0f}"
                           for symbol, value in sorted(latest.items()))
        message = "No intraday series captured"
        if detail:
            message += "\n\nLatest stored values\n" + detail
        ax.text(.5, .5, message, transform=ax.transAxes, color=ct.TEXT_MUTED,
                ha="center", va="center", linespacing=1.5)

    ax = axes[0, 1]
    sx, sy = _running_counts(data["selections"])
    bx, by = _running_counts(data["blocked"])
    if sx:
        ax.step(sx, sy, where="post", color=ct.BULLISH, linewidth=2.2,
                label=f"Selected {len(sx)}")
    if bx:
        ax.step(bx, by, where="post", color=ct.BEARISH, linewidth=2.0,
                label=f"Blocked {len(bx)}")
    ax.set_title("CUMULATIVE OPTION DECISIONS", color=fg, fontweight="bold")
    ax.set_ylabel("Count", color=ct.TEXT_MUTED)
    if sx or bx:
        ax.legend(facecolor=panel, labelcolor=fg, fontsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=ZoneInfo("Asia/Kolkata")))
    else:
        ax.text(.5, .5, "No selected/blocked decisions", transform=ax.transAxes,
                color=ct.TEXT_MUTED, ha="center", va="center")

    ax = axes[1, 0]
    actual = sorted(data["closed"], key=lambda row: row["time"])
    if actual:
        amounts = [row["pnl"] for row in actual]
        ax.bar(range(1, len(actual) + 1), amounts,
               color=[ct.BULLISH if value >= 0 else ct.BEARISH for value in amounts], width=.65)
        ax.axhline(0, color=ct.TEXT_MUTED, linewidth=.8)
        ax.set_xlabel("Closed recorded trades", color=ct.TEXT_MUTED)
        ax.set_ylabel("Net P&L ₹", color=ct.TEXT_MUTED)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.0f}"))
    else:
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(.5, .60, "No closed recorded trades", transform=ax.transAxes,
                color=fg, fontsize=13, ha="center", va="center")
        ax.text(.5, .41, "Generated signals are not booked profit.", transform=ax.transAxes,
                color=ct.TEXT_MUTED, fontsize=9, ha="center", va="center")
    ax.set_title("RECORDED TRADE RESULTS", color=fg, fontweight="bold")

    ax = axes[1, 1]
    closed = sorted(data["closed"], key=lambda x: x["time"])
    labelled_signals = data["labelled_signals"]
    if labelled_signals:
        values = [float(row.get("net_pnl") or 0) for row in labelled_signals]
        cumulative = []
        total = 0.0
        for value in values:
            total += value
            cumulative.append(total)
        color = ct.BULLISH if total >= 0 else ct.BEARISH
        ax.plot(range(1, len(cumulative) + 1), cumulative, color=color, linewidth=2.2,
                label=f"Ideal signal net ₹{total:+,.0f}")
        ax.axhline(0, color=ct.TEXT_MUTED, linewidth=.8)
        ax.legend(facecolor=panel, labelcolor=fg, fontsize=8)
        ax.set_xlabel("Labelled generated signals · simulated", color=ct.TEXT_MUTED)
    else:
        ax.text(.5, .5, "No labelled signal outcomes", transform=ax.transAxes,
                color=ct.TEXT_MUTED, ha="center", va="center")
    ax.set_title("SIMULATED SIGNAL P&L", color=fg, fontweight="bold")
    ax.set_ylabel("₹ cumulative", color=ct.TEXT_MUTED)
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda value, _pos: f"{value/100000:.1f}L" if abs(value) >= 100000 else f"{value:,.0f}"))

    closed_n = len(closed)
    win_rate = (100 * data["wins"] / closed_n) if closed_n else 0
    signal_n = len(data["all_signals"])
    labelled_n = len(data["labelled_signals"])
    signal_wr = 100 * data["all_signal_wins"] / labelled_n if labelled_n else 0
    traded_wr = 100 * data["wins"] / closed_n if closed_n else 0
    fig.suptitle(f"OPTION DESK / SESSION REVIEW\n{data['day']}", color=ct.TEXT_PRIMARY,
                 fontsize=20, fontweight="bold", y=.975)
    metrics = [
        ("RECORDED TRADE NET", f"₹{data['traded_net_pnl']:+,.0f}", ct.BULLISH if data['traded_net_pnl'] > 0 else ct.BEARISH if data['traded_net_pnl'] < 0 else ct.TEXT_PRIMARY),
        ("CLOSED / OPEN TRADES", f"{closed_n} / {len(data['open_trades'])}", ct.TEXT_PRIMARY),
        ("CHARGES / ALL TRADES", f"₹{data['charges']:,.0f}", ct.TEXT_PRIMARY),
        ("GENERATED / LABELLED", f"{signal_n} / {labelled_n}", ct.INFO),
        ("SIMULATED SIGNAL NET", f"₹{data['all_signal_net_pnl']:+,.0f}", ct.WARNING),
        ("PENDING / UNFILLED", f"{len(data['pending_signals'])} / {len(data['unfilled_signals'])}", ct.TEXT_PRIMARY),
    ]
    for index, (label, value, color) in enumerate(metrics):
        col, row = index % 3, index // 3
        stat = fig.add_axes([.065 + col * .30, .805 - row * .085, .27, .07])
        stat.set_facecolor(ct.PANEL)
        stat.set_xticks([]); stat.set_yticks([])
        for spine in stat.spines.values():
            spine.set_color(ct.GRID)
        stat.text(.07, .82, label, transform=stat.transAxes, color=ct.TEXT_MUTED, fontsize=8, va="top")
        stat.text(.07, .45, value, transform=stat.transAxes, color=color, fontsize=20, fontweight="bold", va="top")
    fig.subplots_adjust(left=.095, right=.965, top=.66, bottom=.09, hspace=.38, wspace=.30)
    fig.text(.065, .03, "Recorded trades may be paper or live. Simulated signal P&L is research, not realized income.",
             color=ct.TEXT_MUTED, fontsize=9)
    for ax in axes.flat:
        ax.tick_params(axis="x", labelrotation=20)

    out = Path(output_dir) if output_dir else REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"option_post_market_{data['day']}.png"
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    caption = (
        f"📊 Option session review · {data['day']}\n"
        f"RECORDED TRADES: Closed {closed_n} · Open {len(data['open_trades'])}\n"
        f"Recorded gross ₹{data['traded_gross_pnl']:+,.0f} · Charges ₹{data['charges']:,.0f} · Net ₹{data['traded_net_pnl']:+,.0f}\n"
        f"RESEARCH: Generated {signal_n} · Labelled {labelled_n} · Pending {len(data['pending_signals'])} · Unfilled {len(data['unfilled_signals'])}\n"
        f"Simulated signal net ₹{data['all_signal_net_pnl']:+,.0f} · Avg R {data['ideal_avg_net_r']:+.2f}\n"
        "Recorded trades may be paper or live. Research P&L is not realized income."
    )
    if closed:
        caption += "\n" + "\n".join(
            f"• {trade['symbol']} ₹{trade['pnl']:+,.0f} ({trade['status']})"
            for trade in closed[:3]
        )
    return {"ok": True, "path": str(path), "caption": caption, **data}


def is_post_market(now: Optional[datetime] = None) -> bool:
    current = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    if current.tzinfo is not None:
        current = current.astimezone(ZoneInfo("Asia/Kolkata"))
    return current.weekday() < 5 and current.time() >= time(15, 35)


def build_evidence_digest(day: Optional[str] = None) -> str:
    """EOD evidence digest for the option channel: what the bot LEARNED
    today — labelled outcomes, win rate by OI-flow bucket, autotune
    weight extremes, and progress toward the audit's evidence gates.
    Makes the learning loop visible instead of silent. Best-effort text."""
    import sqlite3
    d = day or date.today().isoformat()
    lines = [f"🧪 <b>OPTION EVIDENCE DIGEST</b> · {d}"]
    try:
        with sqlite3.connect("option_chain_snapshots.db") as conn:
            row = conn.execute(
                "SELECT COUNT(*), SUM(outcome_label=1), ROUND(AVG(net_pnl),0) "
                "FROM option_strike_signals WHERE snapshot_time LIKE ? "
                "AND outcome_label IN (-1,0,1)", (d + "%",)).fetchone()
            n, wins, avg = int(row[0] or 0), int(row[1] or 0), row[2]
            if n:
                lines.append(f"📊 Labelled today: {n} · wins {wins} "
                             f"({wins / n * 100:.0f}%) · avg net ₹{avg}")
                flows = conn.execute(
                    "SELECT flow, COUNT(*), SUM(outcome_label=1), ROUND(AVG(net_pnl),0) "
                    "FROM option_strike_signals WHERE snapshot_time LIKE ? "
                    "AND outcome_label IN (-1,0,1) GROUP BY flow ORDER BY 4 DESC",
                    (d + "%",)).fetchall()
                for f, fn, fw, fa in flows[:5]:
                    lines.append(f"   {f}: {int(fw or 0)}/{fn} · ₹{fa}")
            else:
                lines.append("📊 No outcomes labelled today (labeller runs EOD).")
    except Exception as exc:
        lines.append(f"📊 Strike outcomes unavailable ({str(exc)[:40]})")
    try:
        tune = json.loads(Path("option_strike_autotune.json").read_text())
        weights = tune.get("feature_weights", {}) or {}
        if weights:
            import html as _html
            ranked = sorted(weights.items(), key=lambda kv: kv[1])
            # feature names like premium:<5 must not read as HTML tags
            worst = ", ".join(f"{_html.escape(k)} {v}" for k, v in ranked[:2])
            best = ", ".join(f"{_html.escape(k)} {v}" for k, v in ranked[-2:])
            lines.append(f"⚖️ Autotune ({tune.get('verified_generated_outcomes', 0)} "
                         f"verified): worst {worst} · best {best}")
    except Exception as exc:
        lines.append(f"⚖️ Autotune unavailable ({str(exc)[:40]})")
    try:
        rep = json.loads(Path("option_bot_audit_report.json").read_text())
        s = rep.get("score", {}) or {}
        jr = rep.get("decision_journal", {}) or {}
        blocks = s.get("evidence_blocks", []) or []
        lines.append(f"🎯 Gates: score {s.get('total', '?')}/100 "
                     f"{s.get('readiness', '')} · live selections "
                     f"{jr.get('verified_selected', 0)}/10"
                     + (f" · blocks: {', '.join(blocks)}" if blocks else ""))
    except Exception as exc:
        lines.append(f"🎯 Audit gates unavailable ({str(exc)[:40]})")
    lines.append("<i>Evidence only — no validated edge yet; paper mode.</i>")
    return "\n".join(lines)


def send_post_market_option_report(*, force: bool = False) -> Dict[str, Any]:
    """Send once per session to OPTION_BOT_TOKEN/OPTION_CHAT_ID after 15:35."""
    today = date.today().isoformat()
    if not force and not is_post_market():
        return {"ok": False, "skipped": "not_post_market"}
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    if not force and state.get("sent_for") == today:
        return {"ok": True, "skipped": "already_sent", "day": today}
    token = os.getenv("OPTION_BOT_TOKEN", "").strip()
    chat = os.getenv("OPTION_CHAT_ID", "").strip()
    if not token or not chat:
        return {"ok": False, "skipped": "option_telegram_not_configured"}
    report = generate_option_report(today)
    from alerts import AlertManager
    am = AlertManager(bot_token=token, chat_id=chat, name="option")
    sent = am.send_photo(report["path"], report["caption"])
    digest_sent = False
    try:
        digest_sent = bool(am.send(build_evidence_digest(today),
                                   dedup_key=f"opt_evidence:{today}",
                                   cooldown=6 * 3600))
    except Exception as exc:
        logger.debug("evidence digest send failed: %s", exc)
    if sent:
        STATE_FILE.write_text(json.dumps({"sent_for": today, "sent_at": datetime.now().isoformat()}, indent=2))
    return {"ok": bool(sent), "day": today, "path": report["path"],
            "evidence_digest_sent": digest_sent}


if __name__ == "__main__":
    result = send_post_market_option_report(force="--force" in __import__("sys").argv)
    print(json.dumps(result, indent=2))
