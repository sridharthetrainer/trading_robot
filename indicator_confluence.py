"""
indicator_confluence.py — grouped all-indicator confluence scoring.

This is intentionally not a raw indicator vote counter. Correlated indicators
are grouped first (trend, momentum, volume, volatility, structure, options/OI,
context), then the groups are blended into one directional score.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import math
import numpy as np
import pandas as pd


DEFAULT_WEIGHTS = {
    "trend": 0.18,
    "momentum": 0.13,
    "volume": 0.15,
    "structure": 0.15,
    "breakout": 0.14,
    "volatility": 0.10,
    "options_oi": 0.15,
    "context": 0.05,
}


def _col(df: pd.DataFrame, *names: str):
    lookup = {str(c).lower(): c for c in df.columns}
    for name in names:
        key = name.lower()
        if key in lookup:
            return lookup[key]
    return None


def _last(df: pd.DataFrame, *names: str, default: float = np.nan) -> float:
    c = _col(df, *names)
    if c is None or len(df) == 0:
        return default
    try:
        v = df[c].iloc[-1]
        return float(v) if not pd.isna(v) else default
    except Exception:
        return default


def _clip_group(v: float) -> float:
    return round(float(max(-2.0, min(2.0, v))), 3)


def _dir_mult(direction: str) -> int:
    return 1 if str(direction).upper() in ("BUY", "LONG", "CALL") else -1


def _directional(value: float, direction: str) -> float:
    """Positive value means bullish raw evidence; return aligned-to-trade score."""
    return float(value) * _dir_mult(direction)


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "bullish", "bearish", "confirmed")
    return bool(v)


def _meta_float(meta: Dict[str, Any], *names: str, default: float = np.nan) -> float:
    for name in names:
        try:
            v = meta.get(name)
            if v is not None and v != "":
                return float(v)
        except Exception:
            continue
    return default


def _option_float(option_data: Dict[str, Any] | None, *names: str, default: float = np.nan) -> float:
    if not option_data:
        return default
    for name in names:
        try:
            v = option_data.get(name)
            if v is not None and v != "":
                return float(v)
        except Exception:
            continue
    return default


def _side_from_text(value: Any) -> str | None:
    text = str(value or "").upper()
    bullish_tokens = ("BUY", "LONG", "CALL", "BULL", "SUPPORT", "DEMAND", "FAILED_BREAKDOWN")
    bearish_tokens = ("SELL", "SHORT", "PUT", "BEAR", "RESISTANCE", "SUPPLY", "FAILED_BREAKOUT")
    if any(t in text for t in bullish_tokens):
        return "BUY"
    if any(t in text for t in bearish_tokens):
        return "SELL"
    return None


def _aligned_side_score(side: str | None, direction: str, magnitude: float) -> float:
    if side is None:
        return 0.0
    return magnitude if side == ("BUY" if _dir_mult(direction) > 0 else "SELL") else -magnitude


def _score_trend(df: pd.DataFrame, direction: str) -> Tuple[float, Dict[str, Any]]:
    price = _last(df, "close", default=np.nan)
    ema_fast = _last(df, "ema_fast", "ema_9", default=np.nan)
    ema_slow = _last(df, "ema_slow", "ema_21", "ema_50", default=np.nan)
    ema_trend = _last(df, "ema_trend", "ema_200", default=np.nan)
    adx = _last(df, "adx", default=np.nan)
    plus_di = _last(df, "plus_di", default=np.nan)
    minus_di = _last(df, "minus_di", default=np.nan)
    st_dir = _last(df, "supertrend_dir", default=0.0)

    raw = 0.0
    reasons = []
    if all(not math.isnan(x) for x in (price, ema_fast, ema_slow)):
        if price > ema_fast > ema_slow:
            raw += 0.7; reasons.append("ema_stack_bull")
        elif price < ema_fast < ema_slow:
            raw -= 0.7; reasons.append("ema_stack_bear")
    if all(not math.isnan(x) for x in (price, ema_trend)):
        if price > ema_trend:
            raw += 0.35; reasons.append("above_ema_trend")
        elif price < ema_trend:
            raw -= 0.35; reasons.append("below_ema_trend")
    if st_dir in (1, -1):
        raw += 0.45 * st_dir
        reasons.append("supertrend_bull" if st_dir == 1 else "supertrend_bear")
    if not math.isnan(adx):
        strength = 0.45 if adx >= 25 else 0.20 if adx >= 18 else -0.35
        if not math.isnan(plus_di) and not math.isnan(minus_di):
            raw += strength if plus_di > minus_di else -strength
            reasons.append("di_bull" if plus_di > minus_di else "di_bear")
        else:
            reasons.append("adx_strength" if strength > 0 else "adx_weak")
    return _clip_group(_directional(raw, direction)), {
        "raw": round(raw, 3), "reasons": reasons[:6],
        "adx": None if math.isnan(adx) else round(adx, 2),
    }


def _score_momentum(df: pd.DataFrame, direction: str) -> Tuple[float, Dict[str, Any]]:
    rsi = _last(df, "rsi", "rsi_14", default=np.nan)
    crsi = _last(df, "connors_rsi", default=np.nan)
    macd = _last(df, "macd_hist", default=np.nan)
    close = _last(df, "close", default=np.nan)
    raw = 0.0
    reasons = []
    if not math.isnan(rsi):
        if 52 <= rsi <= 70:
            raw += 0.45; reasons.append("rsi_bull_zone")
        elif 30 <= rsi <= 48:
            raw -= 0.45; reasons.append("rsi_bear_zone")
        elif rsi > 78:
            raw -= 0.25; reasons.append("rsi_overbought")
        elif rsi < 22:
            raw += 0.25; reasons.append("rsi_oversold")
    if not math.isnan(crsi):
        if crsi < 10:
            raw += 0.25; reasons.append("crsi_oversold")
        elif crsi > 90:
            raw -= 0.25; reasons.append("crsi_overbought")
    if not math.isnan(macd):
        if macd > 0:
            raw += 0.45; reasons.append("macd_positive")
        elif macd < 0:
            raw -= 0.45; reasons.append("macd_negative")
    if len(df) >= 6 and not math.isnan(close):
        c = _col(df, "close")
        prev = float(df[c].iloc[-6])
        if prev:
            roc = (close - prev) / abs(prev)
            if roc > 0.002:
                raw += 0.35; reasons.append("roc_bull")
            elif roc < -0.002:
                raw -= 0.35; reasons.append("roc_bear")
    return _clip_group(_directional(raw, direction)), {"raw": round(raw, 3), "reasons": reasons[:6]}


def _score_volume(df: pd.DataFrame, direction: str, meta: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    vr = _last(df, "volume_ratio", "vol_ratio", default=np.nan)
    if math.isnan(vr):
        try:
            vr = float(meta.get("volume_ratio", np.nan))
        except Exception:
            vr = np.nan
    raw = 0.0
    reasons = []
    quality = str(meta.get("volume_data_quality", "")).lower()
    if quality and quality not in ("ok", "missing_index_volume"):
        raw -= 0.45; reasons.append(f"volume_{quality}")
    elif quality == "missing_index_volume":
        raw -= 0.15; reasons.append("index_volume_unavailable")
    if not math.isnan(vr):
        if vr >= 1.5:
            raw += 1.0; reasons.append("volume_expansion")
        elif vr >= 1.1:
            raw += 0.45; reasons.append("volume_above_avg")
        elif vr < 0.55:
            raw -= 0.7; reasons.append("thin_volume")
    if _truthy(meta.get("volume_confirmation")):
        raw += 0.5; reasons.append("pattern_volume_confirmed")
    if _truthy(meta.get("breakout_confirmed") or meta.get("level_breakout_confirmed")):
        if not math.isnan(vr) and vr >= 1.2:
            raw += 0.35; reasons.append("breakout_volume_ok")
        elif not math.isnan(vr) and vr < 0.8:
            raw -= 0.45; reasons.append("breakout_thin_volume")
    if _truthy(meta.get("failed_breakout") or meta.get("failed_breakdown") or meta.get("breakout_rejection")):
        if not math.isnan(vr) and vr < 0.8:
            raw -= 0.35; reasons.append("rejection_weak_volume")
    return _clip_group(raw), {"raw": round(raw, 3), "reasons": reasons[:5], "volume_ratio": None if math.isnan(vr) else round(vr, 2)}


def _score_structure(df: pd.DataFrame, direction: str, meta: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    directional_raw = 0.0
    quality_raw = 0.0
    reasons = []
    ctx = meta.get("structure_context") if isinstance(meta.get("structure_context"), dict) else {}
    wanted = "BULLISH" if _dir_mult(direction) > 0 else "BEARISH"
    if ctx:
        s_dir = str(ctx.get("structure_direction") or "").upper()
        bos = str(ctx.get("last_bos") or "").upper()
        choch = str(ctx.get("choch_direction") or "").upper()
        label = str(ctx.get("structure_label") or "")
        if s_dir == wanted:
            directional_raw += 0.75; reasons.append(f"structure:{label or s_dir.lower()}")
        elif s_dir and s_dir != "NEUTRAL":
            directional_raw -= 0.75; reasons.append(f"structure_against:{label or s_dir.lower()}")
        if bos == wanted:
            directional_raw += 0.35; reasons.append("bos_aligned")
        elif bos and bos != wanted:
            directional_raw -= 0.35; reasons.append("bos_against")
        if choch == wanted:
            directional_raw += 0.45; reasons.append("choch_aligned")
        elif choch and choch != wanted:
            directional_raw -= 0.45; reasons.append("choch_against")
        if _truthy(ctx.get("retest_confirmed")):
            quality_raw += 0.25; reasons.append("structure_retest")
        if _truthy(ctx.get("htf_aligned")):
            quality_raw += 0.25; reasons.append("htf_structure")
        elif _truthy(ctx.get("htf_opposes")):
            quality_raw -= 0.25; reasons.append("htf_structure_against")

    price = _last(df, "close", default=np.nan)
    vwap = _last(df, "vwap", default=np.nan)
    if not math.isnan(price) and not math.isnan(vwap):
        if price > vwap:
            directional_raw += 0.35; reasons.append("above_vwap")
        elif price < vwap:
            directional_raw -= 0.35; reasons.append("below_vwap")
    if meta.get("pattern"):
        pdir = None
        patterns = meta.get("all_patterns") if isinstance(meta.get("all_patterns"), dict) else {}
        selected = str(meta.get("pattern", "")).lower()
        for key, pdata in patterns.items():
            if str(key).lower() == selected or str(pdata.get("pattern", "")).lower() == selected:
                pdir = pdata.get("direction")
                break
        if not pdir:
            pdir = _side_from_text(meta.get("pattern"))
        if pdir:
            directional_raw += 0.9 if str(pdir).upper() in ("BUY", "LONG", "CALL") else -0.9
        else:
            quality_raw += 0.5
        reasons.append(f"pattern:{meta.get('pattern')}")
    if _truthy(meta.get("breakout_confirmed")):
        quality_raw += 0.45; reasons.append("breakout_confirmed")
    if float(meta.get("risk_reward") or 0) >= 1.5:
        quality_raw += 0.35; reasons.append("rr_ok")
    sr_mod = float(meta.get("sr_level_mod") or 0)
    if sr_mod:
        quality_raw += max(-0.6, min(0.6, sr_mod * 0.5)); reasons.append("sr_level")
    pivot_mod = float(meta.get("pivot_boss_mod") or 0)
    if pivot_mod:
        quality_raw += max(-0.5, min(0.5, pivot_mod)); reasons.append("pivot_boss")
    mtf_mod = float(meta.get("mtf_pivot_mod") or 0)
    if mtf_mod:
        quality_raw += max(-0.6, min(0.6, mtf_mod * 0.5)); reasons.append("mtf_pivot")
    if meta.get("ochao_levels"):
        quality_raw += 0.15; reasons.append("ochao_levels")
    aligned = _directional(directional_raw, direction) + quality_raw
    return _clip_group(aligned), {
        "raw": round(directional_raw + quality_raw, 3),
        "directional_raw": round(directional_raw, 3),
        "quality_raw": round(quality_raw, 3),
        "reasons": reasons[:7],
    }


def _score_breakout_location(df: pd.DataFrame, direction: str, meta: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    raw = 0.0
    reasons = []
    price = _last(df, "close", default=np.nan)
    open_ = _last(df, "open", default=np.nan)
    high = _last(df, "high", default=np.nan)
    low = _last(df, "low", default=np.nan)

    if _truthy(meta.get("breakout_confirmed") or meta.get("pattern_breakout_confirmed")):
        raw += 0.65; reasons.append("pattern_breakout_confirmed")
    elif _truthy(meta.get("pattern_breakout")):
        raw += 0.35; reasons.append("pattern_breakout")

    if _truthy(meta.get("level_breakout_confirmed")):
        raw += 0.70; reasons.append("level_breakout_confirmed")
    elif _truthy(meta.get("level_breakout")):
        raw += 0.40; reasons.append("level_breakout")

    if _truthy(meta.get("breakout_retest") or meta.get("retest_confirmed")):
        raw += 0.35; reasons.append("breakout_retest")

    rejection_side = (
        _side_from_text(meta.get("rejection_direction"))
        or _side_from_text(meta.get("level_rejection"))
        or _side_from_text(meta.get("breakout_rejection"))
    )
    if rejection_side:
        raw += _aligned_side_score(rejection_side, direction, 0.65)
        reasons.append(f"level_rejection:{rejection_side.lower()}")

    if _truthy(meta.get("failed_breakout")):
        raw += _aligned_side_score("SELL", direction, 0.70)
        reasons.append("failed_breakout")
    if _truthy(meta.get("failed_breakdown")):
        raw += _aligned_side_score("BUY", direction, 0.70)
        reasons.append("failed_breakdown")

    pattern_name = str(meta.get("pattern", "")).lower()
    if "failed_breakout" in pattern_name:
        raw += _aligned_side_score("SELL", direction, 0.55); reasons.append("pattern_failed_breakout")
    elif "failed_breakdown" in pattern_name:
        raw += _aligned_side_score("BUY", direction, 0.55); reasons.append("pattern_failed_breakdown")
    elif "breakout_retest" in pattern_name:
        raw += 0.35; reasons.append("pattern_breakout_retest")

    if all(not math.isnan(x) for x in (price, open_, high, low)) and high > low:
        body = abs(price - open_)
        upper_wick = high - max(price, open_)
        lower_wick = min(price, open_) - low
        wick_floor = max(body * 1.2, (high - low) * 0.25)
        if _dir_mult(direction) > 0 and lower_wick >= wick_floor and price >= open_:
            raw += 0.30; reasons.append("support_wick_rejection")
        elif _dir_mult(direction) < 0 and upper_wick >= wick_floor and price <= open_:
            raw += 0.30; reasons.append("resistance_wick_rejection")

    return _clip_group(raw), {"raw": round(raw, 3), "reasons": reasons[:7]}


def _score_volatility(df: pd.DataFrame, direction: str, meta: Dict[str, Any], option_data: Dict[str, Any] | None) -> Tuple[float, Dict[str, Any]]:
    er = _last(df, "efficiency_ratio", default=np.nan)
    chop = _last(df, "choppiness_index", default=np.nan)
    nr7 = _last(df, "nr7", default=0.0)
    nr4 = _last(df, "nr4", default=0.0)
    vix = _option_float(option_data, "vix", "india_vix", "indiaVix", default=np.nan)
    if math.isnan(vix):
        vix = _meta_float(meta, "vix", "india_vix", default=np.nan)
    ivp = _option_float(option_data, "iv_percentile", "ivp", default=np.nan)
    if math.isnan(ivp):
        ivp = _meta_float(meta, "iv_percentile", "ivp", default=np.nan)
    raw = 0.0
    reasons = []
    if nr7:
        raw += 0.55; reasons.append("nr7_compression")
    elif nr4:
        raw += 0.30; reasons.append("nr4_compression")
    if not math.isnan(er):
        if er >= 0.45:
            raw += 0.45; reasons.append("efficient_move")
        elif er < 0.20:
            raw -= 0.45; reasons.append("inefficient_chop")
    if not math.isnan(chop):
        if chop > 60:
            raw -= 0.35; reasons.append("high_chop")
        elif chop < 38:
            raw += 0.25; reasons.append("low_chop")
    if not math.isnan(vix):
        if 11 <= vix <= 20:
            raw += 0.25; reasons.append("vix_tradeable")
        elif vix > 24:
            raw -= 0.55; reasons.append("vix_too_hot")
        elif vix < 10:
            raw -= 0.20; reasons.append("vix_too_compressed")
    if not math.isnan(ivp):
        if 25 <= ivp <= 70:
            raw += 0.20; reasons.append("ivp_tradeable")
        elif ivp > 85:
            raw -= 0.35; reasons.append("ivp_expensive")
    return _clip_group(raw), {
        "raw": round(raw, 3),
        "reasons": reasons[:7],
        "vix": None if math.isnan(vix) else round(vix, 2),
        "iv_percentile": None if math.isnan(ivp) else round(ivp, 2),
    }


def _score_options_oi(direction: str, meta: Dict[str, Any], option_data: Dict[str, Any] | None) -> Tuple[float, Dict[str, Any]]:
    raw = 0.0
    reasons = []
    oi_dir = str(meta.get("oi_direction", "")).upper()
    if oi_dir and oi_dir != "NEUTRAL":
        if oi_dir == str(direction).upper():
            raw += 0.8; reasons.append("oi_confirms")
        else:
            raw -= 0.8; reasons.append("oi_contradicts")
    gex = float(meta.get("gex_modifier") or 0)
    if gex:
        raw += max(-0.6, min(0.6, gex)); reasons.append("gex")
    skew = float(meta.get("skew_velocity_mod") or 0)
    if skew:
        raw += max(-0.5, min(0.5, skew)); reasons.append("skew_velocity")
    if option_data:
        pcr = (
            option_data.get("pcr")
            or option_data.get("put_call_ratio")
            or option_data.get("pcr_oi")
            or option_data.get("pcr_change_oi")
        )
        try:
            pcr = float(pcr)
            if direction == "BUY" and pcr > 1.05:
                raw += 0.25; reasons.append("pcr_support")
            elif direction == "SELL" and pcr < 0.95:
                raw += 0.25; reasons.append("pcr_resistance")
        except Exception:
            pass
    iv = _option_float(option_data, "iv", "implied_volatility", "atm_iv", default=np.nan)
    if math.isnan(iv):
        iv = _meta_float(meta, "iv", "implied_volatility", "atm_iv", default=np.nan)
    if not math.isnan(iv):
        if 8 <= iv <= 24:
            raw += 0.15; reasons.append("iv_reasonable")
        elif iv > 35:
            raw -= 0.25; reasons.append("iv_expensive")
    return _clip_group(raw), {
        "raw": round(raw, 3),
        "reasons": reasons[:6],
        "iv": None if math.isnan(iv) else round(iv, 2),
    }


def _score_context(direction: str, meta: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    raw = 0.0
    reasons = []
    for key, cap in (
        ("bse_announcement", 0.4),
        ("fii_futures_signal", 0.4),
        ("promoter_signal", 0.5),
        ("whale_mod", 0.5),
    ):
        try:
            val = float(meta.get(key) or 0)
        except Exception:
            val = 0.0
        if val:
            raw += max(-cap, min(cap, val))
            reasons.append(key)
    global_side = _side_from_text(meta.get("global_bias") or meta.get("cross_asset_bias"))
    if global_side:
        change = abs(_meta_float(meta, "global_change_pct", default=0.0))
        magnitude = 0.45 if change >= 0.005 else 0.25
        raw += _aligned_side_score(global_side, direction, magnitude)
        reasons.append(f"global:{global_side.lower()}")
    news = _meta_float(meta, "news_score", default=0.0)
    if news:
        raw += max(-0.35, min(0.35, _directional(news, direction)))
        reasons.append("news_score")
    mtf_mod = _meta_float(meta, "mtf_indicator_mod", default=0.0)
    if mtf_mod:
        raw += max(-0.55, min(0.55, mtf_mod))
        reasons.append("mtf_indicators")
    mtf_ctx = meta.get("mtf_indicator_context") or meta.get("mtf_context") or {}
    if isinstance(mtf_ctx, dict):
        mtf_side = _side_from_text(mtf_ctx.get("bias"))
        if mtf_side:
            raw += _aligned_side_score(mtf_side, direction, 0.25)
            reasons.append(f"mtf:{mtf_side.lower()}")
    data_conf = _meta_float(meta, "data_confidence", default=1.0)
    if data_conf < 0.70:
        raw -= 0.30; reasons.append("low_data_confidence")
    elif data_conf < 0.90:
        raw -= 0.12; reasons.append("reduced_data_confidence")
    gap = str(meta.get("gap_strategy_bias", "")).upper()
    if gap:
        reasons.append(f"gap:{gap}")
    return _clip_group(raw), {"raw": round(raw, 3), "reasons": reasons[:6]}


def calculate_indicator_confluence(
    df: pd.DataFrame,
    *,
    direction: str,
    strategy: str = "",
    signal_meta: Dict[str, Any] | None = None,
    option_data: Dict[str, Any] | None = None,
    weights: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    """Return grouped confluence score and auditable breakdown."""
    meta = signal_meta or {}
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    total_w = sum(abs(v) for v in w.values()) or 1.0
    w = {k: v / total_w for k, v in w.items()}

    groups = {}
    groups["trend"] = _score_trend(df, direction)
    groups["momentum"] = _score_momentum(df, direction)
    groups["volume"] = _score_volume(df, direction, meta)
    groups["structure"] = _score_structure(df, direction, meta)
    groups["breakout"] = _score_breakout_location(df, direction, meta)
    groups["volatility"] = _score_volatility(df, direction, meta, option_data)
    groups["options_oi"] = _score_options_oi(direction, meta, option_data)
    groups["context"] = _score_context(direction, meta)

    group_scores = {k: v[0] for k, v in groups.items()}
    breakdown = {k: v[1] for k, v in groups.items()}
    directional_score = sum(group_scores[k] * w.get(k, 0.0) for k in group_scores)
    normalized = (directional_score + 2.0) / 4.0 * 10.0
    aligned_groups = sum(1 for v in group_scores.values() if v > 0.15)
    opposing_groups = sum(1 for v in group_scores.values() if v < -0.15)
    return {
        "score": round(float(max(0.0, min(10.0, normalized))), 3),
        "directional_score": round(float(directional_score), 3),
        "score_modifier": round(float(directional_score * 1.2), 3),
        "group_scores": group_scores,
        "breakdown": breakdown,
        "weights": {k: round(v, 3) for k, v in w.items()},
        "aligned_groups": aligned_groups,
        "opposing_groups": opposing_groups,
        "strategy": strategy,
    }
