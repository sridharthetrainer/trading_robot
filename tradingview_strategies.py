"""
tradingview_strategies.py — 7 High-Value TradingView Strategies

All are among TradingView's most-used community scripts,
adapted for NSE 5-minute intraday bars.

Sources:
  EMA Ribbon:        Multiple authors — trend strength via EMA alignment
  Waddah Attar:      Waddah Attar — explosion + volume filter
  Chaikin MF:        Marc Chaikin — institutional money flow
  Awesome Osc:       Bill Williams "Trading Chaos"
  Bollinger %B:      John Bollinger "Bollinger on Bollinger Bands"
  Ehlers Fisher:     John Ehlers "Cybernetic Analysis for Stocks and Futures"
  VIX Fix:           Larry Williams — synthetic VIX for any instrument
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _ema(arr: np.ndarray, n: int) -> float:
    """Fast EMA — returns last value only."""
    if len(arr) < n: return float(arr[-1])
    k = 2.0 / (n + 1)
    e = float(arr[0])
    for v in arr[1:]: e = float(v) * k + e * (1 - k)
    return e


# ─────────────────────────────────────────────────────────────────────────────
# 1. EMA RIBBON — 8 EMAs (3,5,8,13,21,34,55,89) — Fibonacci periods
# ─────────────────────────────────────────────────────────────────────────────
def run_ema_ribbon_strategy(
    df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "", **kw,
) -> Dict:
    """
    EMA Ribbon — 8 EMAs stacked in Fibonacci sequence.
    
    When all 8 EMAs are aligned (3>5>8>13>21>34>55>89) = strong uptrend.
    When ribbon fans out after compression = momentum starting.
    When price crosses through ribbon = potential reversal.
    
    Score based on how many EMAs are correctly stacked.
    """
    empty = {"strategy": "ema_ribbon", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 90: return empty
        c = df_c["close"].values
        periods = [3, 5, 8, 13, 21, 34, 55, 89]
        emas = [_ema(c[-p*3:], p) for p in periods]  # each EMA from enough history

        # Count aligned EMAs (bullish = each shorter above longer)
        bull_aligned = sum(1 for i in range(len(emas)-1) if emas[i] > emas[i+1])
        bear_aligned = sum(1 for i in range(len(emas)-1) if emas[i] < emas[i+1])
        price = float(c[-1])

        # Full alignment = all 7 pairs correct
        if bull_aligned >= 6 and price > emas[0]:
            score = 3.0 + (bull_aligned - 6) * 1.0
            return {"strategy": "ema_ribbon", "score": round(score, 2),
                    "direction": "BUY", "side": "BUY",
                    "ema_aligned": bull_aligned, "ribbon": "bullish_fan"}
        if bear_aligned >= 6 and price < emas[0]:
            score = 3.0 + (bear_aligned - 6) * 1.0
            return {"strategy": "ema_ribbon", "score": round(score, 2),
                    "direction": "SELL", "side": "SELL",
                    "ema_aligned": bear_aligned, "ribbon": "bearish_fan"}
        # Partial — ribbon just starting to align from compression
        if bull_aligned == 5 and price > emas[0]:
            return {"strategy": "ema_ribbon", "score": 2.0,
                    "direction": "BUY", "side": "BUY", "ribbon": "bull_forming"}
        if bear_aligned == 5 and price < emas[0]:
            return {"strategy": "ema_ribbon", "score": 2.0,
                    "direction": "SELL", "side": "SELL", "ribbon": "bear_forming"}
    except Exception as e: logger.debug("ema_ribbon: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 2. WADDAH ATTAR EXPLOSION — Volume × MACD momentum
# ─────────────────────────────────────────────────────────────────────────────
def run_waddah_attar_strategy(
    df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "", **kw,
) -> Dict:
    """
    Waddah Attar Explosion — one of TradingView's most-used NSE strategies.

    Combines:
      1. MACD difference (12,26) × sensitivity = explosion value
      2. Bollinger Band width = dead zone threshold
      3. Volume spike confirmation

    ENTRY:
      BUY:  Explosion > dead_zone AND explosion rising (green histogram)
      SELL: Explosion below dead_zone going negative (red histogram)

    Popular for NSE because it filters out low-momentum fake breakouts.
    """
    empty = {"strategy": "waddah_attar", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 40 or "volume" not in df_c.columns: return empty
        c = df_c["close"].values
        v = df_c["volume"].values
        sensitivity = 150  # standard sensitivity

        # MACD difference
        macd_fast = _ema(c, 12) - _ema(c, 26)
        macd_prev = _ema(c[:-1], 12) - _ema(c[:-1], 26)
        explosion = (macd_fast - macd_prev) * sensitivity

        # Bollinger Band width (20, 2) as dead zone
        sma20 = float(np.mean(c[-20:]))
        std20 = float(np.std(c[-20:]))
        dead_zone = std20 * 2  # BB width proxy

        # Volume
        vol_ratio = float(v[-1]) / (float(np.mean(v[-20:])) + 1e-9)

        bull_explosion = explosion > 0 and explosion > dead_zone and vol_ratio > 1.0
        bear_explosion = explosion < 0 and abs(explosion) > dead_zone and vol_ratio > 1.0

        if bull_explosion:
            score = 3.0 + min(2.0, explosion / (dead_zone + 1e-9) - 1) + (vol_ratio > 1.5) * 0.5
            return {"strategy": "waddah_attar", "score": round(score, 2),
                    "direction": "BUY", "side": "BUY",
                    "explosion": round(explosion, 2), "dead_zone": round(dead_zone, 2)}
        if bear_explosion:
            score = 3.0 + min(2.0, abs(explosion) / (dead_zone + 1e-9) - 1) + (vol_ratio > 1.5) * 0.5
            return {"strategy": "waddah_attar", "score": round(score, 2),
                    "direction": "SELL", "side": "SELL",
                    "explosion": round(explosion, 2)}
    except Exception as e: logger.debug("waddah_attar: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 3. CHAIKIN MONEY FLOW (CMF) — Institutional buying/selling pressure
# ─────────────────────────────────────────────────────────────────────────────
def run_chaikin_mf_strategy(
    df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "", **kw,
) -> Dict:
    """
    Chaikin Money Flow — measures institutional accumulation/distribution.

    CMF = sum(MFV, 20) / sum(volume, 20)
    where MFV = ((close-low - high-close) / (high-low)) × volume

    CMF > 0.1  = institutions accumulating (bullish)
    CMF < -0.1 = institutions distributing (bearish)
    Zero cross = change of institutional stance
    """
    empty = {"strategy": "chaikin_mf", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 22 or "volume" not in df_c.columns: return empty
        h = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        c = df_c["close"].values; v = df_c["volume"].values

        spread = h - l
        mfm    = np.where(spread > 0, ((c - l) - (h - c)) / spread, 0.0)
        mfv    = mfm * v

        cmf_now  = float(np.sum(mfv[-20:])) / (float(np.sum(v[-20:])) + 1e-9)
        cmf_prev = float(np.sum(mfv[-21:-1])) / (float(np.sum(v[-21:-1])) + 1e-9)

        zero_cross_bull = cmf_prev <= 0 < cmf_now
        zero_cross_bear = cmf_prev >= 0 > cmf_now

        if zero_cross_bull or cmf_now > 0.15:
            score = (4.5 if zero_cross_bull else 2.5) + min(1.5, cmf_now * 5)
            return {"strategy": "chaikin_mf", "score": round(score, 2),
                    "direction": "BUY", "side": "BUY", "cmf": round(cmf_now, 3)}
        if zero_cross_bear or cmf_now < -0.15:
            score = (4.5 if zero_cross_bear else 2.5) + min(1.5, abs(cmf_now) * 5)
            return {"strategy": "chaikin_mf", "score": round(score, 2),
                    "direction": "SELL", "side": "SELL", "cmf": round(cmf_now, 3)}
    except Exception as e: logger.debug("chaikin_mf: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 4. AWESOME OSCILLATOR — Bill Williams "Trading Chaos"
# ─────────────────────────────────────────────────────────────────────────────
def run_awesome_oscillator_strategy(
    df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "", **kw,
) -> Dict:
    """
    Awesome Oscillator = SMA(midpoint, 5) - SMA(midpoint, 34)
    where midpoint = (high + low) / 2

    Signals:
      Zero line cross (BUY/SELL) — momentum direction change
      Twin Peaks (BUY) — two peaks below zero, second peak higher
      Saucer (BUY) — three bars, middle is lowest, all below zero
      Divergence — price makes new low/high but AO doesn't
    """
    empty = {"strategy": "awesome_osc", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 40: return empty
        h = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        mid = (h + l) / 2

        ao_vals = []
        for i in range(34, len(mid)+1):
            s = float(np.mean(mid[max(0,i-5):i])) - float(np.mean(mid[max(0,i-34):i]))
            ao_vals.append(s)
        if len(ao_vals) < 3: return empty

        ao  = ao_vals[-1]
        ao1 = ao_vals[-2]
        ao2 = ao_vals[-3]

        # Zero line cross
        bull_cross = ao1 <= 0 < ao
        bear_cross = ao1 >= 0 > ao

        # Saucer — 3 consecutive bars, middle lowest, all same sign
        bull_saucer = ao > 0 and ao1 > 0 and ao2 > 0 and ao1 < ao2 and ao1 < ao
        bear_saucer = ao < 0 and ao1 < 0 and ao2 < 0 and ao1 > ao2 and ao1 > ao

        if bull_cross:
            return {"strategy": "awesome_osc", "score": 4.0,
                    "direction": "BUY", "side": "BUY", "ao": round(ao, 4),
                    "signal": "zero_cross_bull"}
        if bull_saucer:
            return {"strategy": "awesome_osc", "score": 3.0,
                    "direction": "BUY", "side": "BUY", "signal": "saucer_bull"}
        if bear_cross:
            return {"strategy": "awesome_osc", "score": 4.0,
                    "direction": "SELL", "side": "SELL", "ao": round(ao, 4),
                    "signal": "zero_cross_bear"}
        if bear_saucer:
            return {"strategy": "awesome_osc", "score": 3.0,
                    "direction": "SELL", "side": "SELL", "signal": "saucer_bear"}
    except Exception as e: logger.debug("awesome_osc: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 5. BOLLINGER BAND %B — Where price sits within the bands
# ─────────────────────────────────────────────────────────────────────────────
def run_bb_percentb_strategy(
    df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "", **kw,
) -> Dict:
    """
    Bollinger Band %B — scales price position within BB from 0 to 1.
    
    %B = (price - lower_band) / (upper_band - lower_band)
    %B > 1 = price above upper band (extremely overbought)
    %B < 0 = price below lower band (extremely oversold)
    %B = 0.5 = price at midband (VWAP equivalent)

    Combined with BandWidth (volatility) for squeeze + breakout detection.
    """
    empty = {"strategy": "bb_percentb", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 22: return empty
        c = df_c["close"].values
        sma = float(np.mean(c[-20:]))
        std = float(np.std(c[-20:]))
        upper = sma + 2 * std; lower = sma - 2 * std
        band_range = upper - lower

        pct_b = (float(c[-1]) - lower) / (band_range + 1e-9)
        prev_b = (float(c[-2]) - (sma - 2*float(np.std(c[-21:-1])))) / (band_range + 1e-9)

        # Bandwidth — low BW = squeeze, high = expansion
        bw = band_range / sma * 100
        avg_bw = float(np.std(c[-20:])) / float(np.mean(c[-20:])) * 100 * 4

        # Mean reversion: extreme values returning toward center
        oversold   = pct_b < 0 and prev_b < pct_b   # was below band, coming back
        overbought = pct_b > 1 and prev_b > pct_b   # was above band, coming back

        # Breakout from squeeze: BW expanding after low BW
        squeeze_break_bull = pct_b > 0.8 and bw > avg_bw * 1.2
        squeeze_break_bear = pct_b < 0.2 and bw > avg_bw * 1.2

        if oversold:
            score = 3.0 + (abs(pct_b) * 2)
            return {"strategy": "bb_percentb", "score": round(min(score, 5.0), 2),
                    "direction": "BUY", "side": "BUY", "pct_b": round(pct_b, 3)}
        if squeeze_break_bull:
            return {"strategy": "bb_percentb", "score": 3.5,
                    "direction": "BUY", "side": "BUY",
                    "pct_b": round(pct_b, 3), "signal": "squeeze_break"}
        if overbought:
            score = 3.0 + (abs(pct_b - 1) * 2)
            return {"strategy": "bb_percentb", "score": round(min(score, 5.0), 2),
                    "direction": "SELL", "side": "SELL", "pct_b": round(pct_b, 3)}
        if squeeze_break_bear:
            return {"strategy": "bb_percentb", "score": 3.5,
                    "direction": "SELL", "side": "SELL",
                    "pct_b": round(pct_b, 3), "signal": "squeeze_break"}
    except Exception as e: logger.debug("bb_percentb: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 6. EHLERS FISHER TRANSFORM — John Ehlers DSP for markets
# ─────────────────────────────────────────────────────────────────────────────
def run_ehlers_fisher_strategy(
    df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "", **kw,
) -> Dict:
    """
    Ehlers Fisher Transform — converts price to Gaussian distribution.
    Gives extremely clean BUY/SELL signals at turning points.

    Fisher(x) = 0.5 × ln((1+x) / (1-x))
    where x = (price - min) / (max - min) × 2 - 1

    Signal: Fisher line crosses its signal line.
    Ehlers: "Fisher extreme values (> 1.5 or < -1.5) = major turning point"
    """
    empty = {"strategy": "ehlers_fisher", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 12: return empty
        h = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        period = 10

        fisher_vals = []
        for i in range(period, len(h)+1):
            hh = float(np.max(h[i-period:i]))
            ll = float(np.min(l[i-period:i]))
            value = (float(h[i-1]+l[i-1])/2 - ll) / max(hh - ll, 1e-9)
            value = max(-0.999, min(0.999, 2 * value - 1))
            fish  = 0.5 * np.log((1 + value) / (1 - value + 1e-9))
            fisher_vals.append(fish)

        if len(fisher_vals) < 2: return empty
        fish_now  = fisher_vals[-1]
        fish_prev = fisher_vals[-2]

        # Cross: Fisher crosses previous Fisher value (signal line)
        bull_cross = fish_prev < 0 and fish_now > 0
        bear_cross = fish_prev > 0 and fish_now < 0
        extreme_bull = fish_now > 1.5 and fish_now < fish_prev  # extreme + turning
        extreme_bear = fish_now < -1.5 and fish_now > fish_prev

        if bull_cross:
            return {"strategy": "ehlers_fisher", "score": 4.5,
                    "direction": "BUY", "side": "BUY", "fisher": round(fish_now, 3)}
        if extreme_bull:
            return {"strategy": "ehlers_fisher", "score": 4.0,
                    "direction": "BUY", "side": "BUY",
                    "fisher": round(fish_now, 3), "signal": "extreme_reversal"}
        if bear_cross:
            return {"strategy": "ehlers_fisher", "score": 4.5,
                    "direction": "SELL", "side": "SELL", "fisher": round(fish_now, 3)}
        if extreme_bear:
            return {"strategy": "ehlers_fisher", "score": 4.0,
                    "direction": "SELL", "side": "SELL",
                    "fisher": round(fish_now, 3), "signal": "extreme_reversal"}
    except Exception as e: logger.debug("ehlers_fisher: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 7. WILLIAMS VIX FIX — Larry Williams synthetic VIX for any stock
# ─────────────────────────────────────────────────────────────────────────────
def run_vix_fix_strategy(
    df: pd.DataFrame, df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "", **kw,
) -> Dict:
    """
    Williams VIX Fix — creates a VIX-like fear indicator for any stock.

    VIX Fix = (highest_close(22) - low) / highest_close(22) × 100

    Logic: When stocks are in fear (selling), lowest bar is far from
    recent high = VIX Fix spikes. This identifies capitulation bottoms.

    Brilliant for NSE stocks that don't have options (no real VIX).
    VIX Fix > 20 = extreme fear = potential bottom.
    VIX Fix > 30 = panic = highest probability reversal.
    """
    empty = {"strategy": "vix_fix", "score": 0.0, "direction": None, "side": None}
    try:
        _INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
        if symbol.upper() in _INDICES:
            return empty  # use real VIX for indices

        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 25: return empty
        c = df_c["close"].values
        l = df_c["low"].values if "low" in df_c.columns else c

        highest_close = float(np.max(c[-22:]))
        cur_low       = float(l[-1])
        vf            = (highest_close - cur_low) / highest_close * 100

        # Threshold: extreme fear
        if vf >= 30:
            score = 5.0
        elif vf >= 20:
            score = 3.5
        elif vf >= 15:
            score = 2.5
        else:
            score = 0.0

        if score >= 2.5:
            return {
                "strategy":  "vix_fix",
                "score":     round(score, 2),
                "direction": "BUY",
                "side":      "BUY",
                "vix_fix":   round(vf, 1),
                "signal":    ("Panic bottom" if vf >= 30
                              else "Extreme fear" if vf >= 20
                              else "Fear spike"),
            }
    except Exception as e: logger.debug("vix_fix: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 8. FULL VOLUME PROFILE — VRVP + SVP + HVN/LVN + EMA Cloud Filter
#    (tradewrite @Tradewrite method — institutional S/D zones)
# ─────────────────────────────────────────────────────────────────────────────
def run_volume_profile_full_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Full Volume Profile strategy — VRVP + SVP style as described by @Tradewrite.

    Key concepts:
      VRVP = Visible Range Volume Profile — volume at every price over the visible range
      SVP  = Session Volume Profile — volume per session (today only)
      POC  = Point of Control — price with most volume (price magnet)
      VAH  = Value Area High — top of 70% volume zone (resistance)
      VAL  = Value Area Low — bottom of 70% volume zone (support)
      HVN  = High Volume Node — institutional activity zones (price sticks here)
      LVN  = Low Volume Node — price moves through quickly (air pockets)

    tradewrite method:
      1. Find POC alignment with price structure
      2. Mark HVN as support/resistance (price HAS to react)
      3. LVN = fast move zone — expect 20-30 point runs
      4. EMA Cloud (20/50) for direction filter
      5. Only trade when price is AT an institutional volume level

    ENTRY:
      BUY:  Price at VAL/HVN below POC + EMA20>EMA50 (cloud bullish)
      SELL: Price at VAH/HVN above POC + EMA20<EMA50 (cloud bearish)
    """
    empty = {"strategy": "volume_profile_full", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 30 or "volume" not in df_c.columns:
            return empty

        h  = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l  = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        c  = df_c["close"].values
        v  = df_c["volume"].values
        price = float(c[-1])

        # ── Build Volume Profile (price → volume histogram) ────────────────
        price_min = float(np.min(l))
        price_max = float(np.max(h))
        if price_max <= price_min: return empty

        n_bins = 50  # 50 price buckets (like row size 200 scaled)
        bins   = np.linspace(price_min, price_max, n_bins + 1)
        vol_by_price = np.zeros(n_bins)

        for i in range(len(c)):
            # Distribute bar volume across its price range
            bar_lo  = float(l[i]); bar_hi = float(h[i])
            bar_vol = float(v[i])
            for b in range(n_bins):
                bin_lo = bins[b]; bin_hi = bins[b+1]
                # Overlap between bar and bin
                overlap_lo = max(bar_lo, bin_lo)
                overlap_hi = min(bar_hi, bin_hi)
                if overlap_hi > overlap_lo:
                    frac = (overlap_hi - overlap_lo) / max(bar_hi - bar_lo, 1e-9)
                    vol_by_price[b] += bar_vol * frac

        # ── Key Levels ─────────────────────────────────────────────────────
        # POC = bin with most volume
        poc_idx  = int(np.argmax(vol_by_price))
        poc      = float((bins[poc_idx] + bins[poc_idx+1]) / 2)

        # Value Area (70% of total volume around POC)
        total_vol   = float(np.sum(vol_by_price))
        target_vol  = total_vol * 0.70
        va_vol      = vol_by_price[poc_idx]
        va_lo = va_hi = poc_idx
        while va_vol < target_vol and (va_lo > 0 or va_hi < n_bins-1):
            up_vol = vol_by_price[va_hi+1] if va_hi < n_bins-1 else 0
            dn_vol = vol_by_price[va_lo-1] if va_lo > 0 else 0
            if up_vol >= dn_vol and va_hi < n_bins-1:
                va_hi += 1; va_vol += up_vol
            elif va_lo > 0:
                va_lo -= 1; va_vol += dn_vol
            else:
                break
        vah = float((bins[va_hi] + bins[va_hi+1]) / 2)
        val = float((bins[va_lo] + bins[va_lo+1]) / 2)

        # HVN = top 20% volume bins (institutional zones)
        hvn_threshold = np.percentile(vol_by_price, 80)
        hvn_prices    = [(bins[i]+bins[i+1])/2 for i in range(n_bins)
                         if vol_by_price[i] >= hvn_threshold]

        # LVN = bottom 20% volume bins (air pockets — fast move zones)
        lvn_threshold = np.percentile(vol_by_price, 20)
        lvn_prices    = [(bins[i]+bins[i+1])/2 for i in range(n_bins)
                         if vol_by_price[i] <= lvn_threshold]

        # ── EMA Cloud (20/50) for direction filter ─────────────────────────
        def ema_val(arr, n):
            if len(arr) < n: return float(arr[-1])
            k = 2/(n+1); e = float(arr[0])
            for x in arr[1:]: e = float(x)*k + e*(1-k)
            return e

        ema20 = ema_val(c[-25:], 20)
        ema50 = ema_val(c[-55:], 50) if len(c) >= 55 else ema_val(c, len(c)//2)
        cloud_bull = ema20 > ema50  # cloud bullish
        cloud_bear = ema20 < ema50  # cloud bearish
        cloud_flip = abs(ema20 - ema50) / max(ema50, 1) < 0.001  # near flip

        # ── Signal Logic ───────────────────────────────────────────────────
        bin_size = (price_max - price_min) / n_bins

        # Is price at/near a key level?
        near_vah  = abs(price - vah) < bin_size * 1.5
        near_val  = abs(price - val) < bin_size * 1.5
        near_poc  = abs(price - poc) < bin_size * 1.5
        near_hvn  = any(abs(price - h2) < bin_size * 2 for h2 in hvn_prices)
        in_lvn    = any(abs(price - lv) < bin_size * 1.5 for lv in lvn_prices)

        buy_score = sell_score = 0.0

        # BUY: at VAL/HVN support + cloud bullish
        if near_val and cloud_bull:
            buy_score += 3.5
        if near_poc and cloud_bull and price < poc:
            buy_score += 2.5  # POC magnet pulling up
        if near_hvn and cloud_bull and price < poc:
            buy_score += 2.0
        if in_lvn and cloud_bull:
            buy_score += 1.5  # air pocket = fast move expected

        # SELL: at VAH/HVN resistance + cloud bearish
        if near_vah and cloud_bear:
            sell_score += 3.5
        if near_poc and cloud_bear and price > poc:
            sell_score += 2.5
        if near_hvn and cloud_bear and price > poc:
            sell_score += 2.0
        if in_lvn and cloud_bear:
            sell_score += 1.5

        # Cloud flip = reduce score (noise zone)
        if cloud_flip:
            buy_score  *= 0.5
            sell_score *= 0.5

        if buy_score >= 2.5 and buy_score > sell_score:
            return {
                "strategy":   "volume_profile_full",
                "score":      round(buy_score, 2),
                "direction":  "BUY", "side": "BUY",
                "poc":        round(poc, 2),
                "vah":        round(vah, 2),
                "val":        round(val, 2),
                "cloud":      "bullish",
                "vp_zone":    ("VAL" if near_val else "POC" if near_poc else "HVN"),
            }
        if sell_score >= 2.5:
            return {
                "strategy":   "volume_profile_full",
                "score":      round(sell_score, 2),
                "direction":  "SELL", "side": "SELL",
                "poc":        round(poc, 2),
                "vah":        round(vah, 2),
                "val":        round(val, 2),
                "cloud":      "bearish",
                "vp_zone":    ("VAH" if near_vah else "POC" if near_poc else "HVN"),
            }
    except Exception as e:
        logger.debug("volume_profile_full: %s", e)
    return empty


# ── TTM Squeeze (Momentum Oscillator) ─────────────────────────────────────
try:
    from ttm_squeeze import run_ttm_squeeze_strategy
except ImportError:
    def run_ttm_squeeze_strategy(df, symbol="", **kwargs):
        """TTM Squeeze — momentum histogram strategy."""
        return {}
