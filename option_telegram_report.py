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
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
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
                dt = datetime.fromtimestamp(float(ts))
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
    report_day = day or date.today().isoformat()
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

    bg, panel, fg, grid = "#08111f", "#101c2c", "#e6edf3", "#2b3b50"
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), dpi=145)
    fig.patch.set_facecolor(bg)
    for ax in axes.flat:
        ax.set_facecolor(panel)
        ax.tick_params(colors="#aebdca", labelsize=8)
        ax.grid(True, color=grid, alpha=.45, linewidth=.7)
        for spine in ax.spines.values():
            spine.set_color(grid)

    ax = axes[0, 0]
    palette = ["#4dabf7", "#ffd43b", "#63e6be", "#da77f2", "#ff922b"]
    for color, (symbol, series) in zip(palette, sorted(data["spots"].items())):
        if not series:
            continue
        xs, raw = zip(*series)
        base = raw[0]
        pct = [(v / base - 1) * 100 for v in raw]
        ax.plot(xs, pct, color=color, linewidth=2, label=f"{symbol} {raw[-1]:,.0f}")
        ax.annotate(f"{pct[-1]:+.2f}%", (xs[-1], pct[-1]), color=color, fontsize=8,
                    xytext=(5, 0), textcoords="offset points")
    ax.axhline(0, color="#8394a5", linewidth=.8)
    ax.set_title("INDEX MOVE (09:15–15:35 captures)", color=fg, fontweight="bold")
    ax.set_ylabel("Change %", color="#aebdca")
    has_spot_lines = any(len(series) >= 2 for series in data["spots"].values())
    if has_spot_lines:
        ax.legend(facecolor=panel, labelcolor=fg, fontsize=7, loc="best")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    else:
        latest = data.get("latest_spots") or {}
        detail = "\n".join(f"{symbol}: {value:,.0f}"
                           for symbol, value in sorted(latest.items()))
        message = "No intraday series captured"
        if detail:
            message += "\n\nLatest stored values\n" + detail
        ax.text(.5, .5, message, transform=ax.transAxes, color="#aebdca",
                ha="center", va="center", linespacing=1.5)

    ax = axes[0, 1]
    sx, sy = _running_counts(data["selections"])
    bx, by = _running_counts(data["blocked"])
    if sx:
        ax.step(sx, sy, where="post", color="#51cf66", linewidth=2.2,
                label=f"Selected {len(sx)}")
    if bx:
        ax.step(bx, by, where="post", color="#ff6b6b", linewidth=2.0,
                label=f"Blocked {len(bx)}")
    ax.set_title("CUMULATIVE OPTION DECISIONS", color=fg, fontweight="bold")
    ax.set_ylabel("Count", color="#aebdca")
    if sx or bx:
        ax.legend(facecolor=panel, labelcolor=fg, fontsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    else:
        ax.text(.5, .5, "No selected/blocked decisions", transform=ax.transAxes,
                color="#8394a5", ha="center", va="center")

    ax = axes[1, 0]
    types = ["CE", "PE"]
    vals = [data["option_types"].get(t, 0) for t in types]
    bars = ax.bar(types, vals, color=["#ff8787", "#69db7c"], width=.55)
    ax.set_title("SELECTED LEGS", color=fg, fontweight="bold")
    ax.set_ylabel("Distinct selections", color="#aebdca")
    ax.set_ylim(0, max(vals + [1]) * 1.25)
    for bar, value in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width()/2, value + .03, str(value),
                ha="center", va="bottom", color=fg, fontweight="bold")

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
        color = "#51cf66" if total >= 0 else "#ff6b6b"
        ax.plot(range(1, len(cumulative) + 1), cumulative, color=color, linewidth=2.2,
                label=f"Ideal signal net ₹{total:+,.0f}")
        ax.axhline(0, color="#8394a5", linewidth=.8)
        ax.legend(facecolor=panel, labelcolor=fg, fontsize=8)
        ax.set_xlabel("Labelled generated signals", color="#aebdca")
    elif closed:
        cumulative, total = [], 0.0
        for trade in closed:
            total += trade["pnl"]
            cumulative.append(total)
        color = "#51cf66" if total >= 0 else "#ff6b6b"
        ax.plot([t["time"] for t in closed], cumulative, marker="o", color=color,
                linewidth=2.4, label=f"Net ₹{total:+,.0f}")
        ax.axhline(0, color="#8394a5", linewidth=.8)
        for trade, value in zip(closed, cumulative):
            ax.annotate(f"₹{value:+,.0f}", (trade["time"], value), color=fg,
                        fontsize=8, xytext=(0, 7), textcoords="offset points", ha="center")
        ax.legend(facecolor=panel, labelcolor=fg, fontsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    else:
        ax.text(.5, .5, "No closed option trades", transform=ax.transAxes,
                color="#8394a5", ha="center", va="center")
    ax.set_title("IDEAL SIGNAL AFTER-COST P&L", color=fg, fontweight="bold")
    ax.set_ylabel("₹ cumulative", color="#aebdca")

    closed_n = len(closed)
    win_rate = (100 * data["wins"] / closed_n) if closed_n else 0
    signal_n = len(data["all_signals"])
    labelled_n = len(data["labelled_signals"])
    signal_wr = 100 * data["all_signal_wins"] / labelled_n if labelled_n else 0
    traded_wr = 100 * data["wins"] / closed_n if closed_n else 0
    fig.suptitle(f"OPTION BOT • POST-MARKET REPORT • {data['day']}", color="white",
                 fontsize=17, fontweight="bold", y=.985)
    fig.text(.5, .946,
             f"All signals {signal_n}   |   Labelled {labelled_n}   |   Unfilled {len(data['unfilled_signals'])}   |   Pending {len(data['pending_signals'])}   |   "
             f"Signal WR {signal_wr:.0f}%   |   Net ₹{data['all_signal_net_pnl']:+,.0f}",
             color="#b8c7d9", fontsize=10, ha="center")
    fig.autofmt_xdate(rotation=25)
    plt.tight_layout(rect=(0, .01, 1, .92))

    out = Path(output_dir) if output_dir else REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"option_post_market_{data['day']}.png"
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    caption = (
        f"📊 Option post-market • {data['day']}\n"
        f"ALL signals {signal_n} | Labelled {labelled_n} | Unfilled {len(data['unfilled_signals'])} | Pending {len(data['pending_signals'])}\n"
        f"IDEAL SIGNAL P&L: Gross ₹{data['all_signal_gross_pnl']:+,.0f} | Costs ₹{data['all_signal_costs']:,.0f} | "
        f"Net ₹{data['all_signal_net_pnl']:+,.0f} | WR {signal_wr:.0f}%\n"
        f"Avg/signal ₹{data['ideal_avg_net_pnl']:+,.0f} | Avg R {data['ideal_avg_net_r']:+.2f} | "
        f"Actionable-only net ₹{data['actionable_ideal_net_pnl']:+,.0f}\n"
        f"TRADES TAKEN {len(data['trades'])} | Closed {closed_n} | Open {len(data['open_trades'])} | WR {traded_wr:.0f}%\n"
        f"Traded gross ₹{data['traded_gross_pnl']:+,.0f} | Charges ₹{data['charges']:,.0f} | "
        f"Net realized ₹{data['traded_net_pnl']:+,.0f}"
    )
    if closed:
        caption += "\n" + "\n".join(
            f"• {trade['symbol']} ₹{trade['pnl']:+,.0f} ({trade['status']})"
            for trade in closed[:8]
        )
    return {"ok": True, "path": str(path), "caption": caption, **data}


def is_post_market(now: Optional[datetime] = None) -> bool:
    current = now or datetime.now()
    return current.weekday() < 5 and current.time() >= time(15, 35)


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
    sent = AlertManager(bot_token=token, chat_id=chat, name="option").send_photo(
        report["path"], report["caption"])
    if sent:
        STATE_FILE.write_text(json.dumps({"sent_for": today, "sent_at": datetime.now().isoformat()}, indent=2))
    return {"ok": bool(sent), "day": today, "path": report["path"]}


if __name__ == "__main__":
    result = send_post_market_option_report(force="--force" in __import__("sys").argv)
    print(json.dumps(result, indent=2))
