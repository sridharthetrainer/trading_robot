"""
option_underlying_context.py — LIVE 1-minute underlying technicals for the
option bot's signal generation (2026-07-10, operator-requested).

Before this, option_chain_recorder._direction_context read market_context.json
(previous-day bias, static all session) to supply market_regime/market_bias to
build_multistrike_signals — the inputs that drive regime_aligned gating and
scoring of every generated strike signal. This module replaces that with a
live 1m read of the underlying:

  - EMA 20 / 50 / 200 stack (price vs stacked EMAs)
  - MACD(12,26,9) histogram sign + direction
  - Session VWAP side
  - Accumulation/Distribution line slope (Chaikin A/D, indicators.calculate_adl)
  - Breakout vs the prior N-bar high/low AND vs the session high/low
  - Levels: prior-session floor pivots (P/R1/S1), session open/high/low,
    opening range (first 15 minutes)

Emits (regime, bias, detail) in the exact vocabulary the multistrike layer
already consumes. This improves INPUT quality only — generated signals stay
journaled and outcome-labelled by the existing measurement loop, and no edge
is claimed for any of it until that loop shows one.

Pure computation lives in compute_context(df) so it is testable offline.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BREAKOUT_LOOKBACK = 30      # 1m bars for the recent-range breakout check
ADL_SLOPE_BARS = 30         # 1m bars for the accumulation/distribution slope
OPENING_RANGE_BARS = 15     # first 15 minutes
_CACHE: Dict[str, tuple] = {}   # underlying -> (ts, regime, bias, detail)
_CACHE_TTL = 55.0
_FETCHER = None


def _get_fetcher():
    global _FETCHER
    if _FETCHER is None:
        angel = None
        try:
            import os
            from angel import AngelOne
            angel = AngelOne(
                api_key=os.getenv("API_KEY", ""), client_id=os.getenv("CLIENT_ID", ""),
                password=os.getenv("PASSWORD", ""), totp_secret=os.getenv("TOTP_SECRET", ""))
        except Exception as exc:
            logger.debug("underlying ctx broker unavailable: %s", exc)
        from data_fetcher import DataFetcher
        _FETCHER = DataFetcher(angel=angel, paper_trade=False)
    return _FETCHER


def compute_context(df) -> Dict[str, Any]:
    """Pure: 1m OHLCV DataFrame (2 sessions preferred) -> context dict.

    Vote-based: EMA stack, MACD, VWAP side, A/D slope, and breakout each cast
    one directional vote; bias needs a net of >=2 agreeing votes. Regime is
    TREND when the read is strongly one-sided or a breakout fired, RANGE when
    votes net to ~0, MIXED between.
    """
    import numpy as np
    import pandas as pd

    out: Dict[str, Any] = {"ok": False}
    if df is None or len(df) < 60:
        out["reason"] = "insufficient_1m_bars"
        return out
    cols = {str(c).lower(): c for c in df.columns}
    if not {"open", "high", "low", "close"}.issubset(cols):
        out["reason"] = "missing_ohlc"
        return out
    close = pd.to_numeric(df[cols["close"]], errors="coerce")
    high = pd.to_numeric(df[cols["high"]], errors="coerce")
    low = pd.to_numeric(df[cols["low"]], errors="coerce")
    opn = pd.to_numeric(df[cols["open"]], errors="coerce")
    vol = (pd.to_numeric(df[cols["volume"]], errors="coerce")
           if "volume" in cols else pd.Series(0.0, index=df.index))
    last = float(close.iloc[-1])
    votes = 0
    reasons = []

    # Session split (today vs prior session) from the index date when
    # available; fall back to the last 375 bars (one NSE session).
    try:
        dates = pd.to_datetime(df.index).date
        today = dates[-1]
        today_mask = dates == today
        prev_mask = dates != today
    except Exception:
        today_mask = np.arange(len(df)) >= max(0, len(df) - 375)
        prev_mask = ~today_mask
    tdf_close, tdf_high, tdf_low = close[today_mask], high[today_mask], low[today_mask]
    tdf_vol, tdf_open = vol[today_mask], opn[today_mask]

    # 1. EMA 20/50/200 stack
    e20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    e50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    e200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    out["ema20"], out["ema50"], out["ema200"] = round(e20, 2), round(e50, 2), round(e200, 2)
    if last > e20 > e50 > e200:
        votes += 1; reasons.append("ema_stack_bullish")
    elif last < e20 < e50 < e200:
        votes -= 1; reasons.append("ema_stack_bearish")

    # 2. MACD(12,26,9) histogram
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    h_now, h_prev = float(hist.iloc[-1]), float(hist.iloc[-2])
    out["macd_hist"] = round(h_now, 3)
    if h_now > 0 and h_now >= h_prev:
        votes += 1; reasons.append("macd_bull_rising")
    elif h_now < 0 and h_now <= h_prev:
        votes -= 1; reasons.append("macd_bear_falling")

    # 3. Session VWAP side. INDEX 1m candles carry zero volume (indices have
    # no traded volume), so a strict VWAP would never vote for NIFTY/
    # BANKNIFTY — the primary use case. Fall back to the typical-price
    # session mean (TWAP proxy) when volume is absent, and label it.
    if len(tdf_close) >= 5:
        tp = (tdf_high + tdf_low + tdf_close) / 3.0
        if float(tdf_vol.sum()) > 0:
            vwap = float((tp * tdf_vol).cumsum().iloc[-1] / tdf_vol.cumsum().iloc[-1])
            vwap_kind = "vwap"
        else:
            vwap = float(tp.expanding().mean().iloc[-1])
            vwap_kind = "twap"
        out["vwap"] = round(vwap, 2)
        out["vwap_kind"] = vwap_kind
        out["vwap_dist_pct"] = round((last - vwap) / vwap * 100, 3)
        if last > vwap:
            votes += 1; reasons.append(f"above_{vwap_kind}")
        elif last < vwap:
            votes -= 1; reasons.append(f"below_{vwap_kind}")

    # 4. Accumulation/Distribution slope. Same zero-volume problem: Chaikin
    # A/D multiplies CLV by volume, which zeroes out on indices. Use unit
    # volume there — CLV-only accumulation still says whether closes keep
    # landing near bar highs (buying pressure) or bar lows.
    try:
        hl_range = (high - low).replace(0, np.nan)
        clv = ((close - low) - (high - close)) / hl_range
        eff_vol = vol if float(vol.sum()) > 0 else pd.Series(1.0, index=df.index)
        adl = (clv * eff_vol).fillna(0).cumsum()
        adl_slope = float(adl.diff().tail(ADL_SLOPE_BARS).sum())
        out["adl_slope"] = round(adl_slope, 2)
        if adl_slope > 0:
            votes += 1; reasons.append("accumulation")
        elif adl_slope < 0:
            votes -= 1; reasons.append("distribution")
    except Exception as exc:
        logger.debug("adl: %s", exc)

    # 5. Breakout vs recent range and session extremes
    prior_high = float(high.iloc[-(BREAKOUT_LOOKBACK + 1):-1].max())
    prior_low = float(low.iloc[-(BREAKOUT_LOOKBACK + 1):-1].min())
    breakout = ""
    if last > prior_high:
        breakout = "up"; votes += 1; reasons.append(f"breakout_up>{prior_high:.1f}")
    elif last < prior_low:
        breakout = "down"; votes -= 1; reasons.append(f"breakout_down<{prior_low:.1f}")
    out["breakout"] = breakout

    # Levels for the detail payload (and downstream cards)
    if len(tdf_close) >= 1:
        out["day_open"] = round(float(tdf_open.iloc[0]), 2)
        out["day_high"] = round(float(tdf_high.max()), 2)
        out["day_low"] = round(float(tdf_low.min()), 2)
        orb = min(OPENING_RANGE_BARS, len(tdf_close))
        out["or_high"] = round(float(tdf_high.iloc[:orb].max()), 2)
        out["or_low"] = round(float(tdf_low.iloc[:orb].min()), 2)
    try:
        if prev_mask.sum() >= 5:
            from pivot_boss import calc_floor_pivots
            piv = calc_floor_pivots(
                float(high[prev_mask].max()), float(low[prev_mask].min()),
                float(close[prev_mask].iloc[-1]))
            out["pivot"] = piv.get("P")
            out["pivot_r1"] = piv.get("R1")
            out["pivot_s1"] = piv.get("S1")
    except Exception as exc:
        logger.debug("pivots: %s", exc)

    out["ok"] = True
    out["last"] = round(last, 2)
    out["votes"] = votes
    out["reasons"] = reasons
    out["bias"] = "BULLISH" if votes >= 2 else "BEARISH" if votes <= -2 else "NEUTRAL"
    out["regime"] = ("TREND" if (abs(votes) >= 3 or breakout)
                     else "RANGE" if abs(votes) <= 1 else "MIXED")
    return out


def get_underlying_context(underlying: str) -> Tuple[str, str, Dict[str, Any]]:
    """Live (regime, bias, detail) for an index underlying, cached ~1 min.
    Fail-safe: returns ("UNKNOWN", "UNKNOWN", {}) so callers keep their
    existing fallback behavior when 1m data is unavailable."""
    sym = str(underlying or "").upper()
    now = time.time()
    hit = _CACHE.get(sym)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1], hit[2], hit[3]
    regime, bias, detail = "UNKNOWN", "UNKNOWN", {}
    try:
        df = _get_fetcher().get_market_data(sym, "1m", days=2)
        ctx = compute_context(df)
        if ctx.get("ok"):
            regime, bias, detail = ctx["regime"], ctx["bias"], ctx
    except Exception as exc:
        logger.debug("underlying ctx %s: %s", sym, exc)
    _CACHE[sym] = (now, regime, bias, detail)
    return regime, bias, detail
