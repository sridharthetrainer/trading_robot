"""
td_sequential.py

Thomas DeMark — The New Science of Technical Analysis
TD Sequential: Counting Price Exhaustion

CONCEPT:
  Markets don't trend forever. DeMark identified a systematic way
  to count how many bars a trend has run, and predict exhaustion.

  TD SETUP (Phase 1 — counting 9):
    Count 9 consecutive bars where each close > close 4 bars earlier (BUY setup)
    Count 9 consecutive bars where each close < close 4 bars earlier (SELL setup)
    When count reaches 9 = Setup complete = potential exhaustion warning

  TD COUNTDOWN (Phase 2 — counting 13):
    After Setup 9, continue counting: close must be ≤ low[2] (BUY countdown)
    When Countdown reaches 13 = Countdown complete = high-probability reversal

  TD SETUP ALONE (9) = warning, reduce size
  TD COUNTDOWN (13) = high-conviction reversal signal

WHY INSTITUTIONS WATCH IT:
  DeMark's indicators are used by:
  - Tudor Investment Corp (Paul Tudor Jones)
  - SAC Capital
  - Multiple Indian FII desks
  When DeMark 9 fires on NIFTY daily, it is often a turning point.

NSE APPLICATION:
  Run on NIFTY daily chart → exhaustion signals for swing entries
  Run on 5-min chart → intraday exhaustion for options entry
  
  DeMark 9 on 5-min: fires ~3-5 times per day on NIFTY
  DeMark 9 on daily:  fires ~2-3 times per month (major signals)
"""
from __future__ import annotations
import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

SETUP_COUNT    = 9    # bars to complete setup
COUNTDOWN_COUNT= 13   # bars to complete countdown


def calculate_td_sequential(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate TD Sequential Setup and Countdown.

    Adds columns:
      td_buy_setup:    int  — current buy setup count (0-9+)
      td_sell_setup:   int  — current sell setup count (0-9+)
      td_buy_9:        bool — buy setup completed (bearish reversal warning)
      td_sell_9:       bool — sell setup completed (bullish reversal warning)
      td_buy_13:       bool — buy countdown complete (strong reversal)
      td_sell_13:      bool — sell countdown complete (strong reversal)
      td_signal:       'BUY' | 'SELL' | None
      td_score:        float
    """
    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]

    if "close" not in df_c.columns or len(df_c) < 10:
        for col in ["td_buy_setup","td_sell_setup","td_buy_9","td_sell_9",
                    "td_buy_13","td_sell_13","td_signal","td_score"]:
            df_c[col] = 0 if "setup" in col else (False if col in ["td_buy_9","td_sell_9","td_buy_13","td_sell_13"] else None)
        return df_c

    close = df_c["close"].values
    high  = df_c["high"].values  if "high"  in df_c.columns else close
    low   = df_c["low"].values   if "low"   in df_c.columns else close
    n     = len(close)

    buy_setup  = [0] * n
    sell_setup = [0] * n
    buy_9      = [False] * n
    sell_9     = [False] * n
    buy_13     = [False] * n
    sell_13    = [False] * n

    # ── SETUP COUNTING ────────────────────────────────────────────────────────
    # Buy setup: each close < close[i-4]
    # Sell setup: each close > close[i-4]
    buy_count  = 0
    sell_count = 0

    for i in range(4, n):
        if close[i] < close[i-4]:
            buy_count  += 1
            sell_count  = 0
        elif close[i] > close[i-4]:
            sell_count += 1
            buy_count   = 0
        else:
            buy_count  = 0
            sell_count = 0

        buy_setup[i]  = min(buy_count,  SETUP_COUNT)
        sell_setup[i] = min(sell_count, SETUP_COUNT)

        if buy_count  == SETUP_COUNT:
            buy_9[i]  = True
        if sell_count == SETUP_COUNT:
            sell_9[i] = True

    # ── COUNTDOWN (simplified — bars after Setup 9) ───────────────────────────
    # Full DeMark countdown is complex; this is a simplified version:
    # After Buy Setup 9: count bars where close ≤ low[i-2]
    # After Sell Setup 9: count bars where close ≥ high[i-2]
    buy_cd_active  = False
    buy_cd_count   = 0
    sell_cd_active = False
    sell_cd_count  = 0

    for i in range(6, n):
        # Start countdown after Setup 9
        if buy_9[i]:
            buy_cd_active = True
            buy_cd_count  = 0
        if sell_9[i]:
            sell_cd_active = True
            sell_cd_count  = 0

        if buy_cd_active and i >= 2:
            if close[i] <= low[i-2]:
                buy_cd_count += 1
            if buy_cd_count >= COUNTDOWN_COUNT:
                buy_13[i]    = True
                buy_cd_active = False
                buy_cd_count  = 0

        if sell_cd_active and i >= 2:
            if close[i] >= high[i-2]:
                sell_cd_count += 1
            if sell_cd_count >= COUNTDOWN_COUNT:
                sell_13[i]     = True
                sell_cd_active = False
                sell_cd_count  = 0

    # ── Signal generation ─────────────────────────────────────────────────────
    signals = [None] * n
    scores  = [0.0]  * n

    for i in range(n):
        if buy_13[i]:
            signals[i] = "BUY"
            scores[i]  = 9.0   # strongest signal
        elif sell_13[i]:
            signals[i] = "SELL"
            scores[i]  = 9.0
        elif buy_9[i]:
            signals[i] = "BUY"   # setup complete = exhaustion of downtrend
            scores[i]  = 6.5
        elif sell_9[i]:
            signals[i] = "SELL"  # setup complete = exhaustion of uptrend
            scores[i]  = 6.5

    df_c["td_buy_setup"]  = buy_setup
    df_c["td_sell_setup"] = sell_setup
    df_c["td_buy_9"]      = buy_9
    df_c["td_sell_9"]     = sell_9
    df_c["td_buy_13"]     = buy_13
    df_c["td_sell_13"]    = sell_13
    df_c["td_signal"]     = signals
    df_c["td_score"]      = scores
    return df_c


def run_td_sequential_strategy(df, df_htf=None, option_data=None) -> dict:
    """Drop-in strategy for signal_engine STRATEGIES list."""
    try:
        if df is None or len(df) < SETUP_COUNT + 10:
            return {"strategy": "td_sequential", "score": 0.0, "direction": None}

        result    = calculate_td_sequential(df)
        last      = result.iloc[-1]
        direction = last.get("td_signal")
        score     = float(last.get("td_score", 0.0))

        if not direction or score <= 0:
            # Show count progress in debug
            buy_cnt  = int(last.get("td_buy_setup",  0))
            sell_cnt = int(last.get("td_sell_setup", 0))
            if buy_cnt >= 6 or sell_cnt >= 6:
                logger.debug("TD Sequential building: buy=%d sell=%d", buy_cnt, sell_cnt)
            return {"strategy": "td_sequential", "score": 0.0, "direction": None,
                    "reason": f"td_buy_count={buy_cnt}_sell_count={sell_cnt}"}

        is_13 = bool(last.get("td_buy_13") or last.get("td_sell_13"))
        return {
            "strategy":  "td_sequential",
            "score":     round(score, 2),
            "direction": direction,
            "reason":    (
                f"td_{'countdown_13' if is_13 else 'setup_9'}"
                f"_{direction.lower()}_exhaustion"
            ),
        }
    except Exception as e:
        logger.debug("TD Sequential error: %s", e)
        return {"strategy": "td_sequential", "score": 0.0, "direction": None}
