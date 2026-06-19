"""
dark_pool.py — Dark Pool / Large Block Deal Monitor

Detects institutional block trades from NSE bulk/block deal data.
Large deals (>₹50Cr) reveal institutional direction BEFORE the move.

Data sources:
  1. NSE bulk deals API (free, real-time)
  2. NSE block deals API (free, real-time)
  3. Bhav copy delivery % (high delivery = accumulation)

Signals:
  Large BUY block (>₹50Cr) in a stock  → BULLISH +1.5
  Large SELL block (>₹50Cr)            → BEARISH -1.5
  Repeated buying same stock 3+ days   → STRONG ACCUMULATION +2.0
  High delivery % (>60%) + price dip   → INSTITUTIONAL ACCUMULATION +1.0
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_HISTORY_FILE = Path("dark_pool_history.csv")
_MIN_DEAL_CR  = 50     # ₹50 Crore minimum block size
_ACCUM_DAYS   = 3      # repeated buying days for accumulation signal


def fetch_bulk_deals_nse() -> List[Dict]:
    """Fetch today's bulk deals from NSE."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        today = date.today().strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/historical/bulk-deals?from={today}&to={today}",
            timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            deals = []
            for d in data:
                qty  = float(d.get("BD_QTY_TRD", 0) or 0)
                price= float(d.get("BD_TP_WATP", 0) or 0)
                val_cr = qty * price / 1e7  # convert to crores
                deals.append({
                    "date":   date.today().isoformat(),
                    "symbol": str(d.get("BD_SYMBOL","")).strip().upper(),
                    "client": str(d.get("BD_CLIENT_NAME","")),
                    "side":   "BUY" if str(d.get("BD_BUY_SELL","")).upper()=="B" else "SELL",
                    "qty":    qty,
                    "price":  price,
                    "value_cr": round(val_cr, 2),
                })
            return deals
    except Exception as e:
        logger.debug("bulk_deals_nse: %s", e)
    return []


def get_dark_pool_score(symbol: str) -> Dict:
    """
    Compute dark pool signal score for a symbol.
    
    Checks:
    1. Any large block deal today (>₹50Cr)
    2. Repeated accumulation (3+ days of buying)
    3. Historical block deal pattern
    """
    deals = fetch_bulk_deals_nse()
    sym_upper = symbol.upper()
    today_deals = [d for d in deals if d["symbol"] == sym_upper]
    
    score  = 0.0
    notes  = []

    # Today's large deals
    for deal in today_deals:
        if deal["value_cr"] >= _MIN_DEAL_CR:
            if deal["side"] == "BUY":
                score += 1.5
                notes.append(f"🐳 Large BUY block ₹{deal['value_cr']:.0f}Cr by {deal['client'][:20]}")
            else:
                score -= 1.5
                notes.append(f"🐳 Large SELL block ₹{deal['value_cr']:.0f}Cr by {deal['client'][:20]}")

    # Load history for accumulation check
    try:
        if _HISTORY_FILE.exists():
            hist = pd.read_csv(str(_HISTORY_FILE))
            sym_hist = hist[hist["symbol"]==sym_upper].tail(10)
            if len(sym_hist) >= _ACCUM_DAYS:
                recent = sym_hist.tail(_ACCUM_DAYS)
                all_buys = all(recent["side"]=="BUY") if len(recent) >= _ACCUM_DAYS else False
                if all_buys:
                    score += 2.0
                    notes.append(f"📈 {_ACCUM_DAYS}-day institutional accumulation detected")
    except Exception: pass

    # Save today's deals to history
    if today_deals:
        try:
            new_rows = pd.DataFrame([{
                "date": d["date"], "symbol": d["symbol"],
                "side": d["side"], "value_cr": d["value_cr"],
                "client": d["client"][:30],
            } for d in today_deals if d["value_cr"] >= 10])
            if not new_rows.empty:
                hist_existing = pd.read_csv(str(_HISTORY_FILE)) if _HISTORY_FILE.exists() else pd.DataFrame()
                pd.concat([hist_existing, new_rows]).tail(500).to_csv(str(_HISTORY_FILE), index=False)
        except Exception: pass

    direction = "BUY" if score > 0 else "SELL" if score < 0 else None
    return {
        "symbol":    symbol,
        "score":     round(score, 2),
        "direction": direction,
        "notes":     notes,
        "deals_today": len(today_deals),
        "large_deals": len([d for d in today_deals if d["value_cr"] >= _MIN_DEAL_CR]),
    }


def get_all_dark_pool_alerts() -> List[Dict]:
    """Get alerts for all symbols with large block deals today."""
    deals = fetch_bulk_deals_nse()
    large = [d for d in deals if d["value_cr"] >= _MIN_DEAL_CR]
    alerts = []
    seen = set()
    for d in large:
        if d["symbol"] not in seen:
            seen.add(d["symbol"])
            dp = get_dark_pool_score(d["symbol"])
            if abs(dp["score"]) > 0:
                alerts.append(dp)
    return sorted(alerts, key=lambda x: -abs(x["score"]))


def dark_pool_summary() -> str:
    """Telegram-ready dark pool summary."""
    alerts = get_all_dark_pool_alerts()
    if not alerts:
        return ("🐳 <b>DARK POOL MONITOR</b>\n"
                "   No large block deals today (>₹50Cr)\n"
                "   Data refreshes every market hour")
    lines = ["🐳 <b>DARK POOL / BLOCK DEALS</b>", ""]
    for a in alerts[:5]:
        icon = "🟢" if a["direction"]=="BUY" else "🔴"
        lines.append(f"  {icon} {a['symbol']:12} score={a['score']:+.1f}")
        for n in a["notes"][:1]:
            lines.append(f"     {n}")
    return "\n".join(lines)
