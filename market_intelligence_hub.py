"""
market_intelligence_hub.py — Unified market intelligence layer

Combines: promoter signals, rollover/cost-of-carry, order book depth,
composite market sentiment score, regime-aware strategy routing.

All implemented as additive score modifiers to signal_engine.
"""
from __future__ import annotations
import logging, os, time, json
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict[str, dict] = {}
_CACHE_TTL = 300  # 5 min

# ── 1. PROMOTER BUYING SIGNAL ────────────────────────────────────
def get_promoter_signal(symbol: str) -> float:
    """
    Promoter buying = strongest possible fundamental signal.
    Returns score modifier: +1.5 (buying) / -1.5 (selling) / 0 (neutral)
    Source: NSE insider trades (already fetched by omnisource)
    """
    cache_key = f"promoter_{symbol}_{date.today()}"
    if cache_key in _CACHE:
        return _CACHE[cache_key].get("score", 0.0)
    try:
        from omnisource_news_engine import get_omnisource_intelligence
        intel = get_omnisource_intelligence()
        insider = intel.get("insider_trades", [])
        sym_trades = [t for t in insider
                      if t.get("symbol","").upper() == symbol.upper()
                      and "PROM" in str(t.get("person","")).upper()]
        if not sym_trades:
            _CACHE[cache_key] = {"score": 0.0}
            return 0.0
        buys  = sum(1 for t in sym_trades if t.get("direction") == "BUY")
        sells = sum(1 for t in sym_trades if t.get("direction") == "SELL")
        score = 0.0
        if buys > sells:   score =  min(1.5, buys * 0.5)
        elif sells > buys: score = -min(1.5, sells * 0.5)
        _CACHE[cache_key] = {"score": score, "buys": buys, "sells": sells}
        if abs(score) > 0.3:
            logger.info("Promoter signal %s: %.2f (%dB/%dS)", symbol, score, buys, sells)
        return score
    except Exception as e:
        logger.debug("promoter_signal %s: %s", symbol, e)
        return 0.0


# ── 2. ROLLOVER / COST-OF-CARRY SIGNAL ──────────────────────────
def get_rollover_signal(symbol: str = "NIFTY") -> dict:
    """
    Cost of carry = (Futures Price - Spot Price) / Spot Price * 12
    Positive carry + rising OI = bulls rolling → BULLISH
    Negative carry (backwardation) = bears dominant → BEARISH
    NSE provides this free via option chain API.
    """
    cache_key = f"rollover_{symbol}"
    cached = _CACHE.get(cache_key, {})
    if cached and time.time() - cached.get("ts", 0) < _CACHE_TTL:
        return cached

    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                           "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)

        # Get futures quote
        sym_map = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY"}
        nse_sym = sym_map.get(symbol.upper(), symbol.upper())
        r = s.get(
            f"https://www.nseindia.com/api/quote-derivative?symbol={nse_sym}",
            timeout=8
        )
        if r.status_code != 200:
            return {}

        data = r.json()
        spot = float(data.get("underlyingValue", 0) or 0)
        if not spot:
            return {}

        # Find near-month futures
        futures_price = 0.0
        for item in data.get("stocks", []):
            md   = item.get("metadata", {})
            itype = str(md.get("instrumentType", "")).upper()
            if "FUT" in itype:
                futures_price = float(md.get("lastPrice", 0) or 0)
                break

        if not futures_price or not spot:
            return {}

        carry = (futures_price - spot) / spot * 100  # % carry
        # Annualised (assuming ~30 days to expiry)
        carry_annual = carry * 12

        signal = "NEUTRAL"
        score  = 0.0
        if carry > 0.15:      # strong positive carry = bullish
            signal = "BULLISH"
            score  = min(0.8, carry * 2)
        elif carry < -0.10:   # backwardation = bearish
            signal = "BEARISH"
            score  = max(-0.8, carry * 2)

        result = {
            "spot": spot, "futures": futures_price,
            "carry_pct": round(carry, 3),
            "carry_annual": round(carry_annual, 1),
            "signal": signal, "score": score,
            "ts": time.time(),
            "narrative": (f"Futures {'premium' if carry>0 else 'discount'} "
                          f"{abs(carry):.2f}% — {signal.lower()} carry")
        }
        _CACHE[cache_key] = result
        return result
    except Exception as e:
        logger.debug("rollover_signal: %s", e)
        return {}


