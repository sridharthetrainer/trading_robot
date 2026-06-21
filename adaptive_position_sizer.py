from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any
import math


@dataclass
class PositionSizingResult:
    lots: int
    quantity: int
    risk_amount: float
    stop_distance: float
    estimated_risk_per_lot: float
    confidence_scale: float
    regime_scale: float
    volatility_scale: float
    drawdown_scale: float
    final_risk_pct: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lots": self.lots,
            "quantity": self.quantity,
            "risk_amount": self.risk_amount,
            "stop_distance": self.stop_distance,
            "estimated_risk_per_lot": self.estimated_risk_per_lot,
            "confidence_scale": self.confidence_scale,
            "regime_scale": self.regime_scale,
            "volatility_scale": self.volatility_scale,
            "drawdown_scale": self.drawdown_scale,
            "final_risk_pct": self.final_risk_pct,
            "reason": self.reason,
        }


class AdaptivePositionSizer:
    """
    Production-friendly adaptive position sizing.

    Inputs considered:
    - account capital
    - base risk %
    - signal confidence / score
    - market regime
    - volatility / ATR
    - current drawdown
    - option lot size

    Main idea:
    risk_amount = capital * adjusted_risk_pct

    quantity is then derived from:
    estimated_risk_per_lot = stop_distance * lot_size

    lots = floor(risk_amount / estimated_risk_per_lot)
    """

    def __init__(
        self,
        min_risk_pct: float = 0.0025,
        max_risk_pct: float = 0.02,
        default_risk_pct: float = 0.01,
        min_lots: int = 1,
        max_lots: int = 20,
        option_lot_size: int = 65,
    ) -> None:
        self.min_risk_pct = float(min_risk_pct)
        self.max_risk_pct = float(max_risk_pct)
        self.default_risk_pct = float(default_risk_pct)
        self.min_lots = int(min_lots)
        self.max_lots = int(max_lots)
        self.option_lot_size = int(option_lot_size)

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _confidence_scale(
        self,
        confidence: Optional[float] = None,
        score: Optional[float] = None,
    ) -> float:
        """
        Convert confidence / score into risk multiplier.
        """
        conf = self._safe_float(confidence, -1.0)
        scr = self._safe_float(score, -1.0)

        if conf >= 0:
            if conf >= 0.85:
                return 1.25
            if conf >= 0.75:
                return 1.10
            if conf >= 0.65:
                return 1.00
            if conf >= 0.55:
                return 0.80
            return 0.60

        if scr >= 0:
            if scr >= 9:
                return 1.20
            if scr >= 7:
                return 1.05
            if scr >= 6:
                return 1.00
            if scr >= 5:
                return 0.80
            return 0.60

        return 1.00

    def _regime_scale(self, regime: Optional[str], strategy: Optional[str]) -> float:
        reg = str(regime or "").upper()
        strat = str(strategy or "").upper()

        if reg == "TREND":
            if strat in ("TREND", "BREAKOUT"):
                return 1.10
            if strat == "MEAN_REVERSION":
                return 0.75
            return 1.00

        if reg == "RANGE":
            if strat == "MEAN_REVERSION":
                return 1.05
            if strat in ("TREND", "BREAKOUT"):
                return 0.80
            return 0.95

        if reg in ("NO_TRADE", "SIDEWAYS", "UNCLEAR"):
            return 0.60

        return 1.00

    def _volatility_scale(
        self,
        price: Optional[float],
        atr: Optional[float],
    ) -> float:
        """
        Reduce size when ATR is too large relative to price.
        """
        px = self._safe_float(price, 0.0)
        atr_val = self._safe_float(atr, 0.0)

        if px <= 0 or atr_val <= 0:
            return 1.00

        atr_ratio = atr_val / px

        if atr_ratio >= 0.020:
            return 0.55
        if atr_ratio >= 0.015:
            return 0.70
        if atr_ratio >= 0.010:
            return 0.85
        if atr_ratio >= 0.005:
            return 1.00
        return 1.05

    def _drawdown_scale(
        self,
        current_equity: float,
        peak_equity: Optional[float],
    ) -> float:
        """
        Size down during drawdowns.
        """
        eq = self._safe_float(current_equity, 0.0)
        peak = self._safe_float(peak_equity, 0.0)

        if eq <= 0 or peak <= 0 or peak < eq:
            return 1.00

        dd = (peak - eq) / peak

        if dd >= 0.20:
            return 0.40
        if dd >= 0.15:
            return 0.55
        if dd >= 0.10:
            return 0.70
        if dd >= 0.05:
            return 0.85
        return 1.00

    def _estimate_stop_distance(
        self,
        entry_price: Optional[float],
        stop_loss: Optional[float],
        atr: Optional[float] = None,
        fallback_atr_mult: float = 1.2,
    ) -> float:
        entry = self._safe_float(entry_price, 0.0)
        sl = self._safe_float(stop_loss, 0.0)
        atr_val = self._safe_float(atr, 0.0)

        if entry > 0 and sl > 0:
            dist = abs(entry - sl)
            if dist > 0:
                return dist

        if atr_val > 0:
            return atr_val * fallback_atr_mult

        if entry > 0:
            return entry * 0.005  # 0.5% fallback

        return 0.0


    def size_by_delta(
        self,
        capital: float,
        delta: float,
        premium: float,
        lot_size: int,
        target_delta_exposure: Optional[float] = None,
        max_lots: Optional[int] = None,
        confidence: Optional[float] = None,
    ) -> PositionSizingResult:
        """
        Greeks-based position sizing using delta-equivalent exposure.

        Delta tells us how much the option moves per 1-point move in the
        underlying. Sizing by delta ensures consistent real risk regardless
        of which strike (OTM/ATM/ITM) is chosen.

        Target: delta_exposure = lots × lot_size × delta
        lots = TARGET_DELTA_EXPOSURE / (lot_size × delta)

        Parameters
        ----------
        capital               : current account balance
        delta                 : option delta (0.10 to 0.90 typically)
        premium               : current option premium (₹ per unit)
        lot_size              : lot size for the option
        target_delta_exposure : max delta units per trade (default: capital/50000)
        max_lots              : hard cap (overrides instance max_lots if set)
        confidence            : signal confidence → scales target exposure

        Example:
          capital=5,00,000  delta=0.45  lot_size=50
          target_delta = 500000/50000 = 10 delta units
          lots = floor(10 / (50 × 0.45)) = floor(10/22.5) = 0  → 1 lot minimum
        """
        cap  = self._safe_float(capital, 0.0)
        d    = max(0.01, abs(float(delta)))
        prem = self._safe_float(premium, 1.0)
        ls   = max(1, int(lot_size))
        ml   = int(max_lots or self.max_lots)

        # Default target: 1% of capital as delta exposure
        if target_delta_exposure is None:
            target_delta_exposure = cap * 0.01 / max(prem, 1.0)

        # Scale by confidence
        conf_scale = self._confidence_scale(confidence=confidence)
        effective_target = target_delta_exposure * conf_scale

        delta_per_lot = d * ls
        if delta_per_lot <= 0:
            raw_lots = 1
        else:
            raw_lots = max(1, int(effective_target / delta_per_lot))

        final_lots = max(self.min_lots, min(raw_lots, ml))
        quantity   = final_lots * ls
        risk_amount = final_lots * delta_per_lot * prem

        return PositionSizingResult(
            lots               = final_lots,
            quantity           = quantity,
            risk_amount        = round(risk_amount, 2),
            stop_distance      = 0.0,
            estimated_risk_per_lot = round(delta_per_lot * prem, 2),
            confidence_scale   = round(conf_scale, 4),
            regime_scale       = 1.0,
            volatility_scale   = 1.0,
            drawdown_scale     = 1.0,
            final_risk_pct     = round(risk_amount / max(cap, 1), 6),
            reason             = (
                f"delta_sizing: delta={d:.2f} target_delta={effective_target:.1f} "
                f"delta_per_lot={delta_per_lot:.1f} lots={final_lots}"
            ),
        )


    def kelly_lots(
        self,
        win_rate:   float,
        avg_win:    float,
        avg_loss:   float,
        capital:    float,
        lot_value:  float,
        half_kelly: bool = True,
    ) -> int:
        """
        Kelly Criterion position sizing.
        Kelly fraction = (win_rate × avg_win - loss_rate × avg_loss) / avg_win
        Half Kelly (safer) = full Kelly / 2

        Clamps to 1-5 lots max for safety.
        """
        if avg_win <= 0 or avg_loss <= 0 or lot_value <= 0:
            return 1
        loss_rate = 1.0 - win_rate
        kelly_f   = (win_rate * avg_win - loss_rate * avg_loss) / avg_win
        kelly_f   = max(0.0, min(kelly_f, 0.5))  # cap at 50%
        if half_kelly:
            kelly_f /= 2
        if capital <= 0 or lot_value <= 0: return 1
        lots = int((capital * kelly_f) / lot_value)
        return max(1, min(lots, 5))

    def size_position(
        self,
        capital: float,
        entry_price: Optional[float],
        stop_loss: Optional[float],
        confidence: Optional[float] = None,
        score: Optional[float] = None,
        regime: Optional[str] = None,
        strategy: Optional[str] = None,
        atr: Optional[float] = None,
        peak_equity: Optional[float] = None,
        lot_size: Optional[int] = None,
        base_risk_pct: Optional[float] = None,
        fallback_atr_mult: float = 1.2,
    ) -> PositionSizingResult:
        """
        Returns adaptive lot sizing result.
        """
        cap = self._safe_float(capital, 0.0)
        if cap <= 0:
            return PositionSizingResult(
                lots=0,
                quantity=0,
                risk_amount=0.0,
                stop_distance=0.0,
                estimated_risk_per_lot=0.0,
                confidence_scale=0.0,
                regime_scale=0.0,
                volatility_scale=0.0,
                drawdown_scale=0.0,
                final_risk_pct=0.0,
                reason="Invalid capital",
            )

        effective_lot_size = int(lot_size or self.option_lot_size)
        risk_pct = self._safe_float(base_risk_pct, self.default_risk_pct)
        risk_pct = self._clip(risk_pct, self.min_risk_pct, self.max_risk_pct)

        conf_scale = self._confidence_scale(confidence=confidence, score=score)
        reg_scale = self._regime_scale(regime=regime, strategy=strategy)
        vol_scale = self._volatility_scale(price=entry_price, atr=atr)
        dd_scale = self._drawdown_scale(current_equity=cap, peak_equity=peak_equity)

        adjusted_risk_pct = risk_pct * conf_scale * reg_scale * vol_scale * dd_scale
        adjusted_risk_pct = self._clip(adjusted_risk_pct, self.min_risk_pct, self.max_risk_pct)

        risk_amount = cap * adjusted_risk_pct
        stop_distance = self._estimate_stop_distance(
            entry_price=entry_price,
            stop_loss=stop_loss,
            atr=atr,
            fallback_atr_mult=fallback_atr_mult,
        )

        if stop_distance <= 0:
            return PositionSizingResult(
                lots=0,
                quantity=0,
                risk_amount=round(risk_amount, 2),
                stop_distance=0.0,
                estimated_risk_per_lot=0.0,
                confidence_scale=conf_scale,
                regime_scale=reg_scale,
                volatility_scale=vol_scale,
                drawdown_scale=dd_scale,
                final_risk_pct=adjusted_risk_pct,
                reason="Invalid stop distance",
            )

        estimated_risk_per_lot = stop_distance * effective_lot_size
        if estimated_risk_per_lot <= 0:
            return PositionSizingResult(
                lots=0,
                quantity=0,
                risk_amount=round(risk_amount, 2),
                stop_distance=round(stop_distance, 4),
                estimated_risk_per_lot=0.0,
                confidence_scale=conf_scale,
                regime_scale=reg_scale,
                volatility_scale=vol_scale,
                drawdown_scale=dd_scale,
                final_risk_pct=adjusted_risk_pct,
                reason="Invalid risk per lot",
            )

        raw_lots = math.floor(risk_amount / estimated_risk_per_lot)

        if raw_lots < 1:
            return PositionSizingResult(
                lots=0,
                quantity=0,
                risk_amount=round(risk_amount, 2),
                stop_distance=round(stop_distance, 4),
                estimated_risk_per_lot=round(estimated_risk_per_lot, 2),
                confidence_scale=conf_scale,
                regime_scale=reg_scale,
                volatility_scale=vol_scale,
                drawdown_scale=dd_scale,
                final_risk_pct=adjusted_risk_pct,
                reason="Risk budget too small for 1 lot",
            )

        final_lots = max(self.min_lots, min(raw_lots, self.max_lots))
        quantity = final_lots * effective_lot_size

        reason = (
            f"capital={cap:.2f}, risk_pct={adjusted_risk_pct:.4f}, "
            f"risk_amount={risk_amount:.2f}, stop_distance={stop_distance:.2f}, "
            f"risk_per_lot={estimated_risk_per_lot:.2f}, lots={final_lots}"
        )

        return PositionSizingResult(
            lots=final_lots,
            quantity=quantity,
            risk_amount=round(risk_amount, 2),
            stop_distance=round(stop_distance, 4),
            estimated_risk_per_lot=round(estimated_risk_per_lot, 2),
            confidence_scale=round(conf_scale, 4),
            regime_scale=round(reg_scale, 4),
            volatility_scale=round(vol_scale, 4),
            drawdown_scale=round(dd_scale, 4),
            final_risk_pct=round(adjusted_risk_pct, 6),
            reason=reason,
        )


