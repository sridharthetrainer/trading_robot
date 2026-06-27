"""
sample_weights.py — label-overlap (uniqueness) sample weights (López de Prado, AFML ch.4).

WHY: triple-barrier labels overlap in time — a label at bar i spans [i, i+horizon],
so neighbouring labels share outcome bars and are highly correlated. Training as if
they were i.i.d. lets the model over-count concurrent (redundant) samples. Weighting
each sample by its average UNIQUENESS (and optionally by return magnitude) corrects
this, so the ML learns from genuinely independent information.

Pure numpy. Returns weights normalised to mean 1 (so they scale loss, not its size).
"""
from __future__ import annotations

import numpy as np


def label_concurrency(n_bars: int, starts, horizon: int) -> np.ndarray:
    """concurrency[t] = number of labels whose window [start, start+horizon] covers bar t."""
    c = np.zeros(int(n_bars), dtype=float)
    h = max(0, int(horizon))
    for s in starts:
        s = int(s)
        lo, hi = max(0, s), min(int(n_bars) - 1, s + h)
        if lo <= hi:
            c[lo:hi + 1] += 1.0
    return c


def average_uniqueness(starts, horizon: int, n_bars: int) -> np.ndarray:
    """Per-label average uniqueness = mean over its span of 1/concurrency."""
    starts = [int(s) for s in starts]
    conc = label_concurrency(n_bars, starts, horizon)
    conc[conc == 0] = 1.0  # avoid /0 on uncovered bars
    h = max(0, int(horizon))
    u = np.zeros(len(starts), dtype=float)
    for k, s in enumerate(starts):
        lo, hi = max(0, s), min(int(n_bars) - 1, s + h)
        u[k] = float(np.mean(1.0 / conc[lo:hi + 1])) if lo <= hi else 1.0
    return u


def sample_weights(starts, horizon: int, n_bars: int, returns=None) -> np.ndarray:
    """Final per-sample weights ∝ average_uniqueness (× |return| if given), AFML 4.10.
    Normalised to mean 1. Returns all-ones if degenerate."""
    n = len(list(starts))
    if n == 0:
        return np.ones(0)
    u = average_uniqueness(starts, horizon, n_bars)
    w = u.copy()
    if returns is not None:
        r = np.abs(np.asarray(returns, dtype=float))
        if len(r) == len(w):
            w = w * r
    s = w.sum()
    if not np.isfinite(s) or s <= 0:
        return np.ones(n)
    return w * (n / s)   # mean 1
