"""
option_intelligence.py

Institutional options intelligence layer.

Replaces blind ATM selection with delta-appropriate, gamma-aware,
theta-tracked option selection and management.

The 4 Institutional Option Rules
──────────────────────────────────
1. Delta-appropriate strike selection
   Strong conviction (conf > 0.75) → 0.45-0.50 delta (ATM)
   Medium conviction (conf 0.55-0.75) → 0.35-0.40 delta (slight OTM)
   Never buy 0.10 delta options — too little chance of profit

2. Never buy when IV crush will kill the premium
   ATM straddle provides the "fair" premium for expected move
   If option costs > 0.6% of spot for ATM → overpriced
   Rule: only buy when option cost < 0.5% of spot

3. Gamma risk control near expiry
   0-DTE gamma explodes after 1 PM — moves become non-linear
   1-DTE gamma still very high Thursday morning
   Rule: reduce size by 50% when gamma risk is HIGH

4. Theta decay clock
   Every open option position has a theta cost per hour
   If we've held for > 2 hours and not at T1, premium is decaying fast
   Rule: exit option position if theta cost > 15% of remaining premium

ATM Straddle Range Estimator
─────────────────────────────
The straddle price tells us the market's expected daily range.
Fetched from the option chain: CE(ATM) + PE(ATM) at same strike.
This is the most important number for option buyers:
  - If we expect move > 1.5× straddle → buying makes sense
  - If we expect move ≤ straddle → selling makes sense
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Dict, Optional, Tuple


logger = logging.getLogger(__name__)


@dataclass
class OptionMetrics:
    """Live option metrics for an open position."""
    trade_id:       str
    symbol:         str           # e.g. NIFTY22000CE
    strike:         int
    option_type:    str           # CE or PE
    underlying:     str
    entry_premium:  float
    entry_time:     float         # epoch seconds
    entry_delta:    float = 0.0
    current_premium:float = 0.0
    current_delta:  float = 0.0
    gamma_risk:     str   = "LOW" # LOW | MEDIUM | HIGH | EXTREME
    theta_cost_hr:  float = 0.0   # estimated premium decay per hour
    hours_held:     float = 0.0
    theta_consumed: float = 0.0   # total theta decay since entry
    dte_at_entry:   int   = 0

    @property
    def theta_pct_consumed(self) -> float:
        if self.entry_premium <= 0:
            return 0.0
        return self.theta_consumed / self.entry_premium

    @property
    def exit_for_theta(self) -> bool:
        """True if theta decay has consumed > 15% of entry premium."""
        return self.theta_pct_consumed > 0.15


class OptionIntelligence:
    """
    Delta-aware option selection and gamma/theta risk manager.
    """

    # Strike step sizes per index
    STRIKE_STEPS = {
        "NIFTY":      50,
        "BANKNIFTY":  100,
        "FINNIFTY":   50,
        "MIDCPNIFTY": 25,
    }

    # Theta decay rate (approximate % of ATM premium per hour)
    # Faster on 0-DTE, slower on 5+ DTE
    THETA_RATES = {
        0: 0.12,   # 12% per hour on 0-DTE (extreme)
        1: 0.07,   # 7% per hour on 1-DTE
        2: 0.04,   # 4% per hour on 2-DTE
        3: 0.025,
        4: 0.02,
        5: 0.015,  # slows significantly past 5 DTE
    }

    def __init__(self) -> None:
        self._open_metrics: Dict[str, OptionMetrics] = {}
        self._straddle_cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (premium, timestamp)

    # ─────────────────────────────────────────────────────────────────────────
    # DELTA-APPROPRIATE STRIKE SELECTION
    # ─────────────────────────────────────────────────────────────────────────

    def select_strike(
        self,
        underlying:  str,
        spot:        float,
        option_type: str,    # "CE" or "PE"
        confidence:  float,
        day_type:    str    = "UNKNOWN",
        dte:         int    = 3,
    ) -> Dict[str, Any]:
        """
        Select the optimal strike based on confidence and day type.

        Institutional rule:
        - High conviction + trend day → ATM (0.45-0.50 delta)
        - Medium conviction → 1-strike OTM (0.35-0.40 delta)
        - Low conviction → skip (don't buy)
        - Range day → never buy naked options (only spreads)
        """
        step = self.STRIKE_STEPS.get(underlying.upper(), 50)
        atm  = int(round(spot / step) * step)

        # Never buy options on range days (sell spreads instead)
        if day_type == "RANGE_DAY" and confidence < 0.70:
            return {
                "strike":       atm,
                "delta_target": 0.0,
                "recommendation": "SELL_SPREAD_INSTEAD",
                "reason":       "range_day_option_buying_unfavorable",
            }

        # Select strike based on confidence
        if confidence >= 0.75:
            # High conviction: ATM
            strike      = atm
            delta_target = 0.48
            note        = "ATM_high_conviction"
        elif confidence >= 0.60:
            # Medium conviction: 1 strike OTM
            if option_type == "CE":
                strike = atm + step
            else:
                strike = atm - step
            delta_target = 0.38
            note        = "1OTM_medium_conviction"
        elif confidence >= 0.50:
            # Low-medium: 2 strikes OTM only for strong day types
            if day_type in ("TREND_DAY",) and dte >= 3:
                if option_type == "CE":
                    strike = atm + step * 2
                else:
                    strike = atm - step * 2
                delta_target = 0.28
                note        = "2OTM_low_conviction_trend_day"
            else:
                return {
                    "strike":       atm,
                    "delta_target": 0.0,
                    "recommendation": "SKIP",
                    "reason":       "confidence_too_low_for_day_type",
                }
        else:
            return {
                "strike":       atm,
                "delta_target": 0.0,
                "recommendation": "SKIP",
                "reason":       f"confidence_{confidence:.2f}_too_low",
            }

        return {
            "strike":         strike,
            "delta_target":   delta_target,
            "recommendation": "BUY",
            "note":           note,
            "atm":            atm,
            "otm_distance":   abs(strike - atm),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # IV / PREMIUM OVERPRICED CHECK
    # ─────────────────────────────────────────────────────────────────────────

    def is_option_overpriced(
        self,
        premium:    float,
        spot:       float,
        dte:        int,
        iv_rank:    float = 0.50,
    ) -> Dict[str, Any]:
        """
        Check if an option is overpriced.

        Rule: ATM option should not cost more than:
          0-1 DTE: 0.40% of spot
          2-3 DTE: 0.55% of spot
          4-7 DTE: 0.70% of spot
          8+ DTE:  0.90% of spot

        Additionally: if IV Rank > 70%, options are expensive — skip buying.
        """
        if spot <= 0 or premium <= 0:
            return {"overpriced": False, "premium_pct": 0.0}

        premium_pct = premium / spot * 100

        fair_pct_limits = {
            0: 0.40,
            1: 0.40,
            2: 0.55,
            3: 0.55,
            4: 0.70,
            5: 0.70,
            6: 0.70,
            7: 0.70,
        }
        fair_limit = fair_pct_limits.get(min(dte, 7), 0.90)

        overpriced_by_price  = premium_pct > fair_limit
        overpriced_by_iv     = iv_rank > 0.70

        return {
            "overpriced":       overpriced_by_price or overpriced_by_iv,
            "overpriced_price": overpriced_by_price,
            "overpriced_iv":    overpriced_by_iv,
            "premium_pct":      round(premium_pct, 3),
            "fair_limit_pct":   fair_limit,
            "iv_rank":          round(iv_rank, 3),
            "reason": (
                f"premium_{premium_pct:.2f}%>limit_{fair_limit}%"
                if overpriced_by_price else
                f"iv_rank_{iv_rank:.0%}_too_high"
                if overpriced_by_iv else "ok"
            ),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # ATM STRADDLE — EXPECTED RANGE ESTIMATOR
    # ─────────────────────────────────────────────────────────────────────────

    def get_straddle_range(
        self,
        underlying:     str,
        spot:           float,
        ce_premium:     float = 0.0,
        pe_premium:     float = 0.0,
        broker_manager: Any   = None,
    ) -> Dict[str, Any]:
        """
        Compute ATM straddle and expected daily range.

        If premiums not provided, estimates from Black-Scholes approximation
        (ATM option ≈ 0.4 × IV × spot × sqrt(DTE/365)).
        """
        straddle = ce_premium + pe_premium

        if straddle <= 0:
            # Estimate from typical IV (16% annual = 1% daily)
            straddle = spot * 0.010   # rough 1% daily move estimate

        # Expected range: ±straddle from current spot
        upper = spot + straddle
        lower = spot - straddle

        # Should we buy or sell options today?
        # If actual move so far < 30% of straddle → range day → sell
        recommendation = "SELL_STRADDLE" if straddle > spot * 0.008 else "BUY_DIRECTION"

        cache_key = f"{underlying}_{int(spot/100)*100}"
        self._straddle_cache[cache_key] = (straddle, time.time())

        return {
            "straddle":       round(straddle, 2),
            "ce_premium":     round(ce_premium, 2),
            "pe_premium":     round(pe_premium, 2),
            "expected_upper": round(upper, 0),
            "expected_lower": round(lower, 0),
            "expected_range": round(straddle, 0),
            "pct_of_spot":    round(straddle / spot * 100, 3),
            "recommendation": recommendation,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # GAMMA RISK CLASSIFIER
    # ─────────────────────────────────────────────────────────────────────────

    def get_gamma_risk(
        self,
        dte:      int,
        now_time: Optional[dtime] = None,
    ) -> str:
        """
        Classify gamma risk level.

        Gamma risk = how non-linear the option's price moves are.
        Higher gamma = smaller NIFTY move = BIGGER % option move.
        This is a double-edged sword: great for buyers near expiry,
        but dangerous if direction is wrong.

        Returns: "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
        """
        t = now_time or datetime.now().time()

        if dte == 0:
            if t >= dtime(13, 0):
                return "EXTREME"   # afternoon 0-DTE is explosive
            return "HIGH"
        elif dte == 1:
            if t >= dtime(11, 0):
                return "HIGH"
            return "MEDIUM"
        elif dte <= 2:
            return "MEDIUM"
        elif dte <= 5:
            return "LOW"
        else:
            return "LOW"

    def get_gamma_size_multiplier(self, gamma_risk: str) -> float:
        """Return position size multiplier based on gamma risk."""
        return {
            "LOW":     1.00,
            "MEDIUM":  0.75,
            "HIGH":    0.50,   # half size when gamma is high
            "EXTREME": 0.25,   # quarter size or skip
        }.get(gamma_risk, 1.0)

    # ─────────────────────────────────────────────────────────────────────────
    # THETA DECAY CLOCK
    # ─────────────────────────────────────────────────────────────────────────

    def register_position(
        self,
        trade_id:      str,
        symbol:        str,
        strike:        int,
        option_type:   str,
        underlying:    str,
        entry_premium: float,
        dte:           int,
    ) -> OptionMetrics:
        """Register a new option position for theta tracking."""
        theta_rate = self.THETA_RATES.get(min(dte, 5), 0.015)
        theta_hr   = entry_premium * theta_rate

        m = OptionMetrics(
            trade_id       = trade_id,
            symbol         = symbol,
            strike         = strike,
            option_type    = option_type,
            underlying     = underlying,
            entry_premium  = entry_premium,
            entry_time     = time.time(),
            theta_cost_hr  = theta_hr,
            gamma_risk     = self.get_gamma_risk(dte),
            dte_at_entry   = dte,
        )
        self._open_metrics[trade_id] = m
        logger.debug(
            "Option registered | %s %s theta=₹%.1f/hr gamma=%s",
            trade_id, symbol, theta_hr, m.gamma_risk,
        )
        return m

    def update_metrics(
        self,
        trade_id:        str,
        current_premium: float,
        current_dte:     int,
    ) -> Optional[OptionMetrics]:
        """Update live metrics for an open option position."""
        m = self._open_metrics.get(trade_id)
        if not m:
            return None

        hours_held     = (time.time() - m.entry_time) / 3600
        theta_consumed = m.theta_cost_hr * hours_held

        m.current_premium  = current_premium
        m.hours_held       = round(hours_held, 2)
        m.theta_consumed   = round(theta_consumed, 2)
        m.gamma_risk       = self.get_gamma_risk(current_dte)

        return m

    def should_exit_for_theta(
        self,
        trade_id:        str,
        current_premium: float,
        current_dte:     int,
    ) -> Dict[str, Any]:
        """
        Check if theta decay requires exiting a position.

        Exit rules:
        - Held > 2 hours and not reached T1 → check theta
        - Theta consumed > 15% of entry premium → exit
        - 0-DTE after 1:30 PM → always exit (gamma too unpredictable)
        - Premium decayed > 25% from entry for no reason → exit
        """
        m = self.update_metrics(trade_id, current_premium, current_dte)
        if not m:
            return {"exit": False, "reason": "no_metrics"}

        now_t = datetime.now().time()

        # 0-DTE after 1:30 PM: always exit
        if current_dte == 0 and now_t >= dtime(13, 30):
            return {
                "exit":   True,
                "reason": "0dte_afternoon_gamma_danger",
                "theta_pct": round(m.theta_pct_consumed * 100, 1),
            }

        # Theta consumed > 15%
        if m.theta_pct_consumed > 0.15 and m.hours_held > 2.0:
            return {
                "exit":      True,
                "reason":    f"theta_consumed_{m.theta_pct_consumed:.0%}",
                "theta_pct": round(m.theta_pct_consumed * 100, 1),
            }

        # Premium decayed > 25% with no reason (no news, flat market)
        if m.entry_premium > 0 and current_premium < m.entry_premium * 0.75:
            if m.hours_held > 1.0:
                return {
                    "exit":   True,
                    "reason": "premium_decay_25pct",
                    "theta_pct": round(m.theta_pct_consumed * 100, 1),
                }

        return {"exit": False, "reason": "ok", "theta_pct": round(m.theta_pct_consumed * 100, 1)}

    def remove_position(self, trade_id: str) -> None:
        self._open_metrics.pop(trade_id, None)

    def get_all_metrics(self) -> Dict[str, Dict]:
        return {
            tid: {
                "symbol":         m.symbol,
                "hours_held":     m.hours_held,
                "theta_pct":      round(m.theta_pct_consumed * 100, 1),
                "gamma_risk":     m.gamma_risk,
                "exit_for_theta": m.exit_for_theta,
            }
            for tid, m in self._open_metrics.items()
        }


# ── Module singleton ──────────────────────────────────────────────────────────
_intelligence: Optional[OptionIntelligence] = None


def get_option_intelligence() -> OptionIntelligence:
    global _intelligence
    if _intelligence is None:
        _intelligence = OptionIntelligence()
    return _intelligence
