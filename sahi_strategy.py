"""
sahi_strategy.py

SAHI log-derived discretionary strategy rules.

This module is intentionally standalone and side-effect free: it computes
indicators, emits structured trade candidates, and manages open positions, but
it never places broker orders. Live code can consume the returned dictionaries
and route them through the existing risk/execution stack.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import time as dtime
from math import floor
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_dema,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_vwap,
)


ENTRY_ALLOWED_FROM = dtime(9, 16)
NO_TRADE_START = dtime(9, 0)
NO_TRADE_END = dtime(9, 15)
RISK_PER_TRADE = 0.02
MAX_CONCURRENT_TRADES = 5
MAX_DAILY_LOSS_PCT = 0.06
OI_EXIT_THRESHOLD = 0.10
MAX_OPTION_SPREAD_PCT = 0.20
MIN_INDEPENDENT_CONFIRMATIONS = 2


@dataclass
class SahiSignal:
    action: str
    trade_type: str
    side: str
    strategy: str
    confidence: float
    limit_price: float
    stop_loss: float
    target: float
    partial_exit_at: float
    trail_after: float
    order_type: str = "LIMIT"
    qty_hint: int = 0
    reason: str = ""
    indicators: Dict[str, Any] = field(default_factory=dict)
    legs: List[Dict[str, Any]] = field(default_factory=list)
    management: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _empty_signal(trade_type: str, reason: str = "no_signal") -> Dict[str, Any]:
    return {
        "action": "HOLD",
        "trade_type": trade_type,
        "side": "",
        "strategy": "sahi_core",
        "confidence": 0.0,
        "reason": reason,
        "indicators": {},
    }


def _find_col(df: pd.DataFrame, names: Iterable[str]) -> Optional[str]:
    for name in names:
        if name in df.columns:
            return name
    return None


def _ohlcv_cols(df: pd.DataFrame) -> Tuple[Optional[str], str, str, str, Optional[str]]:
    open_col = _find_col(df, ["Open", "open", "OPEN"])
    high_col = _find_col(df, ["High", "high", "HIGH"])
    low_col = _find_col(df, ["Low", "low", "LOW"])
    close_col = _find_col(df, ["Close", "close", "CLOSE", "Adj Close", "adj_close"])
    volume_col = _find_col(df, ["Volume", "volume", "VOLUME", "vol"])
    missing = [name for name, col in (("High", high_col), ("Low", low_col), ("Close", close_col)) if col is None]
    if missing:
        raise ValueError(f"Missing required OHLC columns: {missing}")
    return open_col, high_col, low_col, close_col, volume_col


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _last(series: pd.Series, default: float = 0.0) -> float:
    try:
        value = float(series.iloc[-1])
        return default if pd.isna(value) else value
    except Exception:
        return default


def _prev(series: pd.Series, default: float = 0.0) -> float:
    try:
        value = float(series.iloc[-2])
        return default if pd.isna(value) else value
    except Exception:
        return default


def _is_entry_time_allowed(df: pd.DataFrame) -> bool:
    if not isinstance(df.index, pd.DatetimeIndex) or len(df.index) == 0:
        return True
    t = df.index[-1].time()
    if t < NO_TRADE_START:
        return True
    if NO_TRADE_START <= t <= NO_TRADE_END:
        return False
    return t >= ENTRY_ALLOWED_FROM


def _round_price(value: float) -> float:
    return round(float(value), 2) if np.isfinite(value) else 0.0


def _crossed_above(a: pd.Series, b: pd.Series) -> bool:
    return _prev(a) <= _prev(b) and _last(a) > _last(b)


def _crossed_below(a: pd.Series, b: pd.Series) -> bool:
    return _prev(a) >= _prev(b) and _last(a) < _last(b)


def _recent_swing_low(df: pd.DataFrame, lookback: int = 5) -> float:
    _, _, low_col, _, _ = _ohlcv_cols(df)
    return float(_num(df[low_col]).tail(lookback).min())


def _recent_swing_high(df: pd.DataFrame, lookback: int = 5) -> float:
    _, high_col, _, _, _ = _ohlcv_cols(df)
    return float(_num(df[high_col]).tail(lookback).max())


def _score(parts: List[bool], base: float = 0.45, step: float = 0.08) -> float:
    return round(min(0.95, base + step * sum(bool(x) for x in parts)), 4)


def _context_gate(
    context: Optional[Dict[str, Any]], *, direction: str, option: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Fail closed on market context and execution quality for SAHI entries."""
    ctx = context or {}
    if not ctx or not (ctx.get("market_context_ready") is True or ctx.get("research_proxy") is True):
        return {"ok": False, "reason": "missing_market_context"}
    if ctx.get("market_context_ready") is False or ctx.get("trade_allowed") is False:
        return {"ok": False, "reason": "market_context_blocked"}
    if ctx.get("expiry_transition") and not ctx.get("expiry_transition_liquid", False):
        return {"ok": False, "reason": "expiry_transition_liquidity_risk"}

    sector = str(ctx.get("sector_strength", ctx.get("sector_bias", "neutral"))).lower()
    weak = sector in {"weak", "weakening", "bearish", "lagging"}
    strong = sector in {"strong", "strengthening", "bullish", "leading"}
    if direction == "bullish" and weak:
        return {"ok": False, "reason": "weak_sector_for_long", "sector": sector}
    if direction == "bearish" and strong:
        return {"ok": False, "reason": "strong_sector_for_short", "sector": sector}

    if option is not None:
        bid = float(option.get("bid", option.get("bid_price", 0)) or 0)
        ask = float(option.get("ask", option.get("ask_price", 0)) or 0)
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        spread = (ask - bid) / mid if mid > 0 else float(option.get("spread_pct", 999) or 999)
        oi = float(option.get("oi", option.get("open_interest", 0)) or 0)
        volume = float(option.get("volume", option.get("traded_volume", 0)) or 0)
        allow_missing = bool(ctx.get("allow_missing_option_liquidity", False))
        if not allow_missing and (mid <= 0 or spread > float(ctx.get("max_option_spread_pct", MAX_OPTION_SPREAD_PCT))):
            return {"ok": False, "reason": "option_spread_or_quote_unacceptable", "spread_pct": spread}
        if not allow_missing and (oi <= 0 or volume <= 0):
            return {"ok": False, "reason": "option_participation_weak"}
    return {"ok": True, "reason": "context_confirmed", "sector": sector, "sector_strong": strong}


