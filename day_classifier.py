"""
day_classifier.py

Institutional Day Type Classifier.

Every professional trading desk classifies the day type by 10:00 AM.
Without this, trend strategies are applied to range days and vice versa —
the single biggest cause of strategy losses.

Day Types
─────────
TREND_DAY    (25% of days) — BUY breakouts, add on pullbacks, never fade
RANGE_DAY    (45% of days) — SELL at resistance, BUY at support, fade moves
REVERSAL_DAY (15% of days) — Fade the opening move after reversal confirms
VOLATILE_DAY (15% of days) — NO NEW TRADES. Reduce size on existing.

The ATM Straddle Range
──────────────────────
The options market prices in the expected daily range through the ATM straddle.
CE + PE at the same ATM strike = market's consensus on today's range.

If straddle = 300 points, market expects NIFTY to stay within ±300.
If NIFTY actually moves 500, the straddle buyer profits.
If NIFTY moves only 150, the straddle seller profits (IV crush).

Institutional rule: Only buy options when you expect move > 1.5× straddle.
Otherwise sell the straddle and collect premium.

Cross-Index Confirmation
────────────────────────
Institutions NEVER trade NIFTY without checking BANKNIFTY.
When NIFTY goes up but BANKNIFTY stays flat → weak move (banking stocks not participating)
When both indices move together → strong institutional buying across sectors
Divergence = warning signal. Convergence = confirmation signal.

Scale-In Model
──────────────
Full size on first signal is RETAIL behavior.
Institutions enter in 3 tranches:
    Tranche 1 (50%): On signal confirmation
    Tranche 2 (33%): On pullback to VWAP / key level (adds conviction)
    Tranche 3 (17%): Only if T2 target reached and trend confirmed
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Day type constants ────────────────────────────────────────────────────────
DAY_TREND    = "TREND_DAY"
DAY_RANGE    = "RANGE_DAY"
DAY_REVERSAL = "REVERSAL_DAY"
DAY_VOLATILE = "VOLATILE_DAY"
DAY_UNKNOWN  = "UNKNOWN"

# ── Detection thresholds ──────────────────────────────────────────────────────
ADX_TREND_THRESHOLD    = 22.0   # ADX > 22 by 10 AM = likely trend day
VIX_VOLATILE_THRESHOLD = 20.0   # VIX > 20 = volatile day
VWAP_CROSS_RANGE_MIN   = 3      # crossed VWAP ≥ 3 times = range day
REVERSAL_FILL_PCT      = 0.70   # gap filled > 70% within 30 min = reversal day
STRADDLE_BUY_MULT      = 1.5    # only buy options if expected move > 1.5× straddle


@dataclass
class DayProfile:
    """Complete day-type profile built by 10:00 AM."""
    day_type:            str   = DAY_UNKNOWN
    confidence:          float = 0.0

    # Straddle-based range
    atm_straddle:        float = 0.0   # CE + PE premium at ATM strike
    expected_range_pts:  float = 0.0   # ± this many points from open
    upper_expected:      float = 0.0   # open + expected_range
    lower_expected:      float = 0.0   # open - expected_range
    ok_to_buy_options:   bool  = True  # False when straddle too expensive

    # Opening gap
    gap_pct:             float = 0.0   # opening gap vs previous close
    gap_direction:       str   = ""    # "UP", "DOWN", "FLAT"

    # VWAP analysis
    vwap_slope:          float = 0.0   # positive = bullish, negative = bearish
    vwap_crosses:        int   = 0     # how many times price crossed VWAP

    # Cross-index confirmation
    nifty_direction:     str   = ""    # "UP", "DOWN", "FLAT"
    banknifty_direction: str   = ""    # "UP", "DOWN", "FLAT"
    divergence:          bool  = False # True = indices disagree (weaker signal)

    # Strategy weights for this day type
    strategy_multipliers: Dict[str, float] = field(default_factory=dict)

    # Detected by
    detected_at:         str   = ""    # time when classified

    def to_dict(self) -> Dict[str, Any]:
        return {
            "day_type":           self.day_type,
            "confidence":         round(self.confidence, 3),
            "atm_straddle":       round(self.atm_straddle, 2),
            "expected_range":     round(self.expected_range_pts, 0),
            "ok_to_buy_options":  self.ok_to_buy_options,
            "gap_pct":            round(self.gap_pct, 4),
            "gap_direction":      self.gap_direction,
            "vwap_crosses":       self.vwap_crosses,
            "divergence":         self.divergence,
            "nifty_vs_banknifty": f"{self.nifty_direction}/{self.banknifty_direction}",
            "detected_at":        self.detected_at,
        }


# ── Strategy multipliers per day type ────────────────────────────────────────
_DAY_STRATEGY_MULTS = {
    DAY_TREND: {
        "trend":               2.0,   # trend is king on trend days
        "breakout":            1.8,
        "hour_orb":            1.8,
        "orb":                 1.5,
        "market_structure":    1.8,
        "supertrend_mtf":      1.6,
        "liquidity_sweep":     1.4,
        "mean_reversion":      0.2,   # DANGEROUS on trend days
        "vwap_reversion":      0.3,   # dangerous on trend days
        "vpoc_magnet":         0.5,
        "institutional_scalp": 1.2,
        "order_block":         1.5,
        "scalping":            0.7,
        "ma_cross":            1.4,
    },
    DAY_RANGE: {
        "mean_reversion":      2.0,   # range day is MR territory
        "vwap_reversion":      1.8,
        "vpoc_magnet":         1.8,
        "iron_condor":         2.0,   # sell the range
        "bull_put_spread":     1.6,
        "trend":               0.3,   # trend strategies fail on range days
        "breakout":            0.3,
        "hour_orb":            0.4,
        "orb":                 0.5,
        "market_structure":    0.5,
        "liquidity_sweep":     1.2,
        "institutional_scalp": 1.4,
        "scalping":            1.2,
        "order_block":         1.3,
    },
    DAY_REVERSAL: {
        "mean_reversion":      1.6,   # fade the opening move
        "vwap_reversion":      1.8,
        "liquidity_sweep":     1.8,   # sweeps common on reversal days
        "order_block":         1.5,
        "trend":               0.4,   # don't trend-trade reversals
        "breakout":            0.3,
        "institutional_scalp": 1.3,
        "scalping":            1.0,
    },
    DAY_VOLATILE: {
        # All multipliers < 1 — reduce all trades on volatile days
        k: 0.0 for k in [
            "trend", "mean_reversion", "breakout", "scalping",
            "ma_cross", "orb", "hour_orb", "vwap_reversion",
            "supertrend_mtf", "institutional_scalp", "order_block",
            "liquidity_sweep", "vpoc_magnet", "market_structure",
            "iron_condor", "bull_put_spread",
        ]
    },
    DAY_UNKNOWN: {k: 1.0 for k in [
        "trend", "mean_reversion", "breakout", "scalping", "ma_cross",
        "orb", "hour_orb", "vwap_reversion", "supertrend_mtf",
        "institutional_scalp", "order_block", "liquidity_sweep",
        "vpoc_magnet", "market_structure",
    ]},
}


class DayClassifier:
    """
    Classifies the trading day type by 10:00 AM and provides
    strategy multipliers, straddle-based option entry filter,
    and cross-index divergence detection.

    Refreshes classification every 30 minutes during market hours.
    """

    def __init__(self) -> None:
        self._profile:      DayProfile = DayProfile()
        self._last_updated: float      = 0.0
        self._update_interval: float   = 1800  # 30 min

        # State for day detection
        self._opening_price:  float = 0.0
        self._prev_close:     float = 0.0
        self._vwap_crosses:   int   = 0
        self._last_side:      str   = ""  # was price above or below VWAP last bar?

    # ── Main public API ───────────────────────────────────────────────────────

    def get_profile(
        self,
        df_nifty:    pd.DataFrame,
        df_banknifty: Optional[pd.DataFrame] = None,
        vix:          float = 0.0,
        atm_straddle: float = 0.0,
        open_price:   float = 0.0,
        prev_close:   float = 0.0,
        force:        bool  = False,
    ) -> DayProfile:
        """
        Get or refresh the day profile.
        Returns the cached profile if within update interval.
        """
        now_t = datetime.now().time()

        # Only classify during market hours
        if not (dtime(9, 30) <= now_t <= dtime(15, 15)):
            if self._profile.day_type == DAY_UNKNOWN:
                # Return defaults outside market hours
                return self._profile
            return self._profile

        if force or (time.time() - self._last_updated) > self._update_interval:
            self._profile = self._classify(
                df_nifty=df_nifty,
                df_banknifty=df_banknifty,
                vix=vix,
                atm_straddle=atm_straddle,
                open_price=open_price,
                prev_close=prev_close,
            )
            self._last_updated = time.time()

        return self._profile

    def get_strategy_multiplier(self, strategy: str) -> float:
        """Return score multiplier for strategy given today's day type."""
        day_mults = _DAY_STRATEGY_MULTS.get(
            self._profile.day_type, _DAY_STRATEGY_MULTS[DAY_UNKNOWN]
        )
        return day_mults.get(strategy.lower(), 1.0)

    def ok_to_buy_options(self) -> bool:
        """
        True only when buying options makes sense:
        - Not a volatile day
        - Expected move > 1.5× straddle premium (otherwise sell straddle)
        - IV rank < 70% (not overpriced)
        """
        return self._profile.ok_to_buy_options

    def has_divergence(self) -> bool:
        """True when NIFTY and BANKNIFTY are moving in different directions."""
        return self._profile.divergence

    def get_expected_range(self) -> float:
        """ATM straddle premium as expected daily range in points."""
        return self._profile.expected_range_pts

    def is_volatile_day(self) -> bool:
        return self._profile.day_type == DAY_VOLATILE

    # ── Classification engine ─────────────────────────────────────────────────

    def _classify(
        self,
        df_nifty:     pd.DataFrame,
        df_banknifty: Optional[pd.DataFrame],
        vix:          float,
        atm_straddle: float,
        open_price:   float,
        prev_close:   float,
    ) -> DayProfile:
        profile = DayProfile()
        profile.detected_at = datetime.now().strftime("%H:%M")

        try:
            if df_nifty is None or len(df_nifty) < 6:
                return profile

            close_col = "Close" if "Close" in df_nifty.columns else "close"
            high_col  = "High"  if "High"  in df_nifty.columns else "high"
            low_col   = "Low"   if "Low"   in df_nifty.columns else "low"

            closes = pd.to_numeric(df_nifty[close_col], errors="coerce")
            highs  = pd.to_numeric(df_nifty[high_col],  errors="coerce")
            lows   = pd.to_numeric(df_nifty[low_col],   errors="coerce")

            current_price = float(closes.iloc[-1])
            _open         = float(open_price or closes.iloc[0])
            _prev         = float(prev_close or closes.iloc[0] * 0.999)

            # ── Straddle-based range ──────────────────────────────────────────
            profile.atm_straddle    = float(atm_straddle)
            profile.expected_range_pts = float(atm_straddle)
            profile.upper_expected  = _open + atm_straddle
            profile.lower_expected  = _open - atm_straddle

            # Calculate actual move so far
            session_high = float(highs.max())
            session_low  = float(lows.min())
            actual_range = session_high - session_low

            # Buy options only if we expect move > 1.5× straddle remaining
            remaining_potential = atm_straddle - (actual_range / 2)
            profile.ok_to_buy_options = (
                remaining_potential > atm_straddle * 0.3   # still 30%+ of range left
                and vix < 20
                and atm_straddle > 0
            )

            # ── Gap analysis ──────────────────────────────────────────────────
            if _prev > 0:
                gap = (_open - _prev) / _prev
                profile.gap_pct      = round(gap, 4)
                profile.gap_direction = "UP" if gap > 0.002 else "DOWN" if gap < -0.002 else "FLAT"

            # ── Volatile day check (highest priority) ─────────────────────────
            if vix > VIX_VOLATILE_THRESHOLD:
                profile.day_type   = DAY_VOLATILE
                profile.confidence = 0.90
                profile.strategy_multipliers = _DAY_STRATEGY_MULTS[DAY_VOLATILE]
                logger.info("Day classified as VOLATILE (VIX=%.1f)", vix)
                return profile

            # ── VWAP analysis ─────────────────────────────────────────────────
            try:
                from indicators import calculate_vwap, calculate_adx
                vwap_s = calculate_vwap(df_nifty)
                vwap_v = float(vwap_s.iloc[-1]) if pd.notna(vwap_s.iloc[-1]) else current_price

                # Count VWAP crosses
                crosses = 0
                prev_above = None
                for i in range(len(vwap_s)):
                    try:
                        p = float(closes.iloc[i])
                        w = float(vwap_s.iloc[i])
                        above = p > w
                        if prev_above is not None and above != prev_above:
                            crosses += 1
                        prev_above = above
                    except Exception:
                        pass
                profile.vwap_crosses = crosses

                # VWAP slope (trend of VWAP)
                if len(vwap_s) >= 6:
                    vwap_slope = float(vwap_s.iloc[-1] - vwap_s.iloc[-6])
                    profile.vwap_slope = round(vwap_slope, 2)
                else:
                    vwap_slope = 0.0

                # ADX
                adx_s   = calculate_adx(df_nifty, 14)
                cur_adx = float(adx_s.iloc[-1]) if pd.notna(adx_s.iloc[-1]) else 0.0

            except Exception:
                crosses    = 0
                vwap_slope = 0.0
                cur_adx    = 0.0

            # ── Reversal day detection ────────────────────────────────────────
            if profile.gap_direction in ("UP", "DOWN") and abs(profile.gap_pct) > 0.003:
                gap_size  = abs(profile.gap_pct) * _open
                fill_back = (
                    (session_low - _open) / gap_size if profile.gap_direction == "UP"
                    else (_open - session_high) / gap_size
                )
                if fill_back >= REVERSAL_FILL_PCT:
                    profile.day_type   = DAY_REVERSAL
                    profile.confidence = 0.75
                    profile.strategy_multipliers = _DAY_STRATEGY_MULTS[DAY_REVERSAL]
                    logger.info("Day classified as REVERSAL (gap=%.2f%% fill=%.0f%%)",
                                profile.gap_pct * 100, fill_back * 100)
                    # Still set cross-index before returning
                    self._set_cross_index(profile, df_banknifty, closes)
                    return profile

            # ── Trend vs Range classification ─────────────────────────────────
            trend_score = 0

            if cur_adx > ADX_TREND_THRESHOLD:
                trend_score += 2
            if abs(vwap_slope) > current_price * 0.0005:
                trend_score += 1
            if crosses <= 1:   # barely crossed VWAP = trending
                trend_score += 2
            elif crosses >= VWAP_CROSS_RANGE_MIN:
                trend_score -= 2  # many crosses = ranging

            if trend_score >= 3:
                profile.day_type   = DAY_TREND
                profile.confidence = min(0.85, 0.55 + trend_score * 0.07)
            else:
                profile.day_type   = DAY_RANGE
                profile.confidence = min(0.80, 0.55 + (3 - max(trend_score, 0)) * 0.07)

            profile.strategy_multipliers = _DAY_STRATEGY_MULTS[profile.day_type]
            logger.info(
                "Day classified as %s (conf=%.2f adx=%.1f vwap_crosses=%d)",
                profile.day_type, profile.confidence, cur_adx, crosses,
            )

            # ── Cross-index correlation ───────────────────────────────────────
            self._set_cross_index(profile, df_banknifty, closes)

        except Exception as exc:
            logger.debug("DayClassifier._classify error: %s", exc)

        return profile

    def _set_cross_index(
        self,
        profile:      DayProfile,
        df_banknifty: Optional[pd.DataFrame],
        nifty_closes: pd.Series,
    ) -> None:
        """Detect NIFTY vs BANKNIFTY divergence."""
        try:
            if len(nifty_closes) >= 6:
                n_ret = float(nifty_closes.iloc[-1] - nifty_closes.iloc[-6]) / float(nifty_closes.iloc[-6])
                profile.nifty_direction = "UP" if n_ret > 0.001 else "DOWN" if n_ret < -0.001 else "FLAT"
            else:
                profile.nifty_direction = "FLAT"

            if df_banknifty is not None and len(df_banknifty) >= 6:
                bnf_col = "Close" if "Close" in df_banknifty.columns else "close"
                bnf_c   = pd.to_numeric(df_banknifty[bnf_col], errors="coerce")
                b_ret   = float(bnf_c.iloc[-1] - bnf_c.iloc[-6]) / float(bnf_c.iloc[-6])
                profile.banknifty_direction = "UP" if b_ret > 0.001 else "DOWN" if b_ret < -0.001 else "FLAT"
            else:
                profile.banknifty_direction = profile.nifty_direction

            # Divergence: one up, one down
            profile.divergence = (
                profile.nifty_direction != "FLAT"
                and profile.banknifty_direction != "FLAT"
                and profile.nifty_direction != profile.banknifty_direction
            )

            if profile.divergence:
                logger.info(
                    "INDEX DIVERGENCE: NIFTY=%s BANKNIFTY=%s — weaker signals",
                    profile.nifty_direction, profile.banknifty_direction,
                )

        except Exception as exc:
            logger.debug("_set_cross_index error: %s", exc)


# ── Module singleton ──────────────────────────────────────────────────────────
_classifier: Optional[DayClassifier] = None


def get_day_classifier() -> DayClassifier:
    global _classifier
    if _classifier is None:
        _classifier = DayClassifier()
    return _classifier
