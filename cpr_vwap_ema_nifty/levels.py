"""
Level formulas for CPR_VWAP_EMA_Nifty (confirmed with the user 2026-10-16).

  CPR:        P=(H+L+C)/3, BC=(H+L)/2, TC=2P-BC (BC/TC swapped when BC>TC)
  Traditional R1=2P-L  S1=2P-H  R2=P+(H-L)  S2=P-(H-L)
              R3=2P+(H-2L)  S3=2P-(2H-L)  R4=3P+(H-3L)  S4=3P-(3H-L)
  Camarilla   H3=C+1.1(H-L)/4  H4=C+1.1(H-L)/2  H5=(H/L)*C
              L3=C-1.1(H-L)/4  L4=C-1.1(H-L)/2  L5=C-(H5-C)

No lookahead: every level for a session comes from a COMPLETED earlier period
(daily = previous session, weekly = previous calendar week, monthly = previous
calendar month). build_day_levels() only ever reads rows strictly before the
session it labels.
"""
from __future__ import annotations

from typing import Dict

import pandas as pd


def cpr(h: float, l: float, c: float) -> Dict[str, float]:
    p = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = 2.0 * p - bc
    lo, hi = min(bc, tc), max(bc, tc)
    return {"P": p, "BC": lo, "TC": hi, "width_pct": (hi - lo) / p * 100.0 if p else 0.0}


def traditional(h: float, l: float, c: float) -> Dict[str, float]:
    p = (h + l + c) / 3.0
    return {
        "R1": 2 * p - l, "S1": 2 * p - h,
        "R2": p + (h - l), "S2": p - (h - l),
        "R3": 2 * p + (h - 2 * l), "S3": 2 * p - (2 * h - l),
        "R4": 3 * p + (h - 3 * l), "S4": 3 * p - (3 * h - l),
    }


def camarilla(h: float, l: float, c: float) -> Dict[str, float]:
    rng = h - l
    h5 = (h / l) * c if l else c
    return {
        "H3": c + 1.1 * rng / 4, "H4": c + 1.1 * rng / 2, "H5": h5,
        "L3": c - 1.1 * rng / 4, "L4": c - 1.1 * rng / 2, "L5": c - (h5 - c),
    }


def width_class(width_pct: float, narrow_below: float, wide_above: float) -> str:
    if width_pct < narrow_below:
        return "narrow"
    if width_pct > wide_above:
        return "wide"
    return "normal"


def cpr_relation_prior(today: Dict[str, float], prior: Dict[str, float]) -> str:
    """Today's CPR vs the prior session's CPR."""
    if today["BC"] > prior["TC"]:
        return "higher"
    if today["TC"] < prior["BC"]:
        return "lower"
    if today["TC"] <= prior["TC"] and today["BC"] >= prior["BC"]:
        return "inside"
    if today["TC"] >= prior["TC"] and today["BC"] <= prior["BC"]:
        return "outside"
    return "overlapping_higher" if today["P"] > prior["P"] else "overlapping_lower"


def cpr_vs(today: Dict[str, float], other: Dict[str, float]) -> str:
    """Daily CPR vs a weekly/monthly CPR: above / below / overlapping."""
    if today["BC"] > other["TC"]:
        return "above"
    if today["TC"] < other["BC"]:
        return "below"
    return "overlapping"


def _above_below(x: float, level: float) -> str:
    return "above" if x > level else "below"


def open_location(open_px: float, d_cpr: Dict[str, float], d_h3: float, w_h3: float) -> Dict[str, str]:
    if open_px > d_cpr["TC"]:
        vs_cpr = "above"
    elif open_px < d_cpr["BC"]:
        vs_cpr = "below"
    else:
        vs_cpr = "inside"
    return {
        "open_vs_cpr": vs_cpr,
        "open_vs_daily_h3": _above_below(open_px, d_h3),
        "open_vs_weekly_h3": _above_below(open_px, w_h3),
    }


