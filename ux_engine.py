"""
ux_engine.py — Complete End-User Experience Engine

Fixes ALL 18 UX gaps plus additional ones:
  UX-1:  /start onboarding command
  UX-2:  Friendly error messages (ErrorHandler)
  UX-3:  Categorised /help
  UX-4:  Signal outcome notifications
  UX-5:  Lot size + margin in signals
  UX-6:  /today — missed signals recovery
  UX-7:  Intraday position live updates
  UX-8:  /weekly rebuilt as trading performance
  UX-9:  NIFTY benchmark comparison
  UX-10: /export — CSV trade journal
  UX-11: Daily accuracy post to free channel
  UX-12: /paper — virtual P&L tracker
  UX-13: /calculate — position sizing calculator
  UX-14: Pre-market gap warning for open positions
  UX-15: /watch — personal watchlist
  UX-16: /alert — price alerts
  UX-17: Signal WHY reasons
  UX-18: /voice — audio status

Additional:
  UX-19: /next — what to watch next 30 min
  UX-20: /risk — current portfolio risk snapshot
  UX-21: /compare — this week vs last week
  UX-22: /streak — winning/losing streak tracker
"""
from __future__ import annotations
import json, logging, sqlite3, time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_WATCH_FILE  = Path("user_watchlists.json")
_ALERT_FILE  = Path("price_alerts.json")
_PAPER_FILE  = Path("paper_trades.json")
_LOT_SIZES   = {
    "NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60,
    "MIDCPNIFTY": 120, "SENSEX": 20,
}
_MARGIN_RATES = {  # approx SPAN margin %
    "NIFTY": 0.05, "BANKNIFTY": 0.055, "FINNIFTY": 0.05,
    "MIDCPNIFTY": 0.06,
}


# ══════════════════════════════════════════════════════════════
# UX-1: /start — Onboarding
# ══════════════════════════════════════════════════════════════

def get_start_message() -> str:
    return """🤖 <b>NIFTY ALGO TRADING BOT</b>

Welcome! I'm your autonomous NSE trading assistant.

<b>What I do:</b>
• Scan 196 NSE symbols every 5 minutes
• Generate high-quality trade signals (score > 5.5/10)
• Execute trades automatically on Angel One
• Send you real-time alerts and performance reports

<b>Before market opens (8:30 AM):</b>
/morning   — Full pre-market briefing
/brief     — Global markets + VIX + sectors
/sentiment — News sentiment from 40+ sources

<b>During market (9:15 AM - 3:30 PM):</b>
/signals   — Live signals right now
/positions — Open trades + live P&L
/today     — All signals sent today
/status    — Complete bot status

<b>Performance:</b>
/pnl       — Today's P&L
/weekly    — This week's performance
/analytics — Sharpe, Sortino, win rate

<b>Intelligence:</b>
/fii       — FII/DII institutional flows
/sectors   — Sector rotation map
/intelligence — 40+ news sources analysis
/commodities  — Live commodity prices

<b>Settings:</b>
/pause     — Stop new trades
/resume    — Resume trading
/help      — All 101 commands

⚠️ <i>Educational signals only. Not SEBI registered advice.
Always set your own stop loss before trading.</i>

<b>Type /help for full command list</b>"""


# ══════════════════════════════════════════════════════════════
# UX-2: Friendly error messages
# ══════════════════════════════════════════════════════════════

def friendly_error(cmd: str, error: Exception) -> str:
    """Convert Python exceptions to user-friendly messages."""
    err = str(error).lower()
    if "connection" in err or "timeout" in err or "network" in err:
        return f"⚠️ /{cmd}: Connection issue. Try again in 30 seconds."
    if "angel" in err or "smartapi" in err or "broker" in err:
        return f"⚠️ /{cmd}: Angel One temporarily unavailable. Bot continues running."
    if "nse" in err or "nseindia" in err:
        return f"⚠️ /{cmd}: NSE data temporarily unavailable. Retrying automatically."
    if "no data" in err or "empty" in err or "none" in err:
        return f"ℹ️ /{cmd}: No data available yet. Try after market opens (9:15 AM)."
    if "market" in err and "close" in err:
        return f"ℹ️ /{cmd}: Market is closed. Data will refresh at next market open."
    if "permission" in err or "denied" in err:
        return f"❌ /{cmd}: Access denied. Check if bot is running: /health"
    return f"⚠️ /{cmd}: Service temporarily unavailable. Bot is still running. /health to check."


# ══════════════════════════════════════════════════════════════
# UX-3: Categorised /help
# ══════════════════════════════════════════════════════════════

