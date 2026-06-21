"""
exit_decision.py — end-of-day / lifecycle decision for an open trade.

Answers the question the trader actually faces: carry to next day, close now, or
tighten/book? Rules are grounded in real options trade management (theta + gap
risk overnight, don't carry losers into expiry, protect profits), not a guess.

Pure logic; the caller supplies P&L, days-to-expiry and (optionally) candles for
a trend read. Returns {action, reason, urgency}.
  action  : HOLD | CLOSE | TIGHTEN
  urgency : low | medium | high
"""

from __future__ import annotations

from typing import Dict, Optional
import pandas as pd


def _trend_dir(df: Optional[pd.DataFrame], is_long: bool) -> int:
    """+1 if trend agrees with the position, -1 if against, 0 unknown."""
    try:
        if df is None or len(df) < 20:
            return 0
        from indicators import calculate_supertrend
        _, d = calculate_supertrend(df, 10, 3.0)
        dd = d.dropna()
        if dd.empty:
            return 0
        up = float(dd.iloc[-1]) > 0
        return 1 if (up == is_long) else -1
    except Exception:
        return 0


def _decide(action: str, reason: str, urgency: str = "medium") -> Dict:
    return {"action": action, "reason": reason, "urgency": urgency}


def eod_recommendation(
    side: str,
    entry: float,
    ltp: float,
    pnl_pct: float,
    dte: Optional[int],
    df: Optional[pd.DataFrame] = None,
    is_option: bool = True,
) -> Dict:
    """Recommend HOLD / CLOSE / TIGHTEN for an open trade near the close."""
    is_long  = str(side).upper() in ("BUY", "LONG")
    trend    = _trend_dir(df, is_long)
    in_profit = pnl_pct > 0.5
    losing    = pnl_pct < -0.5
    big_loss  = pnl_pct <= -25

    # 1. Expiry pressure — options decay hardest in the last day; gap risk too.
    if is_option and dte is not None and dte <= 1:
        if in_profit:
            return _decide("CLOSE",
                "In profit with expiry ~1 day away — book it; overnight theta + "
                "gap can wipe the gain.", "high")
        return _decide("CLOSE",
            "Losing option into expiry — theta will keep eroding it overnight; "
            "cut rather than hope.", "high")

    # 2. Loss management — don't carry a losing trade the trend has turned against.
    if big_loss:
        return _decide("CLOSE",
            "Loss beyond the plan — cut and reset.", "high")
    if losing and trend < 0:
        return _decide("CLOSE",
            "At a loss and the trend has turned against the position — cut, "
            "don't average hope.", "high")

    # 3. Protect profits near the close.
    if in_profit:
        if trend > 0 and (dte is None or dte > 2):
            return _decide("HOLD",
                "In profit, trend intact, time left — hold with the trailing SL "
                "doing the work.", "low")
        return _decide("TIGHTEN",
            "In profit but momentum/time fading — tighten the SL or book partial "
            "so it can't round-trip to a loss.", "medium")

    # 4. Small loss, time left, trend not clearly against.
    if dte is None or dte > 2:
        return _decide("HOLD",
            "Small loss, time left, trend not against — hold with the SL in place.",
            "low")
    return _decide("CLOSE",
        "Losing with little time left — cut before expiry decay accelerates.",
        "medium")
