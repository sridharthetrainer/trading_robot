"""
market_structure.py — HH/HL/LH/LL labelling and trend classification.

Walks the chronological pivot sequence and labels each swing relative to the
previous same-type swing, then classifies the regime:
  BULL_TREND  : recent HH + HL
  BEAR_TREND  : recent LH + LL
  RANGE       : mixed / equal
  TRANSITION  : a fresh break of the prior structure
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def classify_structure(swings: pd.DataFrame, min_swings: int = 2) -> Dict[str, Any]:
    if swings is None or len(swings) < 2:
        return {"regime": "UNKNOWN", "labels": [], "last_hh": None, "last_ll": None}

    labels: List[str] = []
    last_high = last_low = None
    for _, row in swings.iterrows():
        if row["kind"] == "H":
            if last_high is None:
                labels.append("H")
            else:
                labels.append("HH" if row["price"] > last_high else "LH")
            last_high = row["price"]
        else:
            if last_low is None:
                labels.append("L")
            else:
                labels.append("HL" if row["price"] > last_low else "LL")
            last_low = row["price"]

    recent = labels[-(2 * min_swings):]
    hh = recent.count("HH")
    hl = recent.count("HL")
    lh = recent.count("LH")
    ll = recent.count("LL")

    if hh >= 1 and hl >= 1 and (lh + ll) == 0:
        regime = "BULL_TREND"
    elif lh >= 1 and ll >= 1 and (hh + hl) == 0:
        regime = "BEAR_TREND"
    elif (hh + hl) >= 1 and (lh + ll) >= 1:
        regime = "TRANSITION"
    else:
        regime = "RANGE"

    return {
        "regime": regime,
        "labels": labels,
        "counts": {"HH": labels.count("HH"), "HL": labels.count("HL"),
                   "LH": labels.count("LH"), "LL": labels.count("LL")},
    }
