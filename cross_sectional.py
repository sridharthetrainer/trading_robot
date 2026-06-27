"""
cross_sectional.py — relative-strength / cross-sectional ranking across the universe.

WHY: every existing strategy is TIME-SERIES ("will NIFTY go up?"), and all measured
edges there are negative. Cross-sectional signals ask a DIFFERENT question — "which
names are strongest RELATIVE to the rest right now?" — a different information
structure and the main untested family. This module only RANKS/scores (measurement);
acting on it must still clear the validation harness (deflated Sharpe / OOS) before
any live use. No edge is claimed here.

Pure numpy.
"""
from __future__ import annotations

import numpy as np


def cross_sectional_rank(values: dict) -> dict:
    """Percentile rank in [0,1] of each symbol's value within the cross-section
    (0 = weakest, 1 = strongest). Ignores NaN/None."""
    items = [(k, float(v)) for k, v in values.items()
             if v is not None and np.isfinite(float(v))]
    if not items:
        return {}
    if len(items) == 1:
        return {items[0][0]: 0.5}
    order = sorted(items, key=lambda x: x[1])
    n = len(order)
    return {k: i / (n - 1) for i, (k, _) in enumerate(order)}


def cross_sectional_zscore(values: dict) -> dict:
    """Cross-sectional z-score per symbol (demeaned by the universe, /std)."""
    items = [(k, float(v)) for k, v in values.items()
             if v is not None and np.isfinite(float(v))]
    if len(items) < 2:
        return {k: 0.0 for k, _ in items}
    arr = np.array([v for _, v in items])
    mu, sd = float(arr.mean()), float(arr.std())
    if sd <= 0:
        return {k: 0.0 for k, _ in items}
    return {k: (v - mu) / sd for k, v in items}


def momentum_scores(prices_by_symbol: dict, lookback: int = 20) -> dict:
    """Lookback return per symbol from a price series (list/array). Skips series
    shorter than lookback+1."""
    out = {}
    for sym, prices in prices_by_symbol.items():
        p = np.asarray([x for x in (prices or []) if x is not None], dtype=float)
        if len(p) < lookback + 1 or p[-lookback - 1] <= 0:
            continue
        out[sym] = float(p[-1] / p[-lookback - 1] - 1.0)
    return out


def long_short_candidates(values: dict, top_frac: float = 0.2) -> dict:
    """Top/bottom relative-strength names. Returns
    {longs:[...], shorts:[...], ranks:{...}} — strongest as longs, weakest as shorts."""
    ranks = cross_sectional_rank(values)
    if not ranks:
        return {"longs": [], "shorts": [], "ranks": {}}
    n = len(ranks)
    k = max(1, int(round(n * max(0.0, min(0.5, top_frac)))))
    ordered = sorted(ranks.items(), key=lambda x: x[1], reverse=True)
    longs = [s for s, _ in ordered[:k]]
    shorts = [s for s, _ in ordered[-k:]][::-1]
    return {"longs": longs, "shorts": shorts, "ranks": ranks}
