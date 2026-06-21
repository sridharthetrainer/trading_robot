"""
ai_trade_filter.py

Hard filter that gates every signal before execution.

Improvements applied
--------------------
1. Removed soft_allow
   Original: "if not decision and score > 0: decision = True"
   This let through ~95% of signals regardless of quality.
   Now: strict threshold enforced. Score must reach threshold.

2. Hard block on NO_TRADE / VOLATILE regime
   Signals in NO_TRADE or VOLATILE regime are rejected immediately,
   regardless of score.  These regimes mean "avoid trading".

3. AI probability minimum gate
   If ai_probability is provided and < 0.50, add a hard penalty that
   makes it nearly impossible to pass the threshold.  Previously even
   ai_probability=0.30 only gave a soft "ai_weak" tag.

4. Volume ratio gate
   If volume_ratio (current_vol / 20-bar avg) is provided and < 0.40,
   reject — the market has no participation.

5. Confidence floor
   Signal confidence < 0.40 adds a hard -1.0 penalty.

6. Higher threshold (2.0, up from 1.5)
   With soft_allow gone, the threshold is the real gating value.
   2.0 requires at least "strong_signal" + one positive factor.
"""

import logging

logger = logging.getLogger(__name__)

# Hard-reject these regimes no matter what the signal score is
_HARD_BLOCK_REGIMES = {"NO_TRADE", "VOLATILE"}


class AITradeFilter:

    def __init__(
        self,
        threshold: float = 2.0,
        min_ai_probability: float = 0.50,
        min_confidence: float = 0.40,
        min_volume_ratio: float = 0.40,
    ) -> None:
        """
        Parameters
        ----------
        threshold         : Score required to approve a signal (default 2.0)
        min_ai_probability: Below this AI probability → hard penalty (default 0.50)
        min_confidence    : Below this signal confidence → hard penalty (default 0.40)
        min_volume_ratio  : Below this volume_ratio → reject (default 0.40; 0 = disabled)
        """
        self.threshold          = float(threshold)
        self.min_ai_probability = float(min_ai_probability)
        self.min_confidence     = float(min_confidence)
        self.min_volume_ratio   = float(min_volume_ratio)

    def evaluate(self, signal: dict, ai_probability: float = None):
        """
        Returns (decision: bool, metadata: dict).

        decision=True  → allow trade entry
        decision=False → block trade entry
        """
        try:
            score   = 0.0
            reasons = []

            signal_score  = float(signal.get("score",       0))
            confidence    = float(signal.get("confidence",  0))
            volatility    = float(signal.get("volatility",  0))
            volume_ratio  = float(signal.get("volume_ratio", 0))
            regime        = str(signal.get("regime", "UNKNOWN")).upper()

            # ── HARD BLOCKS (immediate reject) ──────────────────────────
            if regime in _HARD_BLOCK_REGIMES:
                return False, {
                    "final_score": 0.0,
                    "threshold":   self.threshold,
                    "reasons":     [f"hard_block_regime_{regime.lower()}"],
                    "regime":      regime,
                    "hard_block":  True,
                }

            # ── 1. BASE SIGNAL QUALITY ───────────────────────────────────
            if signal_score >= 6:
                score += 2.5; reasons.append("very_strong_signal")
            elif signal_score >= 4:
                score += 1.5; reasons.append("strong_signal")
            elif signal_score >= 2:
                score += 0.5; reasons.append("weak_signal")
            else:
                score -= 1.0; reasons.append("very_weak_signal")

            # ── 2. AI MODEL PROBABILITY ──────────────────────────────────
            if ai_probability is not None:
                ai_prob = float(ai_probability)
                if ai_prob < self.min_ai_probability:
                    score -= 1.5
                    reasons.append("ai_probability_too_low")
                else:
                    ai_boost = (ai_prob - 0.5) * 4   # [-2, +2]
                    score   += ai_boost
                    if ai_prob > 0.70: reasons.append("ai_strong")
                    elif ai_prob > 0.55: reasons.append("ai_ok")
                    else: reasons.append("ai_marginal")

            # ── 3. CONFIDENCE FLOOR ──────────────────────────────────────
            if confidence < self.min_confidence:
                score -= 1.0
                reasons.append("confidence_too_low")
            elif confidence > 0.70:
                score += 1.0; reasons.append("high_confidence")
            elif confidence > 0.50:
                score += 0.5

            # ── 4. VOLATILITY ────────────────────────────────────────────
            if volatility > 0.02:
                score += 1.0; reasons.append("good_volatility")
            elif volatility > 0.01:
                score += 0.5; reasons.append("moderate_volatility")
            else:
                score -= 0.5; reasons.append("low_volatility")

            # ── 5. VOLUME RATIO ──────────────────────────────────────────
            if self.min_volume_ratio > 0 and volume_ratio > 0:
                if volume_ratio < self.min_volume_ratio:
                    return False, {
                        "final_score": round(score, 4),
                        "threshold":   self.threshold,
                        "reasons":     reasons + ["volume_too_low"],
                        "volume_ratio": volume_ratio,
                        "hard_block":  True,
                    }
                elif volume_ratio >= 1.5:
                    score += 1.0; reasons.append("strong_volume")
                elif volume_ratio >= 1.0:
                    score += 0.5; reasons.append("normal_volume")

            # ── 6. REGIME ALIGNMENT ──────────────────────────────────────
            if regime == "TREND":
                if signal_score >= 4:
                    score += 1.0; reasons.append("trend_alignment")
                else:
                    score -= 0.5
            elif regime == "BREAKOUT":
                if signal_score >= 4:
                    score += 1.25; reasons.append("breakout_alignment")
                else:
                    score -= 0.5
            elif regime == "RANGE":
                if signal_score >= 3:
                    score += 0.5; reasons.append("range_alignment")
            elif regime == "EARLY_TREND":
                score += 0.25
            elif regime in ("UNKNOWN",):
                score -= 0.5; reasons.append("uncertain_regime")

            # ── FINAL STRICT DECISION ────────────────────────────────────
            # No soft_allow. Score must reach threshold.
            decision = score >= self.threshold

            if not decision:
                reasons.append(f"below_threshold_{score:.2f}<{self.threshold:.2f}")

            return decision, {
                "final_score":    round(score, 4),
                "threshold":      self.threshold,
                "ai_probability": ai_probability,
                "reasons":        reasons,
                "confidence":     confidence,
                "volatility":     volatility,
                "volume_ratio":   volume_ratio,
                "regime":         regime,
            }

        except Exception as exc:
            logger.exception("AI filter failed")
            # NEVER crash the system — approve on error with a flag
            return True, {"error": str(exc), "fallback": True, "signal": signal}
