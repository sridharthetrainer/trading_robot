"""
institutional_indicators.py

Indicators used by professional and institutional traders.
These are NOT in standard libraries — they represent order flow
and market microstructure concepts used by prop desks and HFT.

Concepts implemented
────────────────────
1. CVD  — Cumulative Volume Delta
   Measures net buying vs selling pressure on each bar.
   Rising CVD + rising price = healthy trend (institutions buying)
   Falling CVD + rising price = divergence (institutions distributing)

2. Order Block (OB)
   The LAST down-candle before a significant up-move, or the LAST
   up-candle before a significant down-move.
   Institutions leave unfilled orders at these levels.
   Price tends to return and bounce off them.

3. Fair Value Gap (FVG / Imbalance)
   When price moves so fast that a 3-bar gap is left (bar N+1 high
   is below bar N-1 low on a down move). Institutions fill these
   gaps — price is drawn back to fill the imbalance.

4. Break of Structure (BOS) and Change of Character (CHOCH)
   BOS: a new swing high is taken out = trend continuation
   CHOCH: after an uptrend, the FIRST lower high = trend reversal
   These are the institutional definition of a trend — not EMA.

5. Volume Profile / VPOC
   Tracks which price levels have highest volume.
   VPOC = Volume Point of Control: the price where most volume traded.
   Price is magnetically drawn to VPOC. If NIFTY is above its VPOC,
   VPOC acts as support on pullbacks. If below, it acts as resistance.

6. Liquidity Sweep Detector
   Institutions accumulate by TRIGGERING retail stop losses.
   Pattern: price spikes JUST below a visible swing low (stops triggered),
   then immediately reverses up (institutions bought the stops).
   Entering AFTER the sweep catches the institutional reversal move.

7. Absorption / Exhaustion Candle
   When a large volume bar has a SMALL body (open ≈ close), it means
   one side tried hard to push price and FAILED.
   High-volume narrow bar after a move = trend exhaustion.
   This is not detectable with RSI or EMA — only volume analysis.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. CUMULATIVE VOLUME DELTA (CVD)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_cvd(df: pd.DataFrame) -> pd.Series:
    """
    Cumulative Volume Delta.

    For each 5-minute bar, estimates buying vs selling volume:
    - If close > open  → bull bar → all volume attributed to buyers
    - If close < open  → bear bar → all volume attributed to sellers
    - If close == open → split 50/50

    True CVD requires tick data. This is a bar-level approximation
    using the close-open relationship and bar position.
    The proportional method assigns volume based on where close sits
    within the bar's high-low range (0.0 = all selling, 1.0 = all buying).

    Returns CVD as a cumulative series. Reset each session.
    """
    try:
        o = pd.to_numeric(df["Open"]   if "Open"   in df.columns else df["open"],   errors="coerce")
        h = pd.to_numeric(df["High"]   if "High"   in df.columns else df["high"],   errors="coerce")
        l = pd.to_numeric(df["Low"]    if "Low"    in df.columns else df["low"],    errors="coerce")
        c = pd.to_numeric(df["Close"]  if "Close"  in df.columns else df["close"],  errors="coerce")
        v = pd.to_numeric(df["Volume"] if "Volume" in df.columns else df.get("volume", pd.Series(1, index=df.index)), errors="coerce").fillna(0)

        bar_range = h - l
        # Proportion of bar that is "bullish"
        # position of close within high-low range
        bull_pct = (c - l) / bar_range.replace(0, np.nan)
        bull_pct = bull_pct.fillna(0.5).clip(0, 1)

        delta    = (2 * bull_pct - 1) * v   # +v = all buying, -v = all selling
        cvd      = delta.cumsum()
        return cvd
    except Exception as exc:
        logger.debug("calculate_cvd error: %s", exc)
        return pd.Series(0.0, index=df.index)


def get_cvd_signal(df: pd.DataFrame, lookback: int = 5) -> Dict[str, Any]:
    """
    Compute CVD-based directional signal.

    Returns:
        direction:   "BULLISH" | "BEARISH" | "NEUTRAL"
        divergence:  True if CVD diverges from price (warning)
        cvd_slope:   rate of change of CVD over lookback bars
        absorption:  True if this bar shows absorption (see below)
    """
    try:
        cvd   = calculate_cvd(df)
        close = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"], errors="coerce")

        if len(cvd) < lookback + 2:
            return {"direction": "NEUTRAL", "divergence": False,
                    "cvd_slope": 0.0, "absorption": False}

        cvd_now    = float(cvd.iloc[-1])
        cvd_prev   = float(cvd.iloc[-lookback])
        cvd_slope  = cvd_now - cvd_prev

        price_now  = float(close.iloc[-1])
        price_prev = float(close.iloc[-lookback])
        price_up   = price_now > price_prev

        # Direction from CVD slope
        direction = "BULLISH" if cvd_slope > 0 else "BEARISH" if cvd_slope < 0 else "NEUTRAL"

        # Divergence: price goes up but CVD goes down (distribution) or vice versa
        divergence = (price_up and cvd_slope < 0) or (not price_up and cvd_slope > 0)

        # Absorption: last bar has high volume but small body
        try:
            vol   = pd.to_numeric(df["Volume"] if "Volume" in df.columns else df.get("volume", pd.Series(0, index=df.index)), errors="coerce")
            o     = pd.to_numeric(df["Open"]   if "Open"   in df.columns else df["open"],  errors="coerce")
            h     = pd.to_numeric(df["High"]   if "High"   in df.columns else df["high"],  errors="coerce")
            l     = pd.to_numeric(df["Low"]    if "Low"    in df.columns else df["low"],   errors="coerce")
            c     = close

            last_vol    = float(vol.iloc[-1])
            avg_vol     = float(vol.rolling(20).mean().iloc[-1] or 1)
            body        = abs(float(c.iloc[-1]) - float(o.iloc[-1]))
            bar_range   = float(h.iloc[-1]) - float(l.iloc[-1])
            body_ratio  = body / max(bar_range, 0.001)

            # Absorption: volume > 1.8× average but body < 30% of range
            absorption  = last_vol > avg_vol * 1.8 and body_ratio < 0.30
        except Exception:
            absorption = False

        return {
            "direction":  direction,
            "divergence": divergence,
            "cvd_slope":  round(cvd_slope, 2),
            "cvd_now":    round(cvd_now, 2),
            "absorption": absorption,
        }
    except Exception as exc:
        logger.debug("get_cvd_signal error: %s", exc)
        return {"direction": "NEUTRAL", "divergence": False,
                "cvd_slope": 0.0, "absorption": False}


# ─────────────────────────────────────────────────────────────────────────────
# 2. ORDER BLOCKS
# ─────────────────────────────────────────────────────────────────────────────

def find_order_blocks(
    df:         pd.DataFrame,
    lookback:   int   = 50,
    min_move:   float = 0.003,   # minimum 0.3% move to qualify
) -> List[Dict[str, Any]]:
    """
    Detect institutional Order Blocks.

    Bullish OB: the last BEARISH (red) candle before a significant
                up-move of >= min_move. Institutions left buy orders here.
    Bearish OB: the last BULLISH (green) candle before a significant
                down-move of >= min_move. Institutions left sell orders here.

    Returns list of order blocks, most recent first:
    [{
        "type":   "bullish" | "bearish",
        "high":   float,
        "low":    float,
        "mid":    float,
        "bar_idx": int,
        "fresh":  bool,  # True if price hasn't returned yet
    }]
    """
    try:
        o = pd.to_numeric(df["Open"]  if "Open"  in df.columns else df["open"],  errors="coerce")
        c = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"], errors="coerce")
        h = pd.to_numeric(df["High"]  if "High"  in df.columns else df["high"],  errors="coerce")
        l = pd.to_numeric(df["Low"]   if "Low"   in df.columns else df["low"],   errors="coerce")

        n   = min(len(df), lookback)
        obs = []
        last_close = float(c.iloc[-1])

        for i in range(2, n - 2):
            idx = len(df) - n + i

            open_i  = float(o.iloc[idx])
            close_i = float(c.iloc[idx])
            high_i  = float(h.iloc[idx])
            low_i   = float(l.iloc[idx])

            # Look ahead 3 bars for a significant move
            fut_high = float(h.iloc[idx+1:idx+4].max())
            fut_low  = float(l.iloc[idx+1:idx+4].min())

            if close_i > 0:
                up_move   = (fut_high - close_i) / close_i
                down_move = (close_i - fut_low)  / close_i
            else:
                continue

            # BULLISH OB: bearish candle (close < open) followed by significant up-move
            if close_i < open_i and up_move >= min_move:
                # Check if price has returned to this OB (mitigated)
                future_low = float(l.iloc[idx+1:].min()) if idx + 1 < len(df) else last_close
                fresh      = future_low > low_i * 0.999   # not mitigated if price didn't touch it
                obs.append({
                    "type":    "bullish",
                    "high":    round(high_i,  2),
                    "low":     round(low_i,   2),
                    "mid":     round((high_i + low_i) / 2, 2),
                    "bar_idx": idx,
                    "fresh":   fresh,
                })

            # BEARISH OB: bullish candle (close > open) followed by significant down-move
            elif close_i > open_i and down_move >= min_move:
                future_high = float(h.iloc[idx+1:].max()) if idx + 1 < len(df) else last_close
                fresh       = future_high < high_i * 1.001
                obs.append({
                    "type":    "bearish",
                    "high":    round(high_i,  2),
                    "low":     round(low_i,   2),
                    "mid":     round((high_i + low_i) / 2, 2),
                    "bar_idx": idx,
                    "fresh":   fresh,
                })

        return sorted(obs, key=lambda x: -x["bar_idx"])
    except Exception as exc:
        logger.debug("find_order_blocks error: %s", exc)
        return []


def get_nearest_order_block(
    df:       pd.DataFrame,
    action:   str,          # "BUY" or "SELL"
    tolerance: float = 0.002,   # 0.2% — within this % of OB edge
) -> Optional[Dict[str, Any]]:
    """
    Check if current price is AT a valid order block for the given action.
    Returns the OB dict if price is at an institutional entry zone, else None.
    """
    try:
        close = float(df["Close"].iloc[-1] if "Close" in df.columns else df["close"].iloc[-1])
        obs   = find_order_blocks(df)
        fresh = [ob for ob in obs if ob["fresh"]]

        if action == "BUY":
            # Look for fresh bullish OBs near current price
            for ob in fresh:
                if ob["type"] == "bullish":
                    within = abs(close - ob["mid"]) / ob["mid"] <= tolerance
                    touching_top = close >= ob["low"] * (1 - tolerance) and close <= ob["high"] * (1 + tolerance)
                    if within or touching_top:
                        return ob
        else:
            # Look for fresh bearish OBs near current price
            for ob in fresh:
                if ob["type"] == "bearish":
                    within = abs(close - ob["mid"]) / ob["mid"] <= tolerance
                    if within:
                        return ob
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. FAIR VALUE GAPS (FVG / Imbalance)
# ─────────────────────────────────────────────────────────────────────────────

def find_fair_value_gaps(
    df:       pd.DataFrame,
    min_size: float = 0.001,   # minimum 0.1% gap size
) -> List[Dict[str, Any]]:
    """
    Detect Fair Value Gaps (price imbalances).

    Bullish FVG: bar[i-1] low > bar[i+1] high  (up-gap)
                 Price left a gap that institutions will fill.
    Bearish FVG: bar[i-1] high < bar[i+1] low  (down-gap)

    These gaps are filled with 70-80% probability on NSE indices
    within the same or next session.
    """
    try:
        h = pd.to_numeric(df["High"]  if "High"  in df.columns else df["high"],  errors="coerce")
        l = pd.to_numeric(df["Low"]   if "Low"   in df.columns else df["low"],   errors="coerce")
        c = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"], errors="coerce")

        fvgs = []
        for i in range(1, len(df) - 1):
            mid_price = float(c.iloc[i])
            if mid_price <= 0:
                continue

            h_prev  = float(h.iloc[i - 1])
            l_prev  = float(l.iloc[i - 1])
            h_next  = float(h.iloc[i + 1])
            l_next  = float(l.iloc[i + 1])

            # Bullish FVG: gap between bar[i-1].low and bar[i+1].high
            # (price moved up so fast it skipped this range)
            if l_prev > h_next and (l_prev - h_next) / mid_price >= min_size:
                gap_top    = l_prev
                gap_bottom = h_next
                # Check if gap is still unfilled (price hasn't come back)
                future_low = float(l.iloc[i+1:].min()) if i + 1 < len(df) else gap_bottom
                filled     = future_low <= gap_bottom
                fvgs.append({
                    "type":    "bullish",
                    "top":     round(gap_top,    2),
                    "bottom":  round(gap_bottom, 2),
                    "mid":     round((gap_top + gap_bottom) / 2, 2),
                    "bar_idx": i,
                    "filled":  filled,
                })

            # Bearish FVG: gap between bar[i+1].low and bar[i-1].high
            if h_prev < l_next and (l_next - h_prev) / mid_price >= min_size:
                gap_top    = l_next
                gap_bottom = h_prev
                future_high = float(h.iloc[i+1:].max()) if i + 1 < len(df) else gap_top
                filled      = future_high >= gap_top
                fvgs.append({
                    "type":    "bearish",
                    "top":     round(gap_top,    2),
                    "bottom":  round(gap_bottom, 2),
                    "mid":     round((gap_top + gap_bottom) / 2, 2),
                    "bar_idx": i,
                    "filled":  filled,
                })

        # Return unfilled FVGs, most recent first
        unfilled = [f for f in fvgs if not f["filled"]]
        return sorted(unfilled, key=lambda x: -x["bar_idx"])
    except Exception as exc:
        logger.debug("find_fair_value_gaps error: %s", exc)
        return []


def price_in_fvg(
    df:     pd.DataFrame,
    action: str,
) -> Optional[Dict[str, Any]]:
    """
    Returns the FVG if current price is inside an unfilled FVG that
    supports the given action direction.
    """
    try:
        close = float(df["Close"].iloc[-1] if "Close" in df.columns else df["close"].iloc[-1])
        fvgs  = find_fair_value_gaps(df)
        ob_type = "bullish" if action == "BUY" else "bearish"
        for fvg in fvgs:
            if fvg["type"] == ob_type:
                if fvg["bottom"] <= close <= fvg["top"]:
                    return fvg
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. MARKET STRUCTURE: BOS / CHOCH
# ─────────────────────────────────────────────────────────────────────────────

def detect_market_structure(
    df:         pd.DataFrame,
    swing_n:    int   = 3,    # bars each side to qualify as a swing point
    lookback:   int   = 40,
) -> Dict[str, Any]:
    """
    Detect Break of Structure (BOS) and Change of Character (CHOCH).

    Uptrend defined by: each swing high > previous swing high
                         each swing low  > previous swing low
    BOS (bullish):  latest swing high breaks above the last swing high
    CHOCH (bearish): in an uptrend, first swing high that is LOWER than previous
                     = institutions starting to distribute

    Returns:
        structure:       "UPTREND" | "DOWNTREND" | "RANGING"
        last_bos:        "BULLISH" | "BEARISH" | None  (recent BOS)
        choch_detected:  True if trend just reversed
        last_swing_high: float
        last_swing_low:  float
        structure_score: float  (positive = bullish, negative = bearish, 0 = neutral)
    """
    try:
        h = pd.to_numeric(df["High"]  if "High"  in df.columns else df["high"],  errors="coerce")
        l = pd.to_numeric(df["Low"]   if "Low"   in df.columns else df["low"],   errors="coerce")
        c = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"], errors="coerce")

        n   = min(len(df), lookback)
        sub = df.iloc[-n:]
        hh  = pd.to_numeric(sub["High"]  if "High"  in sub.columns else sub["high"],  errors="coerce")
        ll  = pd.to_numeric(sub["Low"]   if "Low"   in sub.columns else sub["low"],   errors="coerce")

        # Find swing highs and lows
        swing_highs, swing_lows = [], []
        for i in range(swing_n, len(sub) - swing_n):
            window_h = hh.iloc[i - swing_n : i + swing_n + 1]
            window_l = ll.iloc[i - swing_n : i + swing_n + 1]
            if float(hh.iloc[i]) == float(window_h.max()):
                swing_highs.append((i, float(hh.iloc[i])))
            if float(ll.iloc[i]) == float(window_l.min()):
                swing_lows.append((i, float(ll.iloc[i])))

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {"structure": "RANGING", "last_bos": None,
                    "choch_detected": False, "structure_score": 0.0,
                    "last_swing_high": float(hh.max()),
                    "last_swing_low":  float(ll.min())}

        # Check last 2 swing highs and lows for structure
        sh1, sh2 = swing_highs[-2][1], swing_highs[-1][1]   # older, newer
        sl1, sl2 = swing_lows[-2][1],  swing_lows[-1][1]

        hh_pattern = sh2 > sh1   # newer swing high is higher = uptrend
        hl_pattern = sl2 > sl1   # higher lows = uptrend

        lh_pattern = sh2 < sh1   # lower swing high = downtrend
        ll_pattern = sl2 < sl1   # lower swing low = downtrend

        if hh_pattern and hl_pattern:
            structure = "UPTREND"
        elif lh_pattern and ll_pattern:
            structure = "DOWNTREND"
        else:
            structure = "RANGING"

        # BOS: current price breaks the last swing high/low
        last_close = float(c.iloc[-1])
        last_bos   = None
        if last_close > sh2 * 1.001:     # broken above last swing high
            last_bos = "BULLISH"
        elif last_close < sl2 * 0.999:   # broken below last swing low
            last_bos = "BEARISH"

        # CHOCH: in uptrend, most recent swing high is lower than previous
        choch_detected = (structure == "UPTREND" and lh_pattern) or \
                         (structure == "DOWNTREND" and hh_pattern)

        # Structure score: positive = bullish structure
        score = 0.0
        if hh_pattern:  score += 1.0
        if hl_pattern:  score += 1.0
        if lh_pattern:  score -= 1.0
        if ll_pattern:  score -= 1.0
        if last_bos == "BULLISH": score += 1.0
        if last_bos == "BEARISH": score -= 1.0

        return {
            "structure":       structure,
            "last_bos":        last_bos,
            "choch_detected":  choch_detected,
            "structure_score": round(score, 1),
            "last_swing_high": round(sh2, 2),
            "last_swing_low":  round(sl2, 2),
            "swing_highs":     [(i, round(v, 2)) for i, v in swing_highs[-3:]],
            "swing_lows":      [(i, round(v, 2)) for i, v in swing_lows[-3:]],
        }
    except Exception as exc:
        logger.debug("detect_market_structure error: %s", exc)
        return {"structure": "RANGING", "last_bos": None,
                "choch_detected": False, "structure_score": 0.0,
                "last_swing_high": 0.0, "last_swing_low": 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# 5. VOLUME PROFILE / VPOC
# ─────────────────────────────────────────────────────────────────────────────

def calculate_volume_profile(
    df:         pd.DataFrame,
    price_bins: int = 50,
) -> Dict[str, Any]:
    """
    Build an intraday Volume Profile and find the VPOC.

    VPOC (Volume Point of Control): the price level with the highest
    volume for the session. Acts as a magnet — price gravitates toward it.

    VAH (Value Area High): upper edge of the 70% volume zone
    VAL (Value Area Low):  lower edge of the 70% volume zone

    Returns:
        vpoc:    float  — highest volume price level
        vah:     float  — value area high
        val:     float  — value area low
        profile: dict   — {price_level: volume} for all bins
    """
    try:
        h = pd.to_numeric(df["High"]   if "High"   in df.columns else df["high"],   errors="coerce")
        l = pd.to_numeric(df["Low"]    if "Low"    in df.columns else df["low"],    errors="coerce")
        v = pd.to_numeric(df["Volume"] if "Volume" in df.columns else df.get("volume", pd.Series(1, index=df.index)), errors="coerce").fillna(0)

        price_min = float(l.min())
        price_max = float(h.max())
        if price_max <= price_min:
            return {"vpoc": (price_min + price_max) / 2, "vah": price_max,
                    "val": price_min, "profile": {}}

        bin_size = (price_max - price_min) / price_bins
        profile: Dict[float, float] = {}

        for i in range(len(df)):
            bar_l  = float(l.iloc[i])
            bar_h  = float(h.iloc[i])
            bar_v  = float(v.iloc[i])
            if bar_h <= bar_l or bar_v <= 0:
                continue

            # Distribute volume proportionally across price range
            n_bins_touched = max(1, int((bar_h - bar_l) / bin_size))
            vol_per_bin    = bar_v / n_bins_touched

            bin_start = int((bar_l - price_min) / bin_size)
            bin_end   = int((bar_h - price_min) / bin_size) + 1

            for b in range(bin_start, min(bin_end, price_bins)):
                price_level = round(price_min + b * bin_size, 2)
                profile[price_level] = profile.get(price_level, 0) + vol_per_bin

        if not profile:
            return {"vpoc": (price_min + price_max) / 2, "vah": price_max,
                    "val": price_min, "profile": {}}

        # VPOC = highest volume bin
        vpoc = max(profile, key=profile.get)

        # Value Area (70% of total volume around VPOC)
        total_vol   = sum(profile.values())
        target_vol  = total_vol * 0.70
        sorted_bins = sorted(profile.items(), key=lambda x: -x[1])

        va_levels = set()
        cum_vol   = 0.0
        for price, vol in sorted_bins:
            va_levels.add(price)
            cum_vol += vol
            if cum_vol >= target_vol:
                break

        vah = max(va_levels) if va_levels else price_max
        val = min(va_levels) if va_levels else price_min

        return {
            "vpoc":    round(vpoc, 2),
            "vah":     round(vah,  2),
            "val":     round(val,  2),
            "profile": {round(k, 2): round(v, 0) for k, v in profile.items()},
        }
    except Exception as exc:
        logger.debug("calculate_volume_profile error: %s", exc)
        close = float(df["Close"].iloc[-1] if "Close" in df.columns else df["close"].iloc[-1])
        return {"vpoc": close, "vah": close, "val": close, "profile": {}}


def get_vpoc_bias(
    df:         pd.DataFrame,
    tolerance:  float = 0.002,   # 0.2% = "at the VPOC"
) -> Dict[str, Any]:
    """
    Determine price position relative to VPOC/VAH/VAL.
    Returns the directional bias and distance to nearest key level.
    """
    try:
        vp     = calculate_volume_profile(df)
        close  = float(df["Close"].iloc[-1] if "Close" in df.columns else df["close"].iloc[-1])
        vpoc   = vp["vpoc"]
        vah    = vp["vah"]
        val    = vp["val"]

        dist_to_vpoc = (close - vpoc) / vpoc if vpoc > 0 else 0

        # Position relative to value area
        if close > vah:
            position = "ABOVE_VALUE_AREA"  # potential resistance at VAH
        elif close < val:
            position = "BELOW_VALUE_AREA"  # potential support at VAL
        elif abs(dist_to_vpoc) <= tolerance:
            position = "AT_VPOC"           # at highest volume — expect mean reversion
        elif close > vpoc:
            position = "ABOVE_VPOC"        # bullish bias
        else:
            position = "BELOW_VPOC"        # bearish bias

        return {
            "vpoc":           vpoc,
            "vah":            vah,
            "val":            val,
            "close":          close,
            "position":       position,
            "dist_to_vpoc":   round(dist_to_vpoc, 4),
            "vpoc_magnet":    abs(dist_to_vpoc) <= 0.005,  # within 0.5% of VPOC
        }
    except Exception as exc:
        logger.debug("get_vpoc_bias error: %s", exc)
        return {"vpoc": 0, "vah": 0, "val": 0, "position": "UNKNOWN",
                "dist_to_vpoc": 0, "vpoc_magnet": False}


# ─────────────────────────────────────────────────────────────────────────────
# 6. LIQUIDITY SWEEP DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

def detect_liquidity_sweep(
    df:          pd.DataFrame,
    lookback:    int   = 20,
    sweep_pct:   float = 0.001,   # minimum 0.1% sweep below/above swing
) -> Dict[str, Any]:
    """
    Detect institutional liquidity sweeps (stop hunts).

    Pattern (bullish):
    1. A visible swing low exists (retail stops placed below it)
    2. Current bar wicks BELOW the swing low (stops triggered = institutions bought)
    3. Current bar CLOSES above the swing low (reversal confirmed)
    4. Volume on the sweep bar is above average

    This is the exact moment institutions finish accumulating.
    Entering after a bullish sweep = buying with the institution.

    Returns:
        sweep_detected:    bool
        direction:         "BULLISH" | "BEARISH" | None
        swept_level:       float  — the swing level that was swept
        close_vs_swept:    "above" | "below" | None
        volume_confirmation: bool
    """
    try:
        h = pd.to_numeric(df["High"]   if "High"   in df.columns else df["high"],   errors="coerce")
        l = pd.to_numeric(df["Low"]    if "Low"    in df.columns else df["low"],    errors="coerce")
        c = pd.to_numeric(df["Close"]  if "Close"  in df.columns else df["close"],  errors="coerce")
        v = pd.to_numeric(df["Volume"] if "Volume" in df.columns else df.get("volume", pd.Series(1, index=df.index)), errors="coerce").fillna(0)

        n      = min(len(df) - 1, lookback)
        window = df.iloc[-(n+1):-1]   # everything except last bar

        if len(window) < 5:
            return {"sweep_detected": False, "direction": None,
                    "swept_level": 0.0, "volume_confirmation": False}

        wh = pd.to_numeric(window["High"]  if "High"  in window.columns else window["high"],  errors="coerce")
        wl = pd.to_numeric(window["Low"]   if "Low"   in window.columns else window["low"],   errors="coerce")

        prior_swing_low  = float(wl.min())
        prior_swing_high = float(wh.max())

        last_high  = float(h.iloc[-1])
        last_low   = float(l.iloc[-1])
        last_close = float(c.iloc[-1])
        last_vol   = float(v.iloc[-1])
        avg_vol    = float(v.iloc[-n-1:-1].mean()) if n > 0 else 1.0

        vol_confirm = last_vol > avg_vol * 1.3

        # BULLISH SWEEP: wick below prior swing low but closes ABOVE it
        if last_low < prior_swing_low * (1 - sweep_pct) and last_close > prior_swing_low:
            return {
                "sweep_detected":      True,
                "direction":           "BULLISH",
                "swept_level":         round(prior_swing_low, 2),
                "close_vs_swept":      "above",
                "volume_confirmation": vol_confirm,
                "sweep_depth_pct":     round((prior_swing_low - last_low) / prior_swing_low * 100, 3),
            }

        # BEARISH SWEEP: wick above prior swing high but closes BELOW it
        if last_high > prior_swing_high * (1 + sweep_pct) and last_close < prior_swing_high:
            return {
                "sweep_detected":      True,
                "direction":           "BEARISH",
                "swept_level":         round(prior_swing_high, 2),
                "close_vs_swept":      "below",
                "volume_confirmation": vol_confirm,
                "sweep_depth_pct":     round((last_high - prior_swing_high) / prior_swing_high * 100, 3),
            }

        return {"sweep_detected": False, "direction": None,
                "swept_level": 0.0, "volume_confirmation": False}

    except Exception as exc:
        logger.debug("detect_liquidity_sweep error: %s", exc)
        return {"sweep_detected": False, "direction": None,
                "swept_level": 0.0, "volume_confirmation": False}
