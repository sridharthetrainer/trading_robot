"""
indicators.py

Production-ready indicator utilities for Indian index / options trading.

Compatible with your existing strategy files:
- calculate_sma
- calculate_ema
- calculate_rsi
- calculate_atr
- calculate_adx
- calculate_supertrend

Also adds:
- calculate_vwap
- calculate_bollinger_bands
- calculate_cpr
- detect_swing_highs_lows
- detect_volume_spike
- add_all_indicators

Design goals:
- Works with DataFrame OR Series where practical
- Handles both uppercase and lowercase OHLCV column names
- Uses Wilder-style smoothing for ATR / ADX
- Safer NaN handling
- Suitable for backtest and live bot pipelines
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, Optional, Union
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------

PriceInput = Union[pd.Series, pd.DataFrame]


def _find_col(df: pd.DataFrame, candidates) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _get_ohlcv_cols(df: pd.DataFrame):
    open_col = _find_col(df, ["Open", "open", "OPEN"])
    high_col = _find_col(df, ["High", "high", "HIGH"])
    low_col = _find_col(df, ["Low", "low", "LOW"])
    close_col = _find_col(df, ["Close", "close", "CLOSE", "Adj Close", "adj_close"])
    volume_col = _find_col(df, ["Volume", "volume", "VOLUME", "vol"])

    missing = []
    if high_col is None:
        missing.append("High")
    if low_col is None:
        missing.append("Low")
    if close_col is None:
        missing.append("Close")

    if missing:
        raise ValueError(f"Missing required OHLC columns: {missing}")

    return open_col, high_col, low_col, close_col, volume_col


def _to_close_series(data: PriceInput) -> pd.Series:
    if isinstance(data, pd.Series):
        return pd.to_numeric(data, errors="coerce")
    if isinstance(data, pd.DataFrame):
        _, _, _, close_col, _ = _get_ohlcv_cols(data)
        return pd.to_numeric(data[close_col], errors="coerce")
    raise TypeError("Input must be pandas Series or DataFrame")


def _wilder_smoothing(series: pd.Series, period: int) -> pd.Series:
    """
    Wilder's smoothing using ewm(alpha=1/period, adjust=False).
    """
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------
# Basic moving averages
# ---------------------------------------------------------------------

def calculate_sma(data: PriceInput, period: int) -> pd.Series:
    close = _to_close_series(data)
    return close.rolling(window=period, min_periods=period).mean()


def calculate_ema(data: PriceInput, period: int) -> pd.Series:
    close = _to_close_series(data)
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------

def calculate_rsi(data: PriceInput, period: int = 14) -> pd.Series:
    close = _to_close_series(data)
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = _wilder_smoothing(gain, period)
    avg_loss = _wilder_smoothing(loss, period)

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # When avg_loss == 0 and gain > 0, RSI should be 100
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100)
    # When both are 0, RSI should be 50
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain == 0)), 50)

    return rsi


# ---------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------

def calculate_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)

    high = pd.to_numeric(data[high_col], errors="coerce")
    low = pd.to_numeric(data[low_col], errors="coerce")
    close = pd.to_numeric(data[close_col], errors="coerce")

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = _wilder_smoothing(true_range, period)
    return atr


# ---------------------------------------------------------------------
# ADX / DI
# ---------------------------------------------------------------------

def calculate_adx(
    data: pd.DataFrame,
    period: int = 14,
    return_di: bool = False
) -> Union[pd.Series, Tuple[pd.Series, pd.Series, pd.Series]]:
    """
    Returns ADX series by default.
    If return_di=True, returns (adx, plus_di, minus_di)
    """
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)

    high = pd.to_numeric(data[high_col], errors="coerce")
    low = pd.to_numeric(data[low_col], errors="coerce")
    close = pd.to_numeric(data[close_col], errors="coerce")

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=data.index,
        dtype=float
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=data.index,
        dtype=float
    )

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = _wilder_smoothing(tr, period)
    plus_dm_smoothed = _wilder_smoothing(plus_dm, period)
    minus_dm_smoothed = _wilder_smoothing(minus_dm, period)

    plus_di = 100 * (plus_dm_smoothed / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm_smoothed / atr.replace(0, np.nan))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = _wilder_smoothing(dx, period)

    if return_di:
        return adx, plus_di, minus_di
    return adx


# ---------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------

def calculate_supertrend(
    data: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0
) -> Tuple[pd.Series, pd.Series]:
    """
    Returns:
        supertrend_line, direction

    direction:
        +1 bullish
        -1 bearish
    """
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)

    high = pd.to_numeric(data[high_col], errors="coerce")
    low = pd.to_numeric(data[low_col], errors="coerce")
    close = pd.to_numeric(data[close_col], errors="coerce")

    atr = calculate_atr(data, period=period)
    hl2 = (high + low) / 2.0

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()

    for i in range(1, len(data)):
        if pd.notna(final_upper.iloc[i - 1]):
            if (basic_upper.iloc[i] < final_upper.iloc[i - 1]) or (close.iloc[i - 1] > final_upper.iloc[i - 1]):
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i - 1]

        if pd.notna(final_lower.iloc[i - 1]):
            if (basic_lower.iloc[i] > final_lower.iloc[i - 1]) or (close.iloc[i - 1] < final_lower.iloc[i - 1]):
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i - 1]

    supertrend = pd.Series(index=data.index, dtype=float)
    direction = pd.Series(index=data.index, dtype=int)

    for i in range(len(data)):
        if i == 0 or pd.isna(atr.iloc[i]):
            supertrend.iloc[i] = np.nan
            direction.iloc[i] = 0
            continue

        prev_st = supertrend.iloc[i - 1]
        prev_upper = final_upper.iloc[i - 1]
        prev_lower = final_lower.iloc[i - 1]

        if pd.isna(prev_st):
            if close.iloc[i] <= final_upper.iloc[i]:
                supertrend.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
            else:
                supertrend.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1
            continue

        if prev_st == prev_upper:
            if close.iloc[i] <= final_upper.iloc[i]:
                supertrend.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
            else:
                supertrend.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1
        else:
            if close.iloc[i] >= final_lower.iloc[i]:
                supertrend.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1
            else:
                supertrend.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1

    return supertrend, direction


# ---------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------

def calculate_vwap(data: pd.DataFrame, anchor: str = "session") -> pd.Series:
    """
    VWAP.
    anchor='session' resets daily if DatetimeIndex is present.
    Otherwise computes cumulative VWAP across full dataset.
    """
    _, high_col, low_col, close_col, volume_col = _get_ohlcv_cols(data)
    if volume_col is None:
        raise ValueError("Volume column required for VWAP")

    high = pd.to_numeric(data[high_col], errors="coerce")
    low = pd.to_numeric(data[low_col], errors="coerce")
    close = pd.to_numeric(data[close_col], errors="coerce")
    volume = pd.to_numeric(data[volume_col], errors="coerce").fillna(0)

    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume

    if anchor == "session" and isinstance(data.index, pd.DatetimeIndex):
        day_key = pd.Series(data.index.date, index=data.index)
        cum_pv = pv.groupby(day_key).cumsum()
        cum_vol = volume.groupby(day_key).cumsum()
        return cum_pv / cum_vol.replace(0, np.nan)

    return pv.cumsum() / volume.cumsum().replace(0, np.nan)


def calculate_vwap_bands(
    data: pd.DataFrame,
    period: int = 20,
    std_mult: float = 1.5,
    anchor: str = "session",
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    VWAP bands using rolling close-to-VWAP deviation.

    Returns (lower, vwap, upper). With a DatetimeIndex and session anchoring,
    the rolling deviation resets by trading day, matching calculate_vwap().
    """
    vwap = calculate_vwap(data, anchor=anchor)
    close = _to_close_series(data)
    deviation = close - vwap
    min_periods = max(5, min(period, period // 2))

    if anchor == "session" and isinstance(data.index, pd.DatetimeIndex):
        day_key = pd.Series(data.index.date, index=data.index)
        dev_std = (
            deviation.groupby(day_key)
            .rolling(window=period, min_periods=min_periods)
            .std(ddof=0)
            .reset_index(level=0, drop=True)
        )
    else:
        dev_std = deviation.rolling(window=period, min_periods=min_periods).std(ddof=0)

    upper = vwap + std_mult * dev_std
    lower = vwap - std_mult * dev_std
    lower.name = "vwap_lower"
    vwap.name = "vwap"
    upper.name = "vwap_upper"
    return lower, vwap, upper


# ---------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------

def calculate_bollinger_bands(
    data: PriceInput,
    period: int = 20,
    std_mult: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    close = _to_close_series(data)
    mid = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return lower, mid, upper



# ---------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------

def calculate_macd(
    data: PriceInput,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Standard MACD.

    Returns
    -------
    macd_line   : EMA(fast) - EMA(slow)
    signal_line : EMA(macd_line, signal)
    histogram   : macd_line - signal_line

    Usage
    -----
    macd, signal, hist = calculate_macd(df)
    bullish = (hist.iloc[-1] > 0) and (hist.iloc[-1] > hist.iloc[-2])
    """
    close       = _to_close_series(data)
    ema_fast    = close.ewm(span=fast,   adjust=False, min_periods=fast).mean()
    ema_slow    = close.ewm(span=slow,   adjust=False, min_periods=slow).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------
# Volume ratio
# ---------------------------------------------------------------------

def calculate_volume_ratio(
    data: pd.DataFrame,
    period: int = 20,
) -> pd.Series:
    """
    volume_ratio = current_volume / rolling_avg_volume(period).

    Values:
      > 1.5 : strong participation (breakout confirmation)
      0.8-1.5: normal
      < 0.5 : dead market — avoid trading
      = 0.0 : volume data unavailable

    Safe: returns zeros if volume column missing.
    """
    try:
        _, _, _, _, volume_col = _get_ohlcv_cols(data)
        if volume_col is None:
            return pd.Series(0.0, index=data.index)
        vol     = pd.to_numeric(data[volume_col], errors="coerce").fillna(0)
        avg_vol = vol.rolling(window=period, min_periods=max(1, period // 2)).mean()
        ratio   = vol / avg_vol.replace(0, np.nan)
        return ratio.fillna(0.0)
    except Exception:
        return pd.Series(0.0, index=data.index)



# ---------------------------------------------------------------------
# Stochastic RSI
# ---------------------------------------------------------------------

def calculate_stoch_rsi(
    data: PriceInput,
    rsi_period:   int = 14,
    stoch_period: int = 14,
    smooth_k:     int = 3,
    smooth_d:     int = 3,
) -> Tuple[pd.Series, pd.Series]:
    """
    Stochastic RSI — RSI of RSI, more sensitive than plain RSI.

    Returns (stoch_k, stoch_d) both in range [0, 1].
    stoch_k < 0.20 = oversold, > 0.80 = overbought.
    Crossover of k above d = bullish momentum.
    """
    rsi = calculate_rsi(data, rsi_period)
    rsi_min = rsi.rolling(stoch_period, min_periods=stoch_period).min()
    rsi_max = rsi.rolling(stoch_period, min_periods=stoch_period).max()
    rsi_range = rsi_max - rsi_min
    stoch_k_raw = (rsi - rsi_min) / rsi_range.replace(0, np.nan)
    stoch_k = stoch_k_raw.rolling(smooth_k, min_periods=1).mean().fillna(0.5)
    stoch_d = stoch_k.rolling(smooth_d, min_periods=1).mean()
    return stoch_k.clip(0, 1), stoch_d.clip(0, 1)


# ---------------------------------------------------------------------
# On-Balance Volume (OBV)
# ---------------------------------------------------------------------

def calculate_obv(data: pd.DataFrame) -> pd.Series:
    """
    On-Balance Volume.
    Accumulates volume on up-days, subtracts on down-days.
    OBV diverging from price = hidden institutional activity.
    """
    _, _, _, close_col, volume_col = _get_ohlcv_cols(data)
    if volume_col is None:
        return pd.Series(0.0, index=data.index)
    close  = pd.to_numeric(data[close_col], errors="coerce")
    volume = pd.to_numeric(data[volume_col], errors="coerce").fillna(0)
    direction = np.sign(close.diff()).fillna(0)
    obv = (direction * volume).cumsum()
    return obv


# ---------------------------------------------------------------------
# Money Flow Index (MFI)
# ---------------------------------------------------------------------

def calculate_mfi(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Money Flow Index — volume-weighted RSI.
    MFI < 20 = oversold (exhaustion selling), > 80 = overbought.
    """
    _, high_col, low_col, close_col, volume_col = _get_ohlcv_cols(data)
    if volume_col is None:
        return pd.Series(50.0, index=data.index)
    high   = pd.to_numeric(data[high_col],   errors="coerce")
    low    = pd.to_numeric(data[low_col],    errors="coerce")
    close  = pd.to_numeric(data[close_col],  errors="coerce")
    volume = pd.to_numeric(data[volume_col], errors="coerce").fillna(0)
    typical = (high + low + close) / 3.0
    raw_flow = typical * volume
    up_flow   = raw_flow.where(typical > typical.shift(1), 0.0)
    down_flow = raw_flow.where(typical < typical.shift(1), 0.0)
    pos_mf = up_flow.rolling(period, min_periods=period).sum()
    neg_mf = down_flow.rolling(period, min_periods=period).sum()
    mfi = 100 - (100 / (1 + pos_mf / neg_mf.replace(0, np.nan)))
    return mfi.fillna(50.0)


# ---------------------------------------------------------------------
# Keltner Channel
# ---------------------------------------------------------------------

def calculate_keltner_channel(
    data:       pd.DataFrame,
    ema_period: int   = 20,
    atr_period: int   = 10,
    multiplier: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Keltner Channel: EMA ± (ATR × multiplier).

    Returns (lower, mid, upper).
    When BB is inside KC = volatility squeeze (impending breakout).
    """
    close_col = _get_ohlcv_cols(data)[3]
    mid   = pd.to_numeric(data[close_col], errors="coerce").ewm(
        span=ema_period, adjust=False, min_periods=ema_period).mean()
    atr   = calculate_atr(data, atr_period)
    upper = mid + multiplier * atr
    lower = mid - multiplier * atr
    return lower, mid, upper


def detect_bb_squeeze(
    data:       pd.DataFrame,
    bb_period:  int   = 20,
    bb_std:     float = 2.0,
    kc_period:  int   = 20,
    kc_mult:    float = 1.5,
) -> pd.Series:
    """
    Detect Bollinger Band squeeze inside Keltner Channel.
    Returns boolean Series: True = squeeze active (breakout imminent).
    """
    close_col = _get_ohlcv_cols(data)[3]
    bb_lower, _, bb_upper = calculate_bollinger_bands(data, bb_period, bb_std)
    kc_lower, _, kc_upper = calculate_keltner_channel(data, kc_period, kc_mult)
    squeeze = (bb_lower >= kc_lower) & (bb_upper <= kc_upper)
    return squeeze.fillna(False)


# ---------------------------------------------------------------------
# Standard Pivot Points
# ---------------------------------------------------------------------

def calculate_pivot_points(data: pd.DataFrame) -> pd.DataFrame:
    """
    Daily standard pivot points from previous day's OHLC.

    Returns DataFrame with columns: pivot, r1, r2, r3, s1, s2, s3
    All forward-filled for intraday use.

    Institutions place orders at R1/R2/S1/S2 — highly reliable on NIFTY.
    """
    open_col, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)
    if not isinstance(data.index, pd.DatetimeIndex):
        cols = ["pivot","r1","r2","r3","s1","s2","s3"]
        return pd.DataFrame({c: np.nan for c in cols}, index=data.index)

    daily = data.resample("1D").agg({
        high_col: "max", low_col: "min", close_col: "last"
    }).dropna()

    ph = daily[high_col].shift(1)
    pl = daily[low_col].shift(1)
    pc = daily[close_col].shift(1)

    pivot = (ph + pl + pc) / 3
    r1 = 2 * pivot - pl
    s1 = 2 * pivot - ph
    r2 = pivot + (ph - pl)
    s2 = pivot - (ph - pl)
    r3 = ph + 2 * (pivot - pl)
    s3 = pl - 2 * (ph - pivot)

    pp = pd.DataFrame(
        {"pivot": pivot, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3},
        index=daily.index,
    )
    intraday = pp.reindex(data.index.normalize(), method="ffill")
    intraday.index = data.index
    return intraday


# ---------------------------------------------------------------------
# CPR (Central Pivot Range)
# ---------------------------------------------------------------------

def calculate_cpr(data: pd.DataFrame) -> pd.DataFrame:
    """
    Daily CPR from previous day's OHLC.
    Useful for intraday systems.

    Returns DataFrame with:
    - pivot
    - bc
    - tc

    For intraday indexed data, values are forward-filled for each day.
    """
    open_col, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)

    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError("DatetimeIndex required for CPR calculation")

    daily = data.resample("1D").agg({
        high_col: "max",
        low_col: "min",
        close_col: "last"
    }).dropna()

    prev_high = daily[high_col].shift(1)
    prev_low = daily[low_col].shift(1)
    prev_close = daily[close_col].shift(1)

    pivot = (prev_high + prev_low + prev_close) / 3.0
    bc = (prev_high + prev_low) / 2.0
    tc = 2 * pivot - bc

    cpr_daily = pd.DataFrame({
        "pivot": pivot,
        "bc": np.minimum(bc, tc),
        "tc": np.maximum(bc, tc),
    }, index=daily.index)

    intraday = cpr_daily.reindex(data.index.normalize(), method="ffill")
    intraday.index = data.index
    return intraday


# ---------------------------------------------------------------------
# Swing highs / lows
# ---------------------------------------------------------------------

def detect_swing_highs_lows(
    data: pd.DataFrame,
    lookback: int = 3
) -> Tuple[pd.Series, pd.Series]:
    """
    Marks swing highs/lows using symmetric window.

    Returns:
        swing_highs: price at swing high else NaN
        swing_lows : price at swing low else NaN
    """
    _, high_col, low_col, _, _ = _get_ohlcv_cols(data)

    high = pd.to_numeric(data[high_col], errors="coerce")
    low = pd.to_numeric(data[low_col], errors="coerce")

    swing_high = pd.Series(np.nan, index=data.index, dtype=float)
    swing_low = pd.Series(np.nan, index=data.index, dtype=float)

    for i in range(lookback, len(data) - lookback):
        window_high = high.iloc[i - lookback:i + lookback + 1]
        window_low = low.iloc[i - lookback:i + lookback + 1]

        if high.iloc[i] == window_high.max():
            swing_high.iloc[i] = high.iloc[i]

        if low.iloc[i] == window_low.min():
            swing_low.iloc[i] = low.iloc[i]

    return swing_high, swing_low


# ---------------------------------------------------------------------
# Volume spike
# ---------------------------------------------------------------------

def detect_volume_spike(
    data: pd.DataFrame,
    period: int = 20,
    multiplier: float = 1.5
) -> pd.Series:
    _, _, _, _, volume_col = _get_ohlcv_cols(data)
    if volume_col is None:
        raise ValueError("Volume column required for volume spike detection")

    volume = pd.to_numeric(data[volume_col], errors="coerce").fillna(0)
    avg_vol = volume.rolling(window=period, min_periods=period).mean()
    return volume > (avg_vol * multiplier)


# ---------------------------------------------------------------------
# Feature builder for live bot / research
# ---------------------------------------------------------------------

def add_all_indicators(
    data: pd.DataFrame,
    ema_fast: int = 9,
    ema_slow: int = 21,
    ema_trend: int = 200,
    rsi_period: int = 14,
    atr_period: int = 14,
    adx_period: int = 14,
    bb_period: int = 20,
    bb_std: float = 2.0,
    st_period: int = 10,
    st_multiplier: float = 3.0,
    swing_lookback: int = 3,
    volume_spike_period: int = 20,
    volume_spike_mult: float = 1.5,
    include_cpr: bool = False,
) -> pd.DataFrame:
    """
    Returns a copy of input DataFrame with useful indicator columns.
    """
    df = data.copy()

    _, high_col, low_col, close_col, volume_col = _get_ohlcv_cols(df)

    close = pd.to_numeric(df[close_col], errors="coerce")
    high = pd.to_numeric(df[high_col], errors="coerce")
    low = pd.to_numeric(df[low_col], errors="coerce")

    df["ema_fast"] = calculate_ema(close, ema_fast)
    df["ema_slow"] = calculate_ema(close, ema_slow)
    df["ema_trend"] = calculate_ema(close, ema_trend)
    df["ema_9"] = df["ema_fast"] if ema_fast == 9 else calculate_ema(close, 9)
    df["ema_20"] = calculate_ema(close, 20)
    df["ema_21"] = df["ema_slow"] if ema_slow == 21 else calculate_ema(close, 21)
    df["ema_50"] = calculate_ema(close, 50)
    df["ema_200"] = df["ema_trend"] if ema_trend == 200 else calculate_ema(close, 200)
    df["ema20"] = df["ema_20"]
    df["ema50"] = df["ema_50"]
    df["ema200"] = df["ema_200"]
    df["sma_20"] = calculate_sma(close, 20)
    df["rsi"] = calculate_rsi(close, rsi_period)
    df["rsi_14"] = df["rsi"] if rsi_period == 14 else calculate_rsi(close, 14)
    df["atr"] = calculate_atr(df, atr_period)
    df["atr_14"] = df["atr"] if atr_period == 14 else calculate_atr(df, 14)

    adx, plus_di, minus_di = calculate_adx(df, period=adx_period, return_di=True)
    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    st_line, st_dir = calculate_supertrend(df, period=st_period, multiplier=st_multiplier)
    df["supertrend"] = st_line
    df["supertrend_dir"] = st_dir

    bb_lower, bb_mid, bb_upper = calculate_bollinger_bands(close, period=bb_period, std_mult=bb_std)
    df["bb_lower"] = bb_lower
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_upper
    df["bb_width"] = (bb_upper - bb_lower) / bb_mid.replace(0, np.nan)

    # MACD
    macd_line, macd_signal, macd_hist = calculate_macd(close)
    df["macd"]        = macd_line
    df["macd_signal"] = macd_signal
    df["macd_hist"]   = macd_hist
    df["macd_bullish"] = ((macd_hist > 0) & (macd_hist > macd_hist.shift(1))).astype(int)
    df["macd_bearish"] = ((macd_hist < 0) & (macd_hist < macd_hist.shift(1))).astype(int)

    # Volume ratio
    df["volume_ratio"] = calculate_volume_ratio(df)
    df["vol_ratio"] = df["volume_ratio"]

    # StochRSI
    stoch_k, stoch_d = calculate_stoch_rsi(close)
    df["stoch_rsi_k"] = stoch_k
    df["stoch_rsi_d"] = stoch_d
    df["stoch_oversold"]  = (stoch_k < 0.20).astype(int)
    df["stoch_overbought"]= (stoch_k > 0.80).astype(int)

    # OBV
    if volume_col is not None:
        df["obv"] = calculate_obv(df)
        df["obv_slope"] = df["obv"].diff(5)  # 5-bar OBV slope
    else:
        df["obv"] = 0.0
        df["obv_slope"] = 0.0

    # MFI
    if volume_col is not None:
        df["mfi"] = calculate_mfi(df)
        df["mfi_oversold"]  = (df["mfi"] < 20).astype(int)
        df["mfi_overbought"]= (df["mfi"] > 80).astype(int)
    else:
        df["mfi"] = 50.0

    # Keltner Channel + squeeze
    kc_lower, kc_mid, kc_upper = calculate_keltner_channel(df)
    df["kc_lower"] = kc_lower
    df["kc_mid"]   = kc_mid
    df["kc_upper"] = kc_upper
    df["bb_squeeze"] = detect_bb_squeeze(df).astype(int)

    # Pivot points
    try:
        pp = calculate_pivot_points(df)
        for col in ["pivot","r1","r2","s1","s2"]:
            df[col] = pp[col]
    except Exception:
        pass

    if volume_col is not None:
        try:
            df["vwap"] = calculate_vwap(df)
            vwap_lower, _, vwap_upper = calculate_vwap_bands(df)
            df["vwap_lower"] = vwap_lower
            df["vwap_upper"] = vwap_upper
            df["vwap_band_width"] = (vwap_upper - vwap_lower) / df["vwap"].replace(0, np.nan)
            df["vwap_dev_pct"] = (close - df["vwap"]) / df["vwap"].replace(0, np.nan)
            df["volume_spike"] = detect_volume_spike(
                df,
                period=volume_spike_period,
                multiplier=volume_spike_mult
            ).astype(int)
        except Exception:
            df["vwap"] = np.nan
            df["vwap_lower"] = np.nan
            df["vwap_upper"] = np.nan
            df["vwap_band_width"] = np.nan
            df["vwap_dev_pct"] = np.nan
            df["volume_spike"] = 0
    else:
        df["vwap"] = np.nan
        df["vwap_lower"] = np.nan
        df["vwap_upper"] = np.nan
        df["vwap_band_width"] = np.nan
        df["vwap_dev_pct"] = np.nan
        df["volume_spike"] = 0

    swing_high, swing_low = detect_swing_highs_lows(df, lookback=swing_lookback)
    df["swing_high"] = swing_high
    df["swing_low"] = swing_low

    df["prev_swing_high"] = df["swing_high"].ffill().shift(1)
    df["prev_swing_low"] = df["swing_low"].ffill().shift(1)

    df["price_above_ema_fast"] = (close > df["ema_fast"]).astype(int)
    df["price_above_ema_slow"] = (close > df["ema_slow"]).astype(int)
    df["price_above_ema_trend"] = (close > df["ema_trend"]).astype(int)

    df["ema_fast_slope"] = df["ema_fast"].diff()
    df["ema_slow_slope"] = df["ema_slow"].diff()

    df["bullish_trend"] = (
        (close > df["ema_fast"]) &
        (df["ema_fast"] > df["ema_slow"]) &
        (df["ema_slow"] > df["ema_trend"]) &
        (df["adx"] > 20)
    ).astype(int)

    df["bearish_trend"] = (
        (close < df["ema_fast"]) &
        (df["ema_fast"] < df["ema_slow"]) &
        (df["ema_slow"] < df["ema_trend"]) &
        (df["adx"] > 20)
    ).astype(int)

    df["breakout_up"] = (close > df["prev_swing_high"]).astype(int)
    df["breakout_down"] = (close < df["prev_swing_low"]).astype(int)

    df["near_vwap_buy_zone"] = ((close >= df["vwap"] * 0.998) & (close <= df["vwap"] * 1.003)).astype(int)
    df["near_vwap_sell_zone"] = ((close <= df["vwap"] * 1.002) & (close >= df["vwap"] * 0.997)).astype(int)

    if include_cpr:
        try:
            cpr = calculate_cpr(df)
            for col in cpr.columns:
                if col == "pivot" and "pivot" in df.columns:
                    df["cpr_pivot"] = cpr[col]
                else:
                    df[col] = cpr[col]
            df["above_tc"] = (close > df["tc"]).astype(int)
            df["below_bc"] = (close < df["bc"]).astype(int)
            df["inside_cpr"] = ((close >= df["bc"]) & (close <= df["tc"])).astype(int)
        except Exception:
            df["pivot"] = np.nan
            df["bc"] = np.nan
            df["tc"] = np.nan
            df["above_tc"] = 0
            df["below_bc"] = 0
            df["inside_cpr"] = 0

    # ── New indicators (gap analysis additions) ─────────────────────────────
    try:
        df["cci"]        = calculate_cci(df)
        df["cci_overbought"] = (df["cci"] > 100).astype(int)
        df["cci_oversold"]   = (df["cci"] < -100).astype(int)
    except Exception: df["cci"] = 0.0

    try:
        df["hma_20"]  = calculate_hma(close, 20)
        df["hma_50"]  = calculate_hma(close, 50)
        df["hma_200"] = calculate_hma(close, 200)
    except Exception: df["hma_20"] = df["hma_50"] = df["hma_200"] = np.nan

    try:
        df["dema_21"] = calculate_dema(close, 21)
        df["tema_21"] = calculate_tema(close, 21)
    except Exception: df["dema_21"] = df["tema_21"] = np.nan

    try:
        df["kama"] = calculate_kama(close)
    except Exception: df["kama"] = np.nan

    try:
        stoch_k, stoch_d = calculate_stochastic(df)
        df["stoch_k"]        = stoch_k
        df["stoch_d"]        = stoch_d
        df["stoch_oversold"] = (stoch_k < 20).astype(int)
        df["stoch_overbought"] = (stoch_k > 80).astype(int)
    except Exception: df["stoch_k"] = df["stoch_d"] = np.nan

    try:
        df["uo"] = calculate_ultimate_oscillator(df)
    except Exception: df["uo"] = np.nan

    try:
        aroon_up, aroon_down, aroon_osc = calculate_aroon(df)
        df["aroon_up"]   = aroon_up
        df["aroon_down"] = aroon_down
        df["aroon_osc"]  = aroon_osc
    except Exception: df["aroon_up"] = df["aroon_down"] = df["aroon_osc"] = np.nan

    try:
        bull_power, bear_power = calculate_elder_ray(df)
        df["bull_power"] = bull_power
        df["bear_power"] = bear_power
    except Exception: df["bull_power"] = df["bear_power"] = np.nan

    try:
        jaw, teeth, lips = calculate_alligator(df)
        df["alligator_jaw"]   = jaw
        df["alligator_teeth"] = teeth
        df["alligator_lips"]  = lips
        df["alligator_sleeping"] = (
            (jaw - teeth).abs() < df["atr"] * 0.5
        ).astype(int) if "atr" in df.columns else 0
    except Exception: df["alligator_jaw"] = df["alligator_teeth"] = df["alligator_lips"] = np.nan

    try:
        df["force_index"] = calculate_force_index(df)
    except Exception: df["force_index"] = np.nan

    try:
        df["adl"] = calculate_adl(df)
        df["adl_slope"] = df["adl"].diff(5)
    except Exception: df["adl"] = df["adl_slope"] = np.nan

    try:
        df["natr"] = calculate_natr(df)
    except Exception: df["natr"] = np.nan

    try:
        dc_upper, dc_lower, dc_mid = calculate_donchian_channel(df)
        df["dc_upper"] = dc_upper
        df["dc_lower"] = dc_lower
        df["dc_mid"]   = dc_mid
    except Exception: df["dc_upper"] = df["dc_lower"] = df["dc_mid"] = np.nan

    try:
        kst_line, kst_signal = calculate_kst(close)
        df["kst"]        = kst_line
        df["kst_signal"] = kst_signal
        df["kst_bullish"] = ((kst_line > kst_signal) & (kst_line.shift(1) <= kst_signal.shift(1))).astype(int)
    except Exception: df["kst"] = df["kst_signal"] = np.nan

    try:
        df["efficiency_ratio"]   = calculate_efficiency_ratio(close, period=10)
        df["choppiness_index"]   = calculate_choppiness_index(df, period=14)
        df["is_trending"]        = (df["efficiency_ratio"] > 0.35).astype(int)
        df["is_choppy"]          = (df["choppiness_index"] > 55).astype(int)
    except Exception:
        df["efficiency_ratio"] = df["choppiness_index"] = np.nan
        df["is_trending"] = df["is_choppy"] = 0

    try:
        df["nr4"] = detect_nr4(df)
        df["nr7"] = detect_nr7(df)
    except Exception:
        df["nr4"] = df["nr7"] = 0

    try:
        df["connors_rsi"] = calculate_connors_rsi(df)
    except Exception:
        df["connors_rsi"] = np.nan

    return df


# ---------------------------------------------------------------------
# Convenience helpers for live signal engines
# ---------------------------------------------------------------------

def detect_regime_from_indicators(
    df: pd.DataFrame,
    adx_trend_threshold: float = 22.0,
    adx_range_threshold: float = 18.0,
) -> pd.Series:
    """
    Returns a regime label per row:
    - TREND
    - RANGE
    - NO_TRADE
    """
    regime = pd.Series("NO_TRADE", index=df.index, dtype=object)

    trend_mask = (
        (df["adx"] >= adx_trend_threshold) &
        (
            ((df["ema_fast"] > df["ema_slow"]) & (df["ema_slow"] > df["ema_trend"])) |
            ((df["ema_fast"] < df["ema_slow"]) & (df["ema_slow"] < df["ema_trend"]))
        )
    )

    range_mask = (
        (df["adx"] <= adx_range_threshold) &
        (df["bb_width"] < df["bb_width"].rolling(20, min_periods=5).mean())
    )

    regime[trend_mask] = "TREND"
    regime[range_mask] = "RANGE"

    return regime


def signal_score_row(row: pd.Series) -> int:
    """
    Simple scoring helper for trend-following systems.
    """
    score = 0

    if row.get("bullish_trend", 0) == 1 or row.get("bearish_trend", 0) == 1:
        score += 2
    if row.get("adx", 0) > 25:
        score += 1
    if row.get("volume_spike", 0) == 1:
        score += 1
    if row.get("supertrend_dir", 0) in (1, -1):
        score += 1
    if row.get("breakout_up", 0) == 1 or row.get("breakout_down", 0) == 1:
        score += 1

    return score


def _to_series(df):
    c = "Close" if "Close" in df.columns else "close"
    return pd.to_numeric(df[c], errors="coerce")

def calculate_rsi_divergence(
    data: pd.DataFrame,
    rsi_period: int = 14,
    lookback: int = 20,
) -> pd.Series:
    """
    Detect RSI Divergence — one of the strongest reversal signals.

    Bullish divergence: price makes a LOWER low but RSI makes a HIGHER low.
    → Selling pressure is weakening despite lower price = reversal likely.

    Bearish divergence: price makes a HIGHER high but RSI makes a LOWER high.
    → Buying pressure is weakening despite higher price = reversal likely.

    Returns: Series with values:
      +1 = bullish divergence (BUY signal)
      -1 = bearish divergence (SELL signal)
       0 = no divergence
    """
    close  = _to_series(data)
    rsi_s  = calculate_rsi(data, rsi_period)
    result = pd.Series(0, index=data.index)

    for i in range(lookback + 2, len(data)):
        window_close = close.iloc[i - lookback : i + 1]
        window_rsi   = rsi_s.iloc[i - lookback : i + 1]
        if window_close.isna().any() or window_rsi.isna().any():
            continue

        c_now = float(close.iloc[i])
        r_now = float(rsi_s.iloc[i])

        # Find swing lows in window
        c_min_val = float(window_close.min())
        c_min_idx = window_close.idxmin()
        r_at_min  = float(rsi_s.loc[c_min_idx]) if c_min_idx in rsi_s.index else 0.0

        # Bullish divergence: current close is at/near window low but RSI is higher
        if c_now <= c_min_val * 1.005:   # near the low
            if r_now > r_at_min + 3.0:   # RSI is meaningfully higher
                result.iloc[i] = 1

        # Find swing highs in window
        c_max_val = float(window_close.max())
        c_max_idx = window_close.idxmax()
        r_at_max  = float(rsi_s.loc[c_max_idx]) if c_max_idx in rsi_s.index else 100.0

        # Bearish divergence: current close is at/near window high but RSI is lower
        if c_now >= c_max_val * 0.995:
            if r_now < r_at_max - 3.0:
                result.iloc[i] = -1

    return result


def calculate_roc(data: PriceInput, period: int = 10) -> pd.Series:
    """
    Rate of Change (ROC) = (close - close[n]) / close[n] × 100.

    Measures price momentum as a percentage.
    ROC > 3%  over 10 days = upward momentum.
    ROC < -3% over 10 days = downward momentum.
    Used by AQR momentum factor and cross-sectional momentum.
    """
    s = _to_series(data)
    return s.pct_change(periods=period).multiply(100).round(4)


def calculate_relative_strength(
    data_symbol: pd.DataFrame,
    data_benchmark: pd.DataFrame,
    period: int = 10,
) -> pd.Series:
    """
    Relative Strength vs benchmark (usually NIFTY).

    RS = symbol_return / benchmark_return over 'period' bars.
    RS > 1.5: symbol outperforming NIFTY significantly = BUY leader
    RS < 0.7: symbol underperforming NIFTY = avoid

    Used to route signals to the strongest instruments.
    """
    sym_col = "Close" if "Close" in data_symbol.columns else "close"
    bnk_col = "Close" if "Close" in data_benchmark.columns else "close"

    sym_ret  = pd.to_numeric(data_symbol[sym_col],   errors="coerce").pct_change(period)
    bnk_ret  = pd.to_numeric(data_benchmark[bnk_col], errors="coerce").pct_change(period)

    # Align indexes
    bnk_ret = bnk_ret.reindex(sym_ret.index, method="ffill")
    denom   = bnk_ret.replace(0, float("nan"))
    rs      = (sym_ret / denom).fillna(1.0)
    return rs.round(4)


def calculate_ichimoku(
    data: pd.DataFrame,
    tenkan: int = 9,
    kijun:  int = 26,
    senkou: int = 52,
) -> dict:
    """
    Ichimoku Cloud — widely used in Asian institutional trading.

    Returns dict of Series:
    - tenkan_sen: Conversion line (9-period midpoint)
    - kijun_sen:  Base line (26-period midpoint)
    - senkou_a:   Leading span A (future cloud upper edge)
    - senkou_b:   Leading span B (future cloud lower edge)
    - chikou:     Current close value; plotting clients may display it 26 bars back

    Trading rules:
    - Price above cloud + TK cross (tenkan > kijun) = BUY
    - Price below cloud + TK cross (tenkan < kijun) = SELL
    - Cloud colour: senkou_a > senkou_b = bullish (green), else bearish (red)
    """
    h = pd.to_numeric(data["High"]  if "High"  in data.columns else data["high"],  errors="coerce")
    l = pd.to_numeric(data["Low"]   if "Low"   in data.columns else data["low"],   errors="coerce")
    c = pd.to_numeric(data["Close"] if "Close" in data.columns else data["close"], errors="coerce")

    def midpoint(n):
        return (h.rolling(n).max() + l.rolling(n).min()) / 2

    tenkan_sen = midpoint(tenkan)
    kijun_sen  = midpoint(kijun)
    senkou_a   = ((tenkan_sen + kijun_sen) / 2).shift(kijun)
    senkou_b   = midpoint(senkou).shift(kijun)
    # Shifting values backward with c.shift(-kijun) inserts FUTURE closes into
    # historical rows. Keep the causal value series here; visualization code may
    # shift only the display coordinate without exposing it to strategies/ML.
    chikou     = c.copy()

    return {
        "tenkan_sen": tenkan_sen,
        "kijun_sen":  kijun_sen,
        "senkou_a":   senkou_a,
        "senkou_b":   senkou_b,
        "chikou":     chikou,
    }


def calculate_kelly_fraction(
    win_rate:   float,
    avg_win:    float,
    avg_loss:   float,
) -> float:
    """
    Kelly Criterion: optimal position size fraction.
    f = (win_rate / |avg_loss|) - ((1 - win_rate) / avg_win)

    For options trading, use half-Kelly (f/2) as safety margin.
    Returns fraction of capital to risk (0.0 to 0.25 max).
    """
    if avg_loss == 0 or avg_win == 0:
        return 0.01
    kelly = (win_rate / abs(avg_loss)) - ((1 - win_rate) / avg_win)
    half_kelly = kelly / 2
    return round(max(0.005, min(0.25, half_kelly)), 4)

def calculate_entropy(
    data: "pd.DataFrame",
    period: int = 20,
    bins:   int = 10,
) -> "pd.Series":
    """
    Shannon Entropy of price returns over a rolling window.

    LOW entropy  (< 0.5): price moves are structured/predictable → TRADE
    HIGH entropy (> 0.8): price moves are random/noisy → AVOID

    This directly measures how "organised" vs "chaotic" the market is.
    Use to filter signals: only trade when entropy is low.

    Entropy = -sum(p * log2(p)) for each probability bucket
    Normalised to 0-1 range (0 = perfectly predictable, 1 = pure noise)
    """
    import numpy as np

    close   = pd.to_numeric(
        data["Close"] if "Close" in data.columns else data["close"],
        errors="coerce"
    )
    returns = close.pct_change().fillna(0)
    result  = pd.Series(0.5, index=data.index)   # default = neutral

    for i in range(period, len(returns)):
        window = returns.iloc[i - period: i].values
        if len(window) < period:
            continue
        # Bin the returns into discrete buckets
        counts, _ = np.histogram(window, bins=bins)
        probs      = counts / counts.sum()
        # Shannon entropy (ignore zero-probability bins)
        probs      = probs[probs > 0]
        entropy    = -np.sum(probs * np.log2(probs))
        # Normalise: max entropy for `bins` bins = log2(bins)
        max_ent    = np.log2(bins)
        normalised = round(float(entropy / max_ent), 4) if max_ent > 0 else 0.5
        result.iloc[i] = normalised

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# NEW INDICATORS — added per gap analysis vs TradingView / Zerodha / books
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_cci(data: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index — (TP − SMA(TP,n)) / (0.015 × MeanDev)."""
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)
    tp = (pd.to_numeric(data[high_col], errors="coerce") +
          pd.to_numeric(data[low_col],  errors="coerce") +
          pd.to_numeric(data[close_col],errors="coerce")) / 3.0
    sma_tp = tp.rolling(period).mean()
    mad    = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


def calculate_wma(data: PriceInput, period: int) -> pd.Series:
    """Weighted Moving Average — linearly-weighted, most recent has highest weight."""
    close = _to_close_series(data)
    weights = np.arange(1, period + 1, dtype=float)
    return close.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def calculate_hma(data: PriceInput, period: int = 20) -> pd.Series:
    """Hull Moving Average — WMA(2×WMA(n/2) − WMA(n), √n). Reduces lag vs EMA."""
    close  = _to_close_series(data)
    half   = max(1, period // 2)
    sqrt_n = max(1, int(round(period ** 0.5)))
    raw    = 2 * calculate_wma(close, half) - calculate_wma(close, period)
    return calculate_wma(raw, sqrt_n)


def calculate_dema(data: PriceInput, period: int = 21) -> pd.Series:
    """Double EMA — 2×EMA(n) − EMA(EMA(n)). Faster response with less lag."""
    close = _to_close_series(data)
    e1    = calculate_ema(close, period)
    e2    = calculate_ema(e1,    period)
    return 2 * e1 - e2


def calculate_tema(data: PriceInput, period: int = 21) -> pd.Series:
    """Triple EMA — 3×EMA − 3×EMA(EMA) + EMA(EMA(EMA))."""
    close = _to_close_series(data)
    e1    = calculate_ema(close, period)
    e2    = calculate_ema(e1,    period)
    e3    = calculate_ema(e2,    period)
    return 3 * e1 - 3 * e2 + e3


def calculate_kama(data: PriceInput, period: int = 10,
                   fast: int = 2, slow: int = 30) -> pd.Series:
    """Kaufman Adaptive Moving Average — adapts smoothing to noise/efficiency ratio."""
    close  = _to_close_series(data).values.astype(float)
    fast_c = 2.0 / (fast + 1)
    slow_c = 2.0 / (slow + 1)
    kama   = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        direction = abs(close[i] - close[i - period])
        volatility = sum(abs(close[j] - close[j - 1]) for j in range(i - period + 1, i + 1))
        er  = direction / volatility if volatility > 0 else 0.0
        sc  = (er * (fast_c - slow_c) + slow_c) ** 2
        prev = kama[i - 1] if not np.isnan(kama[i - 1]) else close[i - 1]
        kama[i] = prev + sc * (close[i] - prev)
    return pd.Series(kama, index=pd.RangeIndex(len(close)))


def calculate_stochastic(data: pd.DataFrame, k_period: int = 14,
                          d_period: int = 3) -> tuple:
    """Price Stochastic %K/%D — different from StochRSI."""
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)
    close = pd.to_numeric(data[close_col], errors="coerce")
    high  = pd.to_numeric(data[high_col],  errors="coerce")
    low   = pd.to_numeric(data[low_col],   errors="coerce")
    lowest  = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return k, d


def calculate_ultimate_oscillator(data: pd.DataFrame,
                                   p1: int = 7, p2: int = 14, p3: int = 28) -> pd.Series:
    """Larry Williams Ultimate Oscillator — weighted BP/TR across 3 periods."""
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)
    close = pd.to_numeric(data[close_col], errors="coerce")
    high  = pd.to_numeric(data[high_col],  errors="coerce")
    low   = pd.to_numeric(data[low_col],   errors="coerce")
    prev_close = close.shift(1)
    bp  = close - pd.concat([low, prev_close], axis=1).min(axis=1)
    tr  = pd.concat([high - low,
                     (high - prev_close).abs(),
                     (low  - prev_close).abs()], axis=1).max(axis=1)
    def _avg(window): return bp.rolling(window).sum() / tr.rolling(window).sum().replace(0, np.nan)
    return 100 * (4 * _avg(p1) + 2 * _avg(p2) + _avg(p3)) / 7.0


def calculate_aroon(data: pd.DataFrame, period: int = 25) -> tuple:
    """Aroon Up/Down/Oscillator — measures bars since n-period high/low."""
    _, high_col, low_col, _, _ = _get_ohlcv_cols(data)
    high = pd.to_numeric(data[high_col], errors="coerce")
    low  = pd.to_numeric(data[low_col],  errors="coerce")
    aroon_up   = high.rolling(period + 1).apply(
        lambda x: (period - (len(x) - 1 - np.argmax(x))) / period * 100, raw=True)
    aroon_down = low.rolling(period + 1).apply(
        lambda x: (period - (len(x) - 1 - np.argmin(x))) / period * 100, raw=True)
    return aroon_up, aroon_down, aroon_up - aroon_down


def calculate_elder_ray(data: pd.DataFrame, period: int = 13) -> tuple:
    """Elder Ray Bull/Bear Power — Bull=High−EMA(13), Bear=Low−EMA(13)."""
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)
    close = pd.to_numeric(data[close_col], errors="coerce")
    high  = pd.to_numeric(data[high_col],  errors="coerce")
    low   = pd.to_numeric(data[low_col],   errors="coerce")
    ema   = calculate_ema(close, period)
    return high - ema, low - ema   # bull_power, bear_power


def calculate_alligator(data: pd.DataFrame) -> tuple:
    """Bill Williams Alligator — Jaw/Teeth/Lips (SMMA shifted)."""
    _, high_col, low_col, close_col, _ = _get_ohlcv_cols(data)
    close = pd.to_numeric(data[close_col], errors="coerce")
    high  = pd.to_numeric(data[high_col],  errors="coerce")
    low   = pd.to_numeric(data[low_col],   errors="coerce")
    median = (high + low) / 2.0
    def _smma(series, n, shift):
        s = series.ewm(alpha=1.0/n, adjust=False).mean()
        return s.shift(shift)
    jaw   = _smma(median, 13, 8)
    teeth = _smma(median, 8,  5)
    lips  = _smma(median, 5,  3)
    return jaw, teeth, lips


def calculate_force_index(data: pd.DataFrame, period: int = 13) -> pd.Series:
    """Elder Force Index — (Close − PrevClose) × Volume, smoothed."""
    _, _, _, close_col, volume_col = _get_ohlcv_cols(data)
    close  = pd.to_numeric(data[close_col], errors="coerce")
    volume = pd.to_numeric(data[volume_col], errors="coerce") if volume_col else pd.Series(1, index=data.index)
    raw = (close - close.shift(1)) * volume
    return calculate_ema(raw, period)


def calculate_adl(data: pd.DataFrame) -> pd.Series:
    """Accumulation/Distribution Line — different from OBV."""
    _, high_col, low_col, close_col, volume_col = _get_ohlcv_cols(data)
    close  = pd.to_numeric(data[close_col], errors="coerce")
    high   = pd.to_numeric(data[high_col],  errors="coerce")
    low    = pd.to_numeric(data[low_col],   errors="coerce")
    volume = pd.to_numeric(data[volume_col], errors="coerce") if volume_col else pd.Series(1, index=data.index)
    hl_range = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / hl_range
    return (clv * volume).cumsum()


def calculate_natr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """Normalized ATR = ATR/Close×100. Enables cross-symbol volatility comparison."""
    _, _, _, close_col, _ = _get_ohlcv_cols(data)
    close = pd.to_numeric(data[close_col], errors="coerce")
    atr   = calculate_atr(data, period)
    return atr / close.replace(0, np.nan) * 100.0


def calculate_fibonacci_levels(high: float, low: float) -> dict:
    """Fibonacci retracement levels from a swing high/low pair."""
    diff = high - low
    return {
        "0.0":   low,
        "0.236": low + 0.236 * diff,
        "0.382": low + 0.382 * diff,
        "0.5":   low + 0.500 * diff,
        "0.618": low + 0.618 * diff,
        "0.786": low + 0.786 * diff,
        "1.0":   high,
        "1.272": low + 1.272 * diff,
        "1.618": low + 1.618 * diff,
    }


def calculate_donchian_channel(data: pd.DataFrame, period: int = 20) -> tuple:
    """Donchian Channel — Upper=max(High,n), Lower=min(Low,n), Mid=(U+L)/2."""
    _, high_col, low_col, _, _ = _get_ohlcv_cols(data)
    high  = pd.to_numeric(data[high_col], errors="coerce")
    low   = pd.to_numeric(data[low_col],  errors="coerce")
    upper = high.rolling(period).max()
    lower = low.rolling(period).min()
    mid   = (upper + lower) / 2.0
    return upper, lower, mid


def calculate_kst(data: PriceInput, r1: int = 10, r2: int = 13, r3: int = 15, r4: int = 20,
                  sma1: int = 10, sma2: int = 13, sma3: int = 15, sma4: int = 30,
                  signal_period: int = 9) -> tuple:
    """Pring KST Oscillator — sum of 4 weighted smoothed ROC values."""
    close = _to_close_series(data)
    def _rcma(roc_p, sma_p, w):
        roc = close.pct_change(roc_p) * 100
        return roc.rolling(sma_p).mean() * w
    kst    = _rcma(r1,sma1,1) + _rcma(r2,sma2,2) + _rcma(r3,sma3,3) + _rcma(r4,sma4,4)
    signal = kst.rolling(signal_period).mean()
    return kst, signal


def calculate_efficiency_ratio(data: PriceInput, period: int = 10) -> pd.Series:
    """
    Kaufman Efficiency Ratio (ER).
    ER = abs(net_price_change) / sum_of_bar_moves over `period` bars.
    ER close to 1.0 → strongly trending; close to 0 → choppy/random.
    Threshold: ER < 0.25 → do not enter trend/breakout trades.
              ER > 0.60 → directional, preferred for trend entries.
    """
    close = _to_close_series(data)
    direction = (close - close.shift(period)).abs()
    volatility = close.diff().abs().rolling(period).sum()
    er = direction / volatility.replace(0, np.nan)
    er.name = "efficiency_ratio"
    return er


def calculate_choppiness_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Choppiness Index (CHOP).
    CHOP = 100 × log10(sum(ATR_1, period) / (highest_high - lowest_low)) / log10(period)
    CHOP > 61.8 → ranging/choppy; CHOP < 38.2 → strongly trending.
    Use to gate trend strategies (allow only when CHOP < 55) and
    mean-reversion strategies (allow only when CHOP > 50).
    """
    o, h, l, c, _ = _get_ohlcv_cols(df)
    high  = df[h]; low = df[l]; close = df[c]
    tr1   = (high - low)
    tr2   = (high - close.shift(1)).abs()
    tr3   = (low  - close.shift(1)).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_sum    = true_range.rolling(period).sum()
    hh         = high.rolling(period).max()
    ll         = low.rolling(period).min()
    chop = 100 * np.log10(atr_sum / (hh - ll).replace(0, np.nan)) / np.log10(period)
    chop.name = "choppiness_index"
    return chop


def calculate_connors_rsi(
    df: pd.DataFrame,
    rsi_period: int = 3,
    streak_period: int = 2,
    rank_period: int = 100,
) -> pd.Series:
    """
    Connors RSI (CRSI) = (RSI(3) + PercentRank(streak, 100) + PercentRank(daily_return, 100)) / 3
    Larry Connors "Short-Term Trading Strategies That Work".
    CRSI < 10 in a trending market → high-probability mean-reversion entry.
    CRSI > 90 → high-probability mean-reversion short entry.
    The streak component (consecutive up/down bars) is the key differentiator
    vs plain RSI — a 5-bar losing streak at support scores differently than a
    5-bar losing streak in free-fall.
    """
    _, h, l, c, _ = _get_ohlcv_cols(df)
    close = pd.to_numeric(df[c], errors="coerce")

    # Component 1: short RSI
    rsi3 = calculate_rsi(close, rsi_period)

    # Component 2: up/down streak length as percentile rank
    delta = close.diff()
    streak = pd.Series(0.0, index=close.index)
    for i in range(1, len(close)):
        if delta.iloc[i] > 0:
            streak.iloc[i] = max(streak.iloc[i - 1] + 1, 1)
        elif delta.iloc[i] < 0:
            streak.iloc[i] = min(streak.iloc[i - 1] - 1, -1)
        else:
            streak.iloc[i] = 0

    def _pct_rank(series, period):
        return series.rolling(period).apply(
            lambda x: pd.Series(x[:-1]).rank(pct=True).iloc[-1] * 100
            if len(x) > 1 else 50.0,
            raw=False,
        )

    streak_rank  = _pct_rank(streak, rank_period)

    # Component 3: single-period return percentile rank
    daily_return = close.pct_change()
    return_rank  = _pct_rank(daily_return, rank_period)

    crsi = (rsi3 + streak_rank + return_rank) / 3
    crsi.name = "connors_rsi"
    return crsi


def detect_nr4(df: pd.DataFrame) -> pd.Series:
    """
    Narrow Range 4 (NR4): True when current bar's range is the smallest of the last 4 bars.
    Toby Crabel "Day Trading with Short Term Price Patterns" — signals volatility compression
    that precedes explosive moves. Use as an entry timing filter, not a direction signal.
    """
    o, h, l, c, _ = _get_ohlcv_cols(df)
    bar_range = df[h] - df[l]
    nr4 = bar_range == bar_range.rolling(4).min()
    nr4.name = "nr4"
    return nr4.astype(int)


def detect_nr7(df: pd.DataFrame) -> pd.Series:
    """
    Narrow Range 7 (NR7): True when current bar's range is the smallest of the last 7 bars.
    Stronger compression signal than NR4; after an NR7 day on the daily chart the next ORB
    has statistically higher follow-through in trending environments.
    """
    o, h, l, c, _ = _get_ohlcv_cols(df)
    bar_range = df[h] - df[l]
    nr7 = bar_range == bar_range.rolling(7).min()
    nr7.name = "nr7"
    return nr7.astype(int)


def calculate_entry_quality_score(
    data: pd.DataFrame,
    direction: str,
    strategy: str = "",
) -> Dict[str, Any]:
    """
    Directional entry-quality score for live signal gating.

    This is not a standalone strategy. It is a compact confluence indicator that
    checks whether the current bar has enough structure for the requested
    direction: VWAP location, EMA alignment, Supertrend, ADX/DI, MACD momentum,
    volume participation, candle body quality, and ATR extension.

    Returns:
      score_modifier : small additive score nudge, usually between -2 and +2
      hard_block     : True only for very poor execution-quality bars
      reasons        : concise reason list for logs/narrative
      metrics        : last-bar diagnostics
    """
    direction = str(direction or "").upper().strip()
    strategy_lc = str(strategy or "").lower()
    if direction not in ("BUY", "SELL") or data is None or len(data) < 30:
        return {
            "score_modifier": 0.0,
            "hard_block": False,
            "reasons": ["insufficient_entry_quality_context"],
            "metrics": {},
        }

    df = data.copy()
    open_col, high_col, low_col, close_col, volume_col = _get_ohlcv_cols(df)
    open_s = pd.to_numeric(df[open_col], errors="coerce") if open_col else _to_close_series(df)
    close = pd.to_numeric(df[close_col], errors="coerce")

    def _series_or(name: str, fallback) -> pd.Series:
        for col in (name, name.lower(), name.upper()):
            if col in df.columns:
                return pd.to_numeric(df[col], errors="coerce")
        return fallback()

    atr = _series_or("atr", lambda: calculate_atr(df))
    ema_fast = _series_or("ema_fast", lambda: calculate_ema(close, 9))
    ema_slow = _series_or("ema_slow", lambda: calculate_ema(close, 21))
    vwap = _series_or("vwap", lambda: calculate_vwap(df) if volume_col else pd.Series(np.nan, index=df.index))
    volume_ratio = _series_or("volume_ratio", lambda: calculate_volume_ratio(df))

    try:
        st_line, st_dir = calculate_supertrend(df)
    except Exception:
        st_dir = pd.Series(0, index=df.index)

    try:
        adx, plus_di, minus_di = calculate_adx(df, return_di=True)
    except Exception:
        adx = plus_di = minus_di = pd.Series(np.nan, index=df.index)

    try:
        _, _, macd_hist = calculate_macd(close)
    except Exception:
        macd_hist = pd.Series(np.nan, index=df.index)

    def _last(series: pd.Series, default: float = 0.0) -> float:
        try:
            value = float(series.iloc[-1])
            return default if pd.isna(value) else value
        except Exception:
            return default

    price = _last(close)
    open_price = _last(open_s, price)
    atr_last = max(_last(atr, 0.0), 1e-9)
    ema_fast_last = _last(ema_fast, price)
    ema_slow_last = _last(ema_slow, price)
    vwap_last = _last(vwap, np.nan)
    adx_last = _last(adx, 0.0)
    plus_last = _last(plus_di, 0.0)
    minus_last = _last(minus_di, 0.0)
    st_last = _last(st_dir, 0.0)
    macd_now = _last(macd_hist, 0.0)
    macd_prev = _last(macd_hist.shift(1), macd_now)
    vr_last = _last(volume_ratio, 0.0)
    body_atr = abs(price - open_price) / atr_last
    extension_atr = abs(price - ema_fast_last) / atr_last

    is_momentum = any(x in strategy_lc for x in (
        "trend", "break", "orb", "momentum", "supertrend", "ma_cross",
        "market_structure", "liquidity", "vp_break",
    ))
    is_reversion = any(x in strategy_lc for x in (
        "mean", "reversion", "vwap", "cpr", "rsi", "scalp",
    ))

    score = 0.0
    reasons = []

    if direction == "BUY":
        if price > ema_fast_last > ema_slow_last:
            score += 0.45; reasons.append("ema_bull")
        elif price < ema_fast_last < ema_slow_last:
            score -= 0.45; reasons.append("ema_bear_against_buy")

        if st_last > 0:
            score += 0.45; reasons.append("supertrend_bull")
        elif st_last < 0:
            score -= 0.45; reasons.append("supertrend_bear_against_buy")

        if plus_last > minus_last:
            score += 0.35; reasons.append("di_bull")
        elif minus_last > plus_last:
            score -= 0.35; reasons.append("di_bear_against_buy")

        if macd_now > 0 and macd_now >= macd_prev:
            score += 0.30; reasons.append("macd_rising")
        elif macd_now < 0 and macd_now <= macd_prev:
            score -= 0.30; reasons.append("macd_falling_against_buy")

        if not pd.isna(vwap_last):
            if price > vwap_last:
                score += 0.25; reasons.append("above_vwap")
            elif is_momentum:
                score -= 0.35; reasons.append("below_vwap_momentum")
    else:
        if price < ema_fast_last < ema_slow_last:
            score += 0.45; reasons.append("ema_bear")
        elif price > ema_fast_last > ema_slow_last:
            score -= 0.45; reasons.append("ema_bull_against_sell")

        if st_last < 0:
            score += 0.45; reasons.append("supertrend_bear")
        elif st_last > 0:
            score -= 0.45; reasons.append("supertrend_bull_against_sell")

        if minus_last > plus_last:
            score += 0.35; reasons.append("di_bear")
        elif plus_last > minus_last:
            score -= 0.35; reasons.append("di_bull_against_sell")

        if macd_now < 0 and macd_now <= macd_prev:
            score += 0.30; reasons.append("macd_falling")
        elif macd_now > 0 and macd_now >= macd_prev:
            score -= 0.30; reasons.append("macd_rising_against_sell")

        if not pd.isna(vwap_last):
            if price < vwap_last:
                score += 0.25; reasons.append("below_vwap")
            elif is_momentum:
                score -= 0.35; reasons.append("above_vwap_momentum")

    if adx_last >= 25:
        score += 0.35; reasons.append("adx_strong")
    elif adx_last >= 18:
        score += 0.15; reasons.append("adx_ok")
    elif is_momentum:
        score -= 0.40; reasons.append("adx_weak")

    if vr_last > 1.5:
        score += 0.35; reasons.append("volume_strong")
    elif vr_last > 1.15:
        score += 0.15; reasons.append("volume_ok")
    elif 0 < vr_last < 0.60 and is_momentum:
        score -= 0.45; reasons.append("volume_thin")

    if body_atr < 0.08 and is_momentum:
        score -= 0.35; reasons.append("tiny_body")
    elif body_atr > 0.18:
        score += 0.10; reasons.append("body_ok")

    hard_block = False
    if extension_atr > 3.2 and not is_reversion:
        score -= 0.80
        hard_block = True
        reasons.append("overextended_entry")
    elif extension_atr > 2.4 and not is_reversion:
        score -= 0.40
        reasons.append("extended_entry")

    score = round(max(-2.0, min(2.0, score)), 3)
    return {
        "score_modifier": score,
        "hard_block": bool(hard_block and score < -0.8),
        "reasons": reasons[:8],
        "metrics": {
            "price": round(price, 2),
            "vwap": round(vwap_last, 2) if not pd.isna(vwap_last) else None,
            "adx": round(adx_last, 2),
            "plus_di": round(plus_last, 2),
            "minus_di": round(minus_last, 2),
            "supertrend_dir": int(st_last) if st_last in (-1, 0, 1) else st_last,
            "macd_hist": round(macd_now, 4),
            "volume_ratio": round(vr_last, 2),
            "body_atr": round(body_atr, 3),
            "extension_atr": round(extension_atr, 3),
        },
    }


def is_market_structured(df: "pd.DataFrame", threshold: float = 0.65) -> bool:
    """
    Returns True if market is structured (low entropy) = safe to trade.
    Returns False if market is noisy (high entropy) = skip signals.

    threshold: entropy above this = noisy (default 0.65)
    """
    try:
        ent = calculate_entropy(df)
        last_entropy = float(ent.iloc[-1])
        return last_entropy < threshold
    except Exception:
        return True   # assume structured if calculation fails
