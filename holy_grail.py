"""
holy_grail.py

Linda Raschke & Larry Connors — Street Smarts
The Holy Grail Setup

CONCEPT (from Chapter 2 of Street Smarts):
  The simplest high-probability setup in the book.

  Rule 1: ADX(14) must be above 30 → market is trending strongly
  Rule 2: Price pulls back to the 20-period EMA (not below it)
  Rule 3: First bar that closes back in trend direction = ENTRY

  That's it. Three rules. 70%+ historical win rate in trending markets.

WHY IT WORKS:
  ADX > 30 confirms the trend has real momentum behind it.
  The pullback to 20-EMA is institutions reloading their positions.
  When they are done reloading, the trend resumes — that is your entry.

  The pullback should be orderly (not a sharp reversal).
  If ADX is rising further during pullback → even stronger signal.

NSE APPLICATION:
  NIFTY trends strongly ~40% of sessions (ADX > 30 days).
  On those days, Holy Grail fires 1-3 times per session.
  Works on 5-min chart for intraday CE/PE entries.
  Works on daily chart for swing position entries.

ENTRY:
  BUY:  ADX > 30 + uptrend (close > 20-EMA) + pullback touched 20-EMA
        + first bar closing above the pullback low = BUY
  SELL: ADX > 30 + downtrend (close < 20-EMA) + pullback touched 20-EMA
        + first bar closing below the pullback high = SELL

STOP:
  Place stop at the low of the pullback (BUY) or high (SELL).
  Typically 10-25 NIFTY points = tight stop = good R:R
"""
from __future__ import annotations
import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

ADX_PERIOD      = 14
ADX_THRESHOLD   = 30    # minimum ADX for Holy Grail setup
EMA_PERIOD      = 20    # 20-period EMA (Raschke's standard)
PULLBACK_BARS   = 5     # look back this many bars for pullback to EMA
EMA_TOUCH_PCT   = 0.002  # price within 0.2% of EMA = "touching" it


def _calc_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    """Calculate ADX."""
    h = df["high"]; l = df["low"]; c = df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    up = h.diff(); dn = -l.diff()
    pos_dm = up.where((up > dn) & (up > 0), 0.0)
    neg_dm = dn.where((dn > up) & (dn > 0), 0.0)
    atr14  = tr.ewm(alpha=1/period, adjust=False).mean()
    pos_di = 100 * pos_dm.ewm(alpha=1/period, adjust=False).mean() / atr14.replace(0, 1)
    neg_di = 100 * neg_dm.ewm(alpha=1/period, adjust=False).mean() / atr14.replace(0, 1)
    dx     = (100 * (pos_di - neg_di).abs() / (pos_di + neg_di).replace(0, 1))
    adx    = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx


def holy_grail_signal(df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None) -> dict:
    """
    Detect Holy Grail setup.

    Returns:
        {
          "direction":   "BUY" | "SELL" | None,
          "score":       float,
          "adx":         float,
          "ema20":       float,
          "pullback_low": float,   # stop placement
          "reason":      str,
        }
    """
    empty = {"direction": None, "score": 0.0, "adx": 0.0, "ema20": 0.0,
             "pullback_low": 0.0, "reason": "no_holy_grail_setup"}

    if df is None or len(df) < ADX_PERIOD + EMA_PERIOD + 5:
        return empty

    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    if not all(c in df_c.columns for c in ["close", "high", "low"]):
        return empty

    close  = df_c["close"]
    high   = df_c["high"]
    low    = df_c["low"]

    # Calculate indicators
    ema20  = close.ewm(span=EMA_PERIOD, adjust=False).mean()
    adx    = _calc_adx(df_c)

    # Current values
    cur_close = float(close.iloc[-1])
    cur_adx   = float(adx.iloc[-1])
    cur_ema   = float(ema20.iloc[-1])
    prev_close= float(close.iloc[-2])
    adx_rising= cur_adx > float(adx.iloc[-2])

    # RULE 1: ADX must be above threshold
    if cur_adx < ADX_THRESHOLD:
        return {**empty, "reason": f"adx_{cur_adx:.1f}_below_{ADX_THRESHOLD}"}

    # Determine trend direction
    uptrend   = cur_close > cur_ema
    downtrend = cur_close < cur_ema

    # RULE 2: Look for pullback to 20-EMA in last PULLBACK_BARS bars
    recent_closes = close.iloc[-PULLBACK_BARS-1:-1].values
    recent_lows   = low.iloc[-PULLBACK_BARS-1:-1].values
    recent_highs  = high.iloc[-PULLBACK_BARS-1:-1].values
    recent_emas   = ema20.iloc[-PULLBACK_BARS-1:-1].values

    pullback_found = False
    pullback_low   = 0.0
    pullback_high  = 0.0

    if uptrend:
        # Look for bar(s) that touched the 20-EMA from above
        for i, (c_val, l_val, e_val) in enumerate(zip(recent_closes, recent_lows, recent_emas)):
            touch = abs(l_val - e_val) / e_val < EMA_TOUCH_PCT or \
                    (l_val <= e_val and c_val >= e_val)
            if touch:
                pullback_found = True
                pullback_low   = l_val
                break

    elif downtrend:
        # Look for bar(s) that touched the 20-EMA from below
        for i, (c_val, h_val, e_val) in enumerate(zip(recent_closes, recent_highs, recent_emas)):
            touch = abs(h_val - e_val) / e_val < EMA_TOUCH_PCT or \
                    (h_val >= e_val and c_val <= e_val)
            if touch:
                pullback_found = True
                pullback_high  = h_val
                break

    if not pullback_found:
        return {**empty, "reason": f"no_ema_pullback_found_adx={cur_adx:.1f}",
                "adx": cur_adx}

    # RULE 3: Current bar closes back in trend direction
    direction = None
    score     = 0.0

    if uptrend and cur_close > prev_close and cur_close > cur_ema:
        direction = "BUY"
        score     = 7.5
        # Bonus if ADX is still rising
        if adx_rising:
            score += 0.5
        # Bonus if price is clearly above EMA (strong resumption)
        clearance = (cur_close - cur_ema) / cur_ema
        if clearance > 0.001:
            score += 0.5

    elif downtrend and cur_close < prev_close and cur_close < cur_ema:
        direction = "SELL"
        score     = 7.5
        if adx_rising:
            score += 0.5
        clearance = (cur_ema - cur_close) / cur_ema
        if clearance > 0.001:
            score += 0.5

    if not direction:
        return {**empty, "reason": f"pullback_found_but_no_resumption_yet",
                "adx": cur_adx}

    return {
        "direction":   direction,
        "score":       round(min(score, 9.0), 2),
        "adx":         round(cur_adx, 1),
        "ema20":       round(cur_ema, 2),
        "pullback_low": round(pullback_low or pullback_high, 2),
        "reason":      (
            f"holy_grail_{direction.lower()}_adx={cur_adx:.1f}"
            f"_ema20={cur_ema:.0f}"
            f"{'_adx_rising' if adx_rising else ''}"
        ),
    }


def run_holy_grail_strategy(df, df_htf=None, option_data=None) -> dict:
    """Drop-in strategy for signal_engine STRATEGIES list."""
    try:
        result = holy_grail_signal(df, df_htf)
        return {
            "strategy":  "holy_grail",
            "score":     float(result.get("score", 0.0)),
            "direction": result.get("direction"),
            "reason":    result.get("reason", ""),
            "adx":       result.get("adx", 0.0),
        }
    except Exception as e:
        logger.debug("Holy Grail error: %s", e)
        return {"strategy": "holy_grail", "score": 0.0, "direction": None}
