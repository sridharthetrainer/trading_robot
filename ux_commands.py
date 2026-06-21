"""
ux_commands.py — User Experience Commands (consolidated)

All shared functions now live in ux_engine.py.
This module re-exports them and adds any ux_commands-specific extras.
Importing either module gives you the same functions.
"""
from __future__ import annotations

# Auto-fix: get DataFetcher with Angel singleton
def _get_angel_data_fetcher():
    try:
        from angel import AngelOne
        import os as _os_adf
        _ang = AngelOne(api_key=_os_adf.getenv("API_KEY",""),
            client_id=_os_adf.getenv("CLIENT_ID",""),
            password=_os_adf.getenv("PASSWORD",""),
            totp_secret=_os_adf.getenv("TOTP_SECRET",""))
    except Exception: _ang = None
    from data_fetcher import DataFetcher
    return DataFetcher(angel=_ang, paper_trade=False)


# Re-export everything from ux_engine (canonical implementations)
try:
    from ux_engine import (
        get_watchlist, set_watchlist,
        get_price_alerts, add_price_alert, check_price_alerts,
        calculate_position_size, get_todays_signals,
        get_weekly_performance, export_trades_csv,
        track_signal_on_paper, get_paper_results,
        generate_voice_status,
    )
except ImportError:
    pass  # ux_engine not available — fallbacks below


import json, logging, os, time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Paths for the local fallback implementations below. These were used but never
# defined (NameError when the Telegram commands ran). Match ux_engine.py's
# canonical filenames (_PAPER_FILE / _ALERT_FILE) so both modules share state.
_PAPER_TRACK = Path("paper_trades.json")
_ALERTS_FILE = Path("price_alerts.json")

def track_signal_on_paper(signal: dict) -> str:
    """UX-12: Let user track a signal virtually."""
    try:
        data = json.loads(_PAPER_TRACK.read_text()) if _PAPER_TRACK.exists() else []
        entry = {
            "symbol":    signal.get("symbol","?"),
            "side":      signal.get("direction","BUY"),
            "entry":     float(signal.get("price",0) or 0),
            "target":    float(signal.get("target",0) or 0),
            "sl":        float(signal.get("stop_loss",0) or 0),
            "score":     float(signal.get("score",0) or 0),
            "strategy":  signal.get("strategy","?"),
            "time":      datetime.now().isoformat(),
            "status":    "OPEN",
        }
        data.append(entry)
        _PAPER_TRACK.write_text(json.dumps(data[-50:], indent=2))  # keep last 50
        sym = entry["symbol"]
        return (f"📝 <b>PAPER TRACKING STARTED</b>\n"
                f"  {entry['side']} {sym} @ ₹{entry['entry']:,.2f}\n"
                f"  Target: ₹{entry['target']:,.2f}  SL: ₹{entry['sl']:,.2f}\n"
                f"  Type /paper to check results")
    except Exception as e:
        return f"❌ Paper track: {e}"




def get_price_alerts() -> list:
    try:
        return json.loads(_ALERTS_FILE.read_text()) if _ALERTS_FILE.exists() else []
    except Exception: return []



def get_paper_results() -> str:
    """Show paper trading results."""
    try:
        if not _PAPER_TRACK.exists():
            return "📝 No paper trades tracked yet\nUse /paper after a signal arrives"
        data = json.loads(_PAPER_TRACK.read_text())
        if not data:
            return "📝 No paper trades yet"

        lines = ["📝 <b>PAPER TRADE RESULTS</b>", ""]
        wins = losses = open_count = 0
        total_pnl = 0.0

        for t in data[-10:]:
            sym   = t.get("symbol","?")
            side  = t.get("side","?")
            entry = float(t.get("entry",0))
            target= float(t.get("target",0))
            sl    = float(t.get("sl",0))
            status= t.get("status","OPEN")
            score = t.get("score",0)

            if status == "OPEN":
                open_count += 1
                lines.append(f"  ⏳ {sym:10} {side:5} @ ₹{entry:,.0f} [OPEN] score={score:.1f}")
            elif status == "TARGET":
                pnl = abs(target-entry) * (1 if side=="BUY" else -1)
                total_pnl += pnl
                wins += 1
                lines.append(f"  ✅ {sym:10} TARGET HIT | +₹{abs(pnl):,.0f}")
            elif status == "SL":
                pnl = -abs(entry-sl)
                total_pnl += pnl
                losses += 1
                lines.append(f"  ❌ {sym:10} SL HIT     | ₹{pnl:,.0f}")

        closed = wins + losses
        wr = wins/closed*100 if closed else 0
        lines += [
            "",
            f"  Wins: {wins}  Losses: {losses}  Open: {open_count}",
            f"  Win rate: {wr:.0f}%" if closed else "",
            f"  Virtual P&L: ₹{total_pnl:+,.0f}" if closed else "",
            "",
            "  ⚠️ Paper trades — no real money involved",
        ]
        return "\n".join(l for l in lines if l is not None)
    except Exception as e:
        return f"❌ Paper results: {e}"