def _with_context(signal: Dict[str, Any], gate: Dict[str, Any]) -> Dict[str, Any]:
    signal.setdefault("indicators", {})["behavioral_context"] = gate
    if gate.get("sector_strong") and signal.get("confidence"):
        signal["confidence"] = round(min(0.95, float(signal["confidence"]) + 0.04), 4)
    return signal


def calculate_indicators(data: pd.DataFrame, timeframe: str = "") -> pd.DataFrame:
    """
    Add the indicators required by the SAHI rule set.

    Works on daily, hourly, 15-minute, and 5-minute OHLCV DataFrames. Intraday
    frames get VWAP and opening-range levels when a DatetimeIndex is available.
    """
    if data is None or data.empty:
        return pd.DataFrame()

    df = data.copy()
    open_col, high_col, low_col, close_col, volume_col = _ohlcv_cols(df)
    close = _num(df[close_col])
    high = _num(df[high_col])
    low = _num(df[low_col])

    for period in (20, 50, 200):
        df[f"ema_{period}"] = calculate_ema(close, period)
    df["dema_20"] = calculate_dema(close, 20)
    df["dema_50"] = calculate_dema(close, 50)
    df["rsi_14"] = calculate_rsi(close, 14)

    macd, macd_signal, macd_hist = calculate_macd(close, 12, 26, 9)
    df["macd"] = macd
    df["macd_signal"] = macd_signal
    df["macd_hist"] = macd_hist

    bb_lower, bb_mid, bb_upper = calculate_bollinger_bands(close, 20, 2.0)
    df["bb_lower"] = bb_lower
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_upper
    df["atr_14"] = calculate_atr(df, 14)
    df["atr_5"] = calculate_atr(df, 5)
    df["atr_20"] = calculate_atr(df, 20)

    df["pdh"] = high.shift(1)
    df["pdl"] = low.shift(1)
    df["donchian_high_20"] = high.rolling(20, min_periods=10).max().shift(1)
    df["donchian_low_20"] = low.rolling(20, min_periods=10).min().shift(1)

    if volume_col:
        volume = _num(df[volume_col]).fillna(0)
        df["volume_avg_20"] = volume.rolling(20, min_periods=5).mean()
        df["volume_avg_10"] = volume.rolling(10, min_periods=3).mean()
        df["volume_ratio_20"] = volume / df["volume_avg_20"].replace(0, np.nan)
        if float(volume.tail(20).sum()) <= 0:
            df["volume_ratio_20"] = np.nan
        try:
            df["vwap"] = calculate_vwap(df)
        except Exception:
            df["vwap"] = np.nan
    else:
        df["volume_avg_20"] = np.nan
        df["volume_avg_10"] = np.nan
        df["volume_ratio_20"] = 1.0
        df["vwap"] = np.nan

    if "oi" in df.columns:
        oi = _num(df["oi"])
        df["oi_change_pct"] = oi.pct_change()
        df["oi_change_30m_pct"] = oi.pct_change(6)
    elif "open_interest" in df.columns:
        oi = _num(df["open_interest"])
        df["oi_change_pct"] = oi.pct_change()
        df["oi_change_30m_pct"] = oi.pct_change(6)

    if isinstance(df.index, pd.DatetimeIndex):
        day_key = pd.Series(df.index.date, index=df.index)
        first_window = df.between_time("09:15", "09:30") if len(df) else df.iloc[0:0]
        if not first_window.empty:
            orh = first_window.groupby(first_window.index.date)[high_col].max()
            orl = first_window.groupby(first_window.index.date)[low_col].min()
            df["orh"] = day_key.map(orh)
            df["orl"] = day_key.map(orl)
        else:
            df["orh"] = np.nan
            df["orl"] = np.nan
    else:
        df["orh"] = np.nan
        df["orl"] = np.nan

    # VWAP detachment slope over the last 5 minutes where possible.
    if "vwap" in df.columns:
        periods = 5 if str(timeframe).lower() in ("1m", "1min") else 1
        minutes = 5 if str(timeframe).lower() in ("1m", "1min") else 5
        df["vwap_slope_5m_per_min"] = df["vwap"].pct_change(periods) / max(minutes, 1)
    else:
        df["vwap_slope_5m_per_min"] = np.nan

    return df


def market_regime(data: pd.DataFrame) -> str:
    """
    SAHI market regime filter.

    Returns low_volatility, gappy, or trending. If data is too short, returns
    unknown rather than forcing a tradeable regime.
    """
    if data is None or len(data) < 20:
        return "unknown"
    df = calculate_indicators(data)
    open_col, _, _, close_col, _ = _ohlcv_cols(df)
    atr_5 = _num(df["atr_5"]).tail(5).mean()
    atr_20 = _num(df["atr_20"]).tail(20).mean()
    close = _last(_num(df[close_col]))
    open_ = _last(_num(df[open_col]), close) if open_col else close
    if atr_20 > 0 and atr_5 < 0.8 * atr_20:
        return "low_volatility"
    if close > 0 and abs(close - open_) / close > 0.01:
        return "gappy"
    return "trending"


