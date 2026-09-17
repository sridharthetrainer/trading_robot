"""
causal_htf.py — causally-correct higher-timeframe reconstruction for backtesting.

Built 2026-09-17 per PREREG_BREAKOUT_HTF_ALIGNMENT.md's required build order
(step 1: a shared causal-slicing helper, proven via a poisoned-input test,
BEFORE the breakout HTF-alignment gate itself is built).

Found and deliberately avoided while building this: backtest_supertrend_mtf.py's
existing _resample_to_15m() + .reindex(method="ffill") pattern leaks future
data into every HTF-derived value. Pandas' default resample labeling
(label="left") names a bar by its START time while the bar's own data extends
up to just before the start of the NEXT bar -- a 15-min bar labeled "09:15"
actually contains data through 09:29:59. ffill-reindexing that onto the 5-min
index makes the 5-min bars AT 09:15/09:20/09:25 see information that, at
their own timestamps, hasn't happened yet. Confirmed by direct reproduction
(a resample + inspection of the labeled bar's constituent rows). This module
does not fix that existing bug (out of scope for this build) -- flagged
separately in the commit message and left for its own dedicated fix.

This module avoids the same mistake by labeling each resampled bar with its
CLOSE time (label="right", closed="left"): a 15-min bar labeled "09:30"
represents data from [09:15, 09:30) and is fully known and closed exactly at
09:30 clock time. Combined with causal_htf_slice()'s strict "< as_of_ts"
filter, this guarantees no future information can reach a decision made at
as_of_ts.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def resample_to_htf(df: pd.DataFrame, freq: str = "15min") -> pd.DataFrame:
    """Resample a primary-timeframe OHLC(V) frame to a higher timeframe,
    labeling each bar by its CLOSE time so the result is safe to causally
    slice with causal_htf_slice() below. Do not use plain df.resample(freq)
    elsewhere and assume it's equivalent -- pandas' default label="left"
    is the leaky convention this function exists to avoid.
    """
    if df is None or not isinstance(df.index, pd.DatetimeIndex) or df.empty:
        return df

    open_col = "Open" if "Open" in df.columns else "open"
    high_col = "High" if "High" in df.columns else "high"
    low_col = "Low" if "Low" in df.columns else "low"
    close_col = "Close" if "Close" in df.columns else "close"
    volume_col = "Volume" if "Volume" in df.columns else (
        "volume" if "volume" in df.columns else None)

    agg = {open_col: "first", high_col: "max", low_col: "min", close_col: "last"}
    if volume_col:
        agg[volume_col] = "sum"

    out = (
        df.resample(freq, label="right", closed="left")
        .agg(agg)
        .dropna(subset=[open_col, high_col, low_col, close_col])
    )
    return out


def causal_htf_slice(df_htf: pd.DataFrame, as_of_ts) -> pd.DataFrame:
    """Return only the HTF bars closed STRICTLY before as_of_ts.

    Contract: df_htf's index must already represent each bar's CLOSE time
    (e.g. resample_to_htf()'s output above, not a raw pandas resample). This
    function only enforces the "strictly before" causality boundary -- it
    does not know or check what timeframe df_htf is at.
    """
    if df_htf is None or not isinstance(df_htf.index, pd.DatetimeIndex):
        return df_htf
    return df_htf[df_htf.index < as_of_ts]


def add_htf_emas(df_htf: pd.DataFrame) -> pd.DataFrame:
    """Add ema20/ema50 columns to a resample_to_htf() frame, computed once
    over the full series. EMA is a purely backward-looking recursive
    calculation (each value depends only on earlier bars), so computing it
    once over the whole series and later restricting only the LOOKUP via
    causal_htf_slice()/htf_bias_asof() is causally safe -- it produces the
    identical value a bar-by-bar continuous computation would have produced
    at that same point, without redundantly recomputing on every query.
    """
    if df_htf is None or df_htf.empty:
        return df_htf
    close_col = "Close" if "Close" in df_htf.columns else "close"
    out = df_htf.copy()
    out["ema20"] = out[close_col].ewm(span=20, adjust=False).mean()
    out["ema50"] = out[close_col].ewm(span=50, adjust=False).mean()
    return out


def htf_bias_asof(df_htf_with_emas: pd.DataFrame, as_of_ts) -> str:
    """Reconstruct mtf.get_htf_bias()'s exact BULLISH/BEARISH/SIDEWAYS
    decision (close > ema20 > ema50 -> BULLISH; close < ema20 < ema50 ->
    BEARISH; else SIDEWAYS), using only HTF bars closed strictly before
    as_of_ts. df_htf_with_emas must already carry ema20/ema50 from
    add_htf_emas() above.
    """
    sliced = causal_htf_slice(df_htf_with_emas, as_of_ts)
    if sliced is None or len(sliced) < 1:
        return "SIDEWAYS"

    latest = sliced.iloc[-1]
    close_col = "Close" if "Close" in sliced.columns else "close"
    try:
        close = float(latest.get(close_col, 0) or 0)
        ema20 = float(latest.get("ema20", 0) or 0)
        ema50 = float(latest.get("ema50", 0) or 0)
    except Exception:
        return "SIDEWAYS"

    if close <= 0 or ema20 <= 0 or ema50 <= 0:
        return "SIDEWAYS"
    if close > ema20 > ema50:
        return "BULLISH"
    if close < ema20 < ema50:
        return "BEARISH"
    return "SIDEWAYS"