# ── VOICE STATUS ─────────────────────────────────────────────


def get_todays_signals() -> str:
    """Get all signals generated today with status (hit/miss/open)."""
    try:
        from pathlib import Path
        import json
        from datetime import date

        # Read from signal_broadcaster's saved signals
        sig_file = Path("signals_today.json")
        if sig_file.exists():
            signals = json.loads(sig_file.read_text())
        else:
            signals = []

        # Also try signal_broadcaster directly
        if not signals:
            try:
                from signal_broadcaster import SignalBroadcaster
                sb = SignalBroadcaster.__new__(SignalBroadcaster)
                sb._load = lambda: None
                sf = Path("signal_history.json")
                if sf.exists():
                    all_sigs = json.loads(sf.read_text())
                    today_str = date.today().isoformat()
                    signals = [s for s in all_sigs if s.get("date","") == today_str]
            except Exception:
                pass

        if not signals:
            return ("📡 <b>TODAY'S SIGNALS</b>\n\n"
                    "  No signals generated yet today\n"
                    "  Market hours: 9:15 AM - 3:30 PM\n"
                    "  Scanner checks every 5 minutes\n\n"
                    "  📱 /status · /diagnose")

        lines = [f"📡 <b>TODAY'S SIGNALS</b> ({len(signals)} total)", ""]
        for sig in signals[-10:]:
            icon = "🟢" if sig.get("direction") == "BUY" else "🔴"
            symbol = sig.get("symbol", "?")
            score  = float(sig.get("score", 0))
            price  = float(sig.get("price", 0) or 0)
            target = float(sig.get("target", 0) or 0)
            sl     = float(sig.get("stop_loss", 0) or 0)
            ts     = sig.get("ts", "")
            
            # Check if target hit or SL hit
            status = "⏳ OPEN"
            try:
                from data_fetcher import DataFetcher
                df = _get_angel_data_fetcher()
                data = df.get_market_data(symbol, interval="5m", days=1)
                if data is not None and not data.empty:
                    for c in data.columns:
                        if c.lower() == 'high':
                            high = float(data[c].max())
                            low  = float(data[c].min())
                            if target and high >= target:
                                status = "✅ TARGET HIT"
                            elif sl and low <= sl:
                                status = "❌ SL HIT"
                            break
            except Exception:
                pass

            lines.append(f"  {icon} {symbol} ₹{price:,.0f} → ₹{target:,.0f}  {status}")
            lines.append(f"    Score: {score:.1f} | SL: ₹{sl:,.0f}")

        lines += ["", "  📱 /signals for last 5 signals"]
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Today signals: {e}"