def gap_adjustment(
    nifty_data: Optional[pd.DataFrame],
    gap_threshold: float = 0.01,
    atr_skip_ratio: float = 0.5,
) -> Dict[str, Any]:
    """
    Gap and index-volatility adjustment.

    If NIFTY gaps more than 1%, reduce size by 50%. If ATR(5) < 0.5 x ATR(20),
    skip NIFTY/BANKNIFTY style index trades.
    """
    result = {
        "gap_pct": 0.0,
        "is_gap_day": False,
        "position_size_mult": 1.0,
        "equity_short_target_pct": (0.01, 0.03),
        "skip_index_trades": False,
        "reason": "neutral",
    }
    if nifty_data is None or len(nifty_data) < 21:
        result["reason"] = "insufficient_nifty_data"
        return result

    df = calculate_indicators(nifty_data)
    open_col, _, _, close_col, _ = _ohlcv_cols(df)
    today_open = _last(_num(df[open_col])) if open_col else _last(_num(df[close_col]))
    prev_close = _prev(_num(df[close_col]))
    if prev_close > 0:
        result["gap_pct"] = round((today_open - prev_close) / prev_close, 4)

    atr5 = _last(_num(df["atr_5"]))
    atr20 = _last(_num(df["atr_20"]))
    if abs(result["gap_pct"]) > gap_threshold:
        result["is_gap_day"] = True
        result["position_size_mult"] = 0.5
        result["equity_short_target_pct"] = (0.005, 0.01)
        result["reason"] = "gap_gt_1pct"
    if atr20 > 0 and atr5 < atr_skip_ratio * atr20:
        result["skip_index_trades"] = True
        result["reason"] = "nifty_atr_compressed"
    return result


def _equity_context(df: pd.DataFrame) -> Dict[str, float]:
    _, high_col, low_col, close_col, volume_col = _ohlcv_cols(df)
    close = _num(df[close_col])
    out = {
        "price": _last(close),
        "prev_price": _prev(close),
        "high": _last(_num(df[high_col])),
        "low": _last(_num(df[low_col])),
        "rsi": _last(_num(df["rsi_14"]), 50.0),
        "rsi_prev": _prev(_num(df["rsi_14"]), 50.0),
        "volume_ratio": _last(_num(df.get("volume_ratio_20", pd.Series(np.nan, index=df.index))), np.nan),
        "vwap": _last(_num(df.get("vwap", pd.Series(np.nan, index=df.index))), np.nan),
        "pdh": _last(_num(df.get("pdh", pd.Series(np.nan, index=df.index))), np.nan),
        "pdl": _last(_num(df.get("pdl", pd.Series(np.nan, index=df.index))), np.nan),
        "orh": _last(_num(df.get("orh", pd.Series(np.nan, index=df.index))), np.nan),
        "orl": _last(_num(df.get("orl", pd.Series(np.nan, index=df.index))), np.nan),
        "bb_mid": _last(_num(df.get("bb_mid", pd.Series(np.nan, index=df.index))), np.nan),
        "ema20": _last(_num(df.get("ema_20", pd.Series(np.nan, index=df.index))), np.nan),
        "ema50": _last(_num(df.get("ema_50", pd.Series(np.nan, index=df.index))), np.nan),
        "dema20": _last(_num(df.get("dema_20", pd.Series(np.nan, index=df.index))), np.nan),
        "dema50": _last(_num(df.get("dema_50", pd.Series(np.nan, index=df.index))), np.nan),
        "donchian_high": _last(_num(df.get("donchian_high_20", pd.Series(np.nan, index=df.index))), np.nan),
        "donchian_low": _last(_num(df.get("donchian_low_20", pd.Series(np.nan, index=df.index))), np.nan),
    }
    return out


