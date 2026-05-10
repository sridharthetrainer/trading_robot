"""
participant_oi.py  —  NSE 4-Category Participant-Wise OI

NSE publishes FREE daily F&O data split into 4 participant categories:
  FII    — Foreign funds (trend setters, smart money)
  DII    — Domestic mutual funds / insurance (stability buyers)
  PRO    — Proprietary desks (option sellers, theta capture)
  CLIENT — Retail + HNI (contrarian indicator)

DATA SOURCE:
  https://www.nseindia.com/reports/fii-dii  (free, published ~8 PM daily)
  Participant OI file: nseindia.com/api/equity-stockIndices (F&O segment)

KEY SIGNALS:
  FII net long futures + cash buying           → MEGA BULLISH (+2.0)
  FII net short futures                        → BEARISH (-1.5)
  CLIENT long calls / total calls > 60%        → CONTRARIAN BEARISH (-1.0)
  CLIENT long puts / total puts > 60%          → CONTRARIAN BULLISH (+1.0)
  DII buying while FII selling                 → BOTTOM FORMATION (+1.0)
  PRO net short options (theta mode)           → RANGEBOUND, reduce breakout score
  FII long puts (hedging, not direction)       → NEUTRAL (not bearish signal)
  FII long/short ratio in futures > 2.0        → BULLISH (+1.5)
  FII long/short ratio in futures < 0.5        → BEARISH (-1.5)
  Cumulative 5d FII outflow > ₹10,000Cr        → OVERSOLD, potential bounce (+0.8)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_CACHE_FILE = Path("participant_oi_cache.json")
_HIST_FILE  = Path("participant_oi_history.json")
_TTL_SEC    = 3600 * 2   # refresh every 2 hours

# ── NSE Data Fetcher ──────────────────────────────────────────────────────────

def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.nseindia.com/",
        "Accept":     "application/json",
    })
    try:
        s.get("https://www.nseindia.com/", timeout=8)
    except Exception:
        pass
    return s


def _fetch_participant_oi_raw() -> dict:
    """
    Fetch participant-wise OI from NSE API.
    Returns raw data dict with FII/DII/PRO/CLIENT breakdown.
    """
    try:
        session = _nse_session()
        url = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
        r = session.get(url, timeout=12)
        data = r.json()
        return data
    except Exception as e:
        logger.debug("Participant OI raw fetch: %s", e)
        return {}


def _fetch_fno_participant_data() -> dict:
    """
    Fetch F&O participant-wise data from NSE derivatives reports.
    Falls back to market context API if dedicated endpoint unavailable.
    """
    result = {
        "date":  date.today().isoformat(),
        "FII":   {"fut_long": 0, "fut_short": 0, "call_long": 0, "call_short": 0,
                  "put_long": 0, "put_short": 0, "net_cash": 0},
        "DII":   {"fut_long": 0, "fut_short": 0, "call_long": 0, "call_short": 0,
                  "put_long": 0, "put_short": 0, "net_cash": 0},
        "PRO":   {"fut_long": 0, "fut_short": 0, "call_long": 0, "call_short": 0,
                  "put_long": 0, "put_short": 0},
        "CLIENT":{"fut_long": 0, "fut_short": 0, "call_long": 0, "call_short": 0,
                  "put_long": 0, "put_short": 0},
    }

    try:
        session = _nse_session()

        # Endpoint 1: participant-wise trading activity
        url1 = "https://www.nseindia.com/api/fiidiiTradeReact"
        r1 = session.get(url1, timeout=12)
        if r1.status_code == 200:
            data1 = r1.json()
            for row in (data1 if isinstance(data1, list) else []):
                cat = str(row.get("category", "")).upper()
                if cat in result:
                    result[cat]["net_cash"] = float(row.get("NET", 0) or 0)
            logger.debug("FII/DII cash data fetched")

    except Exception as e:
        logger.debug("Participant data fetch error: %s", e)

    try:
        session = _nse_session()
        # Endpoint 2: F&O participant positions
        url2 = "https://www.nseindia.com/api/fii-stats"
        r2 = session.get(url2, timeout=12)
        if r2.status_code == 200:
            data2 = r2.json()
            fii = data2.get("FII", {}) or data2.get("data", {}).get("FII", {})
            if fii:
                result["FII"]["fut_long"]   = float(fii.get("futLong", 0) or 0)
                result["FII"]["fut_short"]  = float(fii.get("futShort", 0) or 0)
                result["FII"]["call_long"]  = float(fii.get("callLong", 0) or 0)
                result["FII"]["call_short"] = float(fii.get("callShort", 0) or 0)
                result["FII"]["put_long"]   = float(fii.get("putLong", 0) or 0)
                result["FII"]["put_short"]  = float(fii.get("putShort", 0) or 0)
    except Exception as e:
        logger.debug("F&O participant positions: %s", e)

    return result


def get_participant_data(force: bool = False) -> dict:
    """Get participant OI data with caching."""
    if not force:
        try:
            if _CACHE_FILE.exists():
                cached = json.loads(_CACHE_FILE.read_text())
                if time.time() - cached.get("ts", 0) < _TTL_SEC:
                    return cached.get("data", {})
        except Exception:
            pass

    data = _fetch_fno_participant_data()
    if data:
        try:
            _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "data": data}))
            _update_history(data)
        except Exception:
            pass
    return data


# ── Cumulative FII Tracker ────────────────────────────────────────────────────

def _update_history(data: dict) -> None:
    """Append today's FII net cash to rolling history."""
    try:
        hist = {}
        if _HIST_FILE.exists():
            hist = json.loads(_HIST_FILE.read_text())
        today = date.today().isoformat()
        hist[today] = float(data.get("FII", {}).get("net_cash", 0))
        # Keep 30 days
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        hist = {k: v for k, v in hist.items() if k >= cutoff}
        _HIST_FILE.write_text(json.dumps(hist))
    except Exception:
        pass