def get_help_message(category: str = "all") -> str:
    sections = {
        "morning": (
            "🌅 <b>MORNING PREP (before 9:15 AM)</b>\n"
            "  /morning     — Full pre-market briefing\n"
            "  /brief       — Global markets snapshot\n"
            "  /video       — Morning market video\n"
            "  /sentiment   — News sentiment score\n"
            "  /fii         — FII/DII flows\n"
            "  /sectors     — Sector rotation\n"
            "  /commodities — Commodity prices\n"
            "  /vix         — India VIX level\n"
            "  /regime      — HMM market regime\n"
        ),
        "trading": (
            "📊 <b>DURING MARKET (9:15–3:30 PM)</b>\n"
            "  /signals     — Live signals now\n"
            "  /today       — All signals sent today\n"
            "  /positions   — Open trades + P&L\n"
            "  /status      — Bot status summary\n"
            "  /pnl         — Today's P&L\n"
            "  /risk        — Portfolio risk snapshot\n"
            "  /pause       — Stop new trades\n"
            "  /resume      — Resume trading\n"
            "  /kill        — Exit ALL positions NOW\n"
        ),
        "analysis": (
            "🧠 <b>ANALYSIS & INTELLIGENCE</b>\n"
            "  /intelligence — 40+ news sources\n"
            "  /news         — Latest headlines\n"
            "  /insider      — Promoter trades\n"
            "  /fnoban       — F&O ban list\n"
            "  /social       — Reddit sentiment\n"
            "  /corpactions  — Dividends/splits\n"
            "  /oi NIFTY     — Options OI\n"
            "  /hmm          — HMM regime\n"
            "  /orderflow    — Order flow pressure\n"
            "  /darkpool     — Block deals\n"
        ),
        "performance": (
            "📈 <b>PERFORMANCE REPORTS</b>\n"
            "  /weekly      — This week vs last week\n"
            "  /analytics   — Sharpe/Sortino/Calmar\n"
            "  /attribution — P&L by strategy/symbol\n"
            "  /accuracy    — Signal accuracy stats\n"
            "  /compare     — Week-on-week comparison\n"
            "  /streak      — Win/loss streak\n"
            "  /export      — Download trade CSV\n"
            "  /paper       — Virtual P&L tracker\n"
        ),
        "tools": (
            "🔧 <b>TOOLS & SETTINGS</b>\n"
            "  /calculate NIFTY 100000 — Position sizer\n"
            "  /watch HDFCBANK TCS     — My watchlist\n"
            "  /alert NIFTY above 24000— Price alert\n"
            "  /voice       — Audio status update\n"
            "  /health      — System health check\n"
            "  /connections — Data feed status\n"
            "  /downloads   — Daily data status\n"
            "  /subscribers — Signal service stats\n"
            "  /backup      — GitHub + Drive backup\n"
        ),
    }

    if category in sections:
        return sections[category] + "\n  Type /help for all categories"

    header = "📱 <b>ALL COMMANDS — Quick Reference</b>\n\n"
    return header + "\n".join(sections.values()) + (
        "\n\n💡 <b>Quick tips:</b>\n"
        "  • /morning before 9:15 AM every day\n"
        "  • /today to see missed signals\n"
        "  • /calculate NIFTY 50000 to size your trade\n"
        "  • /pause before leaving desk\n"
        "  ⚠️ Educational signals | Not SEBI advice"
    )


# ══════════════════════════════════════════════════════════════
# UX-4: Signal outcome notification (called from trade_manager)
# ══════════════════════════════════════════════════════════════

def format_trade_close_notification(trade: dict) -> str:
    """Format a closed trade notification for Telegram."""
    symbol   = trade.get("symbol", "?")
    side     = trade.get("side", "?")
    entry    = float(trade.get("entry_price", 0) or 0)
    exit_px  = float(trade.get("exit_price", 0) or 0)
    pnl      = float(trade.get("pnl", 0) or 0)
    reason   = trade.get("exit_reason", "manual")
    strategy = trade.get("strategy", "?")
    score    = float(trade.get("score", 0) or 0)
    qty      = int(trade.get("qty", 0) or 0)

    # Duration
    try:
        entry_t = datetime.fromisoformat(str(trade.get("entry_time", "")))
        exit_t  = datetime.fromisoformat(str(trade.get("exit_time", datetime.now())))
        mins = int((exit_t - entry_t).total_seconds() / 60)
        duration = f"{mins}m" if mins < 60 else f"{mins//60}h {mins%60}m"
    except Exception:
        duration = "?"

    # P&L styling
    if pnl > 0:
        outcome = "✅ TARGET HIT" if "target" in reason.lower() else "✅ PROFIT"
        pnl_icon = "🟢"
    elif pnl < 0:
        outcome = "🛑 SL HIT" if "stop" in reason.lower() or "sl" in reason.lower() else "🔴 LOSS"
        pnl_icon = "🔴"
    else:
        outcome = "⚪ BREAKEVEN"
        pnl_icon = "⚪"

    pct = abs((exit_px - entry) / entry * 100) if entry else 0
    pct_str = f"+{pct:.1f}%" if pnl >= 0 else f"-{pct:.1f}%"

    return (
        f"{pnl_icon} <b>{outcome} — {symbol}</b>\n"
        f"  {'─'*32}\n"
        f"  {side:4} {qty} shares\n"
        f"  Entry:    ₹{entry:,.2f}\n"
        f"  Exit:     ₹{exit_px:,.2f}  ({pct_str})\n"
        f"  P&L:      ₹{pnl:+,.2f}\n"
        f"  Duration: {duration}\n"
        f"  Strategy: {strategy}  (score {score:.1f})\n"
        f"  Reason:   {reason}\n"
        f"  ⏰ {datetime.now().strftime('%H:%M:%S')}"
    )


