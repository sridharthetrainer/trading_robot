"""
fii_tracker.py — Daily FII/DII Data Storage + Pattern Analysis

Stores FII/DII cash market + derivatives data every day.
Finds patterns: consecutive buying/selling, accumulation zones,
smart money positioning vs retail.

Data stored in fii_history.csv:
  date, fii_cash_net, dii_cash_net, fii_fut_long, fii_fut_short,
  fii_call_long, fii_put_long, nifty_close, pcr, vix

Pattern signals:
  - 5d consecutive FII buying → BULLISH +1.5
  - FII building futures long → BULLISH +1.0
  - FII vs DII divergence → trend reversal signal
  - FII put building → market top signal
  - Smart Money Index (FII+DII net vs retail net)
"""
from __future__ import annotations

import json
import logging
import os
from csv import DictReader, DictWriter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_HIST_CSV  = Path("fii_history.csv")
_FIELDNAMES = [
    "date","fii_cash_net","dii_cash_net","fii_fut_long","fii_fut_short",
    "fii_net_futures","fii_call_long","fii_put_long","fii_net_oi",
    "dii_net_oi","nifty_close","pcr","vix","source"
]


# ── Storage ───────────────────────────────────────────────────────────────────
def _load_history() -> List[Dict]:
    if not _HIST_CSV.exists(): return []
    try:
        with open(_HIST_CSV) as f:
            return list(DictReader(f))
    except Exception: return []


def _save_record(record: Dict) -> None:
    exists = _HIST_CSV.exists()
    try:
        with open(_HIST_CSV, "a", newline="") as f:
            w = DictWriter(f, fieldnames=_FIELDNAMES)
            if not exists: w.writeheader()
            # Ensure all fields present
            row = {k: record.get(k, 0) for k in _FIELDNAMES}
            row["date"] = str(record.get("date", date.today().isoformat()))
            w.writerow(row)
    except Exception as e:
        logger.debug("FII save: %s", e)


def record_today(
    fii_cash_net:   float = 0,
    dii_cash_net:   float = 0,
    fii_fut_long:   float = 0,
    fii_fut_short:  float = 0,
    fii_call_long:  float = 0,
    fii_put_long:   float = 0,
    dii_net_oi:     float = 0,
    nifty_close:    float = 0,
    pcr:            float = 0,
    vix:            float = 0,
    source:         str   = "NSE",
) -> None:
    """Save today's FII/DII data. Called after market close (~3:35 PM)."""
    today = date.today().isoformat()
    hist  = _load_history()

    # Don't duplicate same-day record
    if hist and hist[-1].get("date") == today:
        logger.debug("FII history: today already recorded")
        return

    record = {
        "date":           today,
        "fii_cash_net":   round(fii_cash_net, 2),
        "dii_cash_net":   round(dii_cash_net, 2),
        "fii_fut_long":   round(fii_fut_long, 2),
        "fii_fut_short":  round(fii_fut_short, 2),
        "fii_net_futures":round(fii_fut_long - fii_fut_short, 2),
        "fii_call_long":  round(fii_call_long, 2),
        "fii_put_long":   round(fii_put_long, 2),
        "fii_net_oi":     round(fii_fut_long - fii_fut_short + fii_call_long - fii_put_long, 2),
        "dii_net_oi":     round(dii_net_oi, 2),
        "nifty_close":    round(nifty_close, 2),
        "pcr":            round(pcr, 3),
        "vix":            round(vix, 2),
        "source":         source,
    }
    _save_record(record)
    logger.info("FII history recorded | FII cash ₹%.0f Cr | DII ₹%.0f Cr",
                fii_cash_net, dii_cash_net)


