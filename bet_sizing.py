"""
bet_sizing.py — position size from predicted probability (López de Prado, AFML ch.10).

WHY: once a model (e.g. the meta-labeler) outputs a calibrated probability that a
trade wins, bet a SIZE proportional to conviction instead of a fixed lot — bigger
on high-confidence setups, smaller (or skip) on marginal ones. This lifts the
realised P&L *of a real edge*; it does nothing (correctly ~0 size) when the model
is at chance, so it cannot manufacture profit from no edge.

Pure stdlib (math.erf for the normal CDF — no scipy dependency).
"""
from __future__ import annotations

import math


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def bet_size_from_prob(prob: float, pred: int = 1, num_classes: int = 2,
                       step: float = 0.0) -> float:
    """
    prob        : model probability of the PREDICTED class (0..1).
    pred        : trade direction for that class (+1 long, -1 short).
    num_classes : label cardinality (2 for win/lose meta-label).
    step        : optional discretisation (e.g. 0.25 → sizes in {0,.25,.5,.75,1}).

    Returns a signed size in [-1, 1]; |size| scales your max position. At
    prob==1/num_classes (chance) size==0; at prob→1 size→±1.
    """
    p = float(prob)
    if not (0.0 < p < 1.0):
        return float(max(-1, min(1, int(pred)))) if p >= 1.0 else 0.0
    z = (p - 1.0 / num_classes) / math.sqrt(p * (1.0 - p))
    size = (2.0 * _norm_cdf(z) - 1.0) * (1 if pred >= 0 else -1)
    if step and step > 0:
        size = round(size / step) * step
    return float(max(-1.0, min(1.0, size)))


def qty_from_prob(prob: float, max_qty: int, pred: int = 1, *,
                  min_qty: int = 0, step: float = 0.0) -> int:
    """Convenience: integer quantity from probability, clamped to [min_qty, max_qty]
    in the predicted direction (magnitude only; sign handled by the caller/side)."""
    frac = abs(bet_size_from_prob(prob, pred=pred, step=step))
    q = int(round(frac * int(max_qty)))
    return max(int(min_qty), min(int(max_qty), q))