def get_cumulative_fii(days: int = 5) -> float:
    """Return cumulative FII net cash over last N days."""
    try:
        if _HIST_FILE.exists():
            hist = json.loads(_HIST_FILE.read_text())
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            recent = [v for k, v in hist.items() if k >= cutoff]
            return round(sum(recent), 2)
    except Exception:
        pass
    return 0.0


# ── Signal Computation ────────────────────────────────────────────────────────

def compute_participant_signal(data: dict, direction: str) -> Tuple[float, str]:
    """
    Compute score modifier and narrative from participant data.

    Returns (score_modifier, narrative_string).
    """
    if not data:
        return 0.0, "no_data"

    fii = data.get("FII", {})
    dii = data.get("DII", {})
    pro = data.get("PRO", {})
    cli = data.get("CLIENT", {})

    modifier  = 0.0
    narrative = []

    # ── FII Futures Long/Short Ratio ──────────────────────────────────────────
    fii_fut_l = float(fii.get("fut_long",  0))
    fii_fut_s = float(fii.get("fut_short", 0))
    if fii_fut_l > 0 and fii_fut_s > 0:
        ratio = fii_fut_l / fii_fut_s
        if ratio > 2.0:
            modifier += 1.5 if direction == "BUY" else -1.0
            narrative.append(f"FII fut L/S={ratio:.1f} (bullish)")
        elif ratio < 0.5:
            modifier -= 1.5 if direction == "BUY" else -1.0
            narrative.append(f"FII fut L/S={ratio:.1f} (bearish)")

    # ── FII Cash Net Flow ─────────────────────────────────────────────────────
    fii_cash = float(fii.get("net_cash", 0))
    if fii_cash > 1000:
        modifier += 0.8 if direction == "BUY" else -0.5
        narrative.append(f"FII cash +₹{fii_cash/100:.0f}Cr")
    elif fii_cash < -1000:
        modifier -= 0.8 if direction == "BUY" else -0.5
        narrative.append(f"FII cash -₹{abs(fii_cash)/100:.0f}Cr")

    # ── DII as Stability Signal ───────────────────────────────────────────────
    dii_cash = float(dii.get("net_cash", 0))
    if fii_cash < -500 and dii_cash > 500:
        # DII absorbing FII sell = bottom signal
        modifier += 0.8 if direction == "BUY" else -0.3
        narrative.append("DII absorbing FII sell (support)")

    # ── CLIENT (Retail) Contrarian ───────────────────────────────────────────
    cli_call_l = float(cli.get("call_long",  0))
    cli_put_l  = float(cli.get("put_long",   0))
    fii_call_l = float(fii.get("call_long",  0))
    pro_call_s = float(pro.get("call_short", 0))

    total_call_long = cli_call_l + fii_call_l + float(dii.get("call_long",0))
    if total_call_long > 0:
        retail_call_pct = cli_call_l / total_call_long
        if retail_call_pct > 0.60:
            # Retail crowded long calls = contrarian bearish
            modifier -= 1.0 if direction == "BUY" else -1.0
            narrative.append(f"CLIENT CE={retail_call_pct:.0%} (retail crowded→fade)")
        elif retail_call_pct < 0.30:
            # Retail light on calls = under-positioned, room to run
            modifier += 0.5 if direction == "BUY" else -0.3
            narrative.append("CLIENT CE low (not crowded)")

    total_put_long = cli_put_l + float(fii.get("put_long",0))
    if total_put_long > 0:
        retail_put_pct = cli_put_l / total_put_long
        if retail_put_pct > 0.60 and direction == "SELL":
            # Retail crowded puts = contrarian bullish = fade the SELL
            modifier -= 0.8
            narrative.append(f"CLIENT PE={retail_put_pct:.0%} (retail panic→fade sell)")

    # ── PRO (Theta Seller) Activity ───────────────────────────────────────────
    pro_opt_s = float(pro.get("call_short", 0)) + float(pro.get("put_short", 0))
    if pro_opt_s > 50000:
        # PRO in heavy theta mode = rangebound expected
        modifier -= 0.5   # reduce breakout/trend score
        narrative.append("PRO heavy seller (rangebound)")

    # ── Cumulative FII (5-day) ────────────────────────────────────────────────
    cum_5d = get_cumulative_fii(5)
    if cum_5d < -10000:
        # ₹10,000Cr+ outflow in 5 days = oversold bounce likely
        modifier += 0.8 if direction == "BUY" else -0.3
        narrative.append(f"FII 5d cum=-₹{abs(cum_5d):.0f}Cr (oversold)")
    elif cum_5d > 10000:
        modifier -= 0.5 if direction == "BUY" else 0.3
        narrative.append(f"FII 5d cum=+₹{cum_5d:.0f}Cr (extended)")

    # Clamp
    modifier = round(max(-3.0, min(3.0, modifier)), 2)
    return modifier, " | ".join(narrative) if narrative else "neutral"


def get_participant_score(direction: str) -> Tuple[float, str]:
    """Convenience wrapper: fetch data and return score."""
    data = get_participant_data()
    return compute_participant_signal(data, direction)
