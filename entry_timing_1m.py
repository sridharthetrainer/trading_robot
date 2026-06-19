"""
entry_timing_1m.py

1-minute entry timing refinement for index strategies.

HOW IT WORKS
─────────────
Step 1:  5-min strategy fires a signal (BUY/SELL on NIFTY)
Step 2:  Instead of entering immediately at 5-min bar close,
         wait for a 1-min pullback entry trigger
Step 3:  Entry trigger = first 1-min bar that closes in signal direction
         after price touches a key level (VWAP, EMA, prior bar low/high)
Step 4:  Stop-loss placed at 1-min bar low (BUY) or high (SELL)
         This is TIGHTER than a 5-min bar stop — better risk/reward

EXAMPLE
────────
5-min BUY signal on NIFTY at 10:05 (bar closed at 22,500)
1-min chart at 10:05: price is at 22,510 (above entry ideally)
Wait for 1-min pullback to 22,480-22,490 (near 1m EMA or VWAP)
First 1-min green bar after pullback = ENTRY at 22,488
Stop = 1-min bar low = 22,475  (₹13 stop vs ₹25-30 on 5m)
Same signal, same direction — but 40% tighter stop

ONLY FOR INDICES
─────────────────
4 symbols × 2 API calls = 8 calls = 3 seconds. Fast enough.
200 stocks × 2 calls = 400 calls = 133 seconds. Too slow.
1m entry timing is ONLY used for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY.
Stocks continue with 5m bar entry.

TIMEOUT
────────
If no 1m entry trigger fires within 10 minutes of 5m signal,
the system either:
  a) Enters at market (if price hasn't moved >1% away), or
  b) Cancels the signal (if price moved too far — opportunity gone)
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
ENTRY_TIMEOUT_MIN  = 10     # cancel if no 1m trigger within 10 minutes
MAX_PRICE_DRIFT    = 0.008  # cancel if price drifted >0.8% from signal price


def get_1m_entry(
    symbol:       str,
    signal_side:  str,       # "BUY" or "SELL"
    signal_price: float,     # 5m bar close price when signal fired
    df_1m:        pd.DataFrame,   # 1-min OHLCV dataframe (last 30 bars)
    signal_time:  Optional[datetime] = None,
) -> dict:
    """
    Analyse 1-min chart to find optimal entry after a 5-min signal.

    Returns:
        {
          "enter":        True/False,
          "entry_price":  float,
          "stop_loss":    float,
          "reason":       str,
          "improvement":  float,  # ₹ saved vs 5m bar entry
        }
    """
    result = {
        "enter":       False,
        "entry_price": signal_price,
        "stop_loss":   0.0,
        "reason":      "no_trigger",
        "improvement": 0.0,
    }

    if symbol.upper() not in INDEX_SYMBOLS:
        # For stocks: enter at signal price directly (no 1m refinement)
        result["enter"]  = True
        result["reason"] = "stock_direct_entry"
        return result

    if df_1m is None or len(df_1m) < 5:
        result["enter"]  = True
        result["reason"] = "no_1m_data_direct_entry"
        return result

    # Normalise column names
    df = df_1m.copy()
    df.columns = [c.lower() for c in df.columns]
    if "close" not in df.columns:
        result["enter"]  = True
        result["reason"] = "column_error_direct"
        return result

    last   = df.iloc[-1]
    prev   = df.iloc[-2] if len(df) > 1 else last
    price  = float(last["close"])

    # Check timeout — if signal is old, enter directly or cancel
    if signal_time:
        age_min = (datetime.now() - signal_time).total_seconds() / 60
        if age_min > ENTRY_TIMEOUT_MIN:
            drift = abs(price - signal_price) / signal_price
            if drift > MAX_PRICE_DRIFT:
                result["reason"] = f"timeout_price_drifted_{drift:.1%}"
                return result   # cancel — opportunity gone
            else:
                result["enter"]       = True
                result["entry_price"] = price
                result["reason"]      = "timeout_enter_market"
                return result

    # Price drift check — if already moved too far, don't chase
    current_drift = (price - signal_price) / signal_price
    if signal_side == "BUY" and current_drift > MAX_PRICE_DRIFT:
        result["reason"] = f"price_ran_too_far_{current_drift:.1%}_above_signal"
        return result
    if signal_side == "SELL" and current_drift < -MAX_PRICE_DRIFT:
        result["reason"] = f"price_ran_too_far_{abs(current_drift):.1%}_below_signal"
        return result

    # ── 1m Entry Triggers ────────────────────────────────────────────────────

    # Calculate 1m EMA9 and VWAP for key levels
    ema9  = _ema(df["close"], 9)
    vwap  = _vwap(df) if all(c in df.columns for c in ["high","low","close","volume"]) else None

    lo  = float(last["low"]  if "low"  in df.columns else price * 0.999)
    hi  = float(last["high"] if "high" in df.columns else price * 1.001)
    p_lo = float(prev["low"]  if "low"  in df.columns else price * 0.999)
    p_hi = float(prev["high"] if "high" in df.columns else price * 1.001)

    if signal_side == "BUY":
        # Trigger: last 1m bar is GREEN (close > open) and above EMA9
        is_green     = float(last.get("open", price)) < price
        above_ema    = price > ema9 if ema9 else True
        near_vwap    = abs(price - vwap) / vwap < 0.002 if vwap else False
        pullback_ok  = price <= signal_price * 1.003  # within 0.3% of signal

        if is_green and above_ema and pullback_ok:
            entry = price
            sl    = lo * 0.9995   # 1m bar low as stop
            improvement = signal_price - entry   # ₹ saved (negative = paid more)
            result.update({
                "enter":       True,
                "entry_price": round(entry, 2),
                "stop_loss":   round(sl, 2),
                "reason":      "1m_green_bar_above_ema9_pullback",
                "improvement": round(improvement, 2),
            })
        elif near_vwap and is_green:
            entry = price
            sl    = lo * 0.9995
            result.update({
                "enter":       True,
                "entry_price": round(entry, 2),
                "stop_loss":   round(sl, 2),
                "reason":      "1m_vwap_bounce_green",
                "improvement": round(signal_price - entry, 2),
            })
        else:
            result["reason"] = (
                f"waiting_1m_pullback green={is_green} "
                f"ema={above_ema} drift={current_drift:.2%}"
            )

    elif signal_side == "SELL":
        # Trigger: last 1m bar is RED (close < open) and below EMA9
        is_red    = float(last.get("open", price)) > price
        below_ema = price < ema9 if ema9 else True
        near_vwap = abs(price - vwap) / vwap < 0.002 if vwap else False
        pullback_ok = price >= signal_price * 0.997

        if is_red and below_ema and pullback_ok:
            entry = price
            sl    = hi * 1.0005
            result.update({
                "enter":       True,
                "entry_price": round(entry, 2),
                "stop_loss":   round(sl, 2),
                "reason":      "1m_red_bar_below_ema9_pullback",
                "improvement": round(entry - signal_price, 2),
            })
        elif near_vwap and is_red:
            entry = price
            sl    = hi * 1.0005
            result.update({
                "enter":       True,
                "entry_price": round(entry, 2),
                "stop_loss":   round(sl, 2),
                "reason":      "1m_vwap_rejection_red",
                "improvement": round(entry - signal_price, 2),
            })
        else:
            result["reason"] = (
                f"waiting_1m_pullback red={is_red} "
                f"ema={below_ema} drift={current_drift:.2%}"
            )

    return result


def get_1m_stop(df_1m: pd.DataFrame, side: str, lookback: int = 3) -> float:
    """
    Calculate tighter stop-loss using 1-min chart.
    BUY:  lowest low of last `lookback` 1m bars
    SELL: highest high of last `lookback` 1m bars
    """
    if df_1m is None or len(df_1m) < lookback:
        return 0.0
    df = df_1m.copy()
    df.columns = [c.lower() for c in df.columns]
    try:
        if side == "BUY":
            return float(df["low"].iloc[-lookback:].min()) * 0.9995
        else:
            return float(df["high"].iloc[-lookback:].max()) * 1.0005
    except Exception:
        return 0.0


def _ema(series: pd.Series, period: int) -> Optional[float]:
    try:
        if len(series) < period:
            return None
        return float(series.ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return None


def _vwap(df: pd.DataFrame) -> Optional[float]:
    try:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vol     = df["volume"].replace(0, 1)
        return float((typical * vol).sum() / vol.sum())
    except Exception:
        return None