def check_equity_long(data: pd.DataFrame, symbol: str = "", context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if data is None or len(data) < 25:
        return _empty_signal("equity_long", "insufficient_data")
    df = calculate_indicators(data)
    if not _is_entry_time_allowed(df):
        return _empty_signal("equity_long", "blocked_0900_0915")
    gate = _context_gate(context, direction="bullish")
    if not gate["ok"]:
        return _empty_signal("equity_long", gate["reason"])

    c = _equity_context(df)
    allow_missing_volume = bool((context or {}).get("allow_missing_volume", False))
    volume_ok = c["volume_ratio"] >= 1.5 or (allow_missing_volume and not np.isfinite(c["volume_ratio"]))
    close = _num(df[_ohlcv_cols(df)[3]])
    triggers = {
        "pdh_breakout": np.isfinite(c["pdh"]) and c["price"] > c["pdh"],
        "orh_breakout": np.isfinite(c["orh"]) and c["price"] > c["orh"],
        "bb_mid_reclaim": np.isfinite(c["bb_mid"]) and _crossed_above(close, _num(df["bb_mid"])),
        "ema_reclaim": (_crossed_above(close, _num(df["ema_20"])) or _crossed_above(close, _num(df["ema_50"]))),
        "base_breakout": np.isfinite(c["donchian_high"]) and c["price"] > c["donchian_high"],
        "polarity_bounce": np.isfinite(c["ema20"]) and c["low"] <= c["ema20"] <= c["price"],
    }
    confirmations = {
        "rsi_gt_50_rising": c["rsi"] > 50 and c["rsi"] > c["rsi_prev"],
        "volume_gt_1_5x": volume_ok,
        "price_above_vwap": not np.isfinite(c["vwap"]) or c["price"] > c["vwap"],
    }
    if not any(triggers.values()) or not all(confirmations.values()):
        return _empty_signal("equity_long", "conditions_not_met")

    entry = c["high"]
    swing_stop = _recent_swing_low(df)
    pct_stop = entry * 0.975
    stop = min(pct_stop, swing_stop) if swing_stop < entry else pct_stop
    target = entry * 1.05
    partial = entry + 0.5 * (target - entry)
    trail_after = entry + 0.75 * (target - entry)
    return _with_context(SahiSignal(
        action="BUY",
        trade_type="equity_long",
        side="LONG",
        strategy="sahi_equity_long",
        confidence=_score(list(triggers.values()) + list(confirmations.values())),
        limit_price=_round_price(entry),
        stop_loss=_round_price(stop),
        target=_round_price(target),
        partial_exit_at=_round_price(partial),
        trail_after=_round_price(trail_after),
        reason=";".join([k for k, v in {**triggers, **confirmations}.items() if v]),
        indicators=c,
        management={"partial_qty_pct": 0.5, "move_sl_to": "breakeven", "never_widen_sl": True},
    ).to_dict(), gate)


def check_equity_short(data: pd.DataFrame, symbol: str = "", context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if data is None or len(data) < 25:
        return _empty_signal("equity_short", "insufficient_data")
    df = calculate_indicators(data)
    if not _is_entry_time_allowed(df):
        return _empty_signal("equity_short", "blocked_0900_0915")
    gate = _context_gate(context, direction="bearish")
    if not gate["ok"]:
        return _empty_signal("equity_short", gate["reason"])

    c = _equity_context(df)
    allow_missing_volume = bool((context or {}).get("allow_missing_volume", False))
    volume_ok = c["volume_ratio"] >= 1.5 or (allow_missing_volume and not np.isfinite(c["volume_ratio"]))
    close = _num(df[_ohlcv_cols(df)[3]])
    triggers = {
        "pdl_breakdown": np.isfinite(c["pdl"]) and c["price"] < c["pdl"],
        "orl_breakdown": np.isfinite(c["orl"]) and c["price"] < c["orl"],
        "bb_mid_breakdown": np.isfinite(c["bb_mid"]) and _crossed_below(close, _num(df["bb_mid"])),
        "below_short_demas": c["price"] < c["dema20"] and c["price"] < c["dema50"],
        "base_breakdown": np.isfinite(c["donchian_low"]) and c["price"] < c["donchian_low"],
        "ema_rejection": c["high"] >= c["ema20"] and c["price"] < c["ema20"],
    }
    confirmations = {
        "rsi_lt_50_falling": c["rsi"] < 50 and c["rsi"] < c["rsi_prev"],
        "volume_gt_1_5x": volume_ok,
    }
    if not any(triggers.values()) or not all(confirmations.values()):
        return _empty_signal("equity_short", "conditions_not_met")

    entry = c["low"]
    day_high = _recent_swing_high(df, 3)
    stop = max(entry * 1.015, day_high)
    target_pct = 0.02
    if context and context.get("gap_adjustment", {}).get("is_gap_day"):
        target_pct = 0.0075
    target = entry * (1.0 - target_pct)
    partial = entry - 0.5 * (entry - target)
    trail_after = entry - 0.75 * (entry - target)
    return _with_context(SahiSignal(
        action="SELL",
        trade_type="equity_short",
        side="SHORT",
        strategy="sahi_equity_short",
        confidence=_score(list(triggers.values()) + list(confirmations.values())),
        limit_price=_round_price(entry),
        stop_loss=_round_price(stop),
        target=_round_price(target),
        partial_exit_at=_round_price(partial),
        trail_after=_round_price(trail_after),
        reason=";".join([k for k, v in {**triggers, **confirmations}.items() if v]),
        indicators=c,
        management={"partial_qty_pct": 0.5, "move_sl_to": "breakeven", "never_widen_sl": True},
    ).to_dict(), gate)


def _option_ok(option: Optional[Dict[str, Any]], delta_min: float, delta_max: float) -> Tuple[bool, str]:
    if not option:
        return False, "missing_option_quote"
    premium = float(option.get("premium", option.get("ltp", option.get("close", 0))) or 0)
    delta = abs(float(option.get("delta", 0.5) or 0.5))
    dte = option.get("dte", option.get("days_to_expiry", 20))
    try:
        dte_ok = 15 <= int(dte) <= 30
    except Exception:
        dte_ok = True
    if premium <= 0:
        return False, "invalid_premium"
    if not (delta_min <= delta <= delta_max):
        return False, "delta_out_of_range"
    if not dte_ok:
        return False, "dte_out_of_range"
    return True, "ok"


def _option_signal_prices(premium: float, stop_pct: float, target_pct: float) -> Tuple[float, float, float, float]:
    stop_risk = max(premium * stop_pct, 1.0)
    stop = max(0.05, premium - stop_risk)
    target = premium * (1.0 + target_pct)
    partial = premium * 1.125
    trail_after = premium * 1.20
    return stop, target, partial, trail_after


def check_long_call(
    underlying_data: pd.DataFrame,
    option: Optional[Dict[str, Any]] = None,
    symbol: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if underlying_data is None or len(underlying_data) < 30:
        return _empty_signal("long_call", "insufficient_underlying_data")
    df = calculate_indicators(underlying_data)
    if not _is_entry_time_allowed(df):
        return _empty_signal("long_call", "blocked_0900_0915")
    ok, reason = _option_ok(option, 0.40, 0.60)
    if not ok:
        return _empty_signal("long_call", reason)
    gate = _context_gate(context, direction="bullish", option=option)
    if not gate["ok"]:
        return _empty_signal("long_call", gate["reason"])

    c = _equity_context(df)
    premium = float(option.get("premium", option.get("ltp", option.get("close", 0))))
    oi_change = float(option.get("oi_change_30m_pct", option.get("oi_change_pct", 0)) or 0)
    close = _num(df[_ohlcv_cols(df)[3]])
    vwap_slope = _last(_num(df.get("vwap_slope_5m_per_min", pd.Series(0.0, index=df.index))))
    triggers = {
        "vwap_detachment": vwap_slope > 0.005 and c["volume_ratio"] >= 2.0,
        "pdh_breakout": np.isfinite(c["pdh"]) and c["price"] > c["pdh"],
        "bb_mid_breakout": np.isfinite(c["bb_mid"]) and _crossed_above(close, _num(df["bb_mid"])),
        "ema_reclaim": c["price"] > c["ema20"] and c["price"] > c["ema50"],
        "base_breakout": np.isfinite(c["donchian_high"]) and c["price"] > c["donchian_high"],
    }
    confirmations = {
        "rsi_gt_60_rising": c["rsi"] > 60 and c["rsi"] > c["rsi_prev"],
        "oi_short_covering": oi_change <= -0.05,
        "above_20_50_ema": c["price"] > c["ema20"] and c["price"] > c["ema50"],
    }
    if not any(triggers.values()) or not all(confirmations.values()):
        return _empty_signal("long_call", "conditions_not_met")

    stop, target, partial, trail_after = _option_signal_prices(premium, 0.22, 0.50)
    return _with_context(SahiSignal(
        action="BUY",
        trade_type="long_call",
        side="LONG",
        strategy="sahi_long_call",
        confidence=_score(list(triggers.values()) + list(confirmations.values()), base=0.50),
        limit_price=_round_price(premium),
        stop_loss=_round_price(stop),
        target=_round_price(target),
        partial_exit_at=_round_price(partial),
        trail_after=_round_price(trail_after),
        reason=";".join([k for k, v in {**triggers, **confirmations}.items() if v]),
        indicators={**c, "oi_change_30m_pct": oi_change, "vwap_slope_5m_per_min": vwap_slope},
        management={"partial_qty_pct": 0.5, "move_sl_to": "breakeven", "lock_half_profit_after_20pct": True},
    ).to_dict(), gate)


def check_long_put(
    underlying_data: pd.DataFrame,
    option: Optional[Dict[str, Any]] = None,
    symbol: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if underlying_data is None or len(underlying_data) < 30:
        return _empty_signal("long_put", "insufficient_underlying_data")
    df = calculate_indicators(underlying_data)
    if not _is_entry_time_allowed(df):
        return _empty_signal("long_put", "blocked_0900_0915")
    ok, reason = _option_ok(option, 0.40, 0.50)
    if not ok:
        return _empty_signal("long_put", reason)
    gate = _context_gate(context, direction="bearish", option=option)
    if not gate["ok"]:
        return _empty_signal("long_put", gate["reason"])

    c = _equity_context(df)
    premium = float(option.get("premium", option.get("ltp", option.get("close", 0))))
    close = _num(df[_ohlcv_cols(df)[3]])
    ten_day_return = (c["price"] / float(close.iloc[-11]) - 1.0) if len(close) > 11 and close.iloc[-11] else 0.0
    macd_bearish = _last(_num(df["macd_hist"])) < 0
    triggers = {
        "momentum_put": _crossed_below(close, _num(df["bb_mid"])) and macd_bearish and c["rsi"] < 40 and (not np.isfinite(c["vwap"]) or c["price"] < c["vwap"]),
        "downtrend_put": c["price"] < c["dema20"] and c["price"] < c["dema50"] and c["rsi_prev"] >= 50 and c["rsi"] < 50,
        "mean_reversion_put": ten_day_return > 0.15 and c["rsi"] > 70 and c["volume_ratio"] < 1.0,
    }
    confirmations = {
        "rsi_bearish": c["rsi"] < 50 or c["rsi"] > 70,
        "macd_bearish": macd_bearish,
        "price_below_trend": c["price"] < c["dema20"] and c["price"] < c["dema50"],
        "volume_active": np.isfinite(c["volume_ratio"]) and c["volume_ratio"] >= 1.0,
    }
    if not any(triggers.values()) or sum(bool(v) for v in confirmations.values()) < MIN_INDEPENDENT_CONFIRMATIONS:
        return _empty_signal("long_put", "conditions_not_met")

    stop, target, partial, trail_after = _option_signal_prices(premium, 0.28, 0.50)
    return _with_context(SahiSignal(
        action="BUY",
        trade_type="long_put",
        side="LONG",
        strategy="sahi_long_put",
        confidence=_score(list(triggers.values()) + list(confirmations.values()), base=0.50, step=0.08),
        limit_price=_round_price(premium),
        stop_loss=_round_price(stop),
        target=_round_price(target),
        partial_exit_at=_round_price(partial),
        trail_after=_round_price(trail_after),
        reason=";".join([k for k, v in {**triggers, **confirmations}.items() if v]),
        indicators={**c, "ten_day_return": round(ten_day_return, 4), "macd_bearish": macd_bearish},
        management={"partial_qty_pct": 0.5, "move_sl_to": "breakeven", "lock_half_profit_after_20pct": True},
    ).to_dict(), gate)


def check_bull_call_spread(
    underlying_data: pd.DataFrame,
    option_chain: Optional[Dict[str, Any]] = None,
    symbol: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if underlying_data is None or len(underlying_data) < 30:
        return _empty_signal("bull_call_spread", "insufficient_underlying_data")
    if not option_chain:
        return _empty_signal("bull_call_spread", "missing_option_chain")
    df = calculate_indicators(underlying_data)
    gate = _context_gate(context, direction="bullish")
    if not gate["ok"]:
        return _empty_signal("bull_call_spread", gate["reason"])
    c = _equity_context(df)
    macd_bullish = _last(_num(df["macd_hist"])) > 0
    support_bounce = c["low"] <= c["ema20"] <= c["price"]
    heavy_put_writing = float(option_chain.get("put_writing_oi_change_pct", 0) or 0) >= 0.10
    if not (support_bounce and heavy_put_writing and c["rsi"] > 50 and macd_bullish):
        return _empty_signal("bull_call_spread", "conditions_not_met")

    buy_call = dict(option_chain.get("buy_atm_call", {}))
    sell_call = dict(option_chain.get("sell_otm_call", {}))
    for leg in (buy_call, sell_call):
        leg_gate = _context_gate(context, direction="bullish", option=leg)
        if not leg_gate["ok"]:
            return _empty_signal("bull_call_spread", leg_gate["reason"])
    debit = float(buy_call.get("premium", 0)) - float(sell_call.get("premium", 0))
    width = abs(float(sell_call.get("strike", c["price"] * 1.05)) - float(buy_call.get("strike", c["price"])))
    if debit <= 0 or width <= debit:
        return _empty_signal("bull_call_spread", "invalid_spread_prices")
    max_profit = width - debit
    stop = debit * 0.45
    target1 = debit + 0.50 * max_profit
    return _with_context(SahiSignal(
        action="BUY_SPREAD",
        trade_type="bull_call_spread",
        side="BULLISH",
        strategy="sahi_bull_call_spread",
        confidence=0.72,
        limit_price=_round_price(debit),
        stop_loss=_round_price(stop),
        target=_round_price(debit + 0.75 * max_profit),
        partial_exit_at=_round_price(target1),
        trail_after=_round_price(target1),
        reason="support_bounce;heavy_put_writing;rsi_gt_50;macd_bullish",
        indicators={**c, "net_debit": debit, "max_profit": max_profit},
        legs=[
            {"action": "BUY", "option_type": "CE", **buy_call},
            {"action": "SELL", "option_type": "CE", **sell_call},
        ],
        management={"partial_qty_pct": 0.5, "move_sl_to": "net_debit"},
    ).to_dict(), gate)


def check_bear_put_spread(
    underlying_data: pd.DataFrame,
    option_chain: Optional[Dict[str, Any]] = None,
    symbol: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Confirmed bearish structure expressed as a defined-risk debit spread."""
    if underlying_data is None or len(underlying_data) < 30:
        return _empty_signal("bear_put_spread", "insufficient_underlying_data")
    if not option_chain:
        return _empty_signal("bear_put_spread", "missing_option_chain")
    df = calculate_indicators(underlying_data)
    gate = _context_gate(context, direction="bearish")
    if not gate["ok"]:
        return _empty_signal("bear_put_spread", gate["reason"])
    c = _equity_context(df)
    close = _num(df[_ohlcv_cols(df)[3]])
    confirmations = {
        "bearish_structure": c["price"] < c["dema20"] and c["price"] < c["dema50"],
        "rsi_weak": c["rsi"] < 50 and c["rsi"] < c["rsi_prev"],
        "macd_bearish": _last(_num(df["macd_hist"])) < 0,
        "breakdown_confirmed": _crossed_below(close, _num(df["bb_mid"])) or (
            np.isfinite(c["donchian_low"]) and c["price"] < c["donchian_low"]
        ),
    }
    if not all(confirmations.values()):
        return _empty_signal("bear_put_spread", "conditions_not_met")
    buy_put = dict(option_chain.get("buy_atm_put", {}))
    sell_put = dict(option_chain.get("sell_otm_put", {}))
    for leg in (buy_put, sell_put):
        leg_gate = _context_gate(context, direction="bearish", option=leg)
        if not leg_gate["ok"]:
            return _empty_signal("bear_put_spread", leg_gate["reason"])
    debit = float(buy_put.get("premium", 0) or 0) - float(sell_put.get("premium", 0) or 0)
    width = abs(float(buy_put.get("strike", c["price"])) - float(sell_put.get("strike", c["price"] * 0.95)))
    if debit <= 0 or width <= debit:
        return _empty_signal("bear_put_spread", "invalid_spread_prices")
    max_profit = width - debit
    target1 = debit + 0.50 * max_profit
    return _with_context(SahiSignal(
        action="BUY_SPREAD", trade_type="bear_put_spread", side="BEARISH",
        strategy="sahi_bear_put_spread", confidence=0.72,
        limit_price=_round_price(debit), stop_loss=_round_price(debit * 0.45),
        target=_round_price(debit + 0.75 * max_profit),
        partial_exit_at=_round_price(target1), trail_after=_round_price(target1),
        reason=";".join(k for k, v in confirmations.items() if v),
        indicators={**c, "net_debit": debit, "max_profit": max_profit},
        legs=[{"action": "BUY", "option_type": "PE", **buy_put},
              {"action": "SELL", "option_type": "PE", **sell_put}],
        management={"partial_qty_pct": 0.5, "move_sl_to": "net_debit", "never_widen_sl": True},
    ).to_dict(), gate)


def check_short_put(underlying_data: pd.DataFrame, option: Optional[Dict[str, Any]] = None, symbol: str = "", context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if underlying_data is None or len(underlying_data) < 30:
        return _empty_signal("short_put", "insufficient_underlying_data")
    df = calculate_indicators(underlying_data)
    c = _equity_context(df)
    ok, reason = _option_ok(option, 0.0, 0.30)
    if not ok:
        return _empty_signal("short_put", reason)
    gate = _context_gate(context, direction="bullish", option=option)
    if not gate["ok"]:
        return _empty_signal("short_put", gate["reason"])
    premium = float(option.get("premium", option.get("ltp", option.get("close", 0))))
    macd_bullish = _last(_num(df["macd_hist"])) > 0
    close = _num(df[_ohlcv_cols(df)[3]])
    trigger = _crossed_above(close, _num(df["bb_mid"])) and c["rsi"] > 40 and c["rsi"] > c["rsi_prev"] and macd_bullish
    if not trigger:
        return _empty_signal("short_put", "conditions_not_met")
    return _with_context(SahiSignal(
        action="SELL",
        trade_type="short_put",
        side="SHORT_PREMIUM",
        strategy="sahi_short_put",
        confidence=0.64,
        limit_price=_round_price(premium),
        stop_loss=_round_price(premium * 1.50),
        target=_round_price(premium * 0.50),
        partial_exit_at=_round_price(premium * 0.80),
        trail_after=_round_price(premium),
        reason="bb_mid_breakout;rsi_rising;macd_bullish;delta_lte_0_3",
        indicators=c,
        management={"partial_qty_pct": 0.5, "trail_sl_to": "entry_credit"},
    ).to_dict(), gate)


def check_short_call(underlying_data: pd.DataFrame, option: Optional[Dict[str, Any]] = None, symbol: str = "", context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if underlying_data is None or len(underlying_data) < 30:
        return _empty_signal("short_call", "insufficient_underlying_data")
    df = calculate_indicators(underlying_data)
    c = _equity_context(df)
    ok, reason = _option_ok(option, 0.0, 0.30)
    if not ok:
        return _empty_signal("short_call", reason)
    gate = _context_gate(context, direction="bearish", option=option)
    if not gate["ok"]:
        return _empty_signal("short_call", gate["reason"])
    premium = float(option.get("premium", option.get("ltp", option.get("close", 0))))
    macd_bearish = _last(_num(df["macd_hist"])) < 0
    call_writing = float(option.get("oi_change_30m_pct", option.get("oi_change_pct", 0)) or 0) >= 0.10
    close = _num(df[_ohlcv_cols(df)[3]])
    trigger = _crossed_below(close, _num(df["bb_mid"])) and c["rsi"] < 50 and c["rsi"] < c["rsi_prev"] and macd_bearish and call_writing
    if not trigger:
        return _empty_signal("short_call", "conditions_not_met")
    return _with_context(SahiSignal(
        action="SELL",
        trade_type="short_call",
        side="SHORT_PREMIUM",
        strategy="sahi_short_call",
        confidence=0.64,
        limit_price=_round_price(premium),
        stop_loss=_round_price(premium * 1.50),
        target=_round_price(premium * 0.50),
        partial_exit_at=_round_price(premium * 0.80),
        trail_after=_round_price(premium),
        reason="bb_mid_breakdown;rsi_falling;macd_bearish;call_writing",
        indicators=c,
        management={"partial_qty_pct": 0.5, "trail_sl_to": "entry_credit"},
    ).to_dict(), gate)


def check_oi_buildup(
    oi_snapshot: Optional[Any],
    trade_type: str,
    threshold: float = OI_EXIT_THRESHOLD,
) -> Dict[str, Any]:
    """
    Detect adverse OI build-up.

    The function accepts either a dict or a DataFrame. Preferred fields are
    same_strike_oi_change_pct, opposite_oi_change_pct, call_oi_change_pct, and
    put_oi_change_pct. Percent values may be expressed as 0.10 or 10.
    """
    if oi_snapshot is None:
        return {"exit": False, "reason": "no_oi_snapshot", "oi_change_pct": 0.0}

    def pct(value: Any) -> float:
        try:
            v = float(value)
            return v / 100.0 if abs(v) > 1 else v
        except Exception:
            return 0.0

    if isinstance(oi_snapshot, pd.DataFrame):
        if oi_snapshot.empty:
            return {"exit": False, "reason": "empty_oi_snapshot", "oi_change_pct": 0.0}
        row = oi_snapshot.iloc[-1].to_dict()
    else:
        row = dict(oi_snapshot)

    trade = str(trade_type).lower()
    if trade in ("long_call", "short_call"):
        adverse = pct(row.get("same_strike_oi_change_pct", row.get("call_oi_change_pct", row.get("oi_change_pct", 0))))
        label = "call_oi_buildup"
    elif trade in ("long_put", "short_put"):
        adverse = pct(row.get("same_strike_oi_change_pct", row.get("put_oi_change_pct", row.get("oi_change_pct", 0))))
        label = "put_oi_buildup"
    else:
        adverse = pct(row.get("opposite_oi_change_pct", row.get("oi_change_pct", 0)))
        label = "opposite_oi_buildup"
    return {
        "exit": adverse >= threshold,
        "reason": label if adverse >= threshold else "oi_ok",
        "oi_change_pct": round(adverse, 4),
    }


def rollover_filter(
    symbol: str,
    sector: str = "",
    rollover_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Monthly sector rollover filter.

    Missing rollover data is neutral. High rollover is a mild positive for
    longs, while low rollover reduces long exposure instead of hard-blocking
    every trade.
    """
    if not rollover_data:
        return {"allow_long": True, "position_size_mult": 1.0, "bias": "neutral", "reason": "missing_rollover_data"}

    symbol_u = str(symbol).upper()
    sector_key = str(sector or "").upper()
    raw = rollover_data.get(symbol_u, rollover_data.get(sector_key, rollover_data.get(sector, None)))
    if isinstance(raw, dict):
        rollover = raw.get("rollover_pct", raw.get("rollover", raw.get("pct", None)))
    else:
        rollover = raw
    try:
        value = float(rollover)
        value = value / 100.0 if value > 1 else value
    except Exception:
        return {"allow_long": True, "position_size_mult": 1.0, "bias": "neutral", "reason": "invalid_rollover_data"}

    if value >= 0.90:
        return {"allow_long": True, "position_size_mult": 1.15, "bias": "preferred_long", "reason": "rollover_gte_90", "rollover_pct": round(value, 4)}
    if value < 0.85:
        return {"allow_long": True, "position_size_mult": 0.50, "bias": "reduce_longs", "reason": "rollover_lt_85", "rollover_pct": round(value, 4)}
    return {"allow_long": True, "position_size_mult": 1.0, "bias": "neutral", "reason": "rollover_85_90", "rollover_pct": round(value, 4)}


def manage_position(
    position: Dict[str, Any],
    current_price: float,
    oi_snapshot: Optional[Any] = None,
    use_enhancements: bool = True,
) -> Dict[str, Any]:
    """
    Universal SAHI position manager.

    Enforces: no stop widening, no averaging down, partial at milestone,
    breakeven stop after partial, tighter trailing after deeper profit, and
    adverse OI exits.
    """
    pos = dict(position)
    side = str(pos.get("side", "LONG")).upper()
    trade_type = str(pos.get("trade_type", "equity_long"))
    entry = float(pos.get("entry_price", pos.get("entry", 0)) or 0)
    stop = float(pos.get("stop_loss", pos.get("stop", 0)) or 0)
    target = float(pos.get("target", 0) or 0)
    qty = int(pos.get("qty", pos.get("remaining_qty", 0)) or 0)
    remaining_qty = int(pos.get("remaining_qty", qty) or qty)
    partial_taken = bool(pos.get("partial_taken", False))
    original_stop = float(pos.get("original_stop_loss", stop) or stop)

    if entry <= 0 or current_price <= 0 or target <= 0:
        return {"action": "HOLD", "position": pos, "reason": "invalid_position_state"}

    oi = check_oi_buildup(oi_snapshot, trade_type) if oi_snapshot is not None else {"exit": False}
    if oi.get("exit"):
        return {"action": "EXIT_ALL", "position": pos, "reason": oi["reason"], "exit_price": _round_price(current_price)}

    is_short = side in ("SHORT", "SELL", "SHORT_PREMIUM")
    stop_hit = current_price >= stop if is_short else current_price <= stop
    target_hit = current_price <= target if is_short else current_price >= target
    if stop_hit:
        return {"action": "EXIT_ALL", "position": pos, "reason": "stop_loss_hit", "exit_price": _round_price(stop)}
    if target_hit:
        return {"action": "EXIT_ALL", "position": pos, "reason": "target_hit", "exit_price": _round_price(target)}

    total_distance = abs(target - entry)
    favorable = (entry - current_price) if is_short else (current_price - entry)
    progress = favorable / total_distance if total_distance > 0 else 0.0
    actions: List[Dict[str, Any]] = []

    if progress >= 0.50 and not partial_taken and remaining_qty > 1:
        exit_qty = max(1, floor(remaining_qty * 0.50))
        remaining_qty -= exit_qty
        partial_taken = True
        breakeven = entry
        if is_short:
            stop = min(stop, breakeven)
        else:
            stop = max(stop, breakeven)
        actions.append({"action": "PARTIAL_EXIT", "qty": exit_qty, "reason": "profit_50pct_target"})

    if progress >= 0.75:
        if use_enhancements:
            extra_steps = max(0, floor((progress - 0.75) / 0.10))
            lock_fraction = min(0.90, 0.50 + 0.10 * extra_steps)
        else:
            lock_fraction = 0.50
        locked_profit = favorable * lock_fraction
        new_stop = entry - locked_profit if is_short else entry + locked_profit
        if is_short:
            stop = min(stop, new_stop)
        else:
            stop = max(stop, new_stop)
        actions.append({"action": "TIGHTEN_STOP", "stop_loss": _round_price(stop), "lock_fraction": round(lock_fraction, 2)})

    # Hard guard: never widen against original stop.
    if is_short:
        stop = min(stop, original_stop) if original_stop > 0 else stop
    else:
        stop = max(stop, original_stop) if original_stop > 0 else stop

    pos.update({
        "stop_loss": _round_price(stop),
        "remaining_qty": remaining_qty,
        "partial_taken": partial_taken,
        "last_progress": round(progress, 4),
        "average_down_allowed": False,
    })
    if actions:
        return {"action": "MANAGE", "actions": actions, "position": pos, "reason": "milestone_management"}
    return {"action": "HOLD", "position": pos, "reason": "no_management_action"}


ENHANCEMENT_DECISIONS: List[Dict[str, str]] = [
    {
        "id": "S1",
        "suggestion": "Multiple stop loss tightenings",
        "decision": "IMPROVISE",
        "justification": "Use stepwise tightening only after the 75% target milestone; never loosen the stop and cap locked profit at 90% to avoid noise exits.",
    },
    {
        "id": "S2",
        "suggestion": "Early exit on OI build-up at same strike",
        "decision": "IMPROVISE",
        "justification": "Same-strike OI growth is adverse for long options, but immediate exit should be reserved for >=10% buildup with price/RSI deterioration or used as a hard tighten trigger.",
    },
    {
        "id": "S3",
        "suggestion": "Gap day reduce targets and size",
        "decision": "INCLUDE",
        "justification": "Consistent with the core regime filter; gap days get 50% size and equity-short targets compressed to 0.5-1%.",
    },
    {
        "id": "S4",
        "suggestion": "Absolute minimum stop for low-premium options",
        "decision": "INCLUDE",
        "justification": "Prevents meaningless paise-level stops; option stops are max(percent stop, Rs 1).",
    },
    {
        "id": "S5",
        "suggestion": "Monthly sector rollover filter",
        "decision": "IMPROVISE",
        "justification": "Use as a position-size/bias filter, not a standalone entry trigger; missing or stale rollover data remains neutral.",
    },
    {
        "id": "S6",
        "suggestion": "Worst possible price backtest assumption",
        "decision": "IMPROVISE",
        "justification": "Use conservative limit-fill assumptions plus normal cost model. Stop fills should include slippage in live-grade tests, not exact fills only.",
    },
    {
        "id": "S7",
        "suggestion": "Time-based OI re-check every 30 min",
        "decision": "INCLUDE",
        "justification": "Matches options microstructure risk; adverse OI changes above 10% trigger exit/tighten checks.",
    },
    {
        "id": "S8",
        "suggestion": "Bad Call logging without action",
        "decision": "IMPROVISE",
        "justification": "Keep the label for journaling, but also store rule context so repeated bad calls can feed blacklisting or parameter review.",
    },
]
