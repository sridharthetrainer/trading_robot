"""
candlestick_patterns.py

Reusable Japanese candlestick pattern recognition library.

The trading wrapper in candlestick_signals.py can still require pivots, volume
and HTF alignment, while this module focuses on detecting the raw candle
formations and returning auditable PatternSignal objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


DEFAULT_PARAMS: Dict[str, Any] = {
    "doji_size": 0.05,
    "shadow_percent": 5.0,
    "ema_length": 14,
    "engulfing_factor": 1.5,
    "trend_detection": "SMA50",
    "label_color_bullish": "#0000FF",
    "label_color_bearish": "#FF0000",
    "tolerance_pct": 0.0015,
}


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    timestamp: Any = None
    body: float = 0.0
    range: float = 0.0
    body_hi: float = 0.0
    body_lo: float = 0.0
    up_shadow: float = 0.0
    dn_shadow: float = 0.0

    def __post_init__(self) -> None:
        self.body_hi = max(self.open, self.close)
        self.body_lo = min(self.open, self.close)
        self.body = abs(self.close - self.open)
        self.range = max(self.high - self.low, 0.0)
        self.up_shadow = max(self.high - self.body_hi, 0.0)
        self.dn_shadow = max(self.body_lo - self.low, 0.0)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


@dataclass
class PatternSignal:
    pattern_name: str
    pattern_type: str
    timestamp: Any
    location: str
    confidence: float
    price_level: float
    text_label: str
    bars_used: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pattern_name": self.pattern_name,
            "pattern_type": self.pattern_type,
            "timestamp": self.timestamp,
            "location": self.location,
            "confidence": self.confidence,
            "price_level": self.price_level,
            "text_label": self.text_label,
            "bars_used": self.bars_used,
        }


def _norm_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    return out


def _candles_from_df(df: pd.DataFrame) -> List[Candle]:
    d = _norm_df(df)
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(d.columns):
        return []
    candles: List[Candle] = []
    for ts, row in d.iterrows():
        candles.append(Candle(
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0) or 0.0),
            timestamp=ts,
        ))
    return candles


def _body_avg(candles: List[Candle], i: int, length: int = 14) -> float:
    start = max(0, i - length + 1)
    vals = [c.body for c in candles[start:i + 1]]
    return float(np.mean(vals)) if vals else 0.0


def _atr(candles: List[Candle], i: int, length: int = 30) -> float:
    start = max(0, i - length + 1)
    vals = [c.range for c in candles[start:i + 1]]
    return float(np.mean(vals)) if vals else 0.0


def _trend(candles: List[Candle], i: int, mode: str = "SMA50") -> str:
    if i < 3 or str(mode).lower() == "none":
        return "neutral"
    closes = np.array([c.close for c in candles[:i + 1]], dtype=float)
    if str(mode).upper() == "SMA50+SMA200" and len(closes) >= 200:
        sma50 = closes[-50:].mean()
        sma200 = closes[-200:].mean()
        return "up" if sma50 > sma200 else "down" if sma50 < sma200 else "neutral"
    lookback = min(50, len(closes))
    sma = closes[-lookback:].mean()
    if closes[-1] > sma and closes[-1] > closes[max(0, len(closes) - 6)]:
        return "up"
    if closes[-1] < sma and closes[-1] < closes[max(0, len(closes) - 6)]:
        return "down"
    return "neutral"


def _tol(price: float, pct: float = 0.0015) -> float:
    return max(abs(price) * pct, 1e-9)


def _small(c: Candle, avg: float) -> bool:
    return c.body <= max(avg, c.range * 0.30)


def _long(c: Candle, avg: float) -> bool:
    return c.body >= max(avg, c.range * 0.50)


def _marubozu(c: Candle, bullish: Optional[bool] = None) -> bool:
    if c.body <= 0:
        return False
    if bullish is True and not c.bullish:
        return False
    if bullish is False and not c.bearish:
        return False
    return c.up_shadow <= c.body * 0.08 and c.dn_shadow <= c.body * 0.08


def _gap_up(a: Candle, b: Candle) -> bool:
    return b.low > a.high


def _gap_down(a: Candle, b: Candle) -> bool:
    return b.high < a.low


def _sig(c: Candle, name: str, typ: str, label: str, bars: int, confidence: float = 0.65) -> PatternSignal:
    location = "belowbar" if typ == "bullish" else "abovebar" if typ == "bearish" else "abovebar"
    level = c.low if location == "belowbar" else c.high
    return PatternSignal(name, typ, c.timestamp, location, round(float(confidence), 3), float(level), label, bars)


def detect_doji(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if c.range > 0 and c.body <= c.range * float(params.get("doji_size", 0.05)):
        return _sig(c, "Doji", "neutral", "D", 1, 0.50)
    return None


def detect_gravestone_doji(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if c.range > 0 and c.body <= c.range * 0.07 and c.up_shadow >= c.range * 0.60 and c.dn_shadow <= c.range * 0.12:
        return _sig(c, "Gravestone Doji", "bearish", "GD", 1, 0.62)
    return None


def detect_dragonfly_doji(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if c.range > 0 and c.body <= c.range * 0.07 and c.dn_shadow >= c.range * 0.60 and c.up_shadow <= c.range * 0.12:
        return _sig(c, "Dragonfly Doji", "bullish", "DD", 1, 0.62)
    return None


def detect_spinning_top_black(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if c.bearish and c.range > 0 and c.body > c.range * 0.05 and c.up_shadow >= c.range * 0.20 and c.dn_shadow >= c.range * 0.20:
        return _sig(c, "Spinning Top Black", "bearish", "ST", 1, 0.45)
    return None


def detect_spinning_top_white(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if c.bullish and c.range > 0 and c.body > c.range * 0.05 and c.up_shadow >= c.range * 0.20 and c.dn_shadow >= c.range * 0.20:
        return _sig(c, "Spinning Top White", "bullish", "ST", 1, 0.45)
    return None


def detect_hammer(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if c.range > 3 * max(c.body, 1e-9) and c.dn_shadow > c.range * 0.60 and c.up_shadow <= c.range * 0.18:
        return _sig(c, "Hammer", "bullish", "H", 1, 0.68)
    return None


def detect_hanging_man(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if _trend(candles, i, params.get("trend_detection", "SMA50")) == "up" and c.range > 3 * max(c.body, 1e-9) and c.dn_shadow > c.range * 0.60:
        return _sig(c, "Hanging Man", "bearish", "H", 1, 0.62)
    return None


def detect_inverted_hammer(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if c.range > 3 * max(c.body, 1e-9) and c.up_shadow > c.range * 0.60 and c.dn_shadow <= c.range * 0.18:
        return _sig(c, "Inverted Hammer", "bullish", "IH", 1, 0.62)
    return None


def detect_shooting_star(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    c = candles[i]
    if _trend(candles, i, params.get("trend_detection", "SMA50")) == "up" and c.range > 3 * max(c.body, 1e-9) and c.up_shadow > c.range * 0.60:
        return _sig(c, "Shooting Star", "bearish", "SS", 1, 0.68)
    return None


def detect_bullish_engulfing(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    factor = float(params.get("engulfing_factor", 1.5))
    if p.bearish and c.bullish and c.open <= p.close and c.close >= p.open and c.body >= p.body * factor:
        return _sig(c, "Bullish Engulfing", "bullish", "B.E", 2, 0.72)
    return None


def detect_bearish_engulfing(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    factor = float(params.get("engulfing_factor", 1.5))
    if p.bullish and c.bearish and c.open >= p.close and c.close <= p.open and c.body >= p.body * factor:
        return _sig(c, "Bearish Engulfing", "bearish", "B.E", 2, 0.72)
    return None


def detect_bullish_harami(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bearish and c.bullish and c.body_hi < p.body_hi and c.body_lo > p.body_lo and c.body < p.body * 0.55:
        return _sig(c, "Bullish Harami", "bullish", "B.H", 2, 0.58)
    return None


def detect_bearish_harami(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bullish and c.bearish and c.body_hi < p.body_hi and c.body_lo > p.body_lo and c.body < p.body * 0.55:
        return _sig(c, "Bearish Harami", "bearish", "B.H", 2, 0.58)
    return None


def detect_piercing_line(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    midpoint = (p.open + p.close) / 2
    if p.bearish and c.bullish and c.open < p.low and c.close > midpoint and c.close < p.open:
        return _sig(c, "Piercing Line", "bullish", "P.L", 2, 0.70)
    return None


def detect_dark_cloud_cover(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    midpoint = (p.open + p.close) / 2
    if p.bullish and c.bearish and c.open > p.high and c.close < midpoint and c.close > p.open:
        return _sig(c, "Dark Cloud Cover", "bearish", "DCC", 2, 0.70)
    return None


def detect_bullish_separating_lines(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bearish and c.bullish and abs(c.open - p.open) <= _tol(p.open) and c.close > p.high:
        return _sig(c, "Bullish Separating Lines", "bullish", "Separating Lines", 2, 0.61)
    return None


def detect_bearish_separating_lines(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bullish and c.bearish and abs(c.open - p.open) <= _tol(p.open) and c.close < p.low:
        return _sig(c, "Bearish Separating Lines", "bearish", "Separating Lines", 2, 0.61)
    return None


def detect_bullish_kicking(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if _marubozu(p, False) and _marubozu(c, True) and c.open > p.open:
        return _sig(c, "Bullish Kicking", "bullish", "Kicking", 2, 0.78)
    return None


def detect_bearish_kicking(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if _marubozu(p, True) and _marubozu(c, False) and c.open < p.open:
        return _sig(c, "Bearish Kicking", "bearish", "Kicking", 2, 0.78)
    return None


def detect_homing_pigeon_bullish(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bearish and c.bearish and c.body_hi < p.body_hi and c.body_lo > p.body_lo and c.close > p.close:
        return _sig(c, "Homing Pigeon Bullish", "bullish", "H.P", 2, 0.55)
    return None


def detect_homing_pigeon_bearish(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bullish and c.bullish and c.body_hi < p.body_hi and c.body_lo > p.body_lo and c.close < p.close:
        return _sig(c, "Homing Pigeon Bearish", "bearish", "H.P", 2, 0.55)
    return None


def detect_on_neck(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bearish and c.bullish and abs(c.close - p.low) <= _tol(p.low, 0.002):
        return _sig(c, "On-Neck Pattern", "bearish", "On-N", 2, 0.52)
    return None


def detect_in_neck(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bearish and c.bullish and p.low < c.close < (p.open + p.close) / 2:
        return _sig(c, "In-Neck Pattern", "bearish", "In-N", 2, 0.52)
    return None


def detect_thrusting_line(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    midpoint = (p.open + p.close) / 2
    if p.bearish and c.bullish and c.close < midpoint and c.close > p.close:
        return _sig(c, "Thrusting Line", "bearish", "Thrusting Line", 2, 0.50)
    return None


def detect_three_white_soldiers(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if all(x.bullish for x in (a, b, c)) and a.close < b.close < c.close and b.open <= a.close and c.open <= b.close:
        return _sig(c, "Three White Soldiers", "bullish", "TWS", 3, 0.78)
    return None


def detect_three_black_soldiers(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if all(x.bearish for x in (a, b, c)) and a.close > b.close > c.close and b.open >= a.close and c.open >= b.close:
        return _sig(c, "Three Black Soldiers", "bearish", "3BS", 3, 0.78)
    return None


def detect_morning_star(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    avg = _body_avg(candles, i)
    if _long(a, avg) and a.bearish and _small(b, avg) and c.bullish and c.close > (a.open + a.close) / 2:
        return _sig(c, "Morning Star", "bullish", "Morn *", 3, 0.80)
    return None


def detect_evening_star(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    avg = _body_avg(candles, i)
    if _long(a, avg) and a.bullish and _small(b, avg) and c.bearish and c.close < (a.open + a.close) / 2:
        return _sig(c, "Evening Star", "bearish", "Even *", 3, 0.80)
    return None


def detect_abandoned_baby_bottom(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bearish and detect_doji(candles, i - 1, params) and b.high < a.low and c.low > b.high and c.bullish:
        return _sig(c, "Abandoned Baby Bottom", "bullish", "Abandoned", 3, 0.85)
    return None


def detect_abandoned_baby_top(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and detect_doji(candles, i - 1, params) and b.low > a.high and c.high < b.low and c.bearish:
        return _sig(c, "Abandoned Baby Top", "bearish", "Abandoned", 3, 0.85)
    return None


def detect_three_river_morning_star(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bearish and b.body < a.body * 0.45 and b.low < a.low and c.bullish and c.close > b.high:
        return _sig(c, "Three-River Morning Star", "bullish", "3 river Doji-*", 3, 0.70)
    return None


def detect_three_river_evening_star(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and b.body < a.body * 0.45 and b.high > a.high and c.bearish and c.close < b.low:
        return _sig(c, "Three-River Evening Star", "bearish", "Eve Doji-*", 3, 0.70)
    return None


def detect_doji_star_bottom(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bearish and detect_doji(candles, i - 1, params) and b.high < a.body_lo and c.bullish:
        return _sig(c, "Doji Star Bottom", "bullish", "doji-*", 3, 0.66)
    return None


def detect_doji_star_top(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and detect_doji(candles, i - 1, params) and b.low > a.body_hi and c.bearish:
        return _sig(c, "Doji Star Top", "bearish", "doji-*", 3, 0.66)
    return None


def detect_rising_three_methods(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 4:
        return None
    a, b, c, d, e = candles[i - 4], candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    mids = (b, c, d)
    if a.bullish and e.bullish and all(x.bearish and a.low < x.low and x.high < a.high for x in mids) and e.close > a.high:
        return _sig(e, "Rising Three Methods", "bullish", "RTM", 5, 0.76)
    return None


def detect_falling_three_methods(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 4:
        return None
    a, b, c, d, e = candles[i - 4], candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    mids = (b, c, d)
    if a.bearish and e.bearish and all(x.bullish and a.low < x.low and x.high < a.high for x in mids) and e.close < a.low:
        return _sig(e, "Falling Three Methods", "bearish", "FTM", 5, 0.76)
    return None


def detect_mat_hold_pattern(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 4:
        return None
    a, b, c, d, e = candles[i - 4], candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and b.bearish and c.bearish and d.bearish and e.bullish and b.low > a.close * 0.995 and e.close > a.high:
        return _sig(e, "Mat Hold Pattern", "bullish", "Mat Hold Pattern", 5, 0.74)
    return None


def detect_tasuki_upside_gap(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and b.bullish and _gap_up(a, b) and c.bearish and a.high < c.close < b.low:
        return _sig(c, "Tasuki Upside Gap", "bullish", "Tasuki Gap", 3, 0.62)
    return None


def detect_tasuki_downside_gap(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bearish and b.bearish and _gap_down(a, b) and c.bullish and b.high < c.close < a.low:
        return _sig(c, "Tasuki Downside Gap", "bearish", "Tasuki Gap", 3, 0.62)
    return None


def detect_up_gap_side_by_side_white(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if _gap_up(a, b) and b.bullish and c.bullish and abs(b.body - c.body) <= max(b.body, c.body) * 0.35:
        return _sig(c, "Up-Gap Side-by-Side White", "bullish", "Up-Gap", 3, 0.62)
    return None


def detect_down_gap_side_by_side_white(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if _gap_down(a, b) and b.bullish and c.bullish and abs(b.body - c.body) <= max(b.body, c.body) * 0.35:
        return _sig(c, "Down-Gap Side-by-Side White", "bearish", "Down-Gap", 3, 0.58)
    return None


def detect_advance_block(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if all(x.bullish for x in (a, b, c)) and a.body > b.body > c.body and b.up_shadow > b.body * 0.4 and c.up_shadow > c.body * 0.4:
        return _sig(c, "Advance Block", "bearish", "Adv", 3, 0.60)
    return None


def detect_deliberation(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if all(x.bullish for x in (a, b, c)) and a.close < b.close < c.close and c.body < min(a.body, b.body) * 0.6:
        return _sig(c, "Deliberation", "bearish", "Del", 3, 0.58)
    return None


def detect_upside_gap_two_crows(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and b.bearish and c.bearish and b.low > a.high and c.close < b.close:
        return _sig(c, "Upside Gap Two Crows", "bearish", "2 Crows", 3, 0.66)
    return None


def detect_breakaway_bottom(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 4:
        return None
    a, b, c, d, e = candles[i - 4], candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    if a.bearish and _gap_down(a, b) and e.bullish and e.close > b.high and min(c.low, d.low) >= b.low * 0.98:
        return _sig(e, "Breakaway Bottom", "bullish", "Breakaway", 5, 0.72)
    return None


def detect_breakaway_top(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 4:
        return None
    a, b, c, d, e = candles[i - 4], candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and _gap_up(a, b) and e.bearish and e.close < b.low and max(c.high, d.high) <= b.high * 1.02:
        return _sig(e, "Breakaway Top", "bearish", "Breakaway", 5, 0.72)
    return None


def detect_bullish_black_three_gaps(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 3:
        return None
    a, b, c, d = candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    if a.bearish and b.bearish and c.bearish and _gap_down(a, b) and _gap_down(b, c) and d.bullish:
        return _sig(d, "Bullish Black Three Gaps", "bullish", "3gap", 4, 0.65)
    return None


def detect_bearish_white_three_gaps(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 3:
        return None
    a, b, c, d = candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    if a.bullish and b.bullish and c.bullish and _gap_up(a, b) and _gap_up(b, c) and d.bearish:
        return _sig(d, "Bearish White Three Gaps", "bearish", "3gap", 4, 0.65)
    return None


def detect_concealing_baby_swallow(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 3:
        return None
    a, b, c, d = candles[i - 3], candles[i - 2], candles[i - 1], candles[i]
    if all(x.bearish for x in (a, b, c, d)) and _marubozu(a, False) and _marubozu(b, False) and c.high > b.high and d.close > c.close:
        return _sig(d, "Concealing Baby Swallow", "bullish", "Concealing Baby Swallow", 4, 0.70)
    return None


def detect_ladder_bottom(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 4:
        return None
    seq = candles[i - 4:i + 1]
    if all(x.bearish for x in seq[:4]) and seq[-1].bullish and seq[-1].close > seq[-2].high:
        return _sig(seq[-1], "Ladder Bottom", "bullish", "Ladder", 5, 0.68)
    return None


def detect_tweezer_top(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bullish and c.bearish and abs(p.high - c.high) <= _tol(p.high) and _trend(candles, i, params.get("trend_detection", "SMA50")) == "up":
        return _sig(c, "Tweezer Top", "bearish", "TT", 2, 0.64)
    return None


def detect_tweezer_bottom(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bearish and c.bullish and abs(p.low - c.low) <= _tol(p.low) and _trend(candles, i, params.get("trend_detection", "SMA50")) == "down":
        return _sig(c, "Tweezer Bottom", "bullish", "TT", 2, 0.64)
    return None


def detect_low_price_gapping_play_bearish(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    for n in range(2, min(12, i) + 1):
        base = candles[i - n]
        cur = candles[i]
        middle = candles[i - n + 1:i]
        if _gap_down(base, middle[0]) and cur.bearish and all(x.body <= base.range for x in middle) and cur.close < min(x.low for x in middle):
            return _sig(cur, "Low-Price Gapping Play Bearish", "bearish", "Low-Gap", n + 1, 0.62)
    return None


def detect_high_price_gapping_play_bullish(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    for n in range(2, min(12, i) + 1):
        base = candles[i - n]
        cur = candles[i]
        middle = candles[i - n + 1:i]
        if _gap_up(base, middle[0]) and cur.bullish and all(x.body <= base.range for x in middle) and cur.close > max(x.high for x in middle):
            return _sig(cur, "High-Price Gapping Play Bullish", "bullish", "High-Gap", n + 1, 0.62)
    return None


def detect_fred_white_inside_out(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bearish and c.bullish and c.open > p.close and c.open < p.open and c.close > p.open:
        return _sig(c, "Fred Tam White Inside Out", "bullish", "WIO", 2, 0.62)
    return None


def detect_fred_black_inside_out(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if p.bullish and c.bearish and c.open < p.close and c.open > p.open and c.close < p.open:
        return _sig(c, "Fred Tam Black Inside Out", "bearish", "BIO", 2, 0.62)
    return None


def detect_eight_to_ten_record_lows(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    for n in range(8, min(10, i) + 1):
        seq = candles[i - n:i]
        if all(seq[j].low < seq[j - 1].low for j in range(1, len(seq))) and candles[i].bullish and candles[i].close > seq[-1].high:
            return _sig(candles[i], "Eight-to-Ten New Record Lows", "bullish", "8-10 Lows", n + 1, 0.66)
    return None


def detect_eight_to_ten_record_highs(candles: List[Candle], i: int, params: Dict[str, Any]) -> Optional[PatternSignal]:
    for n in range(8, min(10, i) + 1):
        seq = candles[i - n:i]
        if all(seq[j].high > seq[j - 1].high for j in range(1, len(seq))) and candles[i].bearish and candles[i].close < seq[-1].low:
            return _sig(candles[i], "Eight-to-Ten New Record Highs", "bearish", "8-10 Highs", n + 1, 0.66)
    return None


DETECTORS: List[Callable[[List[Candle], int, Dict[str, Any]], Optional[PatternSignal]]] = [
    detect_doji, detect_gravestone_doji, detect_dragonfly_doji,
    detect_spinning_top_black, detect_spinning_top_white,
    detect_hammer, detect_inverted_hammer, detect_hanging_man, detect_shooting_star,
    detect_bullish_engulfing, detect_bearish_engulfing,
    detect_bullish_harami, detect_bearish_harami,
    detect_piercing_line, detect_dark_cloud_cover,
    detect_bullish_separating_lines, detect_bearish_separating_lines,
    detect_bullish_kicking, detect_bearish_kicking,
    detect_homing_pigeon_bullish, detect_homing_pigeon_bearish,
    detect_on_neck, detect_in_neck, detect_thrusting_line,
    detect_three_white_soldiers, detect_three_black_soldiers,
    detect_morning_star, detect_evening_star,
    detect_abandoned_baby_bottom, detect_abandoned_baby_top,
    detect_three_river_morning_star, detect_three_river_evening_star,
    detect_doji_star_bottom, detect_doji_star_top,
    detect_rising_three_methods, detect_falling_three_methods,
    detect_mat_hold_pattern, detect_tasuki_upside_gap, detect_tasuki_downside_gap,
    detect_up_gap_side_by_side_white, detect_down_gap_side_by_side_white,
    detect_advance_block, detect_deliberation, detect_upside_gap_two_crows,
    detect_breakaway_bottom, detect_breakaway_top,
    detect_bullish_black_three_gaps, detect_bearish_white_three_gaps,
    detect_concealing_baby_swallow, detect_ladder_bottom,
    detect_tweezer_top, detect_tweezer_bottom,
    detect_low_price_gapping_play_bearish, detect_high_price_gapping_play_bullish,
    detect_fred_white_inside_out, detect_fred_black_inside_out,
    detect_eight_to_ten_record_lows, detect_eight_to_ten_record_highs,
]


def detect_patterns_at(candles: List[Candle], i: int, params: Optional[Dict[str, Any]] = None) -> List[PatternSignal]:
    cfg = dict(DEFAULT_PARAMS)
    if params:
        cfg.update(params)
    out: List[PatternSignal] = []
    for detector in DETECTORS:
        try:
            sig = detector(candles, i, cfg)
            if sig:
                out.append(sig)
        except Exception:
            continue
    out.sort(key=lambda s: (s.confidence, s.bars_used), reverse=True)
    return out


def detect_candlestick_patterns(
    df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
    lookback: Optional[int] = None,
) -> List[PatternSignal]:
    candles = _candles_from_df(df)
    if not candles:
        return []
    start = 0 if lookback is None else max(0, len(candles) - int(lookback))
    signals: List[PatternSignal] = []
    for i in range(start, len(candles)):
        signals.extend(detect_patterns_at(candles, i, params))
    return signals


def latest_pattern_summary(df: pd.DataFrame, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    candles = _candles_from_df(df)
    if not candles:
        return {"timestamp": None, "patterns": [], "signals": {"bullish_count": 0, "bearish_count": 0, "strongest_pattern": None}}
    patterns = detect_patterns_at(candles, len(candles) - 1, params)
    bullish = sum(1 for p in patterns if p.pattern_type == "bullish")
    bearish = sum(1 for p in patterns if p.pattern_type == "bearish")
    strongest = patterns[0].pattern_name if patterns else None
    return {
        "timestamp": candles[-1].timestamp,
        "patterns": [
            {
                "type": p.pattern_name,
                "pattern_type": p.pattern_type,
                "label": p.text_label,
                "position": p.location,
                "confidence": p.confidence,
            }
            for p in patterns
        ],
        "signals": {
            "bullish_count": bullish,
            "bearish_count": bearish,
            "strongest_pattern": strongest,
        },
    }


def plot_patterns(df: pd.DataFrame, signals: Iterable[PatternSignal], ax=None):
    """Render pattern labels on a matplotlib axis. mplfinance callers can pass ax."""
    import matplotlib.pyplot as plt

    d = _norm_df(df)
    if ax is None:
        _, ax = plt.subplots(figsize=(12, 5))
        ax.plot(d.index, d["close"], color="black", linewidth=1.0)
    atr = float((d["high"] - d["low"]).tail(30).mean()) if {"high", "low"}.issubset(d.columns) else 0.0
    offset = atr * 0.35
    for sig in signals:
        if sig.timestamp not in d.index:
            continue
        y = float(d.loc[sig.timestamp, "low" if sig.location == "belowbar" else "high"])
        y = y - offset if sig.location == "belowbar" else y + offset
        color = DEFAULT_PARAMS["label_color_bullish"] if sig.pattern_type == "bullish" else DEFAULT_PARAMS["label_color_bearish"]
        ax.text(sig.timestamp, y, sig.text_label, color=color, fontsize=8, ha="center", va="center")
    return ax