# ── 3. COMPOSITE MARKET SENTIMENT SCORE (0-100) ──────────────────
def get_composite_sentiment() -> dict:
    """
    Single 0-100 score combining ALL market signals.
    50 = neutral, >70 = bullish, <30 = bearish.
    Shown in every /status message and morning brief.
    """
    cache_key = "composite_sentiment"
    cached = _CACHE.get(cache_key, {})
    if cached and time.time() - cached.get("ts", 0) < 600:
        return cached

    components = {}
    score = 50.0  # start neutral

    # VIX component (inverse)
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                           "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=3)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=6)
        for idx in r.json().get("data", []):
            if "VIX" in str(idx.get("index", "")).upper():
                vix = float(idx.get("last", 20) or 20)
                # VIX 10→+15, VIX 20→0, VIX 30→-15
                vix_component = max(-15, min(15, (20 - vix) * 1.5))
                score += vix_component
                components["vix"] = {"value": vix, "contribution": vix_component}
                break
    except Exception: pass

    # FII flow component
    try:
        from fii_data_fetcher import get_fii_history
        hist = get_fii_history(5)
        if hist is not None:
            fii_cols = [c for c in hist.columns if 'net' in c.lower() and 'fii' in c.lower()]
            if fii_cols:
                fii_net = float(hist[fii_cols[0]].sum())
                # ₹5000Cr → +10, -₹5000Cr → -10
                fii_comp = max(-10, min(10, fii_net / 500))
                score += fii_comp
                components["fii_5d"] = {"value": fii_net, "contribution": fii_comp}
    except Exception: pass

    # News sentiment component
    try:
        from news_sentiment_engine import get_full_sentiment
        sent = get_full_sentiment()
        news_score = float(sent.get("avg_score", 0) or 0)
        news_comp  = news_score * 15  # -1→-15, +1→+15
        score += news_comp
        components["news"] = {"value": news_score, "contribution": news_comp}
    except Exception: pass

    # Rollover carry component
    try:
        rl = get_rollover_signal("NIFTY")
        if rl:
            carry_comp = float(rl.get("score", 0)) * 5
            score += carry_comp
            components["rollover"] = {"contribution": carry_comp}
    except Exception: pass

    # PCR component
    try:
        from oi_builder import get_pcr
        pcr = get_pcr("NIFTY")
        if pcr:
            pcr_val = float(pcr)
            # PCR 1.5→+8, PCR 0.7→-8
            pcr_comp = max(-8, min(8, (pcr_val - 1.0) * 10))
            score += pcr_comp
            components["pcr"] = {"value": pcr_val, "contribution": pcr_comp}
    except Exception: pass

    score = max(0, min(100, score))
    label = ("VERY BULLISH" if score >= 75 else
             "BULLISH"      if score >= 60 else
             "SLIGHTLY BULLISH" if score >= 52 else
             "NEUTRAL"      if score >= 48 else
             "SLIGHTLY BEARISH" if score >= 40 else
             "BEARISH"      if score >= 25 else
             "VERY BEARISH")

    result = {
        "score": round(score, 1),
        "label": label,
        "components": components,
        "emoji": ("🟢🟢" if score >= 70 else "🟢" if score >= 55
                  else "⚪" if score >= 45 else "🔴" if score >= 30 else "🔴🔴"),
        "ts": time.time(),
    }
    _CACHE[cache_key] = result
    return result