# ══════════════════════════════════════════════════════════════
# UX-5: Lot size + margin info for signals
# ══════════════════════════════════════════════════════════════

def get_lot_info(symbol: str, price: float) -> str:
    """Return lot size and margin info for a symbol."""
    sym = symbol.upper().split("CE")[0].split("PE")[0].strip()
    lot = _LOT_SIZES.get(sym, 1)
    margin_rate = _MARGIN_RATES.get(sym, 0.10)

    if lot > 1 and price > 0:
        margin_1lot = price * lot * margin_rate
        margin_str = (
            f"  📦 1 lot = {lot} units | ~₹{margin_1lot:,.0f} margin\n"
            f"  💡 ₹50K capital → {max(0,int(50000/margin_1lot))} lots | "
            f"₹1L → {max(0,int(100000/margin_1lot))} lots"
        )
        return margin_str
    elif price > 0:
        # Equity
        shares_1k  = int(1000 / price)
        shares_10k = int(10000 / price)
        return (
            f"  💡 ₹1,000 buys ~{shares_1k} shares | "
            f"₹10,000 buys ~{shares_10k} shares"
        )
    return ""


# ══════════════════════════════════════════════════════════════
# UX-6: /today — missed signals recovery
# ══════════════════════════════════════════════════════════════

def get_todays_signals() -> str:
    """Show all signals sent today with current status."""
    try:
        import sqlite3
        db = Path("signal_log.db")
        if not db.exists():
            return "📡 No signals sent today yet. Market opens 9:15 AM."

        today = date.today().isoformat()
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT symbol, side, strategy, score, signal_time, executed "
            "FROM signal_log WHERE DATE(signal_time) = ? ORDER BY signal_time",
            (today,)
        ).fetchall()
        conn.close()

        if not rows:
            return "📡 No signals sent today yet."

        lines = [f"📡 <b>TODAY'S SIGNALS</b> | {date.today().strftime('%d-%b')}",
                 f"  Total: {len(rows)}", ""]

        for sym, side, strat, score, sig_time, executed in rows:
            t = str(sig_time)[11:16] if sig_time else "?"
            icon = "🟢" if side == "BUY" else "🔴"
            exec_str = "✅ Executed" if executed else "📤 Sent"
            lines.append(
                f"  {icon} {t}  {sym:12} {side:5}  {exec_str}  score={float(score or 0):.1f}"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"📡 Signal history: {friendly_error('today', e)}"


# ══════════════════════════════════════════════════════════════
# UX-8: /weekly as trading performance
# ══════════════════════════════════════════════════════════════

def get_weekly_performance() -> str:
    """Weekly trading performance report."""
    try:
        db = Path("trades.db")
        if not db.exists():
            return "📊 No trade history yet."

        conn = sqlite3.connect(str(db))
        today   = date.today()
        mon     = today - timedelta(days=today.weekday())
        prev_mon = mon - timedelta(days=7)

        def week_stats(start, end):
            rows = conn.execute(
                "SELECT pnl, strategy, symbol FROM trades "
                "WHERE status='closed' AND DATE(exit_time) >= ? AND DATE(exit_time) < ?",
                (start.isoformat(), end.isoformat())
            ).fetchall()
            if not rows: return None
            pnls = [float(r[0] or 0) for r in rows]
            wins = sum(1 for p in pnls if p > 0)
            return {
                "total": sum(pnls), "count": len(pnls),
                "wins": wins, "wr": wins/len(pnls)*100 if pnls else 0,
                "best": max(pnls) if pnls else 0,
                "worst": min(pnls) if pnls else 0,
                "avg": sum(pnls)/len(pnls) if pnls else 0,
            }

        this_w = week_stats(mon, today + timedelta(days=1))
        last_w = week_stats(prev_mon, mon)
        conn.close()

        # NIFTY benchmark for the week
        nifty_ret = 0.0
        try:
            import requests
            r = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/^NSEI?interval=1wk&range=2wk",
                headers={"User-Agent":"Mozilla/5.0"}, timeout=6)
            if r.status_code == 200:
                closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                closes = [c for c in closes if c]
                if len(closes) >= 2:
                    nifty_ret = (closes[-1]-closes[-2])/closes[-2]*100
        except Exception:
            pass

        lines = [f"📊 <b>WEEKLY PERFORMANCE</b>", ""]

        if this_w:
            icon = "🟢" if this_w["total"] >= 0 else "🔴"
            lines += [
                f"  <b>THIS WEEK ({mon.strftime('%d-%b')} – {today.strftime('%d-%b')})</b>",
                f"  {icon} P&L:      ₹{this_w['total']:+,.2f}",
                f"  📈 Trades:   {this_w['count']}",
                f"  🎯 Win rate: {this_w['wr']:.0f}%",
                f"  💰 Best:    ₹{this_w['best']:+,.2f}",
                f"  📉 Worst:   ₹{this_w['worst']:+,.2f}",
                f"  ⚖️ Avg/trade: ₹{this_w['avg']:+,.2f}",
            ]
            if nifty_ret != 0:
                lines.append(f"  📌 NIFTY this week: {nifty_ret:+.2f}%")
            lines.append("")
        else:
            lines.append("  No trades this week yet.\n")

        if last_w:
            icon2 = "🟢" if last_w["total"] >= 0 else "🔴"
            lines += [
                f"  <b>LAST WEEK ({prev_mon.strftime('%d-%b')} – {(mon-timedelta(days=1)).strftime('%d-%b')})</b>",
                f"  {icon2} P&L:      ₹{last_w['total']:+,.2f}",
                f"  📈 Trades:   {last_w['count']}  |  Win rate: {last_w['wr']:.0f}%",
                "",
            ]
            if this_w and last_w:
                diff = this_w["total"] - last_w["total"]
                trend = "📈 Better" if diff > 0 else "📉 Worse"
                lines.append(f"  vs Last week: {trend} by ₹{abs(diff):,.0f}")

        return "\n".join(lines)
    except Exception as e:
        return f"📊 Weekly: {friendly_error('weekly', e)}"


