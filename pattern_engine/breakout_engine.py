"""
breakout_engine.py — detect price / volume / ATR breakouts and false breakouts.

Stateless helpers operating on a validated OHLCV frame. Thresholds come from
config (breakout.*). Used by pattern detectors and usable standalone.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BreakoutSignal:
    is_breakout: bool
    direction: str            # UP / DOWN / NONE
    price_break: bool
    volume_break: bool
    atr_break: bool
    false_breakout: bool
    level: float
    bar_index: int


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def evaluate_breakout(df: pd.DataFrame, level: float, idx: int,
                      *, volume_ma_period: int = 20, volume_multiplier: float = 1.5,
                      atr_period: int = 14, atr_mult: float = 1.5,
                      lookahead_false: int = 3) -> BreakoutSignal:
    """Classify the bar at `idx` as a breakout of `level` (up or down)."""
    if idx <= 0 or idx >= len(df):
        return BreakoutSignal(False, "NONE", False, False, False, False, level, idx)
    close = float(df["close"].iloc[idx]); prev = float(df["close"].iloc[idx - 1])
    vol = df["volume"].to_numpy(dtype=float)
    lo = max(0, idx - volume_ma_period)
    vma = vol[lo:idx].mean() if idx > lo else vol[idx]
    atr = _atr(df, atr_period)
    a = float(atr.iloc[idx]) if not np.isnan(atr.iloc[idx]) else 0.0

    up = prev <= level < close
    down = prev >= level > close
    direction = "UP" if up else ("DOWN" if down else "NONE")
    price_break = up or down
    volume_break = bool(vma > 0 and vol[idx] >= vma * volume_multiplier)
    atr_break = bool(a > 0 and abs(close - level) >= a * 0)  # close beyond level
    atr_break = bool(a > 0 and abs(close - prev) >= a * atr_mult)

    # false breakout: price closed back on the original side within lookahead
    false_bo = False
    end = min(len(df), idx + 1 + lookahead_false)
    if up:
        false_bo = bool((df["close"].iloc[idx + 1:end] < level).any())
    elif down:
        false_bo = bool((df["close"].iloc[idx + 1:end] > level).any())

    return BreakoutSignal(
        is_breakout=price_break and not false_bo, direction=direction,
        price_break=price_break, volume_break=volume_break, atr_break=atr_break,
        false_breakout=false_bo, level=float(level), bar_index=idx,
    )