def golden_pivot_zone(d_cpr: Dict[str, float], d_cam: Dict[str, float]) -> str:
    """'bullish' if daily Camarilla L3 lies inside the daily CPR [BC, TC];
    'bearish' if daily H3 lies inside it; else 'none'. Both cannot hold at once:
    CPR width |2C-(H+L)|/3 <= (H-L)/3 is always smaller than H3-L3 = 0.55(H-L)."""
    if d_cpr["BC"] <= d_cam["L3"] <= d_cpr["TC"]:
        return "bullish"
    if d_cpr["BC"] <= d_cam["H3"] <= d_cpr["TC"]:
        return "bearish"
    return "none"


def session_ohlc(spot_1m: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC from 09:15-15:29 1m bars. Index: normalized date."""
    s = spot_1m.between_time("09:15", "15:29")
    d = s.groupby(s.index.normalize()).agg(open=("open", "first"), high=("high", "max"),
                                            low=("low", "min"), close=("close", "last"))
    return d.dropna()


def _prior_period_hlc(daily: pd.DataFrame, freq: str) -> pd.DataFrame:
    """For each session, H/L/C of the most recent COMPLETED period (week/month)
    before the period that session belongs to."""
    per = daily.index.to_period(freq)
    agg = daily.groupby(per).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = agg.shift(1)
    out = prev.reindex(per)
    out.index = daily.index
    return out


def build_day_levels(daily: pd.DataFrame, narrow_below: float, wide_above: float) -> pd.DataFrame:
    """One row per session with every level and setup classification.
    Sessions without enough history (first 2 days / first week / first month)
    are dropped."""
    wk = _prior_period_hlc(daily, "W-SUN")
    mo = _prior_period_hlc(daily, "M")
    rows = []
    idx = list(daily.index)
    for i in range(2, len(idx)):
        day = idx[i]
        y, yy = daily.iloc[i - 1], daily.iloc[i - 2]
        w, m = wk.loc[day], mo.loc[day]
        if w.isna().any() or m.isna().any():
            continue
        d_cpr = cpr(y.high, y.low, y.close)
        prior_cpr = cpr(yy.high, yy.low, yy.close)
        w_cpr = cpr(w.high, w.low, w.close)
        m_cpr = cpr(m.high, m.low, m.close)
        d_cam = camarilla(y.high, y.low, y.close)
        w_cam = camarilla(w.high, w.low, w.close)
        m_cam = camarilla(m.high, m.low, m.close)
        d_trad = traditional(y.high, y.low, y.close)
        w_trad = traditional(w.high, w.low, w.close)
        o = float(daily.iloc[i].open)
        row = {
            "date": day, "open": o,
            "P": d_cpr["P"], "BC": d_cpr["BC"], "TC": d_cpr["TC"], "cpr_width_pct": d_cpr["width_pct"],
            "cpr_width": width_class(d_cpr["width_pct"], narrow_below, wide_above),
            "cpr_vs_prior_day": cpr_relation_prior(d_cpr, prior_cpr),
            "cpr_vs_weekly": cpr_vs(d_cpr, w_cpr),
            "cpr_vs_monthly": cpr_vs(d_cpr, m_cpr),
            "WP": w_cpr["P"], "WBC": w_cpr["BC"], "WTC": w_cpr["TC"],
            "MP": m_cpr["P"], "MBC": m_cpr["BC"], "MTC": m_cpr["TC"],
            "PDH": y.high, "PDL": y.low, "PWH": w.high, "PWL": w.low,
            **d_trad, **{f"W{k}": v for k, v in w_trad.items()},
            **d_cam, **{f"W{k}": v for k, v in w_cam.items()}, **{f"M{k}": v for k, v in m_cam.items()},
        }
        row.update(open_location(o, d_cpr, d_cam["H3"], w_cam["H3"]))
        row["golden_pivot_zone"] = golden_pivot_zone(d_cpr, d_cam)
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")