if __name__ == "__main__":
    sizer = AdaptivePositionSizer(
        min_risk_pct=0.0025,
        max_risk_pct=0.02,
        default_risk_pct=0.01,
        min_lots=1,
        max_lots=10,
        option_lot_size=50,
    )

    result = sizer.size_position(
        capital=100000,
        entry_price=220.0,
        stop_loss=208.0,
        confidence=0.78,
        score=8,
        regime="TREND",
        strategy="TREND",
        atr=9.5,
        peak_equity=108000,
        lot_size=50,
        base_risk_pct=0.01,
    )

    print(result.to_dict())


def auto_lots_from_capital(
    capital:     float,
    option_price: float,
    lot_size:    int   = 75,
    risk_pct:    float = 0.02,   # 2% of capital per trade
    max_lots:    int   = 5,
) -> int:
    """
    Automatically determine lot size from available capital.
    
    Formula: lots = floor((capital × risk_pct) / (option_price × lot_size))
    
    Example: capital=₹1L, option=₹100, lot=75, risk=2%
      lots = floor(2000 / 7500) = 0 → bump to 1 (minimum)
      
    Example: capital=₹5L, option=₹100, lot=75, risk=2%  
      lots = floor(10000 / 7500) = 1
      
    Example: capital=₹25L, option=₹100, lot=75, risk=2%
      lots = floor(50000 / 7500) = 6 → capped at max_lots=5
    """
    if option_price <= 0 or lot_size <= 0:
        return 1
    risk_amount  = capital * risk_pct
    cost_per_lot = option_price * lot_size
    lots         = max(1, min(max_lots, int(risk_amount / cost_per_lot)))
    return lots