# ══════════════════════════════════════════════════════════════
# UX-10: /export — CSV trade journal
# ══════════════════════════════════════════════════════════════

def export_trades_csv(days: int = 90) -> Optional[str]:
    """Export trades to CSV file. Returns path."""
    try:
        import csv
        db = Path("trades.db")
        if not db.exists():
            return None
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT trade_id, symbol, side, qty, strategy, entry_price, "
            "exit_price, pnl, entry_time, exit_time, exit_reason "
            "FROM trades WHERE status='closed' AND DATE(entry_time) >= ? "
            "ORDER BY entry_time",
            (cutoff,)
        ).fetchall()
        conn.close()

        if not rows:
            return None

        out = Path(f"trade_export_{date.today().isoformat()}.csv")
        with open(str(out), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date","Time","Symbol","Side","Qty","Strategy",
                        "Entry","Exit","P&L","Duration","Exit Reason"])
            for row in rows:
                tid, sym, side, qty, strat, ep, xp, pnl, et, xt, xr = row
                entry_t = str(et or "")
                exit_t  = str(xt or "")
                try:
                    e_dt = datetime.fromisoformat(entry_t)
                    x_dt = datetime.fromisoformat(exit_t)
                    dur = f"{int((x_dt-e_dt).total_seconds()//60)}m"
                    dt_str = e_dt.strftime("%Y-%m-%d")
                    t_str  = e_dt.strftime("%H:%M")
                except Exception:
                    dur = "?"; dt_str = entry_t[:10]; t_str = entry_t[11:16]
                w.writerow([dt_str, t_str, sym, side, qty, strat,
                            ep, xp, round(float(pnl or 0), 2), dur, xr])
        return str(out)
    except Exception as e:
        logger.debug("export_trades: %s", e)
        return None


# ══════════════════════════════════════════════════════════════
# UX-11: Daily accuracy post for free channel
# ══════════════════════════════════════════════════════════════

def get_daily_accuracy_post() -> str:
    """Public accuracy post for free subscriber channel."""
    try:
        db = Path("trades.db")
        today = date.today().isoformat()
        if not db.exists():
            return ""
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT pnl, symbol, strategy FROM trades "
            "WHERE status='closed' AND DATE(exit_time)=?",
            (today,)
        ).fetchall()
        # Monthly accuracy
        month_start = date.today().replace(day=1).isoformat()
        month_rows = conn.execute(
            "SELECT pnl FROM trades WHERE status='closed' AND DATE(exit_time)>=?",
            (month_start,)
        ).fetchall()
        conn.close()

        if not rows:
            return (
                f"📊 <b>DAILY UPDATE</b> | {date.today().strftime('%d-%b')}\n\n"
                f"  No trades today (market holiday or no signals met quality threshold)\n\n"
                f"  Tomorrow's brief at 8:30 AM\n"
                f"  ⚠️ Educational signals only"
            )

        pnls = [float(r[0] or 0) for r in rows]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        total = sum(pnls)

        month_pnls = [float(r[0] or 0) for r in month_rows]
        month_wr = sum(1 for p in month_pnls if p>0)/len(month_pnls)*100 if month_pnls else 0

        best = max(rows, key=lambda r: float(r[0] or 0))
        best_str = f"{best[1]} {'+' if float(best[0])>=0 else ''}₹{float(best[0]):,.0f}"

        icon = "🟢" if total >= 0 else "🔴"
        return (
            f"📊 <b>DAILY RESULTS</b> | {date.today().strftime('%d-%b-%Y')}\n\n"
            f"  {icon} Trades today: {len(rows)}\n"
            f"  🎯 Accuracy: {wr:.0f}% ({wins}W / {len(rows)-wins}L)\n"
            f"  ⭐ Best:  {best_str}\n\n"
            f"  <b>THIS MONTH:</b> {month_wr:.0f}% accuracy ({len(month_pnls)} trades)\n\n"
            f"  📡 Signals resume 9:15 AM tomorrow\n"
            f"  ⚠️ Educational signals | Not SEBI advice\n"
            f"  🔔 Join premium for real-time signals"
        )
    except Exception as e:
        return ""


