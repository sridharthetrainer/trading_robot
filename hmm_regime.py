"""
hmm_regime.py — HMM + Shannon Entropy Regime Detection

Fits Hidden Markov Model on price returns + volatility.
States: TRENDING_UP, TRENDING_DOWN, MEAN_REVERTING, HIGH_NOISE

Also computes Shannon entropy to detect market randomness.
High entropy (>0.8) → HIGH_NOISE → reduce position size 50%.
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_STATES = {0:"TRENDING_UP", 1:"TRENDING_DOWN", 2:"MEAN_REVERTING", 3:"HIGH_NOISE"}

def _shannon_entropy(arr: np.ndarray, bins: int = 20) -> float:
    """Shannon entropy on direction changes — higher = more random."""
    try:
        signs = np.sign(arr)
        counts = np.array([np.sum(signs==s) for s in [-1,0,1]])
        counts = counts[counts>0]
        probs  = counts / counts.sum()
        return float(-np.sum(probs * np.log2(probs + 1e-10)) / np.log2(len(counts)+1))
    except Exception:
        return 0.5


def detect_regime_hmm(df: pd.DataFrame) -> Dict:
    """
    Full HMM regime detection.
    Returns regime, confidence, entropy, size_multiplier.
    Falls back to rule-based if hmmlearn not installed.
    """
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 20:
            return {"regime":"NEUTRAL","confidence":0.5,"entropy":0.5,"size_multiplier":1.0}

        closes = df_c["close"].values.astype(float)
        returns = np.diff(np.log(closes + 1e-9))
        if len(returns) < 10:
            return {"regime":"NEUTRAL","confidence":0.5,"entropy":0.5,"size_multiplier":1.0}

        # Shannon entropy
        entropy = _shannon_entropy(returns)

        # Realized volatility (20-period)
        rv = pd.Series(returns).rolling(5).std().fillna(0).values

        # Trend strength (linear regression slope)
        x = np.arange(min(20, len(closes)))
        slope = np.polyfit(x, closes[-len(x):], 1)[0] / (closes[-1] + 1e-9) * 100

        try:
            # HMM
            from hmmlearn.hmm import GaussianHMM
            features = np.column_stack([returns[-40:], rv[-40:]])
            model = GaussianHMM(n_components=4, covariance_type="diag",
                                n_iter=100, random_state=42)
            model.fit(features)
            states = model.predict(features)
            state_means = []
            for s in range(4):
                mask = states == s
                if mask.any():
                    state_means.append((s, float(np.mean(features[mask, 0]))))
            state_means.sort(key=lambda x: x[1], reverse=True)
            # Map states by mean return
            state_map = {}
            if len(state_means) >= 4:
                state_map[state_means[0][0]] = "TRENDING_UP"
                state_map[state_means[1][0]] = "TRENDING_DOWN"
                state_map[state_means[2][0]] = "MEAN_REVERTING"
                state_map[state_means[3][0]] = "HIGH_NOISE"
            current_state_raw = int(states[-1])
            regime_raw = state_map.get(current_state_raw, "NEUTRAL")
            # Get probability of current state
            log_probs = model.score_samples(features)
            confidence = float(np.exp(log_probs[-1] / max(len(features),1)) + 0.5)
            confidence = min(confidence, 1.0)
            source = "HMM"
        except ImportError:
            # Rule-based fallback (no hmmlearn)
            regime_raw, confidence = _rule_based_regime(returns, slope, rv[-1] if len(rv) else 0)
            source = "rules"

        # Override with HIGH_NOISE if entropy is very high
        if entropy > 0.82:
            regime_raw = "HIGH_NOISE"

        # Size multiplier
        size_mult = {
            "TRENDING_UP":   1.0,
            "TRENDING_DOWN": 1.0,
            "MEAN_REVERTING":1.0,
            "HIGH_NOISE":    0.5,   # halve position size in noise
        }.get(regime_raw, 1.0)

        return {
            "regime":          regime_raw,
            "confidence":      round(confidence, 3),
            "entropy":         round(entropy, 3),
            "size_multiplier": size_mult,
            "slope":           round(slope, 4),
            "source":          source,
            "description":     _describe(regime_raw, entropy),
        }

    except Exception as e:
        logger.debug("hmm_regime: %s", e)
        return {"regime":"NEUTRAL","confidence":0.5,"entropy":0.5,"size_multiplier":1.0}


def _rule_based_regime(returns, slope, rv) -> Tuple[str, float]:
    """Rule-based fallback when hmmlearn not available."""
    mean_ret = float(np.mean(returns[-10:])) if len(returns) >= 10 else 0
    std_ret  = float(np.std(returns[-10:]))  if len(returns) >= 10 else 0.01
    if abs(slope) > 0.15 and mean_ret > 0.001:
        return "TRENDING_UP",   0.70
    elif abs(slope) > 0.15 and mean_ret < -0.001:
        return "TRENDING_DOWN", 0.70
    elif std_ret < 0.008:
        return "MEAN_REVERTING",0.65
    else:
        return "HIGH_NOISE",    0.55


def _describe(regime: str, entropy: float) -> str:
    d = {
        "TRENDING_UP":   "Price trending up → momentum + breakout strategies preferred",
        "TRENDING_DOWN": "Price trending down → short + put strategies preferred",
        "MEAN_REVERTING":"Price oscillating → VWAP reversion + range strategies preferred",
        "HIGH_NOISE":    f"Market noise high (entropy={entropy:.2f}) → reduce size 50%",
    }
    return d.get(regime, "Neutral market conditions")


def get_regime_score_modifier(regime: str, strategy_name: str) -> float:
    """Score modifier based on regime × strategy type matching."""
    # Keywords are substring-matched against the strategy name. Extended
    # (2026-06-12) so previously regime-neutral strategies participate:
    # trend-followers (ichimoku/donchian/kama/kst/aroon/weinstein/ribbon),
    # breakout family (orb/squeeze/donchian), reversal/exhaustion family
    # (divergence/harmonic/gartley/td_seq/williams/oops/fisher).
    _map = {
        "TRENDING_UP": {
            "trend":1.3,"momentum":1.2,"breakout":1.4,"ema":1.2,
            "ichimoku":1.2,"donchian":1.2,"kama":1.1,"kst":1.1,"aroon":1.1,
            "weinstein":1.2,"ribbon":1.1,"orb":1.1,"squeeze":1.1,
            "mean_reversion":-0.5,"vwap_reversion":-0.3,
            "divergence":-0.3,"harmonic":-0.3,"gartley":-0.3,
            "td_seq":-0.3,"oops":-0.2,
        },
        "TRENDING_DOWN": {
            "trend":1.3,"momentum":1.2,"short":1.4,
            "ichimoku":1.1,"donchian":1.1,"weinstein":1.1,
            "mean_reversion":-0.5,"buy":-.3,
            "harmonic":-0.3,"gartley":-0.3,
        },
        "MEAN_REVERTING": {
            "mean_reversion":1.4,"vwap":1.3,"range":1.3,
            "divergence":1.2,"harmonic":1.2,"gartley":1.2,"td_seq":1.2,
            "williams":1.1,"oops":1.1,"fisher":1.1,"percentb":1.1,
            "trend":-0.3,"breakout":-0.4,"momentum":-0.2,
            "donchian":-0.3,"orb":-0.3,"squeeze":-0.2,
        },
        "HIGH_NOISE": {
            "_all": -0.5,  # reduce ALL in noise
        },
    }
    regime_rules = _map.get(regime, {})
    if "_all" in regime_rules:
        return float(regime_rules["_all"])
    strat_lower = strategy_name.lower()
    for keyword, mod in regime_rules.items():
        if keyword in strat_lower:
            return float(mod)
    return 0.0
