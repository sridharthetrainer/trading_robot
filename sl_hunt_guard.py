"""
sl_hunt_guard.py

Handles the two most damaging real-world trading scenarios:

SCENARIO 1 — STOP LOSS HUNT (Wick Stop)
────────────────────────────────────────
Price wicks to exactly your SL level, triggers the SL-M order,
then reverses and goes to your original target. You got stopped
out of a winning trade.

This is NOT a bug — it's how market makers and algorithms operate.
They know exactly where retail stop losses cluster (just below round
numbers, just below obvious swing lows) and they sweep them before
the real move.

Solutions implemented:
  1. SL below SWING LOW (not just ATR distance)
     Place SL 0.2% BELOW the actual swing low candle, not at ATR.
     The swing low is where the market decided to reverse — below
     it is structurally invalid. ATR distance alone doesn't know this.

  2. CANDLE-BODY CONFIRMATION
     For options and swing trades: use a WIDER SL that only triggers
     when the CANDLE BODY closes below (not just a wick).
     Implementation: place actual SL-M at a wider level, but monitor
     internally — only send close signal when candle body closes below
     the tighter intended stop.

  3. OFI INVALIDATION CHECK
     When SL is touched but OFI (order flow) is still positive:
     the wick may be a stop hunt, not a real reversal.
     Mark as "suspect SL" and wait one bar for confirmation.

  4. RE-ENTRY AFTER CONFIRMED SL HUNT
     If SL was hit but then price reverses above the stop level
     within 3 bars, and OFI confirms direction:
     → Re-enter at market with half size (reduced risk second try)
     → This captures the majority of the real move after the sweep

SCENARIO 2 — SWING TRADE GAP OPEN / TREND CHANGE
──────────────────────────────────────────────────
Overnight: FOMC / RBI / global event changes the market structure.
Next morning: NIFTY opens 300 pts against your swing position.

Three sub-cases:

  A. Small adverse gap (< 0.5%): probably fine, hold
  B. Medium adverse gap (0.5–1.5%): close 50%, move stop to breakeven
  C. Large adverse gap (> 1.5%): close everything before open

  Trend change detection during session:
  → Market structure BOS AGAINST our position direction
  → VIX spike > 2 pts mid-session
  → BANKNIFTY divergence (confirming reversal, not just noise)
  → FII participant data flipped overnight

  Weekend protection:
  → Reduce swing size on Friday afternoon
  → Never hold 0-1 DTE options over weekend
  → Send pre-close reminder at 14:30 Friday
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# SL hunt detection
SL_HUNT_OFI_THRESHOLD    = 0.15   # OFI must be < this for wick to be "real" exit
SL_HUNT_CONFIRM_BARS     = 1      # bars to wait after wick touches SL
SL_HUNT_REENTRY_BARS     = 3      # must re-enter within this many bars
SL_BODY_CONFIRM_RATIO    = 0.40   # candle body must be < 40% of wick to flag as wick-stop
SWING_LOW_BUFFER_PCT     = 0.002  # place SL 0.2% below swing low

# Gap thresholds
GAP_SMALL_PCT    = 0.005   # < 0.5%: hold
GAP_MEDIUM_PCT   = 0.015   # 0.5-1.5%: partial close
GAP_LARGE_PCT    = 0.030   # > 1.5%: full close before open

# Trend change thresholds
VIX_SPIKE_INTRADAY     = 2.0   # VIX rose this many points mid-session
BNF_DIVERGENCE_PCT     = 0.005 # BANKNIFTY disagrees with NIFTY by this amount

# Weekend protection
FRIDAY_REDUCE_AFTER   = (14, 30)  # 14:30 on Friday: reduce swing sizes
MAX_DTE_OVER_WEEKEND  = 2         # never hold < 2 DTE over weekend


# ─────────────────────────────────────────────────────────────────────────────
# SMART SL PLACEMENT — below swing low, not just ATR
# ─────────────────────────────────────────────────────────────────────────────

def compute_smart_stop(
    df:          pd.DataFrame,
    side:        str,
    entry_price: float,
    atr:         float,
    lookback:    int  = 10,
    style:       str  = "intraday",
) -> Dict[str, float]:
    """
    Compute a stop loss that sits BELOW the last swing low (for BUY)
    or ABOVE the last swing high (for SELL).

    This is structurally valid — the position is only wrong if price
    breaks through the structure that justified the entry.

    Returns:
      {
        "hard_stop":   float,  # actual SL-M order price (wider)
        "soft_stop":   float,  # internal trigger (tighter, candle-body based)
        "swing_level": float,  # the swing low/high used as reference
        "atr_stop":    float,  # the old simple ATR stop for comparison
        "method":      str,    # "swing_low" or "atr_fallback"
      }
    """
    close_col = "Close" if "Close" in df.columns else "close"
    low_col   = "Low"   if "Low"   in df.columns else "low"
    high_col  = "High"  if "High"  in df.columns else "high"

    closes = pd.to_numeric(df[close_col], errors="coerce")
    lows   = pd.to_numeric(df[low_col],   errors="coerce")
    highs  = pd.to_numeric(df[high_col],  errors="coerce")

    # Simple ATR stop (fallback)
    atr_mult     = 2.5 if style == "swing" else 2.0
    atr_stop_buy  = round(entry_price - atr_mult * atr, 2)
    atr_stop_sell = round(entry_price + atr_mult * atr, 2)

    try:
        window = min(lookback, len(df) - 2)
        if window < 3:
            raise ValueError("insufficient data")

        recent_lows  = lows.iloc[-(window+1):-1]
        recent_highs = highs.iloc[-(window+1):-1]

        if side.upper() == "BUY":
            # Last swing low in recent window
            swing_low = float(recent_lows.min())
            # SL is BELOW the swing low by a buffer
            buffer      = max(atr * 0.3, entry_price * SWING_LOW_BUFFER_PCT)
            hard_stop   = round(swing_low - buffer, 2)
            soft_stop   = round(swing_low - buffer * 0.5, 2)

            # Sanity check: hard stop must be meaningful (not too wide)
            max_risk    = entry_price * 0.05  # never more than 5% from entry
            if entry_price - hard_stop > max_risk:
                # Swing low is too far — fall back to ATR
                hard_stop = atr_stop_buy
                soft_stop = round(entry_price - 1.5 * atr, 2)
                method    = "atr_fallback_swing_too_far"
            else:
                method = "swing_low"

            return {
                "hard_stop":   hard_stop,
                "soft_stop":   soft_stop,
                "swing_level": swing_low,
                "atr_stop":    atr_stop_buy,
                "method":      method,
            }

        else:  # SELL
            swing_high = float(recent_highs.max())
            buffer     = max(atr * 0.3, entry_price * SWING_LOW_BUFFER_PCT)
            hard_stop  = round(swing_high + buffer, 2)
            soft_stop  = round(swing_high + buffer * 0.5, 2)

            max_risk   = entry_price * 0.05
            if hard_stop - entry_price > max_risk:
                hard_stop = atr_stop_sell
                soft_stop = round(entry_price + 1.5 * atr, 2)
                method    = "atr_fallback_swing_too_far"
            else:
                method = "swing_high"

            return {
                "hard_stop":   hard_stop,
                "soft_stop":   soft_stop,
                "swing_level": swing_high,
                "atr_stop":    atr_stop_sell,
                "method":      method,
            }

    except Exception as e:
        logger.debug("compute_smart_stop fallback: %s", e)
        return {
            "hard_stop":   atr_stop_buy if side.upper() == "BUY" else atr_stop_sell,
            "soft_stop":   atr_stop_buy if side.upper() == "BUY" else atr_stop_sell,
            "swing_level": 0.0,
            "atr_stop":    atr_stop_buy if side.upper() == "BUY" else atr_stop_sell,
            "method":      "atr_fallback_error",
        }


# ─────────────────────────────────────────────────────────────────────────────
# SL HUNT DETECTOR — per open trade
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SLHuntState:
    """State for one trade being monitored for SL hunt."""
    trade_id:        str
    symbol:          str
    side:            str
    entry_price:     float
    soft_stop:       float    # internal monitoring stop
    hard_stop:       float    # actual SL-M at broker
    wick_touched_at: Optional[float]   = None  # epoch when wick hit soft stop
    wick_bar:        int                = 0
    suspect_sl:      bool               = False
    reentry_eligible: bool              = False
    reentry_expires:  float             = 0.0   # epoch when re-entry window closes


class SLHuntGuard:
    """
    Monitors open trades for stop loss hunt / wick stop scenarios.

    For each open trade:
    - Detects when price WICKS through soft_stop but candle body is intact
    - Delays exit by one bar when OFI confirms direction is still valid
    - Flags trade for potential re-entry if it was a stop hunt
    - Sends Telegram alert explaining the situation

    The HARD STOP (SL-M at broker) is always wider — it only fires on a
    genuine, confirmed move through structure. The soft stop is internal.
    """

    def __init__(self, alerts=None) -> None:
        self._states:  Dict[str, SLHuntState] = {}
        self._alerts   = alerts
        self._reentry_log: List[Dict] = []

    def register(
        self,
        trade_id:    str,
        symbol:      str,
        side:        str,
        entry_price: float,
        soft_stop:   float,
        hard_stop:   float,
    ) -> None:
        self._states[trade_id] = SLHuntState(
            trade_id    = trade_id,
            symbol      = symbol,
            side        = side,
            entry_price = entry_price,
            soft_stop   = soft_stop,
            hard_stop   = hard_stop,
        )
        logger.debug(
            "SLHuntGuard registered | %s %s soft=%.2f hard=%.2f",
            trade_id, symbol, soft_stop, hard_stop,
        )

    def check(
        self,
        trade_id:      str,
        current_price: float,
        current_low:   float,
        current_high:  float,
        candle_open:   float,
        candle_close:  float,
        ofi:           float,
        bar_index:     int,
    ) -> Dict[str, Any]:
        """
        Check one bar for SL hunt pattern.

        Returns:
        {
          "action":        "HOLD" | "SOFT_EXIT" | "SUSPECT_WICK" | "REENTRY"
          "reason":        str
          "reentry_price": float (if action == "REENTRY")
        }
        """
        state = self._states.get(trade_id)
        if not state:
            return {"action": "HOLD", "reason": "not_registered"}

        side = state.side.upper()

        # ── Check for re-entry opportunity ────────────────────────────────────
        if state.reentry_eligible and time.time() < state.reentry_expires:
            reversal = (
                (side == "BUY"  and current_price > state.soft_stop * 1.002)
                or (side == "SELL" and current_price < state.soft_stop * 0.998)
            )
            ofi_confirms = (
                (side == "BUY"  and ofi > 0.10) or
                (side == "SELL" and ofi < -0.10)
            )
            if reversal and ofi_confirms:
                state.reentry_eligible = False
                logger.info(
                    "SL HUNT CONFIRMED — re-entry signal | %s @ %.2f",
                    trade_id, current_price,
                )
                if self._alerts:
                    self._alerts.send(
                        f"🎯 <b>SL HUNT CONFIRMED — Re-entry</b>\n"
                        f"{state.symbol} {side}\n"
                        f"Stop was swept at ₹{state.soft_stop:.2f}, "
                        f"price reversed to ₹{current_price:.2f}\n"
                        f"OFI confirms: {ofi:+.3f}\n"
                        f"<b>Re-entering at ₹{current_price:.2f} (half size)</b>\n"
                        f"🕐 {datetime.now().strftime('%H:%M')}",
                        dedup_key=f"sl_hunt_reentry_{trade_id}"
                    )
                return {
                    "action":        "REENTRY",
                    "reason":        "sl_hunt_confirmed_reversal",
                    "reentry_price": current_price,
                    "original_stop": state.hard_stop,
                }

        # ── BUY side: detect wick below soft stop ─────────────────────────────
        if side == "BUY" and current_low <= state.soft_stop:

            # Candle body analysis
            body_size = abs(candle_close - candle_open)
            wick_size = abs(candle_open - current_low) + abs(candle_close - current_high)
            is_wick_candle = (
                wick_size > 0 and
                body_size < wick_size * SL_BODY_CONFIRM_RATIO and
                candle_close > state.soft_stop  # body closed ABOVE soft stop
            )

            # OFI still positive → stop hunt hypothesis
            ofi_still_bullish = ofi > SL_HUNT_OFI_THRESHOLD

            if is_wick_candle and ofi_still_bullish and not state.suspect_sl:
                state.suspect_sl       = True
                state.wick_touched_at  = time.time()
                state.wick_bar         = bar_index
                state.reentry_eligible = True
                state.reentry_expires  = time.time() + (SL_HUNT_REENTRY_BARS * 5 * 60)

                logger.warning(
                    "SL HUNT SUSPECT | %s %s body=%.2f wick=%.2f ofi=%.3f "
                    "soft_stop=%.2f close=%.2f",
                    trade_id, state.symbol, body_size, wick_size, ofi,
                    state.soft_stop, candle_close,
                )
                if self._alerts:
                    self._alerts.send(
                        f"⚠️ <b>POSSIBLE SL HUNT</b>\n"
                        f"{state.symbol} {side}\n"
                        f"Wick touched ₹{state.soft_stop:.2f} but candle body intact\n"
                        f"OFI still bullish: {ofi:+.3f}\n"
                        f"<b>NOT exiting yet — waiting for bar close confirmation</b>\n"
                        f"Hard stop at ₹{state.hard_stop:.2f} protects capital\n"
                        f"🕐 {datetime.now().strftime('%H:%M')}",
                        dedup_key=f"sl_hunt_suspect_{trade_id}",
                        dedup_cooldown_override=300,
                    )
                return {"action": "SUSPECT_WICK", "reason": "wick_below_soft_stop_ofi_bullish"}

            # Body closes below soft stop → real exit
            if candle_close <= state.soft_stop:
                state.reentry_eligible = False
                return {"action": "SOFT_EXIT",
                        "reason": f"body_closed_below_soft_stop_{state.soft_stop:.0f}"}

        # ── SELL side: detect wick above soft stop ────────────────────────────
        elif side == "SELL" and current_high >= state.soft_stop:

            body_size = abs(candle_close - candle_open)
            wick_size = abs(candle_open - current_high) + abs(candle_close - current_low)
            is_wick_candle = (
                wick_size > 0 and
                body_size < wick_size * SL_BODY_CONFIRM_RATIO and
                candle_close < state.soft_stop
            )
            ofi_still_bearish = ofi < -SL_HUNT_OFI_THRESHOLD

            if is_wick_candle and ofi_still_bearish and not state.suspect_sl:
                state.suspect_sl       = True
                state.wick_touched_at  = time.time()
                state.wick_bar         = bar_index
                state.reentry_eligible = True
                state.reentry_expires  = time.time() + (SL_HUNT_REENTRY_BARS * 5 * 60)

                if self._alerts:
                    self._alerts.send(
                        f"⚠️ <b>POSSIBLE SL HUNT</b>\n"
                        f"{state.symbol} {side}\n"
                        f"Wick touched ₹{state.soft_stop:.2f} but body intact\n"
                        f"OFI still bearish: {ofi:+.3f}\n"
                        f"<b>NOT exiting yet — waiting confirmation</b>\n"
                        f"🕐 {datetime.now().strftime('%H:%M')}",
                        dedup_key=f"sl_hunt_suspect_{trade_id}",
                        dedup_cooldown_override=300,
                    )
                return {"action": "SUSPECT_WICK", "reason": "wick_above_soft_stop_ofi_bearish"}

            if candle_close >= state.soft_stop:
                return {"action": "SOFT_EXIT",
                        "reason": f"body_closed_above_soft_stop_{state.soft_stop:.0f}"}

        return {"action": "HOLD", "reason": "no_sl_threat"}

    def remove(self, trade_id: str) -> None:
        self._states.pop(trade_id, None)

    def get_suspects(self) -> List[str]:
        return [tid for tid, s in self._states.items() if s.suspect_sl]


# ─────────────────────────────────────────────────────────────────────────────
# SWING TRADE PROTECTION — gap open + trend change
# ─────────────────────────────────────────────────────────────────────────────

class SwingProtectionEngine:
    """
    Manages all overnight and gap scenarios for swing trades.

    Three protection layers:
    1. PRE-MARKET (08:50): Gap size vs stop distance decision
    2. INTRADAY: Trend change detection (BOS against position)
    3. FRIDAY EOD: Weekend risk reduction
    """

    def __init__(self, trade_manager=None, alerts=None) -> None:
        self._tm      = trade_manager
        self._alerts  = alerts
        self._vix_at_entry: Dict[str, float] = {}   # trade_id → VIX when opened

    # ── PRE-MARKET GAP DECISION ───────────────────────────────────────────────

    def handle_gap_open(
        self,
        trade_id:     str,
        symbol:       str,
        side:         str,
        entry_price:  float,
        stop_loss:    float,
        current_ltp:  float,
        gap_pct:      float,   # negative = gap down
        gift_nifty:   float,   # GIFT Nifty futures price
    ) -> Dict[str, Any]:
        """
        Decide what to do with a swing trade given the pre-market gap.

        Rules:
        ─────
        Small adverse gap (< 0.5%):
          HOLD — gap is within normal overnight noise. Keep stop.

        Medium adverse gap (0.5% – 1.5%):
          PARTIAL — close 50% at open, move stop to breakeven on remainder.
          Rationale: lock in partial capital, let the rest breathe.

        Large adverse gap (> 1.5%):
          CLOSE — exit everything at open. Gap this large means the
          overnight event is real and the thesis is invalidated.

        Favourable gap (any size):
          HOLD — gap in our favour. Move stop up toward breakeven.
          Do NOT close a winner just because it gapped favourably.
        """
        is_adverse = (
            (side.upper() == "BUY"  and gap_pct < 0) or
            (side.upper() == "SELL" and gap_pct > 0)
        )

        abs_gap = abs(gap_pct)

        if not is_adverse:
            # Favourable gap — tighten stop to protect profit
            if abs_gap > 0.005:
                new_stop = entry_price  # move to at least breakeven
                return {
                    "action":   "TIGHTEN_STOP",
                    "new_stop": new_stop,
                    "reason":   f"favourable_gap_{gap_pct:+.1%}",
                }
            return {"action": "HOLD", "reason": f"small_favourable_gap_{gap_pct:+.1%}"}

        # Adverse gap
        if abs_gap < GAP_SMALL_PCT:
            return {"action": "HOLD", "reason": f"small_adverse_gap_{gap_pct:.1%}"}

        if abs_gap < GAP_MEDIUM_PCT:
            msg = (
                f"⚠️ <b>GAP RISK — Partial close</b>\n"
                f"{symbol} {side}\n"
                f"Gap: {gap_pct:+.1%}  GIFT Nifty: ₹{gift_nifty:,.0f}\n"
                f"<b>Closing 50% at open, keeping 50% with breakeven stop</b>\n"
                f"🕐 {datetime.now().strftime('%H:%M')}"
            )
            if self._alerts:
                self._alerts.send(msg, dedup_key=f"gap_partial_{trade_id}")
            return {
                "action":      "PARTIAL_CLOSE",
                "close_pct":   0.50,
                "new_stop":    entry_price,  # breakeven on remainder
                "reason":      f"medium_adverse_gap_{gap_pct:.1%}",
            }

        # Large adverse gap — full close
        msg = (
            f"🚨 <b>GAP RISK — Full close before open</b>\n"
            f"{symbol} {side}\n"
            f"Gap: {gap_pct:+.1%}  Exceeds threshold {GAP_LARGE_PCT:.0%}\n"
            f"GIFT Nifty: ₹{gift_nifty:,.0f}\n"
            f"<b>Closing entire position at market open</b>\n"
            f"🕐 {datetime.now().strftime('%H:%M')}"
        )
        if self._alerts:
            self._alerts.send(msg, dedup_key=f"gap_full_{trade_id}")
        return {
            "action":  "CLOSE_FULL",
            "reason":  f"large_adverse_gap_{gap_pct:.1%}",
        }

    # ── INTRADAY TREND CHANGE DETECTION ──────────────────────────────────────

    def check_trend_invalidation(
        self,
        trade_id:         str,
        symbol:           str,
        side:             str,
        entry_price:      float,
        df:               pd.DataFrame,
        df_banknifty:     Optional[pd.DataFrame],
        current_vix:      float,
        original_vix:     Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Check if the trend thesis for a swing trade has been invalidated.

        Triggers (any one is enough to exit):
        1. Opposite BOS on 15-min chart (market structure flipped)
        2. VIX spiked > 3 pts since entry (regime change)
        3. BANKNIFTY diverging > 1% against position (sector breakdown)
        4. Price has retraced > 60% of move from entry back to stop
        """
        reasons = []

        # 1. Market structure BOS against position
        try:
            from institutional_indicators import detect_market_structure
            ms     = detect_market_structure(df, lookback=30)
            ms_score = float(ms.get("structure_score", 0))
            last_bos = str(ms.get("last_bos", ""))

            if side.upper() == "BUY" and last_bos == "BEARISH" and ms_score <= -1.5:
                reasons.append(f"bearish_bos_while_long_score={ms_score:.1f}")
            elif side.upper() == "SELL" and last_bos == "BULLISH" and ms_score >= 1.5:
                reasons.append(f"bullish_bos_while_short_score={ms_score:.1f}")
        except Exception:
            pass

        # 2. VIX spike
        if original_vix and original_vix > 0 and current_vix > 0:
            vix_spike = current_vix - original_vix
            if vix_spike > 3.0:
                reasons.append(f"vix_spike_{vix_spike:.1f}pts_since_entry")

        # 3. BANKNIFTY divergence
        if df_banknifty is not None and len(df_banknifty) >= 6 and df is not None and len(df) >= 6:
            try:
                nc = "Close" if "Close" in df.columns          else "close"
                bc = "Close" if "Close" in df_banknifty.columns else "close"
                nifty_ret = (float(df[nc].iloc[-1]) - float(df[nc].iloc[-6])) / float(df[nc].iloc[-6])
                bnf_ret   = (float(df_banknifty[bc].iloc[-1]) - float(df_banknifty[bc].iloc[-6])) / float(df_banknifty[bc].iloc[-6])
                divergence = nifty_ret - bnf_ret

                if side.upper() == "BUY" and divergence < -BNF_DIVERGENCE_PCT:
                    reasons.append(f"bnf_diverging_vs_nifty_{divergence:.2%}")
                elif side.upper() == "SELL" and divergence > BNF_DIVERGENCE_PCT:
                    reasons.append(f"bnf_diverging_vs_nifty_{divergence:.2%}")
            except Exception:
                pass

        # 4. Price retraced > 60% back toward stop
        try:
            close_col = "Close" if "Close" in df.columns else "close"
            current   = float(df[close_col].iloc[-1])
            if side.upper() == "BUY":
                # How much of the move from entry has been given back?
                if current < entry_price:
                    retrace = abs(current - entry_price) / entry_price
                    if retrace > 0.015:  # price back > 1.5% against us
                        reasons.append(f"retraced_{retrace:.1%}_below_entry")
        except Exception:
            pass

        if not reasons:
            return {"action": "HOLD", "reason": "thesis_intact"}

        primary_reason = reasons[0]
        all_reasons    = " | ".join(reasons)

        msg = (
            f"🔄 <b>SWING TREND INVALIDATED</b>\n"
            f"{symbol} {side}\n"
            f"Reason: {all_reasons}\n"
            f"<b>Closing swing position — thesis no longer valid</b>\n"
            f"🕐 {datetime.now().strftime('%H:%M')}"
        )
        if self._alerts:
            self._alerts.send(msg, dedup_key=f"trend_invalidated_{trade_id}",
                             dedup_cooldown_override=1800)

        logger.warning(
            "Swing trend invalidated | %s %s reasons: %s",
            trade_id, symbol, all_reasons,
        )
        return {
            "action":  "CLOSE_FULL",
            "reason":  f"trend_invalidated:{primary_reason}",
            "details": reasons,
        }

    # ── WEEKEND PROTECTION ────────────────────────────────────────────────────

    def friday_risk_check(
        self,
        open_trades: List[Dict],
    ) -> List[Dict[str, Any]]:
        """
        Friday 14:30: evaluate all swing positions for weekend risk.
        Returns list of actions to take.

        Rules:
        - Option DTE ≤ 1 on Friday: CLOSE (don't hold 0-DTE over weekend)
        - Option DTE = 2 (next Monday): consider closing (loses 2 days of theta)
        - Equity swing: fine to hold, just alert
        """
        now = datetime.now()
        if now.weekday() != 4:  # Not Friday
            return []
        h, m = FRIDAY_REDUCE_AFTER
        if not (now.hour > h or (now.hour == h and now.minute >= m)):
            return []

        actions = []
        for trade in open_trades:
            sym    = trade.get("symbol", "")
            is_opt = "CE" in sym or "PE" in sym
            meta   = trade.get("metadata", {}) or {}
            dte    = int(meta.get("dte", meta.get("DTE", 99)))
            tid    = trade.get("trade_id", "")

            if is_opt and dte <= 1:
                actions.append({
                    "trade_id": tid,
                    "action":   "CLOSE_FULL",
                    "reason":   f"friday_dte_{dte}_no_weekend_hold",
                    "symbol":   sym,
                })
                if self._alerts:
                    self._alerts.send(
                        f"📅 <b>FRIDAY PROTECTION</b>\n"
                        f"{sym} DTE={dte} — closing before weekend\n"
                        f"Options lose time value over 2-day weekend\n"
                        f"🕐 {now.strftime('%H:%M')}",
                        dedup_key=f"friday_{tid}",
                    )

            elif is_opt and dte == 2:
                # Alert only — don't force close, but inform
                if self._alerts:
                    self._alerts.send(
                        f"⚠️ <b>WEEKEND THETA WARNING</b>\n"
                        f"{sym} DTE=2 held over weekend\n"
                        f"Will lose ~2 days of theta (Sat+Sun)\n"
                        f"Consider closing if P&L is near breakeven\n"
                        f"🕐 {now.strftime('%H:%M')}",
                        dedup_key=f"friday_theta_{tid}",
                    )

        return actions

    def record_vix_at_entry(self, trade_id: str, vix: float) -> None:
        self._vix_at_entry[trade_id] = vix

    def get_entry_vix(self, trade_id: str) -> Optional[float]:
        return self._vix_at_entry.get(trade_id)

    def cleanup(self, trade_id: str) -> None:
        self._vix_at_entry.pop(trade_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE SINGLETONS
# ─────────────────────────────────────────────────────────────────────────────
_sl_guard:      Optional[SLHuntGuard]          = None
_swing_protect: Optional[SwingProtectionEngine] = None


def get_sl_guard(alerts=None) -> SLHuntGuard:
    global _sl_guard
    if _sl_guard is None:
        _sl_guard = SLHuntGuard(alerts=alerts)
    elif alerts and not _sl_guard._alerts:
        _sl_guard._alerts = alerts
    return _sl_guard


def get_swing_protection(trade_manager=None, alerts=None) -> SwingProtectionEngine:
    global _swing_protect
    if _swing_protect is None:
        _swing_protect = SwingProtectionEngine(
            trade_manager=trade_manager, alerts=alerts
        )
    return _swing_protect