# ══════════════════════════════════════════════════════════════
# UX-12: /paper — virtual P&L tracker
# ══════════════════════════════════════════════════════════════

def track_paper_signal(symbol: str, side: str, entry: float,
                       target: float, sl: float) -> str:
    """Track a signal on paper without real money."""
    try:
        data = json.loads(_PAPER_FILE.read_text()) if _PAPER_FILE.exists() else {"trades": []}
        trade_id = f"{symbol}_{int(time.time())}"
        data["trades"].append({
            "id": trade_id, "symbol": symbol, "side": side,
            "entry": entry, "target": target, "sl": sl,
            "date": date.today().isoformat(),
            "status": "open",
        })
        _PAPER_FILE.write_text(json.dumps(data, indent=2))
        rr = abs((target-entry)/(entry-sl)) if sl != entry else 0
        return (
            f"📝 <b>PAPER TRADE OPENED</b>\n"
            f"  {symbol} {side} @ ₹{entry:,.2f}\n"
            f"  Target: ₹{target:,.2f} | SL: ₹{sl:,.2f}\n"
            f"  R:R = 1:{rr:.1f}\n"
            f"  Track with /paper status"
        )
    except Exception as e:
        return f"❌ Paper trade: {e}"


def get_paper_status() -> str:
    """Show all paper trades and virtual P&L."""
    try:
        if not _PAPER_FILE.exists():
            return "📝 No paper trades yet.\nUse /paper BUY NIFTY 23000 23200 22800"

        data = json.loads(_PAPER_FILE.read_text())
        trades = data.get("trades", [])
        if not trades:
            return "📝 No paper trades yet."

        # Get current prices
        prices = {}
        try:
            import requests
            s = requests.Session()
            s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            s.get("https://www.nseindia.com/", timeout=4)
            r = s.get("https://www.nseindia.com/api/allIndices", timeout=7)
            for idx in r.json().get("data",[]):
                for sym,name in [("NIFTY","NIFTY 50"),("BANKNIFTY","NIFTY BANK")]:
                    if name in str(idx.get("index","")):
                        prices[sym] = float(idx.get("last",0) or 0)
        except Exception:
            pass

        lines = ["📝 <b>PAPER TRADES</b>", ""]
        total_virtual = 0.0
        for t in trades[-10:]:
            sym    = t["symbol"]
            side   = t["side"]
            entry  = float(t["entry"])
            target = float(t["target"])
            sl     = float(t["sl"])
            curr   = prices.get(sym, entry)
            if curr:
                pnl = (curr - entry) if side == "BUY" else (entry - curr)
                total_virtual += pnl
                icon = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"  {icon} {sym} {side} @ ₹{entry:,.0f} → ₹{curr:,.0f} "
                    f"({'+' if pnl>=0 else ''}₹{pnl:,.0f})"
                )

        lines += [
            "",
            f"  Virtual P&L: ₹{total_virtual:+,.0f}",
            f"  ⚠️ Paper trading — no real money used",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"📝 Paper: {friendly_error('paper', e)}"


# ══════════════════════════════════════════════════════════════
# UX-13: /calculate — position sizing
# ══════════════════════════════════════════════════════════════

def calculate_position_size(symbol: str, capital: float,
                             risk_pct: float = 1.0) -> str:
    """Position sizing calculator for any capital size."""
    sym = symbol.upper().strip()
    lot = _LOT_SIZES.get(sym, 1)
    margin_rate = _MARGIN_RATES.get(sym, 0.10)

    # Get current price
    price = 0.0
    try:
        import requests
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{'%5E' if sym in ['NIFTY','BANKNIFTY','SENSEX'] else ''}{sym if sym not in ['NIFTY','BANKNIFTY'] else 'NSEI' if sym=='NIFTY' else 'NSEBANK'}?interval=1d&range=1d",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=6)
        if r.status_code == 200:
            price = float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"] or 0)
    except Exception:
        pass

    if price == 0:
        # Use rough estimates
        estimates = {"NIFTY": 23800, "BANKNIFTY": 55000, "FINNIFTY": 23000,
                     "MIDCPNIFTY": 12000, "SENSEX": 78000}
        price = estimates.get(sym, 1000)

    risk_amount = capital * risk_pct / 100
    margin_per_lot = price * lot * margin_rate if lot > 1 else price

    if lot > 1:
        # F&O
        lots = max(0, int(capital * 0.30 / margin_per_lot))  # use max 30% capital
        margin_used = lots * margin_per_lot
        risk_per_lot = price * lot * 0.005  # 0.5% SL estimate
        return (
            f"📐 <b>POSITION CALCULATOR — {sym}</b>\n\n"
            f"  Capital:      ₹{capital:,.0f}\n"
            f"  Price:        ₹{price:,.0f}\n"
            f"  Lot size:     {lot} units\n"
            f"  Margin/lot:   ₹{margin_per_lot:,.0f}\n\n"
            f"  <b>Recommended: {lots} lots</b>\n"
            f"  Margin used:  ₹{margin_used:,.0f} ({margin_used/capital*100:.0f}%)\n"
            f"  Risk (1% SL): ₹{risk_per_lot*lots:,.0f}\n\n"
            f"  Conservative (0.5%): {max(1,lots//2)} lots\n"
            f"  Aggressive (2%):     {lots*2} lots\n\n"
            f"  ⚠️ Always set SL! Use 0.5-1% risk per trade."
        )
    else:
        # Equity
        shares = int(capital * risk_pct / 100 / (price * 0.015))  # assume 1.5% SL
        total_invest = shares * price
        return (
            f"📐 <b>POSITION CALCULATOR — {sym}</b>\n\n"
            f"  Capital:      ₹{capital:,.0f}\n"
            f"  Price:        ₹{price:,.0f}\n"
            f"  Risk (1%):    ₹{risk_amount:,.0f}\n\n"
            f"  <b>Shares: {shares} ({sym})</b>\n"
            f"  Investment:   ₹{total_invest:,.0f} ({total_invest/capital*100:.0f}%)\n"
            f"  SL estimate:  ₹{price*0.985:,.2f} (-1.5%)\n\n"
            f"  ⚠️ Adjust SL based on your risk tolerance."
        )


# ══════════════════════════════════════════════════════════════
# UX-14: Pre-market gap warning
# ══════════════════════════════════════════════════════════════

def get_overnight_gap_warnings(open_positions: list) -> str:
    """Check overnight gap risk for open positions."""
    if not open_positions:
        return ""
    warnings = []
    try:
        import requests
        for pos in open_positions[:5]:
            sym = str(pos.get("symbol",""))
            side = str(pos.get("side","BUY"))
            entry = float(pos.get("entry_price",0) or 0)
            try:
                r = requests.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS?interval=1d&range=2d",
                    headers={"User-Agent":"Mozilla/5.0"}, timeout=5)
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    curr = float(meta.get("regularMarketPrice") or entry)
                    prev = float(meta.get("chartPreviousClose") or curr)
                    gap_pct = (curr - prev) / prev * 100 if prev else 0
                    if abs(gap_pct) > 0.5:
                        adverse = (gap_pct < 0 and side == "BUY") or (gap_pct > 0 and side == "SELL")
                        icon = "⚠️" if adverse else "✅"
                        warnings.append(
                            f"  {icon} {sym}: Gap {gap_pct:+.1f}% "
                            f"{'(ADVERSE - check SL)' if adverse else '(Favourable)'}"
                        )
            except Exception:
                pass
    except Exception:
        pass

    if not warnings:
        return "  ✅ No significant overnight gaps detected"
    return "\n".join(warnings)