# ── Pattern Analysis ──────────────────────────────────────────────────────────
def analyse_fii_patterns(lookback: int = 20) -> Dict:
    """
    Analyse FII/DII patterns from stored history.
    Returns signal score and identified patterns.
    """
    hist = _load_history()
    if len(hist) < 5:
        return {"score": 0.0, "patterns": [], "days": len(hist)}

    recent = hist[-lookback:]

    def _f(row, key): return float(row.get(key, 0) or 0)

    fii_cash   = [_f(r,"fii_cash_net") for r in recent]
    dii_cash   = [_f(r,"dii_cash_net") for r in recent]
    fii_fut    = [_f(r,"fii_net_futures") for r in recent]
    fii_oi     = [_f(r,"fii_net_oi") for r in recent]
    nifty      = [_f(r,"nifty_close") for r in recent]

    score    = 0.0
    patterns = []

    # ── Pattern 1: Consecutive FII cash buying/selling (5 days) ──────────────
    last5_cash = fii_cash[-5:]
    consec_buy  = all(x > 0 for x in last5_cash)
    consec_sell = all(x < 0 for x in last5_cash)
    if consec_buy:
        score += 1.5
        total = sum(last5_cash)
        patterns.append(f"📈 FII 5d consecutive buying ₹{total:,.0f}Cr → BULLISH")
    elif consec_sell:
        score -= 1.5
        total = sum(last5_cash)
        patterns.append(f"📉 FII 5d consecutive selling ₹{abs(total):,.0f}Cr → BEARISH")

    # ── Pattern 2: Cumulative FII 10-day ─────────────────────────────────────
    cum10 = sum(fii_cash[-10:])
    if cum10 > 5000:
        score += 1.0
        patterns.append(f"🏦 FII 10d net ₹{cum10:,.0f}Cr (strong accumulation)")
    elif cum10 < -5000:
        score -= 1.0
        patterns.append(f"🏦 FII 10d net ₹{cum10:,.0f}Cr (strong distribution)")

    # ── Pattern 3: FII vs DII divergence ─────────────────────────────────────
    last5_dii = dii_cash[-5:]
    dii_buying = sum(last5_dii) > 0
    fii_selling = sum(last5_cash) < 0
    if fii_selling and dii_buying:
        score += 0.5  # DII buying what FII sells = often bottoms
        patterns.append("⚖️ FII selling + DII buying = potential support zone")

    # ── Pattern 4: Futures positioning ───────────────────────────────────────
    last5_fut = fii_fut[-5:]
    net_fut = sum(last5_fut)
    if net_fut > 0 and sum(last5_cash) > 0:
        score += 1.0
        patterns.append(f"📊 FII long futures + cash = strong conviction BUY")
    elif net_fut < 0 and sum(last5_cash) < 0:
        score -= 1.0
        patterns.append(f"📊 FII short futures + cash selling = strong SELL conviction")

    # ── Pattern 5: OI trend (put/call building) ───────────────────────────────
    recent_oi = fii_oi[-5:]
    if len(recent_oi) >= 3:
        oi_trend = recent_oi[-1] - recent_oi[0]
        if oi_trend > 0:
            score += 0.5
            patterns.append(f"📈 FII net OI increasing = building long positions")
        elif oi_trend < 0:
            score -= 0.5
            patterns.append(f"📉 FII net OI decreasing = unwinding longs")

    # ── Smart Money Index ─────────────────────────────────────────────────────
    smi = sum(fii_cash[-5:]) + sum(dii_cash[-5:])
    patterns.append(f"🧠 Smart Money Index (5d): ₹{smi:,.0f}Cr ({'bullish' if smi>0 else 'bearish'})")

    return {
        "score":    round(min(max(score, -3.0), 3.0), 2),
        "patterns": patterns,
        "days":     len(hist),
        "fii_5d":   round(sum(fii_cash[-5:]), 0),
        "dii_5d":   round(sum(dii_cash[-5:]), 0),
        "consec_buy": consec_buy,
        "consec_sell":consec_sell,
    }


def fii_summary_text(lookback: int = 10) -> str:
    """Human-readable FII/DII summary for Telegram /fii command."""
    hist = _load_history()
    if not hist:
        return "📊 <b>FII/DII TRACKER</b>\n  No data yet — collecting from today"

    analysis = analyse_fii_patterns(lookback)
    recent   = hist[-5:]

    lines = [
        "📊 <b>FII/DII PATTERN ANALYSIS</b>",
        f"  Data: {len(hist)} trading days stored",
        "",
        "<b>Last 5 days:</b>",
    ]
    for r in recent:
        d = r.get("date","")
        fc = float(r.get("fii_cash_net",0) or 0)
        dc = float(r.get("dii_cash_net",0) or 0)
        ic = "🟢" if fc > 0 else "🔴"
        lines.append(f"  {d}  FII: {ic}₹{fc:+,.0f}Cr  DII: ₹{dc:+,.0f}Cr")

    lines += [
        "",
        f"<b>5d cumulative:</b>",
        f"  FII: ₹{analysis['fii_5d']:+,.0f}Cr",
        f"  DII: ₹{analysis['dii_5d']:+,.0f}Cr",
        f"  Signal score: {analysis['score']:+.1f}",
        "",
        "<b>Patterns detected:</b>",
    ]
    for p in analysis["patterns"]:
        lines.append(f"  {p}")

    return "\n".join(lines)
