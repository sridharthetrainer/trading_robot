from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import math

import numpy as np
import pandas as pd

from pivot_boss import build_ochao_levels


INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "SENSEX"}


def _norm(df: Any) -> Optional[pd.DataFrame]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    return out if "close" in out.columns else None


def _num(v: Any, default: float = math.nan) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _last(df: pd.DataFrame, col: str, default: float = math.nan) -> float:
    if col not in df.columns or len(df[col]) == 0:
        return default
    return _num(pd.to_numeric(df[col], errors="coerce").iloc[-1], default)


def _ema(df: Optional[pd.DataFrame], length: int) -> float:
    if df is None or "close" not in df.columns or len(df) < max(5, min(length, 50)):
        return math.nan
    close = pd.to_numeric(df["close"], errors="coerce")
    return _num(close.ewm(span=length, adjust=False).mean().iloc[-1])


def _ema_cross(df: Optional[pd.DataFrame], length: int, side: str) -> bool:
    if df is None or "close" not in df.columns or len(df) < max(6, min(length, 50)):
        return False
    close = pd.to_numeric(df["close"], errors="coerce")
    ema = close.ewm(span=length, adjust=False).mean()
    c0, c1 = _num(close.iloc[-2]), _num(close.iloc[-1])
    e0, e1 = _num(ema.iloc[-2]), _num(ema.iloc[-1])
    if any(math.isnan(x) for x in (c0, c1, e0, e1)):
        return False
    if side == "BUY":
        return c0 <= e0 and c1 > e1
    return c0 >= e0 and c1 < e1


def _inside(value: float, low: float, high: float) -> bool:
    return all(not math.isnan(x) and x > 0 for x in (value, low, high)) and low <= value <= high


def _near(price: float, level: float, pct: float = 0.0015) -> bool:
    return level > 0 and abs(price - level) / level <= pct


def _frames_from(option_data: Mapping[str, Any] | None, df: pd.DataFrame, df_htf: Any) -> Dict[str, Any]:
    option_data = option_data or {}
    frames = dict(option_data.get("frames") or option_data.get("timeframes") or {})
    frames.setdefault("5m", df)
    frames.setdefault("primary", df)
    if df_htf is not None:
        frames.setdefault("15m", df_htf)
        frames.setdefault("htf", df_htf)
    return frames