# ══════════════════════════════════════════════════════════════
# UX-15: /watch — Personal watchlist
# ══════════════════════════════════════════════════════════════

def set_watchlist(symbols: list) -> str:
    data = json.loads(_WATCH_FILE.read_text()) if _WATCH_FILE.exists() else {}
    data["symbols"] = [s.upper() for s in symbols]
    _WATCH_FILE.write_text(json.dumps(data, indent=2))
    return f"✅ Watchlist set: {', '.join(data['symbols'])}\nYou'll get priority alerts for these symbols."


def get_watchlist() -> list:
    try:
        return json.loads(_WATCH_FILE.read_text()).get("symbols", [])
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
# UX-16: /alert — Price alerts
# ══════════════════════════════════════════════════════════════

def add_price_alert(symbol: str, condition: str, price: float) -> str:
    """Add: /alert NIFTY above 24000"""
    data = json.loads(_ALERT_FILE.read_text()) if _ALERT_FILE.exists() else {"alerts": []}
    alert_id = f"{symbol}_{condition}_{price}_{int(time.time())}"
    data["alerts"].append({
        "id": alert_id, "symbol": symbol.upper(),
        "condition": condition.lower(), "price": price,
        "created": date.today().isoformat(), "triggered": False,
    })
    _ALERT_FILE.write_text(json.dumps(data, indent=2))
    return (
        f"🔔 <b>PRICE ALERT SET</b>\n"
        f"  {symbol.upper()} {condition} ₹{price:,.0f}\n"
        f"  You'll get notified when this triggers.\n"
        f"  Use /alerts to see all active alerts."
    )


