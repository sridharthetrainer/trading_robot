"""
signals.py

Unified signal engine for the trading system.

Returns a dict like:
{
    "action":     "BUY" | "SELL" | "HOLD",
    "strategy":   "trend" | "mean_reversion" | "breakout" | "scalping" | "default",
    "confidence": 0.0 - 1.0,
    "reason":     "...",
    "indicators": {...}
}

Fixes applied
-------------
1. Bollinger Band unpacking corrected (v2 of this file)
   calculate_bollinger_bands() returns (lower, mid, upper).
   Previous version unpacked as:  _, upper_bb, lower_bb
   which assigned: upper_bb = middle band, lower_bb = upper band.
   All mean-reversion signals were evaluated against wrong bands.
   Fixed: lower_bb, _, upper_bb = calculate_bollinger_bands(...)

2. Dynamic confidence derived from indicator values (from prior session)
   All strategies now compute confidence from live indicator readings
   rather than returning hardcoded floats.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

from indicators import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    calculate_volume_ratio,
    calculate_vwap,
)

logger = logging.getLogger(__name__)

_TREND_CONF_BASE,  _TREND_CONF_MAX  = 0.45, 0.90
_MR_CONF_BASE,     _MR_CONF_MAX     = 0.45, 0.88
_BO_CONF_BASE,     _BO_CONF_MAX     = 0.45, 0.90
_SCALP_CONF_BASE,  _SCALP_CONF_MAX  = 0.45, 0.85
_DEF_CONF_BASE,    _DEF_CONF_MAX    = 0.40, 0.82


def _cfg(config: Dict, name: str, default):
    if isinstance(config, dict):
        return config.get(name, default)
    return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalize_strategy_hint(config: Dict) -> str:
    hint = str(_cfg(config, "strategy", _cfg(config, "STRATEGY", "default"))).lower().strip()
    aliases = {
        "mr": "mean_reversion", "meanreversion": "mean_reversion",
        "breakout_expansion": "breakout", "ema": "trend", "ma": "trend",
    }
    return aliases.get(hint, hint)


def _compute_trend_confidence(
    current_adx: float, adx_threshold: float,
    fast_ema: float, slow_ema: float,
    last_close: float, current_rsi: float, action: str,
    macd_hist: float = 0.0,
    volume_ratio: float = 0.0,
) -> float:
    """
    Trend confidence.
    Adds MACD histogram direction (+0.10) and volume ratio (+0.10)
    as additional confirmations.
    """
    conf = _TREND_CONF_BASE
    conf += 0.25 * min(1.0, max(0.0, current_adx - adx_threshold) / max(1.0, 45.0 - adx_threshold))
    if last_close > 0:
        conf += 0.10 * min(1.0, abs(fast_ema - slow_ema) / last_close / 0.008)
    if action == "BUY" and current_rsi > 50:
        conf += 0.10 * min(1.0, (current_rsi - 50.0) / 20.0)
    elif action == "SELL" and current_rsi < 50:
        conf += 0.10 * min(1.0, (50.0 - current_rsi) / 20.0)
    # MACD confirmation: histogram moving in same direction as trade
    if action == "BUY"  and macd_hist > 0: conf += 0.10
    if action == "SELL" and macd_hist < 0: conf += 0.10
    # Volume confirmation
    if volume_ratio >= 1.5: conf += 0.10
    elif volume_ratio >= 1.0: conf += 0.05
    elif 0 < volume_ratio < 0.5: conf -= 0.10
    return round(_clamp(conf, _TREND_CONF_BASE, _TREND_CONF_MAX), 4)


def _compute_mr_confidence(
    current_rsi: float, oversold: float, overbought: float,
    last_close: float, current_lower: float, current_upper: float,
    current_atr: float, action: str,
) -> float:
    conf = _MR_CONF_BASE
    if action == "BUY" and current_rsi < oversold:
        conf += 0.20 * min(1.0, (oversold - current_rsi) / max(1.0, oversold) / 0.30)
    elif action == "SELL" and current_rsi > overbought:
        conf += 0.20 * min(1.0, (current_rsi - overbought) / max(1.0, 100.0 - overbought) / 0.30)
    if current_atr > 0:
        if action == "BUY" and last_close < current_lower:
            conf += 0.20 * min(1.0, (current_lower - last_close) / current_atr)
        elif action == "SELL" and last_close > current_upper:
            conf += 0.20 * min(1.0, (last_close - current_upper) / current_atr)
    return round(_clamp(conf, _MR_CONF_BASE, _MR_CONF_MAX), 4)


def _compute_breakout_confidence(
    last_close: float, prev_level: float, current_atr: float,
    current_adx: float, adx_threshold: float, action: str,
    volume_ratio: float = 0.0,
    macd_hist: float = 0.0,
) -> float:
    """
    Breakout confidence.
    Volume ratio is critical for breakout validity:
    - volume_ratio >= 1.5 : +0.20 (strong confirmation)
    - volume_ratio < 0.8  : -0.20 (likely false breakout)
    MACD alignment: +0.10
    """
    conf = _BO_CONF_BASE
    if current_atr > 0 and prev_level > 0:
        mag = ((last_close - prev_level) if action == "BUY" else (prev_level - last_close)) / current_atr
        conf += 0.25 * min(1.0, mag / 1.5)
    conf += 0.20 * min(1.0, max(0.0, current_adx - adx_threshold) / max(1.0, 45.0 - adx_threshold))
    # Volume is critical for breakout confirmation
    if volume_ratio >= 1.5: conf += 0.20
    elif volume_ratio >= 1.2: conf += 0.10
    elif 0 < volume_ratio < 0.80: conf -= 0.20
    # MACD alignment
    if action == "BUY"  and macd_hist > 0: conf += 0.10
    if action == "SELL" and macd_hist < 0: conf += 0.10
    return round(_clamp(conf, _BO_CONF_BASE, _BO_CONF_MAX), 4)


def _compute_scalping_confidence(
    current_rsi: float, rsi_long_threshold: float, rsi_short_threshold: float,
    fast_ema: float, slow_ema: float, last_close: float,
    current_vwap: float, action: str,
) -> float:
    conf = _SCALP_CONF_BASE
    if action == "BUY" and current_rsi > rsi_long_threshold:
        conf += 0.15 * min(1.0, (current_rsi - rsi_long_threshold) / 15.0)
    elif action == "SELL" and current_rsi < rsi_short_threshold:
        conf += 0.15 * min(1.0, (rsi_short_threshold - current_rsi) / 15.0)
    if current_vwap > 0 and last_close > 0:
        conf += 0.15 * min(1.0, abs(last_close - current_vwap) / current_vwap / 0.003)
    if last_close > 0:
        conf += 0.10 * min(1.0, abs(fast_ema - slow_ema) / last_close / 0.005)
    return round(_clamp(conf, _SCALP_CONF_BASE, _SCALP_CONF_MAX), 4)


def _compute_default_confidence(
    current_adx: float, fast_ema: float, slow_ema: float,
    last_close: float, current_rsi: float, action: str,
) -> float:
    conf = _DEF_CONF_BASE
    if current_adx > 0:
        conf += 0.20 * min(1.0, current_adx / 40.0)
    if last_close > 0:
        conf += 0.10 * min(1.0, abs(fast_ema - slow_ema) / last_close / 0.008)
    if action == "BUY" and current_rsi > 50:
        conf += 0.10 * min(1.0, (current_rsi - 50.0) / 20.0)
    elif action == "SELL" and current_rsi < 50:
        conf += 0.10 * min(1.0, (50.0 - current_rsi) / 20.0)
    return round(_clamp(conf, _DEF_CONF_BASE, _DEF_CONF_MAX), 4)


def _trend_signal(data: pd.DataFrame, config: Dict) -> Dict:
    fast_period   = int(_cfg(config, "fast_ema",      _cfg(config, "FAST_EMA",      9)))
    slow_period   = int(_cfg(config, "slow_ema",      _cfg(config, "SLOW_EMA",      21)))
    adx_threshold = _cfg(config,     "adx_threshold", _cfg(config, "ADX_THRESHOLD", 18))
    use_adx       = adx_threshold is not None

    if len(data) < max(fast_period, slow_period) + 2:
        return {"action": "HOLD", "strategy": "trend", "confidence": 0.0,
                "reason": "not_enough_data", "indicators": {}}

    ema_fast = calculate_ema(data, fast_period)
    ema_slow = calculate_ema(data, slow_period)
    adx      = calculate_adx(data, 14) if use_adx else None
    atr      = calculate_atr(data, 14)
    rsi      = calculate_rsi(data, 14)

    fast_now    = _safe_float(ema_fast.iloc[-1])
    slow_now    = _safe_float(ema_slow.iloc[-1])
    fast_prev   = _safe_float(ema_fast.iloc[-2])
    slow_prev   = _safe_float(ema_slow.iloc[-2])
    current_adx = _safe_float(adx.iloc[-1]) if adx is not None else 0.0
    current_atr = _safe_float(atr.iloc[-1])
    current_rsi = _safe_float(rsi.iloc[-1])
    last_close  = _safe_float(data["Close"].iloc[-1])

    # New indicators
    try:
        stoch_k_s, _ = calculate_stoch_rsi(data)
        stoch_k_val  = _safe_float(stoch_k_s.iloc[-1])
    except Exception: stoch_k_val = 0.0
    try:
        obv_s       = calculate_obv(data)
        obv_slope   = float(obv_s.diff(5).iloc[-1]) if len(obv_s) >= 6 else 0.0
    except Exception: obv_slope = 0.0

    # 200-EMA filter: only buy above 200-EMA, only sell below
    try:
        ema200 = calculate_ema(data, 200)
        current_ema200 = _safe_float(ema200.iloc[-1])
    except Exception:
        current_ema200 = 0.0

    # Volume confirmation on crossover bar
    vol_ratio_val = 0.0
    try:
        vol_ratio_val = _safe_float(calculate_volume_ratio(data, 20).iloc[-1])
    except Exception:
        vol_ratio_val = 1.0

    bullish_cross = fast_now > slow_now and fast_prev <= slow_prev
    bearish_cross = fast_now < slow_now and fast_prev >= slow_prev
    pass_adx      = (current_adx >= float(adx_threshold)) if use_adx else True
    # Require volume >= 1.2× average on crossover bar
    pass_volume   = vol_ratio_val >= 1.2
    # 200-EMA structural filter
    above_200 = current_ema200 <= 0 or last_close > current_ema200
    below_200 = current_ema200 <= 0 or last_close < current_ema200

    action = "HOLD"
    if bullish_cross and pass_adx and pass_volume and above_200:
        action = "BUY"
    elif bearish_cross and pass_adx and pass_volume and below_200:
        action = "SELL"

    confidence = 0.0
    if action != "HOLD":
        confidence = _compute_trend_confidence(
            current_adx=current_adx, adx_threshold=float(adx_threshold or 18),
            fast_ema=fast_now, slow_ema=slow_now,
            last_close=last_close, current_rsi=current_rsi, action=action,
            stoch_k=stoch_k_val, obv_slope=obv_slope,
        )

    return {
        "action": action, "strategy": "trend", "confidence": confidence,
        "reason": "ema_crossover",
        "indicators": {"ema_fast": fast_now, "ema_slow": slow_now, "adx": current_adx,
                       "rsi": current_rsi, "atr": current_atr, "close": last_close},
    }


def _mean_reversion_signal(data: pd.DataFrame, config: Dict) -> Dict:
    rsi_period = int(_cfg(config, "rsi_period", _cfg(config, "RSI_PERIOD", 14)))
    bb_period  = int(_cfg(config, "bb_period",  _cfg(config, "BB_PERIOD",  20)))
    bb_std     = float(_cfg(config, "bb_std",   _cfg(config, "BB_STD",     2.0)))
    oversold   = float(_cfg(config, "oversold", _cfg(config, "OVERSOLD",   30)))
    overbought = float(_cfg(config, "overbought", _cfg(config, "OVERBOUGHT", 70)))

    if len(data) < max(rsi_period, bb_period) + 2:
        return {"action": "HOLD", "strategy": "mean_reversion", "confidence": 0.0,
                "reason": "not_enough_data", "indicators": {}}

    rsi = calculate_rsi(data, rsi_period)
    atr = calculate_atr(data, 14)

    # FIXED: (lower, mid, upper) — unpack lower first, upper last
    lower_bb, _, upper_bb = calculate_bollinger_bands(data, bb_period, bb_std)

    last_close    = _safe_float(data["Close"].iloc[-1])
    current_rsi   = _safe_float(rsi.iloc[-1])
    current_upper = _safe_float(upper_bb.iloc[-1])
    current_lower = _safe_float(lower_bb.iloc[-1])
    current_atr   = _safe_float(atr.iloc[-1])

    # Disable MR in strong trends (ADX > 28 = trending market, not reverting)
    adx_mr = calculate_adx(data, 14)
    current_adx_mr = _safe_float(adx_mr.iloc[-1])
    if current_adx_mr > 28:
        return {"action": "HOLD", "strategy": "mean_reversion", "confidence": 0.0,
                "reason": f"adx_too_high_for_mr_{current_adx_mr:.1f}", "indicators": {}}

    long_signal  = current_rsi <= oversold  and last_close <= current_lower
    short_signal = current_rsi >= overbought and last_close >= current_upper

    action = "HOLD"
    if long_signal:
        action = "BUY"
    elif short_signal:
        action = "SELL"

    confidence = 0.0
    if action != "HOLD":
        confidence = _compute_mr_confidence(
            current_rsi=current_rsi, oversold=oversold, overbought=overbought,
            last_close=last_close, current_lower=current_lower, current_upper=current_upper,
            current_atr=current_atr, action=action,
        )

    return {
        "action": action, "strategy": "mean_reversion", "confidence": confidence,
        "reason": "rsi_bollinger_reversion",
        "indicators": {"rsi": current_rsi, "upper_bb": current_upper,
                       "lower_bb": current_lower, "atr": current_atr, "close": last_close},
    }


def _breakout_signal(data: pd.DataFrame, config: Dict) -> Dict:
    channel_period = int(_cfg(config, "channel_period", _cfg(config, "CHANNEL_PERIOD", 20)))
    adx_threshold  = _cfg(config, "adx_threshold", _cfg(config, "ADX_THRESHOLD", 20))
    use_adx        = adx_threshold is not None

    if len(data) < channel_period + 2:
        return {"action": "HOLD", "strategy": "breakout", "confidence": 0.0,
                "reason": "not_enough_data", "indicators": {}}

    highs  = pd.to_numeric(data["High"],  errors="coerce")
    lows   = pd.to_numeric(data["Low"],   errors="coerce")
    closes = pd.to_numeric(data["Close"], errors="coerce")

    rolling_high = highs.rolling(channel_period).max()
    rolling_low  = lows.rolling(channel_period).min()
    atr  = calculate_atr(data, 14)
    adx  = calculate_adx(data, 14) if use_adx else None

    last_close  = _safe_float(closes.iloc[-1])
    prev_high   = _safe_float(rolling_high.iloc[-2])
    prev_low    = _safe_float(rolling_low.iloc[-2])
    current_atr = _safe_float(atr.iloc[-1])
    current_adx = _safe_float(adx.iloc[-1]) if adx is not None else 0.0
    pass_adx    = (current_adx >= float(adx_threshold)) if use_adx else True

    action = "HOLD"
    if last_close > prev_high and pass_adx:
        action = "BUY"
    elif last_close < prev_low and pass_adx:
        action = "SELL"

    confidence = 0.0
    if action != "HOLD":
        confidence = _compute_breakout_confidence(
            last_close=last_close,
            prev_level=prev_high if action == "BUY" else prev_low,
            current_atr=current_atr, current_adx=current_adx,
            adx_threshold=float(adx_threshold or 20), action=action,
        )

    return {
        "action": action, "strategy": "breakout", "confidence": confidence,
        "reason": "donchian_breakout",
        "indicators": {"prev_high": prev_high, "prev_low": prev_low,
                       "adx": current_adx, "atr": current_atr, "close": last_close},
    }


def _scalping_signal(data: pd.DataFrame, config: Dict) -> Dict:
    fast_ema_p          = int(_cfg(config,   "fast_ema",           _cfg(config, "FAST_EMA", 3)))  # optimised for scalping
    slow_ema_p          = int(_cfg(config,   "slow_ema",           _cfg(config, "SLOW_EMA", 8)))  # faster response
    rsi_period          = int(_cfg(config,   "rsi_period",         _cfg(config, "RSI_PERIOD", 7)))
    rsi_long_threshold  = float(_cfg(config, "rsi_long_threshold",  55))
    rsi_short_threshold = float(_cfg(config, "rsi_short_threshold", 45))
    use_vwap_filter     = bool(_cfg(config,  "use_vwap_filter",     True))

    if len(data) < max(fast_ema_p, slow_ema_p, rsi_period) + 2:
        return {"action": "HOLD", "strategy": "scalping", "confidence": 0.0,
                "reason": "not_enough_data", "indicators": {}}

    ema_fast = calculate_ema(data, fast_ema_p)
    ema_slow = calculate_ema(data, slow_ema_p)
    rsi      = calculate_rsi(data, rsi_period)
    vwap     = calculate_vwap(data)
    atr      = calculate_atr(data, 14)

    fast_now     = _safe_float(ema_fast.iloc[-1])
    slow_now     = _safe_float(ema_slow.iloc[-1])
    current_rsi  = _safe_float(rsi.iloc[-1])
    current_vwap = _safe_float(vwap.iloc[-1])
    last_close   = _safe_float(data["Close"].iloc[-1])
    current_atr  = _safe_float(atr.iloc[-1])

    # Bar-range check: don't scalp overextended bars (range > 0.8×ATR)
    try:
        bar_high = _safe_float(data["High" if "High" in data.columns else "high"].iloc[-1])
        bar_low  = _safe_float(data["Low"  if "Low"  in data.columns else "low" ].iloc[-1])
        bar_range = bar_high - bar_low
        not_overextended = bar_range <= current_atr * 0.8
    except Exception:
        not_overextended = True

    bullish = fast_now > slow_now and current_rsi >= rsi_long_threshold and not_overextended
    bearish = fast_now < slow_now and current_rsi <= rsi_short_threshold and not_overextended

    if use_vwap_filter:
        bullish = bullish and last_close >= current_vwap
        bearish = bearish and last_close <= current_vwap

    action = "HOLD"
    if bullish:
        action = "BUY"
    elif bearish:
        action = "SELL"

    confidence = 0.0
    if action != "HOLD":
        confidence = _compute_scalping_confidence(
            current_rsi=current_rsi,
            rsi_long_threshold=rsi_long_threshold,
            rsi_short_threshold=rsi_short_threshold,
            fast_ema=fast_now, slow_ema=slow_now,
            last_close=last_close, current_vwap=current_vwap, action=action,
        )

    return {
        "action": action, "strategy": "scalping", "confidence": confidence,
        "reason": "ema_rsi_vwap_scalp",
        "indicators": {"ema_fast": fast_now, "ema_slow": slow_now, "rsi": current_rsi,
                       "vwap": current_vwap, "atr": current_atr, "close": last_close},
    }


def _default_signal(data: pd.DataFrame, config: Dict) -> Dict:
    if len(data) < 30:
        return {"action": "HOLD", "strategy": "default", "confidence": 0.0,
                "reason": "not_enough_data", "indicators": {}}

    ema_fast = calculate_ema(data, 9)
    ema_slow = calculate_ema(data, 21)
    rsi      = calculate_rsi(data, 14)
    adx      = calculate_adx(data, 14)
    atr      = calculate_atr(data, 14)

    fast_now    = _safe_float(ema_fast.iloc[-1])
    slow_now    = _safe_float(ema_slow.iloc[-1])
    fast_prev   = _safe_float(ema_fast.iloc[-2])
    slow_prev   = _safe_float(ema_slow.iloc[-2])
    current_rsi = _safe_float(rsi.iloc[-1])
    current_adx = _safe_float(adx.iloc[-1])
    current_atr = _safe_float(atr.iloc[-1])
    last_close  = _safe_float(data["Close"].iloc[-1])

    bullish = fast_now > slow_now and fast_prev <= slow_prev and current_rsi >= 50
    bearish = fast_now < slow_now and fast_prev >= slow_prev and current_rsi <= 50

    action = "HOLD"
    if bullish:
        action = "BUY"
    elif bearish:
        action = "SELL"

    confidence = 0.0
    if action != "HOLD":
        confidence = _compute_default_confidence(
            current_adx=current_adx, fast_ema=fast_now, slow_ema=slow_now,
            last_close=last_close, current_rsi=current_rsi, action=action,
        )

    return {
        "action": action, "strategy": "default", "confidence": confidence,
        "reason": "ema_rsi_default",
        "indicators": {"ema_fast": fast_now, "ema_slow": slow_now, "rsi": current_rsi,
                       "adx": current_adx, "atr": current_atr, "close": last_close},
    }


def get_signal(
    data: pd.DataFrame,
    config: Optional[Dict] = None,
    symbol: Optional[str] = None,
) -> Dict:
    config = config or {}

    if data is None or len(data) == 0:
        return {"action": "HOLD", "strategy": "default", "confidence": 0.0,
                "reason": "empty_data", "indicators": {}}

    strategy = _normalize_strategy_hint(config)

    if strategy == "trend":
        return _trend_signal(data, config)
    if strategy == "mean_reversion":
        return _mean_reversion_signal(data, config)
    if strategy == "breakout":
        return _breakout_signal(data, config)
    if strategy == "scalping":
        return _scalping_signal(data, config)

    return _default_signal(data, config)
