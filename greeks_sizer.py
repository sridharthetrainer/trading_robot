"""
greeks_sizer.py

Greeks-based and IV-based position sizing.

PROBLEM WITH CURRENT SIZING
─────────────────────────────
Current: "Risk 1% of ₹1,00,000 = ₹1,000 per trade"
  22000CE at delta 0.40 → moves ₹30 per 1% NIFTY move
  22500CE at delta 0.25 → moves ₹19 per 1% NIFTY move
  Same % risk, very different outcome sensitivity.

SOLUTION: SIZE BY DELTA
─────────────────────────
Target: "I want ₹50 P&L per 1% NIFTY move"
  22000CE delta=0.40, spot=22000:
    ₹50 / (0.40 × 22000 × 0.01) = ₹50 / 88 = 0.57 lots → 1 lot
  22500CE delta=0.25, spot=22000:
    ₹50 / (0.25 × 22000 × 0.01) = ₹50 / 55 = 0.91 lots → 1 lot
  Much more consistent.

IV-BASED SIZING
─────────────────
High IV (VIX>18): options overpriced → buy fewer lots
Low IV (VIX<13):  options cheap → can buy more lots for same risk

Usage:
  sizer = GreeksSizer()
  lots = sizer.size_by_delta(
      delta=0.40, spot=22000, target_pnl_per_pct=50, lot_size=75
  )
  lots = sizer.size_by_iv(lots, vix=20.0)
"""
from __future__ import annotations
import logging, math
from typing import Optional

logger = logging.getLogger(__name__)


class GreeksSizer:
    """
    Position sizing using option Greeks and IV.
    Use alongside existing AdaptivePositionSizer.
    """

    def __init__(
        self,
        target_pnl_per_pct: float = 50.0,    # ₹50 P&L per 1% underlying move
        max_lots:           int   = 10,
        min_lots:           int   = 1,
        lot_size:           int   = 75,
    ) -> None:
        self.target_pnl_per_pct = target_pnl_per_pct
        self.max_lots           = max_lots
        self.min_lots           = min_lots
        self.lot_size           = lot_size

    def size_by_delta(
        self,
        delta:      float,   # option delta (0-1)
        spot:       float,   # underlying spot price
        lot_size:   int = 0,
        target:     float = 0.0,
    ) -> int:
        """
        Calculate lots so that P&L = target_pnl_per_pct on 1% spot move.

        Formula:
          P&L per lot per 1% = delta × spot × 0.01 × lot_size
          lots = target_pnl / (delta × spot × 0.01 × lot_size)
        """
        lot = lot_size or self.lot_size
        tgt = target or self.target_pnl_per_pct
        delta = abs(delta)
        if delta < 0.01 or spot < 100:
            return self.min_lots

        pnl_per_lot = delta * spot * 0.01 * lot
        if pnl_per_lot <= 0:
            return self.min_lots

        raw_lots = tgt / pnl_per_lot
        lots     = max(self.min_lots, min(self.max_lots, round(raw_lots)))
        logger.debug(
            "Delta sizing: delta=%.2f spot=%.0f → pnl_per_lot=₹%.0f → %d lots",
            delta, spot, pnl_per_lot, lots
        )
        return lots

    def size_by_iv(self, base_lots: int, vix: float) -> int:
        """
        Adjust lot count based on current IV (VIX).
        High IV = options expensive = buy fewer. Low IV = buy more.

        VIX < 12:   1.3× lots (options cheap)
        VIX 12-15:  1.0× (normal)
        VIX 15-20:  0.8× (slightly expensive)
        VIX 20-25:  0.6× (expensive, reduce)
        VIX > 25:   0.4× (very expensive, minimal exposure)
        """
        if vix < 12:    mult = 1.3
        elif vix < 15:  mult = 1.0
        elif vix < 20:  mult = 0.8
        elif vix < 25:  mult = 0.6
        else:           mult = 0.4

        adjusted = max(1, min(self.max_lots, round(base_lots * mult)))
        if adjusted != base_lots:
            logger.debug(
                "IV sizing: VIX=%.1f mult=%.1f %d→%d lots",
                vix, mult, base_lots, adjusted
            )
        return adjusted

    def size_combined(
        self,
        delta:      float,
        spot:       float,
        vix:        float,
        lot_size:   int   = 0,
        confidence: float = 1.0,
    ) -> int:
        """
        Combined sizing: delta-based → IV-adjusted → confidence-scaled.

        confidence: 0.5-1.0 from AI filter (higher = more lots)
        """
        lots = self.size_by_delta(delta, spot, lot_size)
        lots = self.size_by_iv(lots, vix)
        # Confidence scaling: 0.5 conf = 70% of lots, 1.0 conf = 100%
        conf_mult = 0.7 + 0.3 * min(confidence, 1.0)
        lots      = max(1, min(self.max_lots, round(lots * conf_mult)))
        return lots

    def theta_daily_cost(
        self,
        theta:    float,   # option theta (negative)
        qty:      int,     # total qty (lots × lot_size)
    ) -> float:
        """Daily theta cost for a position in rupees."""
        return abs(theta) * qty

    def expected_move_range(
        self,
        spot:   float,
        vix:    float,
        days:   float = 1.0,
    ) -> tuple:
        """
        Expected 1-sigma price range based on current IV.
        Returns (lower, upper) price range.
        """
        daily_vol = (vix / 100) / math.sqrt(252) * math.sqrt(days)
        move      = spot * daily_vol
        return (round(spot - move, 2), round(spot + move, 2))

    def is_option_fairly_priced(
        self,
        premium: float,
        delta:   float,
        spot:    float,
        dte:     int,
        vix:     float,
    ) -> dict:
        """
        Basic sanity check: is this option premium reasonable?
        Returns {"fair": bool, "reason": str, "expected_range": tuple}
        """
        lo, hi = self.expected_move_range(spot, vix, dte)
        move   = hi - spot

        # For ATM option: intrinsic ≈ 0, time value ≈ move × delta
        expected_premium = move * abs(delta) * 0.7   # 0.7 = rough adjustment
        overpriced_by    = (premium - expected_premium) / max(expected_premium, 1)

        if overpriced_by > 0.5:
            return {
                "fair":   False,
                "reason": f"overpriced_{overpriced_by:.0%}_vs_expected_₹{expected_premium:.0f}",
                "expected": expected_premium,
            }
        return {
            "fair":    True,
            "reason":  f"fair_premium_₹{premium:.0f}_expected_₹{expected_premium:.0f}",
            "expected": expected_premium,
        }


_sizer: Optional[GreeksSizer] = None
def get_greeks_sizer(**kwargs) -> GreeksSizer:
    global _sizer
    if _sizer is None:
        _sizer = GreeksSizer(**kwargs)
    return _sizer
