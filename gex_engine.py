"""
gex_engine.py — Live Gamma Exposure (GEX) Engine for NSE

Wraps greeks_live.compute_gex with:
  - Per-symbol result caching (5-minute TTL)
  - Signal score modifiers based on GEX regime
  - Delta Wall proximity scoring
  - GEX flip-point proximity scoring
  - Dashboard-ready summary

GEX Logic (SpotGamma / Brent Kochuba methodology):
  Positive GEX  → Dealers are LONG gamma → sell rallies, buy dips → RANGE-BOUND
  Negative GEX  → Dealers are SHORT gamma → buy rallies, sell dips → TRENDING/VOLATILE
  Delta Wall    → Strike with highest |GEX| = strongest mechanical S/R
  GEX Flip      → Where total GEX crosses zero = volatility expansion trigger

Score modifier logic:
  - Price above Delta Wall + direction=BUY → GEX resistance ahead: -0.6
  - Price below Delta Wall + direction=SELL → GEX support ahead: -0.6
  - Price at GEX flip point (±0.3%) → volatility expansion likely: +0.8 for breakout, -0.4 for MR
  - GEX regime=RANGE_BOUND + direction matches regime → +0.5 for MR strategies
  - GEX regime=TRENDING/VOLATILE → +0.5 for trend/breakout, -0.5 for MR
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_GEX_CACHE: Dict[str, Dict] = {}   # {symbol: {result, ts, spot}}
_GEX_TTL   = 300                    # 5-minute cache

try:
    from greeks_live import compute_gex, compute_greeks
    _GREEKS_OK = True
except ImportError:
    _GREEKS_OK = False
    logger.warning("greeks_live unavailable — GEX engine disabled")


def compute_and_cache_gex(
    symbol:   str,
    spot:     float,
    chain:    list,
    dte_days: int = 0,
) -> Dict:
    """
    Compute GEX for symbol and cache the result.
    Call this from the option chain refresh cycle (every 5 min).
    Returns the GEX result dict.
    """
    if not _GREEKS_OK or not chain or not spot:
        return {}
    try:
        result = compute_gex(spot, chain, dte_days=dte_days)
        _GEX_CACHE[symbol.upper()] = {
            "result": result,
            "ts":     time.time(),
            "spot":   spot,
        }
        logger.debug(
            "GEX[%s] total=%.0f regime=%s delta_wall=%.0f flip=%.0f",
            symbol, result.get("total_gex", 0), result.get("regime", "?"),
            result.get("delta_wall", 0), result.get("flip_point", 0),
        )
        return result
    except Exception as e:
        logger.debug("compute_and_cache_gex[%s]: %s", symbol, e)
        return {}


def get_cached_gex(symbol: str) -> Dict:
    """Return cached GEX result if still fresh, else empty dict."""
    entry = _GEX_CACHE.get(symbol.upper(), {})
    if not entry:
        return {}
    if time.time() - entry.get("ts", 0) > _GEX_TTL:
        return {}
    return entry.get("result", {})


def get_gex_score_modifier(
    symbol:    str,
    spot:      float,
    direction: str,
    strategy:  str = "",
) -> float:
    """
    Return a score modifier based on GEX regime and price vs key GEX levels.

    Args:
        symbol:    Instrument name (e.g. "NIFTY")
        spot:      Current price
        direction: "BUY" or "SELL"
        strategy:  Strategy name — used to distinguish MR vs trend strategies

    Returns:
        float modifier to add to adjusted_score
    """
    gex = get_cached_gex(symbol)
    if not gex or not spot:
        return 0.0

    total_gex   = float(gex.get("total_gex", 0))
    delta_wall  = float(gex.get("delta_wall", 0))
    flip_point  = float(gex.get("flip_point", spot))
    regime      = str(gex.get("regime", ""))

    mod   = 0.0
    stl   = strategy.lower()
    is_mr = any(x in stl for x in ["mean_rev", "vwap_rev", "cpr", "rsi_div", "scalp"])
    is_tr = any(x in stl for x in ["trend", "break", "orb", "momentum", "supertrend"])

    # ── GEX regime modifier ────────────────────────────────────────────────────
    if "RANGE_BOUND" in regime:
        if is_mr:   mod += 0.5    # mean-rev thrives in range-bound GEX
        if is_tr:   mod -= 0.4    # trend strategies face headwind from dealer hedging
    elif "TRENDING" in regime or "VOLATILE" in regime:
        if is_tr:   mod += 0.5    # trend/breakout has tailwind from dealer amplification
        if is_mr:   mod -= 0.4    # mean-rev dangerous in GEX-amplified moves

    # ── Delta Wall proximity ───────────────────────────────────────────────────
    if delta_wall and spot:
        dist_pct = (delta_wall - spot) / spot
        # Within 0.5% of Delta Wall — magnetic resistance/support
        if abs(dist_pct) < 0.005:
            if direction == "BUY"  and dist_pct > 0:
                mod -= 0.7   # Delta Wall above — strong resistance ahead
            elif direction == "SELL" and dist_pct < 0:
                mod -= 0.7   # Delta Wall below — strong support ahead
            elif direction == "BUY"  and dist_pct < 0:
                mod += 0.5   # Price above Delta Wall — dealers buy on dips = support
            elif direction == "SELL" and dist_pct > 0:
                mod += 0.5   # Price below Delta Wall — dealers sell on rallies = resistance

    # ── GEX Flip Point proximity ───────────────────────────────────────────────
    if flip_point and spot:
        flip_dist_pct = abs(flip_point - spot) / spot
        if flip_dist_pct < 0.003:   # within 0.3% of flip point
            # At flip point: volatility expansion imminent
            if is_tr:   mod += 0.8   # breakout/trend strategies love flip-point breaks
            if is_mr:   mod -= 0.5   # mean-rev dangerous near flip point

    return round(mod, 3)


def get_gex_summary(symbol: str = "NIFTY") -> Dict:
    """
    Return a dashboard-ready GEX summary dict for the given symbol.
    Includes: regime, total_gex, delta_wall, flip_point, interpretation,
    and top 5 strikes by absolute GEX (for heatmap rendering).
    """
    gex = get_cached_gex(symbol)
    if not gex:
        return {
            "symbol":  symbol,
            "status":  "no_data",
            "regime":  "UNKNOWN",
            "total_gex": 0,
        }

    gex_by_strike = gex.get("gex_by_strike", {})
    top_strikes = sorted(
        gex_by_strike.items(),
        key=lambda x: abs(x[1]),
        reverse=True,
    )[:5]

    return {
        "symbol":        symbol,
        "status":        "ok",
        "total_gex":     gex.get("total_gex", 0),
        "regime":        gex.get("regime", "UNKNOWN"),
        "vol_regime":    gex.get("vol_regime", "UNKNOWN"),
        "delta_wall":    gex.get("delta_wall", 0),
        "flip_point":    gex.get("flip_point", 0),
        "interpretation":gex.get("interpretation", ""),
        "top_strikes":   [{"strike": s, "gex": g} for s, g in top_strikes],
    }


def get_all_gex_summaries() -> Dict[str, Dict]:
    """Return GEX summaries for all cached symbols."""
    return {sym: get_gex_summary(sym) for sym in _GEX_CACHE}


def force_gex_refresh(
    symbol:   str,
    spot:     float,
    chain:    list,
    dte_days: int = 0,
) -> Dict:
    """Force recompute even if cache is fresh. Used after options chain update."""
    key = symbol.upper()
    _GEX_CACHE.pop(key, None)
    return compute_and_cache_gex(symbol, spot, chain, dte_days)