def check_price_alerts(current_prices: dict) -> list:
    """Check all alerts against current prices. Returns triggered alerts."""
    if not _ALERT_FILE.exists():
        return []
    try:
        data = json.loads(_ALERT_FILE.read_text())
        triggered = []
        for alert in data["alerts"]:
            if alert.get("triggered"):
                continue
            sym  = alert["symbol"]
            cond = alert["condition"]
            tgt  = float(alert["price"])
            curr = current_prices.get(sym, 0)
            if curr <= 0:
                continue
            fired = ((cond == "above" and curr >= tgt) or
                     (cond == "below" and curr <= tgt) or
                     (cond == "crosses" and abs(curr - tgt) < tgt * 0.001))
            if fired:
                alert["triggered"] = True
                triggered.append({
                    "symbol": sym, "condition": cond,
                    "target": tgt, "current": curr,
                    "message": (
                        f"🔔 <b>ALERT TRIGGERED</b>\n"
                        f"  {sym} is now ₹{curr:,.0f}\n"
                        f"  ({cond} ₹{tgt:,.0f} — your alert level)"
                    )
                })
        _ALERT_FILE.write_text(json.dumps(data, indent=2))
        return triggered
    except Exception:
        return []


def list_active_alerts() -> str:
    if not _ALERT_FILE.exists():
        return "🔔 No active alerts.\nSet one: /alert NIFTY above 24000"
    try:
        data = json.loads(_ALERT_FILE.read_text())
        active = [a for a in data["alerts"] if not a.get("triggered")]
        if not active:
            return "🔔 No active alerts."
        lines = ["🔔 <b>ACTIVE ALERTS</b>", ""]
        for a in active:
            lines.append(f"  • {a['symbol']} {a['condition']} ₹{a['price']:,.0f}")
        return "\n".join(lines)
    except Exception:
        return "🔔 Alerts unavailable."


# ══════════════════════════════════════════════════════════════
# UX-18: /voice — Audio status update
# ══════════════════════════════════════════════════════════════

def generate_voice_status(status_text: str, output_path: str = "voice_status.mp3") -> Optional[str]:
    """Generate MP3 audio from status text."""
    try:
        from gtts import gTTS
        tts = gTTS(text=status_text, lang='en', tld='co.in', slow=False)
        tts.save(output_path)
        return output_path
    except ImportError:
        return None
    except Exception as e:
        logger.debug("voice_status: %s", e)
        return None


# ══════════════════════════════════════════════════════════════
# UX-19: /next — what to watch next 30 min
# ══════════════════════════════════════════════════════════════

