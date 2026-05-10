"""
candlestick_signals.py

Steve Nison — Japanese Candlestick Charting Techniques
Implemented for NSE intraday options trading.

KEY INSIGHT FROM NISON:
  A candlestick pattern alone is NOT a signal.
  A candlestick pattern AT A KEY LEVEL is a high-probability signal.

  Bullish engulfing in the middle of nowhere = 52% win rate (coin flip)
  Bullish engulfing exactly at S1 pivot support = 70-75% win rate

  This is why candlestick_signals.py always checks:
    → Is the pattern at a pivot level (S1/R1/TC/BC/PDH/PDL)?
    → Is volume confirming (above 20-bar average)?
    → Is the HTF (15-min) aligned with the pattern direction?

PATTERNS IMPLEMENTED (7 from Nison):
  1. Bullish Engulfing    — bearish bar fully engulfed by bullish bar
  2. Bearish Engulfing    — bullish bar fully engulfed by bearish bar
  3. Hammer               — small body, long lower wick, at support
  4. Shooting Star        — small body, long upper wick, at resistance
  5. Doji                 — open ≈ close, indecision at key level
  6. Marubozu             — full body, no wicks, pure momentum
  7. Morning/Evening Star — 3-bar reversal pattern

SCORING:
  Pattern alone:           3.0 - 5.0
  Pattern at pivot level: +2.0
  Pattern with volume:    +1.5
  Pattern with HTF align: +1.0
  Max possible score:      9.5
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# Minimum wick-to-body ratio for hammer/shooting star
HAMMER_WICK_RATIO     = 2.0   # lower wick must be 2x the body
ENGULF_BODY_RATIO     = 0.5   # engulfing bar body must be 50% larger than previous
DOJI_BODY_PCT         = 0.10  # body < 10% of total range = doji
MARUBOZU_WICK_PCT     = 0.05  # wicks < 5% of body = marubozu
PIVOT_PROXIMITY_PCT   = 0.003 # within 0.3% of a pivot level = "at the level"
VOLUME_CONFIRM_RATIO  = 1.2   # volume > 1.2x 20-bar average = confirming


# ── Individual Pattern Detectors ──────────────────────────────────────────────

def is_bullish_engulfing(o1: float, h1: float, l1: float, c1: float,
                          o2: float, h2: float, l2: float, c2: float) -> bool:
    """
    Bar 1 (prev): bearish (c1 < o1)
    Bar 2 (curr): bullish (c2 > o2), body completely engulfs bar 1 body
    """
    prev_bearish = c1 < o1
    curr_bullish = c2 > o2
    if not (prev_bearish and curr_bullish):
        return False
    prev_body = abs(o1 - c1)
    curr_body = abs(o2 - c2)
    # Current open below prev close, current close above prev open
    engulfs = (o2 <= c1) and (c2 >= o1)
    # Current body is meaningfully larger
    larger  = curr_body >= prev_body * (1 + ENGULF_BODY_RATIO)
    return engulfs and larger


def is_bearish_engulfing(o1: float, h1: float, l1: float, c1: float,
                          o2: float, h2: float, l2: float, c2: float) -> bool:
    """
    Bar 1 (prev): bullish (c1 > o1)
    Bar 2 (curr): bearish (c2 < o2), body completely engulfs bar 1 body
    """
    prev_bullish = c1 > o1
    curr_bearish = c2 < o2
    if not (prev_bullish and curr_bearish):
        return False
    prev_body = abs(o1 - c1)
    curr_body = abs(o2 - c2)
    engulfs   = (o2 >= c1) and (c2 <= o1)
    larger    = curr_body >= prev_body * (1 + ENGULF_BODY_RATIO)
    return engulfs and larger


def is_hammer(o: float, h: float, l: float, c: float) -> bool:
    """
    Small real body in upper half of range.
    Lower wick at least 2x the body. Very small or no upper wick.
    Bullish reversal at support.
    """
    body      = abs(c - o)
    rng       = h - l
    if rng <= 0 or body <= 0:
        return False
    lower_wick  = min(o, c) - l
    upper_wick  = h - max(o, c)
    body_small  = body < rng * 0.35
    long_lower  = lower_wick >= body * HAMMER_WICK_RATIO
    short_upper = upper_wick <= body * 0.5
    return body_small and long_lower and short_upper


def is_shooting_star(o: float, h: float, l: float, c: float) -> bool:
    """
    Small real body in lower half of range.
    Upper wick at least 2x the body. Very small or no lower wick.
    Bearish reversal at resistance.
    """
    body      = abs(c - o)
    rng       = h - l
    if rng <= 0 or body <= 0:
        return False
    upper_wick  = h - max(o, c)
    lower_wick  = min(o, c) - l
    body_small  = body < rng * 0.35
    long_upper  = upper_wick >= body * HAMMER_WICK_RATIO
    short_lower = lower_wick <= body * 0.5
    return body_small and long_upper and short_lower


def is_doji(o: float, h: float, l: float, c: float) -> bool:
    """
    Open ≈ Close (body < 10% of range).
    Represents indecision — significant at key levels.
    """
    body = abs(c - o)
    rng  = h - l
    return rng > 0 and body < rng * DOJI_BODY_PCT


def is_bullish_marubozu(o: float, h: float, l: float, c: float) -> bool:
    """
    Strong bullish candle with no (or tiny) wicks.
    Pure buying momentum — gap open and close at high.
    """
    if c <= o:
        return False
    body        = c - o
    upper_wick  = h - c
    lower_wick  = o - l
    no_upper    = upper_wick <= body * MARUBOZU_WICK_PCT
    no_lower    = lower_wick <= body * MARUBOZU_WICK_PCT
    return no_upper and no_lower and body > 0


def is_bearish_marubozu(o: float, h: float, l: float, c: float) -> bool:
    """
    Strong bearish candle with no (or tiny) wicks.
    Pure selling momentum.
    """
    if c >= o:
        return False
    body        = o - c
    upper_wick  = h - o
    lower_wick  = c - l
    no_upper    = upper_wick <= body * MARUBOZU_WICK_PCT
    no_lower    = lower_wick <= body * MARUBOZU_WICK_PCT
    return no_upper and no_lower and body > 0


def is_morning_star(bars: list) -> bool:
    """
    3-bar bullish reversal:
    Bar 1: large bearish
    Bar 2: small body (doji-like), gaps down
    Bar 3: large bullish, closes above midpoint of bar 1
    """
    if len(bars) < 3:
        return False
    o1,h1,l1,c1 = bars[0]
    o2,h2,l2,c2 = bars[1]
    o3,h3,l3,c3 = bars[2]
    bar1_bearish  = c1 < o1 and (o1 - c1) > (h1 - l1) * 0.5
    bar2_small    = abs(c2 - o2) < (h2 - l2) * 0.3
    bar3_bullish  = c3 > o3 and (c3 - o3) > (h3 - l3) * 0.5
    bar3_recovery = c3 > (o1 + c1) / 2
    return bar1_bearish and bar2_small and bar3_bullish and bar3_recovery


def is_evening_star(bars: list) -> bool:
    """
    3-bar bearish reversal:
    Bar 1: large bullish
    Bar 2: small body, gaps up
    Bar 3: large bearish, closes below midpoint of bar 1
    """
    if len(bars) < 3:
        return False
    o1,h1,l1,c1 = bars[0]
    o2,h2,l2,c2 = bars[1]
    o3,h3,l3,c3 = bars[2]
    bar1_bullish  = c1 > o1 and (c1 - o1) > (h1 - l1) * 0.5
    bar2_small    = abs(c2 - o2) < (h2 - l2) * 0.3
    bar3_bearish  = c3 < o3 and (o3 - c3) > (h3 - l3) * 0.5
    bar3_decline  = c3 < (o1 + c1) / 2
    return bar1_bullish and bar2_small and bar3_bearish and bar3_decline


# ── Pivot Level Proximity Check ───────────────────────────────────────────────

def nearest_pivot_level(price: float, levels: dict) -> Tuple[str, float, float]:
    """
    Find the nearest pivot level to current price.
    Returns (level_name, level_price, distance_pct).
    """
    best_name = ""
    best_val  = 0.0
    best_dist = float("inf")
    for name, val in levels.items():
        if val <= 0:
            continue
        dist = abs(price - val) / val
        if dist < best_dist:
            best_dist = dist
            best_name = name
            best_val  = val
    return best_name, best_val, best_dist


# ── Main Signal Generator ─────────────────────────────────────────────────────

def candlestick_signal(
    df:         pd.DataFrame,
    df_htf:     Optional[pd.DataFrame] = None,
    pivot_levels: Optional[dict]       = None,
) -> dict:
    """
    Scan last 3 bars for candlestick patterns at key pivot levels.

    Args:
        df:           5-min OHLCV dataframe
        df_htf:       15-min dataframe for HTF alignment
        pivot_levels: dict of pivot prices {P, R1, R2, S1, S2, TC, BC, PDH, PDL, H3, H4, L3, L4}

    Returns:
        {
          "direction":   "BUY" | "SELL" | None,
          "pattern":     pattern name,
          "score":       float,
          "at_level":    level name (e.g. "S1"),
          "reason":      str,
        }
    """
    empty = {"direction": None, "pattern": None, "score": 0.0, "at_level": "", "reason": "no_pattern"}

    if df is None or len(df) < 5:
        return empty

    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    needed = ["open","high","low","close"]
    if not all(c in df_c.columns for c in needed):
        return empty

    # Last 3 bars
    bars_raw = df_c[["open","high","low","close"]].tail(3).values.tolist()
    if len(bars_raw) < 2:
        return empty

    o1,h1,l1,c1 = bars_raw[-2]   # previous bar
    o2,h2,l2,c2 = bars_raw[-1]   # current bar

    # Volume confirmation
    vol_confirm = False
    if "volume" in df_c.columns:
        vol_now = float(df_c["volume"].iloc[-1])
        vol_avg = float(df_c["volume"].tail(20).mean())
        vol_confirm = vol_now >= vol_avg * VOLUME_CONFIRM_RATIO

    # HTF alignment
    htf_bias = "NEUTRAL"
    if df_htf is not None and len(df_htf) >= 5:
        try:
            htf = df_htf.copy()
            htf.columns = [c.lower() for c in htf.columns]
            htf_close = htf["close"].iloc[-1]
            htf_ema20 = htf["close"].ewm(span=20).mean().iloc[-1]
            htf_bias  = "BULLISH" if htf_close > htf_ema20 else "BEARISH"
        except Exception:
            pass

    # ── Pattern Detection ─────────────────────────────────────────────────────
    pattern   = None
    direction = None
    base_score = 0.0

    if is_bullish_engulfing(o1,h1,l1,c1, o2,h2,l2,c2):
        pattern   = "bullish_engulfing"
        direction = "BUY"
        base_score = 5.5

    elif is_bearish_engulfing(o1,h1,l1,c1, o2,h2,l2,c2):
        pattern   = "bearish_engulfing"
        direction = "SELL"
        base_score = 5.5

    elif is_hammer(o2,h2,l2,c2) and c2 > o2:
        pattern   = "hammer"
        direction = "BUY"
        base_score = 4.5

    elif is_shooting_star(o2,h2,l2,c2) and c2 < o2:
        pattern   = "shooting_star"
        direction = "SELL"
        base_score = 4.5

    elif is_bullish_marubozu(o2,h2,l2,c2):
        pattern   = "bullish_marubozu"
        direction = "BUY"
        base_score = 5.0

    elif is_bearish_marubozu(o2,h2,l2,c2):
        pattern   = "bearish_marubozu"
        direction = "SELL"
        base_score = 5.0

    elif len(bars_raw) >= 3 and is_morning_star(bars_raw):
        pattern   = "morning_star"
        direction = "BUY"
        base_score = 6.0

    elif len(bars_raw) >= 3 and is_evening_star(bars_raw):
        pattern   = "evening_star"
        direction = "SELL"
        base_score = 6.0

    elif is_doji(o2,h2,l2,c2):
        # Doji alone = indecision, not a trade signal
        # Only signal if at a key level
        pattern = "doji"
        base_score = 3.0
        # direction set below based on pivot proximity

    if pattern is None:
        return empty

    # ── Pivot Level Proximity Bonus ───────────────────────────────────────────
    at_level   = ""
    level_bonus = 0.0

    if pivot_levels:
        level_name, level_val, dist = nearest_pivot_level(c2, pivot_levels)
        if dist < PIVOT_PROXIMITY_PCT:
            at_level    = level_name
            level_bonus = 2.0

            # Confirm direction from pivot context
            bullish_levels = {"S1","S2","S3","BC","PDL","L3","L4"}
            bearish_levels = {"R1","R2","R3","TC","PDH","H3","H4"}

            if direction is None:  # doji: direction from level
                if level_name in bullish_levels:
                    direction = "BUY"
                elif level_name in bearish_levels:
                    direction = "SELL"

            # Sanity: bullish pattern at support = great
            #         bullish pattern at resistance = ignore
            if direction == "BUY"  and level_name in bearish_levels:
                level_bonus = -1.0  # fighting resistance
            if direction == "SELL" and level_name in bullish_levels:
                level_bonus = -1.0  # fighting support

    if direction is None:
        return empty

    # ── Volume and HTF bonuses ────────────────────────────────────────────────
    score = base_score + level_bonus
    if vol_confirm:
        score += 1.5
    if (direction == "BUY"  and htf_bias == "BULLISH") or \
       (direction == "SELL" and htf_bias == "BEARISH"):
        score += 1.0
    elif (direction == "BUY"  and htf_bias == "BEARISH") or \
         (direction == "SELL" and htf_bias == "BULLISH"):
        score -= 1.5   # fighting HTF trend

    if score <= 0:
        return empty

    reason = (
        f"{pattern}"
        f"{'_at_' + at_level if at_level else ''}"
        f"{'_vol_confirmed' if vol_confirm else ''}"
        f"{'_htf_' + htf_bias.lower() if htf_bias != 'NEUTRAL' else ''}"
    )

    return {
        "direction":  direction,
        "pattern":    pattern,
        "score":      round(min(score, 9.5), 2),
        "at_level":   at_level,
        "reason":     reason,
    }


def run_candlestick_strategy(df, df_htf=None, option_data=None) -> dict:
    """
    Drop-in strategy for signal_engine.py STRATEGIES list.
    Fetches pivot levels from option_data if available.
    """
    try:
        pivot_levels = {}
        if option_data and isinstance(option_data, dict):
            pivot_levels = option_data.get("pivot_levels", {})

        # Try to get pivot levels from pivot_boss if available
        if not pivot_levels and df is not None and len(df) >= 80:
            try:
                from pivot_boss import calc_floor_pivots, calc_camarilla_pivots, calc_cpr
                df_c = df.copy()
                df_c.columns = [c.lower() for c in df_c.columns]
                prev = df_c.iloc[:78]
                H = float(prev["high"].max())  if "high"  in prev.columns else 0
                L = float(prev["low"].min())   if "low"   in prev.columns else 0
                C = float(prev["close"].iloc[-1])
                if H > 0 and L > 0:
                    fp  = calc_floor_pivots(H, L, C)
                    cam = calc_camarilla_pivots(H, L, C)
                    cpr = calc_cpr(H, L, C)
                    pivot_levels = {**fp, **cam,
                                    "TC": cpr["tc"], "BC": cpr["bc"],
                                    "PDH": H, "PDL": L}
            except Exception:
                pass

        result = candlestick_signal(df, df_htf, pivot_levels)
        return {
            "strategy":  f"candlestick_{result.get('pattern','unknown')}",
            "score":     float(result.get("score", 0.0)),
            "direction": result.get("direction"),
            "reason":    result.get("reason",""),
            "pattern":   result.get("pattern",""),
            "at_level":  result.get("at_level",""),
        }
    except Exception as e:
        logger.debug("Candlestick strategy error: %s", e)
        return {"strategy":"candlestick","score":0.0,"direction":None}