def _first_frame(frames: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        val = frames.get(name)
        if val is not None:
            return val
    return None


def _level_pack(df: pd.DataFrame, df_htf: Any, option_data: Mapping[str, Any] | None) -> Dict[str, Dict[str, float]]:
    existing = (option_data or {}).get("ochao_levels")
    if isinstance(existing, dict) and existing.get("daily"):
        return existing
    return build_ochao_levels(df, df_htf if df_htf is not None else df)


def _pivot_bias(price: float, pack: Mapping[str, float], prefix: str = "") -> int:
    pivot = _num(pack.get(f"{prefix}P"))
    tc = _num(pack.get(f"{prefix}TC"))
    bc = _num(pack.get(f"{prefix}BC"))
    if price > max(pivot, tc, bc):
        return 1
    if price < min(pivot, tc, bc):
        return -1
    return 0


def run_pivot_scalping_strategy(
    df,
    df_htf=None,
    option_data: Optional[Dict[str, Any]] = None,
    symbol: str = "",
) -> Dict[str, Any]:
    """CPR/Camarilla MTF option scalper for index weekly options.

    The signal is intentionally strict: CPR/Camarilla location creates the
    setup, then 1m/5m EMA50 and 1m EMA200 decide the fast entry direction.
    """
    try:
        import config as _cfg
        if not bool(getattr(_cfg, "ENABLE_PIVOT_SCALPING_STRATEGY", True)):
            return {"strategy": "pivot_scalping", "score": 0.0, "direction": None}
    except Exception:
        pass

    symbol_u = str(symbol or (option_data or {}).get("symbol", "")).upper()
    try:
        import config as _cfg
        allowed = set(getattr(_cfg, "PIVOT_SCALPING_UNDERLYINGS", INDEX_UNDERLYINGS))
    except Exception:
        allowed = INDEX_UNDERLYINGS
    if symbol_u and symbol_u not in allowed:
        return {"strategy": "pivot_scalping", "score": 0.0, "direction": None}

    d5 = _norm(df)
    if d5 is None or len(d5) < 20:
        return {"strategy": "pivot_scalping", "score": 0.0, "direction": None}

    frames = _frames_from(option_data, d5, df_htf)
    d1 = _norm(_first_frame(frames, "1m", "df_1m", "one_minute"))
    d15 = _norm(_first_frame(frames, "15m", "htf"))
    d60 = _norm(_first_frame(frames, "60m", "1h", "df_1h"))
    dd = _norm(_first_frame(frames, "D", "daily"))

    price = _last(d5, "close")
    prev_price = _num(pd.to_numeric(d5["close"], errors="coerce").iloc[-2])
    if math.isnan(price) or math.isnan(prev_price) or price <= 0:
        return {"strategy": "pivot_scalping", "score": 0.0, "direction": None}

    levels = _level_pack(d5, df_htf, option_data)
    daily = levels.get("daily", {}) if isinstance(levels, dict) else {}
    weekly = levels.get("weekly", {}) if isinstance(levels, dict) else {}
    monthly = levels.get("monthly", {}) if isinstance(levels, dict) else {}
    yearly = levels.get("yearly", {}) if isinstance(levels, dict) else {}
    if not daily:
        return {"strategy": "pivot_scalping", "score": 0.0, "direction": None}

    p = _num(daily.get("P"))
    tc = _num(daily.get("TC"))
    bc = _num(daily.get("BC"))
    cpr_low, cpr_high = min(tc, bc), max(tc, bc)
    r1 = _num(daily.get("R1"))
    s1 = _num(daily.get("S1"))
    pdh = _num(daily.get("H"))
    pdl = _num(daily.get("L"))
    h3 = _num(daily.get("H3"))
    h4 = _num(daily.get("H4"))
    h5 = _num(daily.get("H5"))
    l3 = _num(daily.get("L3"))
    l4 = _num(daily.get("L4"))
    l5 = _num(daily.get("L5"))

    ema1_200 = _ema(d1, 200)
    ema1_50 = _ema(d1, 50)
    ema5_50 = _ema(d5, 50)
    ema60_50 = _ema(d60, 50)
    ema60_200 = _ema(d60, 200)
    day_20 = _ema(dd, 20)
    day_50 = _ema(dd, 50)
    day_200 = _ema(dd, 200)

    if math.isnan(ema1_200):
        ema1_200 = _ema(d5, 200)
    if math.isnan(ema1_50):
        ema1_50 = ema5_50

    bullish_fast = price > ema1_200 and price > ema1_50 and price > ema5_50
    bearish_fast = price < ema1_200 and price < ema1_50 and price < ema5_50
    cross_buy = _ema_cross(d1, 50, "BUY") or _ema_cross(d5, 50, "BUY")
    cross_sell = _ema_cross(d1, 50, "SELL") or _ema_cross(d5, 50, "SELL")

    daily_bias = _pivot_bias(price, daily)
    weekly_bias = _pivot_bias(price, weekly, "W_")
    monthly_bias = _pivot_bias(price, monthly, "M_")
    yearly_bias = _pivot_bias(price, yearly, "Y_")
    pivot_sum = daily_bias + weekly_bias + monthly_bias + yearly_bias
    pivot_votes = [x for x in (daily_bias, weekly_bias, monthly_bias, yearly_bias) if x != 0]

    in_cpr = _inside(price, cpr_low, cpr_high)
    l3_in_cpr = _inside(l3, cpr_low, cpr_high)
    h3_in_cpr = _inside(h3, cpr_low, cpr_high)
    golden_bull = l3_in_cpr or _inside(s1, cpr_low, cpr_high)
    golden_bear = h3_in_cpr or _inside(r1, cpr_low, cpr_high)

    support_reaction = (
        _near(price, s1) or _near(price, pdl) or _near(price, l3) or _near(price, l4)
    ) and price >= prev_price
    resistance_reaction = (
        _near(price, r1) or _near(price, pdh) or _near(price, h3) or _near(price, h4)
    ) and price <= prev_price
    upper_trigger = max(r1, pdh, cpr_high)
    lower_trigger = min(s1, pdl, cpr_low)
    breakout_buy = (
        (prev_price <= upper_trigger < price)
        or (price > upper_trigger and bullish_fast)
        or (price > h4 and prev_price <= h4)
    )
    breakdown_sell = (
        (prev_price >= lower_trigger > price)
        or (price < lower_trigger and bearish_fast)
        or (price < l4 and prev_price >= l4)
    )

    # Avoid the no-man's-land between Camarilla reversal and extreme levels
    # unless there is a real breakout through H4/L4.
    no_trade_zone = (
        (_inside(price, h3, h4) and not (breakout_buy or price > upper_trigger))
        or (_inside(price, l4, l3) and not (breakdown_sell or price < lower_trigger))
        or (_inside(price, h4, h5) and not breakout_buy)
        or (_inside(price, l5, l4) and not breakdown_sell)
    )

    buy_score = sell_score = 0.0
    buy_reasons = []
    sell_reasons = []

    if bullish_fast:
        buy_score += 2.0; buy_reasons.append("1m_200_1m50_5m50_bull")
    if bearish_fast:
        sell_score += 2.0; sell_reasons.append("1m_200_1m50_5m50_bear")
    if cross_buy:
        buy_score += 1.1; buy_reasons.append("ema50_cross_buy")
    if cross_sell:
        sell_score += 1.1; sell_reasons.append("ema50_cross_sell")
    if pivot_sum >= 2:
        buy_score += 1.4; buy_reasons.append("daily_weekly_monthly_yearly_cpr_bull")
    elif pivot_sum <= -2:
        sell_score += 1.4; sell_reasons.append("daily_weekly_monthly_yearly_cpr_bear")
    elif daily_bias < 0 and weekly_bias > 0 and support_reaction:
        buy_score += 0.9; buy_reasons.append("below_daily_supported_by_weekly")
    elif daily_bias > 0 and weekly_bias < 0 and resistance_reaction:
        sell_score += 0.9; sell_reasons.append("above_daily_rejected_by_weekly")

    if support_reaction:
        buy_score += 1.2; buy_reasons.append("support_reaction_s1_pdl_camarilla")
    if resistance_reaction:
        sell_score += 1.2; sell_reasons.append("resistance_reaction_r1_pdh_camarilla")
    if breakout_buy:
        buy_score += 1.6; buy_reasons.append("level_breakout_pdh_r1_h4")
    if breakdown_sell:
        sell_score += 1.6; sell_reasons.append("level_breakdown_pdl_s1_l4")
    if in_cpr and golden_bull and bullish_fast:
        buy_score += 1.0; buy_reasons.append("golden_bullish_pivot_inside_cpr")
    if in_cpr and golden_bear and bearish_fast:
        sell_score += 1.0; sell_reasons.append("golden_bearish_pivot_inside_cpr")

    if not math.isnan(ema60_50):
        if price > ema60_50:
            buy_score += 0.35; sell_score -= 0.25
        elif price < ema60_50:
            sell_score += 0.35; buy_score -= 0.25
    if not math.isnan(ema60_200):
        if price > ema60_200:
            buy_score += 0.35
        elif price < ema60_200:
            sell_score += 0.35
    if all(not math.isnan(x) for x in (day_20, day_50, day_200)):
        if price > day_20 > day_50 > day_200:
            buy_score += 0.6; buy_reasons.append("daily_20_50_200_bull")
        elif price < day_20 < day_50 < day_200:
            sell_score += 0.6; sell_reasons.append("daily_20_50_200_bear")

    if no_trade_zone:
        buy_score -= 1.0
        sell_score -= 1.0
    if in_cpr and not (golden_bull or golden_bear or cross_buy or cross_sell):
        buy_score -= 0.8
        sell_score -= 0.8

    direction = "BUY" if buy_score > sell_score else "SELL" if sell_score > buy_score else None
    score = max(buy_score, sell_score)
    reasons = buy_reasons if direction == "BUY" else sell_reasons
    if not direction or score < 4.2:
        return {
            "strategy": "pivot_scalping",
            "score": 0.0,
            "direction": None,
            "levels": daily,
            "ochao_levels": levels,
            "cpr_bias": "BULLISH" if daily_bias > 0 else "BEARISH" if daily_bias < 0 else "INSIDE_CPR",
            "reason": "no_pivot_scalp_setup",
        }

    volume_ratio = _last(d5, "volume_ratio", default=math.nan)
    if math.isnan(volume_ratio) and "volume" in d5.columns and len(d5) >= 20:
        vol = pd.to_numeric(d5["volume"], errors="coerce")
        volume_ratio = _num(vol.iloc[-1] / max(vol.tail(20).mean(), 1.0), 1.0)
    if not math.isnan(volume_ratio):
        if volume_ratio >= 1.2:
            score += 0.4; reasons.append("volume_confirmed")
        elif volume_ratio < 0.6 and (breakout_buy or breakdown_sell):
            score -= 0.7; reasons.append("weak_breakout_volume")

    score = max(0.0, min(9.5, score + 3.0))
    return {
        "strategy": "pivot_scalping",
        "score": round(float(score), 2),
        "direction": direction,
        "style": "scalping",
        "instrument_type": "OPTION",
        "option_underlying": symbol_u or None,
        "reason": ",".join(reasons[:5]),
        "levels": daily,
        "ochao_levels": levels,
        "cpr_bias": "BULLISH" if daily_bias > 0 else "BEARISH" if daily_bias < 0 else "INSIDE_CPR",
        "cpr_structure": {
            "in_cpr": in_cpr,
            "golden_bullish_pivot": golden_bull,
            "golden_bearish_pivot": golden_bear,
            "support_reaction": support_reaction,
            "resistance_reaction": resistance_reaction,
            "no_trade_zone": no_trade_zone,
        },
        "level_breakout": bool(breakout_buy or breakdown_sell),
        "level_breakout_confirmed": bool(breakout_buy or breakdown_sell),
        "level_rejection": bool(support_reaction or resistance_reaction),
        "rejection_direction": direction if (support_reaction or resistance_reaction) else None,
        "volume_confirmation": bool(not math.isnan(volume_ratio) and volume_ratio >= 1.0),
        "pivot_scalp_context": {
            "price": round(price, 2),
            "daily_bias": daily_bias,
            "weekly_bias": weekly_bias,
            "monthly_bias": monthly_bias,
            "yearly_bias": yearly_bias,
            "pivot_votes": len(pivot_votes),
            "ema1_200": None if math.isnan(ema1_200) else round(ema1_200, 2),
            "ema1_50": None if math.isnan(ema1_50) else round(ema1_50, 2),
            "ema5_50": None if math.isnan(ema5_50) else round(ema5_50, 2),
            "volume_ratio": None if math.isnan(volume_ratio) else round(volume_ratio, 2),
        },
    }
