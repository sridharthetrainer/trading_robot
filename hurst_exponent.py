"""
hurst_exponent.py — Hurst Exponent regime classifier.

Used by: regime.py (optional enrichment), signal_engine.py (soft gate).

The Hurst Exponent (H) measures long-range dependence in a price series:
  H > 0.55  →  TRENDING   (persistence: up days follow up days)
  H < 0.45  →  MEAN_REVERT (anti-persistence: mean reverting)
  0.45–0.55 →  RANDOM_WALK (no detectable edge from trend/MR strategies)

Used by Winton Capital, Aspect Capital, Millburn for regime routing.
QuantConnect surfaces it as a Research / Indicator node.

Method: Rescaled Range (R/S) analysis — no pandas required, pure stdlib.
Works on a list of close prices (minimum ~30 bars).

Usage
-----
    from hurst_exponent import hurst_class, hurst_value
    h = hurst_value(closes)          # float 0-1
    regime = hurst_class(closes)     # "TRENDING" | "MEAN_REVERT" | "RANDOM_WALK"
"""
from __future__ import annotations

import math
from typing import List, Literal

HurstClass = Literal["TRENDING", "MEAN_REVERT", "RANDOM_WALK", "UNKNOWN"]

_TREND_THRESHOLD  = 0.55
_MR_THRESHOLD     = 0.45
_MIN_BARS         = 30


def hurst_value(closes: List[float], max_lags: int = 20) -> float:
    """
    Estimate Hurst exponent using R/S (Rescaled Range) analysis.

    Parameters
    ----------
    closes   : list of close prices (oldest first), minimum 30 values
    max_lags : number of lag sizes to use; more = smoother but slower

    Returns
    -------
    float in [0, 1], or 0.5 if estimation fails / too few bars
    """
    n = len(closes)
    if n < _MIN_BARS:
        return 0.5

    try:
        # Log returns
        log_rets = [math.log(closes[i] / closes[i - 1])
                    for i in range(1, n) if closes[i - 1] > 0 and closes[i] > 0]
        m = len(log_rets)
        if m < _MIN_BARS - 1:
            return 0.5

        # Lag sizes: 10 evenly spaced from 8 to m//2
        min_lag = max(8, m // 20)
        step = max(1, (m // 2 - min_lag) // max_lags)
        lags = list(range(min_lag, m // 2, step))[:max_lags]
        if len(lags) < 4:
            return 0.5

        rs_vals = []
        for lag in lags:
            chunks = [log_rets[i: i + lag] for i in range(0, m - lag + 1, lag)]
            rs_chunk = []
            for chunk in chunks:
                if len(chunk) < 4:
                    continue
                mean_c = sum(chunk) / len(chunk)
                devs = [chunk[j] - mean_c for j in range(len(chunk))]
                cum = [sum(devs[:k + 1]) for k in range(len(devs))]
                rng = max(cum) - min(cum)
                std = math.sqrt(sum(d * d for d in devs) / max(len(devs) - 1, 1))
                if std > 0:
                    rs_chunk.append(rng / std)
            if rs_chunk:
                rs_vals.append((math.log(lag), math.log(sum(rs_chunk) / len(rs_chunk))))

        if len(rs_vals) < 4:
            return 0.5

        # OLS: log(R/S) ~ H * log(lag) + c
        xs = [v[0] for v in rs_vals]
        ys = [v[1] for v in rs_vals]
        n_pts = len(xs)
        mean_x = sum(xs) / n_pts
        mean_y = sum(ys) / n_pts
        ss_xy = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n_pts))
        ss_xx = sum((xs[i] - mean_x) ** 2 for i in range(n_pts))
        h = ss_xy / ss_xx if ss_xx > 0 else 0.5
        return round(max(0.0, min(1.0, h)), 4)

    except Exception:
        return 0.5


def hurst_class(closes: List[float]) -> HurstClass:
    """
    Classify market regime using Hurst Exponent.

    Returns
    -------
    "TRENDING"    → H > 0.55  (momentum strategies work better)
    "MEAN_REVERT" → H < 0.45  (mean-reversion strategies work better)
    "RANDOM_WALK" → 0.45-0.55 (no structural edge; reduce size)
    "UNKNOWN"     → too few bars
    """
    if len(closes) < _MIN_BARS:
        return "UNKNOWN"
    h = hurst_value(closes)
    if h > _TREND_THRESHOLD:
        return "TRENDING"
    if h < _MR_THRESHOLD:
        return "MEAN_REVERT"
    return "RANDOM_WALK"


def hurst_strategy_multiplier(closes: List[float], strategy_type: str) -> float:
    """
    Returns a score multiplier [0.5, 1.5] based on Hurst regime vs strategy type.

    strategy_type: "TREND" | "MEAN_REVERT" | "BREAKOUT" | other
    Use to softly adjust confluence scores without hard-disabling strategies.
    """
    if len(closes) < _MIN_BARS:
        return 1.0
    h = hurst_value(closes)
    st = strategy_type.upper()

    if st in ("TREND", "BREAKOUT", "MOMENTUM"):
        # Trend strategies benefit from H > 0.5
        return round(1.0 + (h - 0.5) * 2.0, 3)   # H=0.7 → 1.4x, H=0.3 → 0.6x
    elif st in ("MEAN_REVERT", "SCALP", "MR"):
        # MR strategies benefit from H < 0.5
        return round(1.0 + (0.5 - h) * 2.0, 3)    # H=0.3 → 1.4x, H=0.7 → 0.6x
    else:
        return 1.0
