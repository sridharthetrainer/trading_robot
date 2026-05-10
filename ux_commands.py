"""
ux_commands.py — User Experience Commands (consolidated)

All shared functions now live in ux_engine.py.
This module re-exports them and adds any ux_commands-specific extras.
Importing either module gives you the same functions.
"""
from __future__ import annotations

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