# ── 4. REGIME-AWARE STRATEGY ROUTING ────────────────────────────
REGIME_STRATEGY_MAP = {
    # Only run these strategies in each regime
    "TREND":        {"preferred": ["trend", "breakout", "supertrend_mtf", "orb",
                                   "morning_momentum", "hour_orb", "weinstein_stage",
                                   "ma_cross", "ema_ribbon"],
                     "blocked":  ["mean_reversion", "vwap_reversion", "stat_arb"]},
    "RANGE":        {"preferred": ["mean_reversion", "vwap_reversion", "stat_arb",
                                   "vpoc_magnet", "rsi_divergence", "bb_percentb"],
                     "blocked":  ["breakout", "morning_momentum", "hour_orb"]},
    "BREAKOUT":     {"preferred": ["breakout", "vp_breakout", "orb", "vwap_bands",
                                   "failed_breakout", "market_structure"],
                     "blocked":  ["mean_reversion", "stat_arb"]},
    "CHOPPY":       {"preferred": ["scalping", "delta_neutral_theta"],
                     "blocked":  ["breakout", "trend", "orb", "morning_momentum"]},
    "HIGH_NOISE":   {"preferred": [],
                     "blocked":  ["ALL"]},  # block everything
    "EARLY_TREND":  {"preferred": ["breakout", "ma_cross", "orb"],
                     "blocked":  ["mean_reversion"]},
    "SIDEWAYS":     {"preferred": ["mean_reversion", "vwap_reversion", "scalping"],
                     "blocked":  ["trend", "breakout"]},
}


def should_strategy_run_in_regime(strategy_name: str, regime: str) -> Tuple[bool, str]:
    """
    Regime-aware strategy routing — IMPROVEMENT B.
    Returns (should_run, reason).
    """
    regime_upper = str(regime).upper()
    if regime_upper not in REGIME_STRATEGY_MAP:
        return True, "Unknown regime — allow all"

    mapping   = REGIME_STRATEGY_MAP[regime_upper]
    blocked   = mapping.get("blocked", [])
    preferred = mapping.get("preferred", [])

    if "ALL" in blocked:
        return False, f"Regime {regime} blocks all strategies"

    strat_lower = strategy_name.lower().replace("run_","").replace("_strategy","")
    for b in blocked:
        if b.lower() in strat_lower:
            return False, f"Regime {regime} blocks {strategy_name}"

    return True, "OK"


def get_preferred_strategies_for_regime(regime: str) -> list:
    regime_upper = str(regime).upper()
    if regime_upper not in REGIME_STRATEGY_MAP:
        return []
    return REGIME_STRATEGY_MAP[regime_upper].get("preferred", [])


# ── 5. SMART RE-ENTRY COOLDOWN ───────────────────────────────────
_SL_HIT_LOG: Dict[str, float] = {}  # symbol → timestamp of last SL hit
_SL_COOLDOWN_SECS = 1800  # 30 minutes

def register_sl_hit(symbol: str) -> None:
    """Called when SL is hit — starts cooldown for smart re-entry."""
    _SL_HIT_LOG[symbol.upper()] = time.time()
    logger.info("SL cooldown started: %s (30 min)", symbol)


def is_in_sl_cooldown(symbol: str) -> bool:
    """
    IMPROVEMENT G: Smart re-entry cooldown after SL hit.
    Prevents immediate re-entry in same direction (price retraces first).
    """
    last = _SL_HIT_LOG.get(symbol.upper(), 0)
    if time.time() - last < _SL_COOLDOWN_SECS:
        remaining = int((_SL_COOLDOWN_SECS - (time.time() - last)) / 60)
        logger.debug("SL cooldown active for %s (%d min remaining)", symbol, remaining)
        return True
    return False


# ── 6. ORDER BOOK DEPTH CHECK ───────────────────────────────────
def check_order_book_depth(symbol: str, qty: int) -> Tuple[bool, str]:
    """
    GAP 19: Check if sufficient depth exists before placing large order.
    Returns (sufficient_depth: bool, reason: str).
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                           "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=3)
        r = s.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",
            timeout=6
        )
        if r.status_code != 200:
            return True, "Depth API unavailable — proceeding"

        data = r.json()
        depth = data.get("marketDeptData", {})
        buy_qty  = sum(int(b.get("quantity", 0)) for b in depth.get("buy", [])[:3])
        sell_qty = sum(int(s.get("quantity", 0)) for s in depth.get("sell", [])[:3])

        # If our order is >20% of visible depth, flag it
        relevant = buy_qty if qty > 0 else sell_qty
        if relevant > 0 and qty > relevant * 0.20:
            return False, (f"Low depth: {relevant} at top 3 levels, "
                           f"order {qty} = {qty/relevant*100:.0f}% — use TWAP")
        return True, f"Depth OK: {relevant} available"
    except Exception:
        return True, "Depth check skipped"
