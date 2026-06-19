"""
williams_systems.py

Larry Williams — Long-Term Secrets to Short-Term Trading
Three strategies: Williams %R, Volatility Breakout, OOPS Pattern

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY 1: WILLIAMS %R
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  %R = (Highest High - Close) / (Highest High - Lowest Low) × -100
  Range: 0 to -100
  %R < -80 = oversold (like RSI < 20)
  %R > -20 = overbought (like RSI > 80)

  Trading rule:
    %R falls below -80 then rises back above -80 → BUY
    %R rises above -20 then falls back below -20 → SELL
    Faster and more sensitive than RSI — better for 5-min intraday

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY 2: VOLATILITY BREAKOUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Formula: Today's entry = Yesterday's Close + X% of Yesterday's Range
  X = typically 0.3 to 0.7 (calibrated per market)

  BUY  if today's price rises above: Prev Close + 0.5 × Prev Range
  SELL if today's price falls below: Prev Close - 0.5 × Prev Range

  The factor X adapts: trending days have larger ranges → larger thresholds
  Works on NIFTY because big range days are followed by more big moves

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY 3: OOPS PATTERN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Williams named it "OOPS!" because it catches traders off guard.

  BUY  setup: Today opens BELOW yesterday's low (gap down)
              If price then rallies BACK ABOVE yesterday's low → BUY
              Trapped shorts scramble to cover → explosive move up

  SELL setup: Today opens ABOVE yesterday's high (gap up)
              If price then falls BACK BELOW yesterday's high → SELL
              Trapped longs scramble to exit → explosive move down

  This is PERFECT for NSE because:
  - NIFTY frequently gaps at open due to global overnight news
  - Reversals back inside previous day range are common
  - When they happen, the move is fast and decisive
"""
from __future__ import annotations
import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

# Williams %R settings
WR_PERIOD     = 14     # standard period
WR_OVERSOLD   = -80    # below this = oversold
WR_OVERBOUGHT = -20    # above this = overbought

# Volatility Breakout settings
VB_FACTOR     = 0.5    # 50% of previous range

# OOPS settings
OOPS_BUFFER   = 0.0005  # 0.05% buffer for confirmation


def calc_williams_r(high: pd.Series, low: pd.Series,
                    close: pd.Series, period: int = WR_PERIOD) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    wr = -100 * (hh - close) / (hh - ll).replace(0, 1)
    return wr


def williams_r_signal(df: pd.DataFrame) -> dict:
    """Williams %R signal: exits from oversold/overbought."""
    df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
    if "close" not in df_c.columns or len(df_c) < WR_PERIOD + 3:
        return {"direction": None, "score": 0.0}

    wr       = calc_williams_r(df_c["high"], df_c["low"], df_c["close"])
    cur_wr   = float(wr.iloc[-1])
    prev_wr  = float(wr.iloc[-2])
    prev2_wr = float(wr.iloc[-3])

    direction = None
    score     = 0.0

    # BUY: %R was below -80 (oversold), now crossed back above -80
    if prev2_wr < WR_OVERSOLD and prev_wr < WR_OVERSOLD and cur_wr > WR_OVERSOLD:
        direction = "BUY"
        # Stronger signal if %R crossed from very deep oversold
        depth = abs(min(prev_wr, prev2_wr))
        score = 5.5 + min((depth - 80) / 20, 1.5)

    # SELL: %R was above -20 (overbought), now crossed back below -20
    elif prev2_wr > WR_OVERBOUGHT and prev_wr > WR_OVERBOUGHT and cur_wr < WR_OVERBOUGHT:
        direction = "SELL"
        depth = abs(max(prev_wr, prev2_wr))
        score = 5.5 + min((20 - depth) / 20, 1.5)

    return {
        "direction": direction,
        "score":     round(score, 2),
        "wr":        round(cur_wr, 1),
        "reason":    f"williams_r_{cur_wr:.1f}_{direction or 'no_signal'}",
    }