def calculate_position_size(symbol: str, capital: float, risk_pct: float = 1.0) -> str:
    """Calculate position size for a symbol given capital and risk %."""
    try:
        # Get current price
        price = 0.0
        try:
            from data_fetcher import DataFetcher
            df = _get_angel_data_fetcher()
            data = df.get_market_data(symbol, interval="5m", days=1)
            if data is not None and not data.empty:
                for c in data.columns:
                    if c.lower() == 'close':
                        price = float(data[c].iloc[-1])
                        break
        except Exception:
            pass

        if price <= 0:
            # Fallback: try yf_compat
            try:
                import yf_compat as yf
                d = yf.download(f"{symbol}.NS" if "." not in symbol else symbol,
                                period="1d")
                if d is not None and not d.empty:
                    for c in d.columns:
                        if 'close' in str(c).lower():
                            price = float(d[c].iloc[-1])
                            break
            except Exception:
                pass

        if price <= 0:
            return f"⚠️ Cannot get price for {symbol} — market may be closed"

        # Get lot size
        lot_size = 1
        lot_sizes = {
            "NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 65,
            "MIDCPNIFTY": 120, "SENSEX": 20, "BANKEX": 15,
        }
        lot_size = lot_sizes.get(symbol.upper(), 1)

        # Calculate position
        risk_amount  = capital * (risk_pct / 100)
        sl_distance  = price * 0.015  # 1.5% default SL
        qty_by_risk  = int(risk_amount / sl_distance) if sl_distance > 0 else 0
        qty_by_capital = int(capital / price)
        recommended  = min(qty_by_risk, qty_by_capital)

        # Lot adjustment
        if lot_size > 1:
            lots = max(1, recommended // lot_size)
            recommended = lots * lot_size

        margin_needed = recommended * price * 0.20  # ~20% margin for F&O

        return (
            f"📐 <b>POSITION SIZE — {symbol}</b>\n\n"
            f"  Current Price:  ₹{price:,.2f}\n"
            f"  Capital:        ₹{capital:,.0f}\n"
            f"  Risk:           {risk_pct}% = ₹{risk_amount:,.0f}\n\n"
            f"  <b>RECOMMENDED</b>\n"
            f"  Quantity:       {recommended}\n"
            + (f"  Lots:           {recommended // lot_size} × {lot_size}\n"
               if lot_size > 1 else "")
            + f"  Margin needed:  ₹{margin_needed:,.0f}\n"
            f"  SL distance:    ₹{sl_distance:,.2f} (1.5%)\n\n"
            f"  <b>KELLY SIZING</b>\n"
            f"  Conservative:   {max(1, recommended // 2)} qty\n"
            f"  Aggressive:     {recommended} qty\n\n"
            f"  ⚠️ Always use stop loss. This is educational only."
        )
    except Exception as e:
        return f"❌ Calculate: {e}"


# ═══════════════════════════════════════════════════════════════
# FALLBACK IMPLEMENTATIONS (when ux_engine import fails)
# ═══════════════════════════════════════════════════════════════

if 'get_watchlist' not in dir():
    def get_watchlist() -> list:
        """Get user's watchlist symbols."""
        try:
            return json.loads(Path("watchlist.json").read_text()) if Path("watchlist.json").exists() else []
        except Exception:
            return []

if 'set_watchlist' not in dir():
    def set_watchlist(symbols: list) -> str:
        Path("watchlist.json").write_text(json.dumps(symbols))
        return f"Watchlist updated: {len(symbols)} symbols"

if 'add_price_alert' not in dir():
    def add_price_alert(symbol: str, condition: str, price: float) -> str:
        alerts = json.loads(Path("price_alerts.json").read_text()) if Path("price_alerts.json").exists() else []
        alerts.append({"symbol": symbol, "condition": condition, "price": price,
                        "created": datetime.now().isoformat(), "triggered": False})
        Path("price_alerts.json").write_text(json.dumps(alerts, indent=2))
        return f"Alert set: {symbol} {condition} ₹{price:,.2f}"

if 'check_price_alerts' not in dir():
    def check_price_alerts(current_prices: dict = None) -> list:
        try:
            alerts = json.loads(Path("price_alerts.json").read_text()) if Path("price_alerts.json").exists() else []
            return [a for a in alerts if not a.get("triggered")]
        except Exception:
            return []

if 'export_trades_csv' not in dir():
    def export_trades_csv(days: int = 30) -> Optional[str]:
        try:
            import sqlite3, csv
            conn = sqlite3.connect("trades.db", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_time >= date('now', ?) ORDER BY exit_time",
                (f"-{days} days",)
            ).fetchall()
            conn.close()
            if not rows: return None
            out = f"trades_export_{datetime.now().strftime('%Y%m%d')}.csv"
            with open(out, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(rows[0].keys())
                w.writerows([tuple(r) for r in rows])
            return out
        except Exception:
            return None

if 'get_weekly_performance' not in dir():
    def get_weekly_performance() -> str:
        try:
            import sqlite3
            conn = sqlite3.connect("trades.db", check_same_thread=False)
            row = conn.execute(
                "SELECT COUNT(*) as cnt, "
                "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(realized_pnl) as total_pnl "
                "FROM trades WHERE status='CLOSED' AND exit_time >= date('now', '-7 days')"
            ).fetchone()
            conn.close()
            cnt, wins, pnl = row[0] or 0, row[1] or 0, row[2] or 0
            wr = wins/cnt*100 if cnt else 0
            return (f"📊 <b>WEEKLY PERFORMANCE</b>\n\n"
                    f"  Trades:   {cnt}\n"
                    f"  Wins:     {wins} ({wr:.0f}%)\n"
                    f"  Net P&L:  ₹{pnl:+,.2f}\n")
        except Exception as e:
            return f"⚠️ Weekly: {e}"

if 'generate_voice_status' not in dir():
    def generate_voice_status() -> Optional[str]:
        return None  # voice generation requires gtts — optional