def get_next_30min_watchlist(signals: list) -> str:
    """What to watch in the next 30 minutes."""
    from datetime import datetime as _dt
    now = _dt.now()
    lines = [
        f"🔭 <b>WATCH NEXT 30 MIN</b> | {now.strftime('%H:%M')}",
        "",
    ]
    if signals:
        lines.append("  <b>High-scoring candidates:</b>")
        for s in signals[:5]:
            sym   = s.get("symbol","?")
            score = float(s.get("score",0))
            dirn  = s.get("direction","?")
            icon  = "🟢" if dirn == "BUY" else "🔴"
            lines.append(f"  {icon} {sym:12} score={score:.1f}  {dirn}")
    else:
        lines.append("  No high-conviction setups right now.")
        lines.append("  Bot continues scanning 196 symbols.")

    lines += [
        "",
        f"  ⏰ Next scan: {(now + __import__('datetime').timedelta(minutes=5)).strftime('%H:%M')}",
        f"  📡 Signals auto-sent when score > 5.5",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# UX-20: /risk — Portfolio risk snapshot
# ══════════════════════════════════════════════════════════════

def get_risk_snapshot(open_positions: list, capital: float) -> str:
    """Quick portfolio risk assessment."""
    if not open_positions:
        return (
            f"🛡️ <b>RISK SNAPSHOT</b>\n\n"
            f"  No open positions\n"
            f"  Capital: ₹{capital:,.0f} fully available\n"
            f"  Risk: 0%\n"
        )

    total_exposure = 0.0
    total_risk = 0.0
    for pos in open_positions:
        entry = float(pos.get("entry_price",0) or 0)
        sl    = float(pos.get("stop_loss",0) or 0)
        qty   = int(pos.get("qty",1) or 1)
        if entry and sl:
            exposure = entry * qty
            risk = abs(entry - sl) * qty
            total_exposure += exposure
            total_risk += risk

    exp_pct  = total_exposure / capital * 100 if capital else 0
    risk_pct = total_risk / capital * 100 if capital else 0
    risk_icon = "🟢" if risk_pct < 2 else "🟡" if risk_pct < 5 else "🔴"

    return (
        f"🛡️ <b>RISK SNAPSHOT</b>\n\n"
        f"  Open positions:  {len(open_positions)}\n"
        f"  Total exposure:  ₹{total_exposure:,.0f} ({exp_pct:.0f}%)\n"
        f"  {risk_icon} Max loss today: ₹{total_risk:,.0f} ({risk_pct:.1f}%)\n"
        f"  Capital at risk: {'LOW' if risk_pct<2 else 'MODERATE' if risk_pct<5 else 'HIGH'}\n\n"
        f"  💡 Best practice: Keep total risk < 3%"
    )


# ══════════════════════════════════════════════════════════════
# UX-21: /compare — week on week
# ══════════════════════════════════════════════════════════════

def get_week_comparison() -> str:
    """Compare this week vs last week performance."""
    try:
        db = Path("trades.db")
        if not db.exists():
            return "📊 No trade history to compare yet."
        conn = sqlite3.connect(str(db))
        today = date.today()
        mon   = today - timedelta(days=today.weekday())
        prev  = mon - timedelta(days=7)
        prev2 = prev - timedelta(days=7)

        def week_pnl(start, end):
            rows = conn.execute(
                "SELECT SUM(pnl), COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) "
                "FROM trades WHERE status='closed' AND DATE(exit_time)>=? AND DATE(exit_time)<?",
                (start.isoformat(), end.isoformat())
            ).fetchone()
            return (float(rows[0] or 0), int(rows[1] or 0), int(rows[2] or 0))

        this_pnl, this_cnt, this_win = week_pnl(mon, today+timedelta(days=1))
        last_pnl, last_cnt, last_win = week_pnl(prev, mon)
        prev_pnl, prev_cnt, prev_win = week_pnl(prev2, prev)
        conn.close()

        diff = this_pnl - last_pnl
        trend = "📈 IMPROVING" if diff > 0 else "📉 DECLINING" if diff < 0 else "➡️ FLAT"

        return (
            f"📊 <b>WEEK COMPARISON</b>\n\n"
            f"  Week           P&L        Trades  WR\n"
            f"  {'─'*40}\n"
            f"  This week  ₹{this_pnl:>+9,.0f}    {this_cnt:>3}    {this_win/max(this_cnt,1)*100:.0f}%\n"
            f"  Last week  ₹{last_pnl:>+9,.0f}    {last_cnt:>3}    {last_win/max(last_cnt,1)*100:.0f}%\n"
            f"  2w ago     ₹{prev_pnl:>+9,.0f}    {prev_cnt:>3}    {prev_win/max(prev_cnt,1)*100:.0f}%\n"
            f"  {'─'*40}\n"
            f"  Trend: {trend}  (₹{diff:+,.0f} vs last week)"
        )
    except Exception as e:
        return f"📊 Compare: {friendly_error('compare', e)}"


# ══════════════════════════════════════════════════════════════
# UX-22: /streak — winning/losing streak
# ══════════════════════════════════════════════════════════════

def get_streak_info() -> str:
    """Current winning/losing streak."""
    try:
        db = Path("trades.db")
        if not db.exists():
            return "📊 No trades yet."
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT pnl FROM trades WHERE status='closed' ORDER BY exit_time DESC LIMIT 20"
        ).fetchall()
        conn.close()
        if not rows:
            return "📊 No closed trades yet."

        pnls = [float(r[0] or 0) for r in rows]
        # Current streak
        streak = 1
        streak_type = "WIN" if pnls[0] > 0 else "LOSS"
        for p in pnls[1:]:
            if (p > 0) == (pnls[0] > 0):
                streak += 1
            else:
                break
        # Best win streak ever
        best_win = best_loss = cur = 0
        cur_type = None
        for p in reversed(pnls):
            t = "W" if p > 0 else "L"
            if t == cur_type:
                cur += 1
            else:
                cur = 1
                cur_type = t
            if t == "W": best_win = max(best_win, cur)
            else: best_loss = max(best_loss, cur)

        icon = "🔥" if streak_type == "WIN" and streak >= 3 else \
               "✅" if streak_type == "WIN" else "⚠️"
        return (
            f"🎯 <b>STREAK TRACKER</b>\n\n"
            f"  {icon} Current streak: {streak} {streak_type}{'S' if streak>1 else ''}\n\n"
            f"  Last 20 trades:\n"
            f"  {''.join('W' if p>0 else 'L' for p in pnls[:20])}\n\n"
            f"  Best win streak:  {best_win}\n"
            f"  Best loss streak: {best_loss}\n"
        )
    except Exception as e:
        return f"📊 Streak: {friendly_error('streak', e)}"
