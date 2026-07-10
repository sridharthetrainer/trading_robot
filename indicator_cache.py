"""
indicator_cache.py — Compute indicators ONCE per symbol, reuse across all 63 strategies

Before: RSI(14) computed 63 times per symbol per scan = 12,348 redundant calculations
After:  RSI(14) computed 1 time per symbol, cached, reused by all strategies

Usage:
    from indicator_cache import get_indicators
    ind = get_indicators(df, symbol)
    rsi = ind["rsi_14"]
    macd = ind["macd"]
    bb_upper = ind["bb_upper"]
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Global cache: {symbol: {indicators...}}
_CACHE: Dict[str, dict] = {}
_CACHE_TIMESTAMP: Dict[str, float] = {}
_CACHE_FINGERPRINT: Dict[str, tuple] = {}
_CACHE_TTL = 280  # 4 min 40s — just under 5-min scan interval


def _fingerprint(df: pd.DataFrame) -> tuple:
    """Fingerprint the latest bar so cache never hides a changed live candle."""
    if df is None or len(df) == 0:
        return ()
    try:
        lookup = {str(c).lower(): c for c in df.columns}
        last = df.iloc[-1]
        parts = [len(df), str(df.index[-1])]
        for name in ("open", "high", "low", "close", "volume"):
            col = lookup.get(name)
            if col is None:
                parts.append(None)
            else:
                value = last[col]
                parts.append(None if pd.isna(value) else round(float(value), 8))
        return tuple(parts)
    except Exception:
        return (len(df),)


def get_indicators(df: pd.DataFrame, symbol: str = "") -> dict:
    """Compute all standard indicators for a DataFrame. Cached per symbol."""
    import time
    fp = _fingerprint(df)
    
    # Check cache
    if symbol and symbol in _CACHE:
        if (
            time.time() - _CACHE_TIMESTAMP.get(symbol, 0) < _CACHE_TTL
            and _CACHE_FINGERPRINT.get(symbol) == fp
        ):
            return _CACHE[symbol]
    
    if df is None or len(df) < 5:
        return {}
    
    try:
        def _col(*names: str) -> Optional[str]:
            lookup = {str(c).lower(): c for c in df.columns}
            for name in names:
                key = name.lower()
                if key in lookup:
                    return lookup[key]
            return None

        close_col = _col("close", "Close", "adj close", "adj_close")
        high_col = _col("high", "High")
        low_col = _col("low", "Low")
        volume_col = _col("volume", "Volume", "vol")
        if close_col is None or high_col is None or low_col is None:
            return {}

        close = df[close_col].astype(float)
        high = df[high_col].astype(float)
        low = df[low_col].astype(float)
        volume = (
            df[volume_col].astype(float)
            if volume_col is not None
            else pd.Series(0, index=df.index)
        )
        
        ind = {}
        
        # RSI (14, 7, 21)
        for period in (14, 7, 21):
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(period).mean()
            loss = (-delta.clip(upper=0)).rolling(period).mean()
            rs = gain / loss.replace(0, np.nan)
            ind[f"rsi_{period}"] = (100 - 100 / (1 + rs)).fillna(50)
        ind["rsi"] = ind["rsi_14"]
        
        # EMAs (9, 20, 50, 100, 200)
        for period in (9, 20, 50, 100, 200):
            if len(close) >= period:
                ind[f"ema_{period}"] = close.ewm(span=period, adjust=False).mean()
            else:
                ind[f"ema_{period}"] = close  # fallback
        ind["ema_fast"] = ind["ema_9"]
        ind["ema20"] = ind["ema_20"]
        ind["ema_slow"] = ind["ema_50"]
        ind["ema50"] = ind["ema_50"]
        ind["ema_trend"] = ind["ema_200"]
        ind["ema200"] = ind["ema_200"]
        
        # SMAs (10, 20, 50, 200)
        for period in (10, 20, 50, 200):
            if len(close) >= period:
                ind[f"sma_{period}"] = close.rolling(period).mean()
        
        # MACD (12, 26, 9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        ind["macd"] = ema12 - ema26
        ind["macd_signal"] = ind["macd"].ewm(span=9, adjust=False).mean()
        ind["macd_hist"] = ind["macd"] - ind["macd_signal"]
        
        # Bollinger Bands (20, 2)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        ind["bb_upper"] = sma20 + 2 * std20
        ind["bb_lower"] = sma20 - 2 * std20
        ind["bb_mid"] = sma20
        ind["bb_pct"] = (close - ind["bb_lower"]) / (ind["bb_upper"] - ind["bb_lower"]).replace(0, np.nan)
        
        # ATR (14)
        tr = pd.DataFrame({
            "hl": high - low,
            "hc": (high - close.shift()).abs(),
            "lc": (low - close.shift()).abs(),
        }).max(axis=1)
        ind["atr_14"] = tr.rolling(14).mean()
        ind["atr"] = ind["atr_14"]
        ind["atr_pct"] = ind["atr_14"] / close * 100
        
        # SuperTrend (10, 3)
        atr = ind["atr_14"]
        hl2 = (high + low) / 2
        ind["supertrend_upper"] = hl2 + 3 * atr
        ind["supertrend_lower"] = hl2 - 3 * atr
        
        # VWAP (if intraday data with volume)
        if len(df) > 10 and volume.sum() > 0:
            cum_vol = volume.cumsum()
            cum_vp = (close * volume).cumsum()
            ind["vwap"] = cum_vp / cum_vol.replace(0, np.nan)
        
        # Volume analysis
        if volume.sum() > 0:
            ind["vol_sma_20"] = volume.rolling(20).mean()
            ind["vol_ratio"] = volume / ind["vol_sma_20"].replace(0, np.nan)
            ind["volume_ratio"] = ind["vol_ratio"]
        
        # Stochastic (14, 3)
        if len(close) >= 14:
            low14 = low.rolling(14).min()
            high14 = high.rolling(14).max()
            ind["stoch_k"] = ((close - low14) / (high14 - low14).replace(0, np.nan) * 100).fillna(50)
            ind["stoch_d"] = ind["stoch_k"].rolling(3).mean()
        
        # ADX (14)
        try:
            plus_dm = high.diff().clip(lower=0)
            minus_dm = (-low.diff()).clip(lower=0)
            atr14 = tr.rolling(14).mean()
            plus_di = 100 * plus_dm.rolling(14).mean() / atr14.replace(0, np.nan)
            minus_di = 100 * minus_dm.rolling(14).mean() / atr14.replace(0, np.nan)
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
            ind["adx"] = dx.rolling(14).mean()
            ind["plus_di"] = plus_di
            ind["minus_di"] = minus_di
        except Exception:
            pass

        # Extended indicators (2026-07-10): connors_rsi, nr4/nr7, efficiency_
        # ratio, choppiness_index. These were only produced by
        # indicators.add_all_indicators(), which the LIVE path never calls —
        # signal_engine's fallback to it only fires when CORE indicators are
        # missing, and this cache always supplies those. Every consumer
        # defaulted silently: crsi_mod and nr_mod logged 0 on all 10,717
        # signals over 10 days, and the choppiness/efficiency regime gate
        # (penalize trend strategies in chop, mean-reversion in trends) was a
        # no-op since it was written. Reuses the canonical implementations
        # from indicators.py — one source of truth, no reimplementation.
        try:
            from indicators import (
                calculate_connors_rsi, detect_nr4, detect_nr7,
                calculate_efficiency_ratio, calculate_choppiness_index,
            )
            ind["nr4"] = detect_nr4(df)
            ind["nr7"] = detect_nr7(df)
            ind["efficiency_ratio"] = calculate_efficiency_ratio(close, period=10)
            ind["choppiness_index"] = calculate_choppiness_index(df, period=14)
            ind["connors_rsi"] = calculate_connors_rsi(df)
        except Exception as e:
            logger.debug("extended indicators %s: %s", symbol, e)
        
        # Cache it
        if symbol:
            _CACHE[symbol] = ind
            _CACHE_TIMESTAMP[symbol] = __import__("time").time()
            _CACHE_FINGERPRINT[symbol] = fp
            # Evict old entries
            if len(_CACHE) > 250:
                oldest = sorted(_CACHE_TIMESTAMP, key=_CACHE_TIMESTAMP.get)[:50]
                for k in oldest:
                    _CACHE.pop(k, None)
                    _CACHE_TIMESTAMP.pop(k, None)
                    _CACHE_FINGERPRINT.pop(k, None)
        
        return ind
    except Exception as e:
        logger.debug("indicator_cache %s: %s", symbol, e)
        return {}


def clear_cache():
    """Clear all cached indicators (call at EOD)."""
    _CACHE.clear()
    _CACHE_TIMESTAMP.clear()
    _CACHE_FINGERPRINT.clear()