def volatility_breakout_signal(df: pd.DataFrame) -> dict:
    """Larry Williams volatility breakout."""
    df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
    if "close" not in df_c.columns or len(df_c) < 10:
        return {"direction": None, "score": 0.0}

    # Get yesterday's OHLC (last 78 5-min bars = 1 session)
    if len(df_c) >= 90:
        prev_bars  = df_c.iloc[-90:-12]
    else:
        prev_bars  = df_c.iloc[:-6] if len(df_c) > 10 else df_c

    if len(prev_bars) < 5:
        return {"direction": None, "score": 0.0}

    prev_high  = float(prev_bars["high"].max())  if "high"  in prev_bars.columns else 0
    prev_low   = float(prev_bars["low"].min())   if "low"   in prev_bars.columns else 0
    prev_close = float(prev_bars["close"].iloc[-1])
    prev_range = prev_high - prev_low

    if prev_range <= 0:
        return {"direction": None, "score": 0.0}

    cur_close  = float(df_c["close"].iloc[-1])
    prev_close2= float(df_c["close"].iloc[-2])

    buy_level  = prev_close + VB_FACTOR * prev_range
    sell_level = prev_close - VB_FACTOR * prev_range

    direction  = None
    score      = 0.0

    if prev_close2 <= buy_level < cur_close:
        direction = "BUY"
        score     = 6.0
    elif prev_close2 >= sell_level > cur_close:
        direction = "SELL"
        score     = 6.0

    return {
        "direction":   direction,
        "score":       score,
        "buy_level":   round(buy_level, 2),
        "sell_level":  round(sell_level, 2),
        "reason":      f"vb_breakout_{direction or 'no_signal'}",
    }


def oops_signal(df: pd.DataFrame) -> dict:
    """
    Williams OOPS pattern.
    Gap outside previous range, then return inside = explosive reversal.
    """
    df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
    if "open" not in df_c.columns or len(df_c) < 10:
        return {"direction": None, "score": 0.0}

    # Previous session OHLC
    if len(df_c) >= 90:
        prev_bars = df_c.iloc[-90:-12]
    else:
        prev_bars = df_c.iloc[:-6] if len(df_c) > 6 else df_c

    if len(prev_bars) < 5:
        return {"direction": None, "score": 0.0}

    prev_high  = float(prev_bars["high"].max())  if "high"  in prev_bars.columns else 0
    prev_low   = float(prev_bars["low"].min())   if "low"   in prev_bars.columns else 0

    # Today's data
    today_bars  = df_c.iloc[-12:]
    today_open  = float(today_bars["open"].iloc[0])
    cur_close   = float(df_c["close"].iloc[-1])

    if prev_high <= 0 or prev_low <= 0:
        return {"direction": None, "score": 0.0}

    direction = None
    score     = 0.0

    # OOPS BUY: opened below prev_low, now back above prev_low
    if today_open < prev_low and cur_close > prev_low * (1 + OOPS_BUFFER):
        direction  = "BUY"
        gap_size   = (prev_low - today_open) / prev_low
        score      = 7.0 + min(gap_size * 100, 2.0)  # bigger gap = stronger OOPS

    # OOPS SELL: opened above prev_high, now back below prev_high
    elif today_open > prev_high and cur_close < prev_high * (1 - OOPS_BUFFER):
        direction  = "SELL"
        gap_size   = (today_open - prev_high) / prev_high
        score      = 7.0 + min(gap_size * 100, 2.0)

    return {
        "direction":  direction,
        "score":      round(score, 2),
        "today_open": today_open,
        "prev_high":  round(prev_high, 2),
        "prev_low":   round(prev_low, 2),
        "reason":     f"oops_{direction or 'no_signal'}_open={today_open:.0f}",
    }


def run_williams_r_strategy(df, df_htf=None, option_data=None) -> dict:
    """Williams %R strategy for signal_engine."""
    try:
        r = williams_r_signal(df)
        return {"strategy": "williams_r", "score": r["score"],
                "direction": r["direction"], "reason": r.get("reason","")}
    except Exception as e:
        logger.debug("Williams %R: %s", e)
        return {"strategy": "williams_r", "score": 0.0, "direction": None}


def run_volatility_breakout_strategy(df, df_htf=None, option_data=None) -> dict:
    """Williams Volatility Breakout for signal_engine."""
    try:
        r = volatility_breakout_signal(df)
        return {"strategy": "volatility_breakout", "score": r["score"],
                "direction": r["direction"], "reason": r.get("reason","")}
    except Exception as e:
        logger.debug("Volatility breakout: %s", e)
        return {"strategy": "volatility_breakout", "score": 0.0, "direction": None}


def run_oops_strategy(df, df_htf=None, option_data=None) -> dict:
    """Williams OOPS pattern for signal_engine."""
    try:
        r = oops_signal(df)
        return {"strategy": "oops", "score": r["score"],
                "direction": r["direction"], "reason": r.get("reason","")}
    except Exception as e:
        logger.debug("OOPS: %s", e)
        return {"strategy": "oops", "score": 0.0, "direction": None}
