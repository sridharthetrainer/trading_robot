"""
supertrend_mtf_strategy.py

Multi-Timeframe Supertrend strategy.

Supertrend is already computed in indicators.py but never used in any
live strategy. This wires it into a dedicated MTF strategy.

Logic
-----
- Compute Supertrend on both 5-min (primary) and 15-min (HTF) data
- BUY  when: both TF Supertrend = BULLISH (+1)
- SELL when: both TF Supertrend = BEARISH (-1)
- HOLD when: timeframes disagree

Enhanced entries
---------------
- 5-min Supertrend just flipped (prev=-1, now=+1) + 15-min=+1 → strongest BUY
- 5-min RSI > 50 for BUY, < 50 for SELL → adds confidence
- MACD histogram alignment → adds confidence

This is particularly powerful on NIFTY/BANKNIFTY where trends persist
longer than on individual stocks due to index liquidity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd

from indicators import calculate_supertrend, calculate_rsi, calculate_macd

logger = logging.getLogger(__name__)

ST_PERIOD     = 10
ST_MULTIPLIER = 3.0
CONF_BASE     = 0.54
CONF_MAX      = 0.88


def _safe(s: pd.Series, default: float = 0.0) -> float:
    try:
        v = s.iloc[-1]
        return float(v) if pd.notna(v) else default
    except Exception:
        return default


def supertrend_mtf_signal(
    df: pd.DataFrame,
    df_htf: pd.DataFrame,
    period:     int   = ST_PERIOD,
    multiplier: float = ST_MULTIPLIER,
) -> Dict[str, Any]:
    """
    Compute multi-timeframe Supertrend signal.

    Parameters
    ----------
    df     : 5-minute OHLCV DataFrame
    df_htf : 15-minute (or higher) OHLCV DataFrame

    Returns signal dict with action, strategy, confidence, reason, indicators.
    """
    _hold = {"action": "HOLD", "strategy": "supertrend_mtf",
             "confidence": 0.0, "reason": "no_signal", "indicators": {}}

    try:
        if df is None or len(df) < period + 5:
            return {**_hold, "reason": "insufficient_5m_data"}

        # 5-min Supertrend
        _, st_dir_5m = calculate_supertrend(df, period=period, multiplier=multiplier)
        dir_now  = int(_safe(st_dir_5m, 0))
        dir_prev = int(st_dir_5m.iloc[-2]) if len(st_dir_5m) >= 2 else dir_now

        # HTF Supertrend
        htf_dir = 0
        if df_htf is not None and len(df_htf) >= period + 3:
            try:
                _, st_dir_htf = calculate_supertrend(df_htf, period=period, multiplier=multiplier)
                htf_dir = int(_safe(st_dir_htf, 0))
            except Exception:
                htf_dir = dir_now   # fallback: use 5m direction

        # Both timeframes must agree
        if dir_now == 0 or htf_dir == 0:
            return {**_hold, "reason": "supertrend_not_computed"}

        if dir_now != htf_dir:
            return {**_hold, "reason": f"mtf_disagreement_5m={dir_now}_htf={htf_dir}"}

        action = "BUY" if dir_now == 1 else "SELL"

        # Confirmation indicators
        rsi_s    = calculate_rsi(df, 14)
        current_rsi = _safe(rsi_s, 50.0)

        _, _, macd_hist_s = calculate_macd(df)
        current_hist = _safe(macd_hist_s, 0.0)

        # Confidence
        conf = CONF_BASE

        # Fresh flip on 5-min = strongest entry timing
        just_flipped = (dir_prev != dir_now)
        if just_flipped:
            conf += 0.12

        # RSI in right zone
        if action == "BUY" and current_rsi > 50:
            conf += 0.08 * min(1.0, (current_rsi - 50) / 20)
        elif action == "SELL" and current_rsi < 50:
            conf += 0.08 * min(1.0, (50 - current_rsi) / 20)

        # MACD histogram alignment
        if action == "BUY" and current_hist > 0:
            conf += 0.06
        elif action == "SELL" and current_hist < 0:
            conf += 0.06

        conf = round(min(conf, CONF_MAX), 4)

        return {
            "action":     action,
            "strategy":   "supertrend_mtf",
            "confidence": conf,
            "reason":     f"st_mtf_5m={dir_now}_htf={htf_dir}_flip={just_flipped}",
            "indicators": {
                "st_dir_5m":  dir_now,
                "st_dir_htf": htf_dir,
                "just_flipped": just_flipped,
                "rsi":        round(current_rsi, 2),
                "macd_hist":  round(current_hist, 4),
            },
        }

    except Exception as exc:
        logger.exception("supertrend_mtf_signal failed: %s", exc)
        return {**_hold, "reason": f"error:{exc}"}
