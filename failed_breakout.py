"""
failed_breakout.py

Adam Grimes — The Art and Science of Technical Analysis

THE FAILED BREAKOUT CONCEPT:
  A breakout that immediately reverses is often MORE powerful
  than a successful breakout — in the opposite direction.

  WHY IT WORKS:
    1. Price breaks above PDH at 9:20 AM
    2. Retail traders buy CEs (bullish breakout)
    3. Institutions sell into the breakout (distribution)
    4. Price reverses back below PDH within 1-3 candles
    5. Trapped retail traders panic-sell their CEs
    6. CE premiums collapse, PE premiums explode
    7. System buys PE (SELL signal) as trapped longs exit

  This is called a "bear trap" when it happens at resistance,
  and a "bull trap" when it happens at support.

DETECTION RULES:
  Failed bearish breakout (bull trap) → BUY signal:
    - Price broke below S1/PDL/BC in last 3 bars
    - Price returned ABOVE the broken level within 2 bars
    - Current bar closes above the broken level
    - Volume was high on breakdown (trapped sellers)

  Failed bullish breakout (bear trap) → SELL signal:
    - Price broke above R1/PDH/TC in last 3 bars
    - Price returned BELOW the broken level within 2 bars
    - Current bar closes below the broken level
    - Volume was high on breakout (trapped buyers)

SCORING:
  Base score: 7.0 (failed breakouts are high conviction)
  At major level (PDH/PDL, R1/S1): +1.5
  Volume confirmation: +1.0
  HTF alignment: +0.5
  Max: 9.0+
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

REVERSAL_WINDOW    = 3     # bars within which reversal must happen
BREAKOUT_THRESHOLD = 0.001 # 0.1% beyond level = valid breakout
VOLUME_CONFIRM     = 1.5   # breakout bar volume > 1.5x average


def failed_breakout_signal(
    df:           pd.DataFrame,
    df_htf:       Optional[pd.DataFrame] = None,
    pivot_levels: Optional[dict]         = None,
) -> dict:
    """
    Detect failed breakout pattern and generate signal.

    Returns:
        {
          "direction":      "BUY" | "SELL" | None,
          "pattern":        "failed_bull_breakout" | "failed_bear_breakout",
          "score":          float,
          "broken_level":   level name,
          "broken_price":   float,
          "reason":         str,
        }
    """
    empty = {"direction": None, "pattern": None, "score": 0.0,
             "broken_level": "", "broken_price": 0.0, "reason": "no_failed_breakout"}

    if df is None or len(df) < 10:
        return empty

    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    if "close" not in df_c.columns or "high" not in df_c.columns:
        return empty

    # Get pivot levels if not provided
    if not pivot_levels:
        pivot_levels = {}
        if len(df_c) >= 80:
            try:
                from pivot_boss import calc_floor_pivots, calc_cpr
                prev = df_c.iloc[:78]
                H = float(prev["high"].max())
                L = float(prev["low"].min())
                C = float(prev["close"].iloc[-1])
                if H > 0 and L > 0:
                    fp  = calc_floor_pivots(H, L, C)
                    cpr = calc_cpr(H, L, C)
                    pivot_levels = {**fp, "TC": cpr["tc"], "BC": cpr["bc"],
                                    "PDH": H, "PDL": L}
            except Exception:
                pass

    if not pivot_levels:
        return empty

    # Look at last N bars
    window = df_c.tail(REVERSAL_WINDOW + 2)
    if len(window) < 4:
        return empty

    closes  = window["close"].values
    highs   = window["high"].values
    lows    = window["low"].values if "low" in window.columns else closes

    # Volume
    vol_data = df_c["volume"].values if "volume" in df_c.columns else None
    vol_avg  = float(df_c["volume"].tail(20).mean()) if vol_data is not None else 1

    current_close = closes[-1]
    current_high  = highs[-1]
    current_low   = lows[-1]

    # ── Check each key level for failed breakout ──────────────────────────────

    # Priority levels — check these first (more reliable)
    priority_levels = ["PDH", "PDL", "R1", "S1", "TC", "BC", "R2", "S2"]
    all_levels      = priority_levels + [k for k in pivot_levels if k not in priority_levels]

    for level_name in all_levels:
        level_price = pivot_levels.get(level_name, 0)
        if level_price <= 0:
            continue

        # ── FAILED BULL BREAKOUT (bear trap → SELL) ───────────────────────
        # Resistance levels: PDH, R1, R2, TC, H3, H4
        if level_name in {"PDH", "R1", "R2", "TC", "H3", "H4"}:
            # Was level broken upward in last 2-3 bars?
            breakout_bar  = None
            for i in range(-REVERSAL_WINDOW - 1, -1):
                if abs(i) > len(highs):
                    break
                if highs[i] > level_price * (1 + BREAKOUT_THRESHOLD):
                    breakout_bar = i
                    break

            if breakout_bar is not None:
                # Did price return below the level?
                if current_close < level_price:
                    # Confirm: breakout bar had high volume
                    vol_confirm = False
                    if vol_data is not None and abs(breakout_bar) <= len(vol_data):
                        breakout_vol = vol_data[breakout_bar]
                        vol_confirm  = breakout_vol > vol_avg * VOLUME_CONFIRM

                    # HTF alignment
                    htf_bearish = False
                    if df_htf is not None and len(df_htf) >= 5:
                        try:
                            htf = df_htf.copy()
                            htf.columns = [c.lower() for c in htf.columns]
                            htf_close = htf["close"].iloc[-1]
                            htf_ema20 = htf["close"].ewm(span=20).mean().iloc[-1]
                            htf_bearish = htf_close < htf_ema20
                        except Exception:
                            pass

                    score = 7.0
                    if level_name in ("PDH", "R1"):
                        score += 1.5
                    if vol_confirm:
                        score += 1.0
                    if htf_bearish:
                        score += 0.5

                    logger.info(
                        "Failed bull breakout: %s broke %.1f then returned below → SELL signal score=%.1f",
                        level_name, level_price, score
                    )
                    return {
                        "direction":     "SELL",
                        "pattern":       "failed_bull_breakout",
                        "score":         round(min(score, 9.5), 2),
                        "broken_level":  level_name,
                        "broken_price":  level_price,
                        "reason":        (
                            f"failed_breakout_above_{level_name}_{level_price:.0f}_"
                            f"trapped_bulls_now_selling"
                            f"{'_vol_confirmed' if vol_confirm else ''}"
                        ),
                    }

        # ── FAILED BEAR BREAKOUT (bull trap → BUY) ────────────────────────
        # Support levels: PDL, S1, S2, BC, L3, L4
        if level_name in {"PDL", "S1", "S2", "BC", "L3", "L4"}:
            # Was level broken downward in last 2-3 bars?
            breakout_bar = None
            for i in range(-REVERSAL_WINDOW - 1, -1):
                if abs(i) > len(lows):
                    break
                if lows[i] < level_price * (1 - BREAKOUT_THRESHOLD):
                    breakout_bar = i
                    break

            if breakout_bar is not None:
                # Did price return above the level?
                if current_close > level_price:
                    vol_confirm = False
                    if vol_data is not None and abs(breakout_bar) <= len(vol_data):
                        breakout_vol = vol_data[breakout_bar]
                        vol_confirm  = breakout_vol > vol_avg * VOLUME_CONFIRM

                    htf_bullish = False
                    if df_htf is not None and len(df_htf) >= 5:
                        try:
                            htf = df_htf.copy()
                            htf.columns = [c.lower() for c in htf.columns]
                            htf_close = htf["close"].iloc[-1]
                            htf_ema20 = htf["close"].ewm(span=20).mean().iloc[-1]
                            htf_bullish = htf_close > htf_ema20
                        except Exception:
                            pass

                    score = 7.0
                    if level_name in ("PDL", "S1"):
                        score += 1.5
                    if vol_confirm:
                        score += 1.0
                    if htf_bullish:
                        score += 0.5

                    logger.info(
                        "Failed bear breakout: %s broke %.1f then returned above → BUY signal score=%.1f",
                        level_name, level_price, score
                    )
                    return {
                        "direction":     "BUY",
                        "pattern":       "failed_bear_breakout",
                        "score":         round(min(score, 9.5), 2),
                        "broken_level":  level_name,
                        "broken_price":  level_price,
                        "reason":        (
                            f"failed_breakdown_below_{level_name}_{level_price:.0f}_"
                            f"trapped_bears_covering"
                            f"{'_vol_confirmed' if vol_confirm else ''}"
                        ),
                    }

    return empty


def run_failed_breakout_strategy(df, df_htf=None, option_data=None) -> dict:
    """Drop-in strategy for signal_engine.py STRATEGIES list."""
    try:
        pivot_levels = {}
        if option_data and isinstance(option_data, dict):
            pivot_levels = option_data.get("pivot_levels", {})

        result = failed_breakout_signal(df, df_htf, pivot_levels)
        return {
            "strategy":  result.get("pattern", "failed_breakout") or "failed_breakout",
            "score":     float(result.get("score", 0.0)),
            "direction": result.get("direction"),
            "reason":    result.get("reason", ""),
        }
    except Exception as e:
        logger.debug("Failed breakout error: %s", e)
        return {"strategy": "failed_breakout", "score": 0.0, "direction": None}
