"""
pivot_engine.py — configurable swing-point (pivot) detection.

A swing high at bar i requires high[i] to exceed the highs of `left_bars` bars
before and `right_bars` bars after (fractal definition). Defaults (2/2) match the
spec's high[i] > high[i±1], high[i±2]. Vectorised with numpy for speed.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("pattern_engine")


def find_swings(df: pd.DataFrame, left: int = 2, right: int = 2,
                min_separation: int = 1) -> Tuple[List[int], List[int]]:
    """Return (swing_high_indices, swing_low_indices).

    Pure-price fractal pivots. `min_separation` thins clusters by keeping the
    most extreme pivot within any `min_separation`-bar window.
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(df)
    sh: List[int] = []
    sl: List[int] = []
    for i in range(left, n - right):
        win_h = highs[i - left:i + right + 1]
        win_l = lows[i - left:i + right + 1]
        if highs[i] == win_h.max() and (win_h == highs[i]).sum() == 1:
            sh.append(i)
        if lows[i] == win_l.min() and (win_l == lows[i]).sum() == 1:
            sl.append(i)
    return _thin(sh, highs, min_separation, want_max=True), \
        _thin(sl, lows, min_separation, want_max=False)


def _thin(idxs: List[int], series: np.ndarray, min_sep: int, want_max: bool) -> List[int]:
    if min_sep <= 1 or not idxs:
        return idxs
    kept: List[int] = []
    for i in idxs:
        if kept and (i - kept[-1]) < min_sep:
            better = series[i] > series[kept[-1]] if want_max else series[i] < series[kept[-1]]
            if better:
                kept[-1] = i
        else:
            kept.append(i)
    return kept


def swing_frame(df: pd.DataFrame, sh: List[int], sl: List[int]) -> pd.DataFrame:
    """Chronological table of pivots: columns [index, kind, price]."""
    rows = [(i, "H", float(df["high"].iloc[i])) for i in sh] + \
           [(i, "L", float(df["low"].iloc[i])) for i in sl]
    rows.sort(key=lambda r: r[0])
    return pd.DataFrame(rows, columns=["index", "kind", "price"])
