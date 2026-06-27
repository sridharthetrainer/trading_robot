#!/usr/bin/env python3
"""
macro_global_profit_engine.py

Global + commodity + sector sentiment engine for NIFTY option selection.

This module does not place trades. It returns a permission/filter decision that
the signal engine can use to improve option-buying quality.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_PATH = "signal_log.db"
CACHE_PATH = Path("macro_global_sentiment_cache.json")
CACHE_TTL_SEC = 300
PROFIT_QUALITY_ALERT_THRESHOLD = 75


ASSET_GROUPS: Dict[str, Dict[str, Any]] = {
    "gift_nifty": {
        "weight": 25,
        "effect": 1,
        "scale": 1.0,
        "assets": ["GIFT", "SGXNIFTY", "NIFTY"],
    },
    "us_futures": {
        "weight": 15,
        "effect": 1,
        "scale": 1.2,
        "assets": ["DOW_FUT", "NASDAQ_FUT", "SP500_FUT", "SP500", "NASDAQ", "DOW"],
    },
    "asia": {
        "weight": 12,
        "effect": 1,
        "scale": 1.5,
        "assets": ["NIKKEI", "HANGSENG", "SHANGHAI", "TAIWAN", "KOSPI"],
    },
    "europe": {
        "weight": 10,
        "effect": 1,
        "scale": 1.5,
        "assets": ["DAX", "FTSE", "CAC"],
    },
    "crude": {
        "weight": 10,
        "effect": -1,
        "scale": 2.5,
        "assets": ["BRENT", "WTI"],
    },
    "currency": {
        "weight": 10,
        "effect": -1,
        "scale": 0.8,
        "assets": ["USDINR", "DXY"],
    },
    "gold_vix": {
        "weight": 8,
        "effect": -1,
        "scale": 2.0,
        "assets": ["GOLD", "INDIAVIX", "USVIX"],
    },
    "bond_yield": {
        "weight": 5,
        "effect": -1,
        "scale": 3.0,
        "assets": ["US10Y"],
    },
    "copper_growth": {
        "weight": 5,
        "effect": 1,
        "scale": 2.0,
        "assets": ["COPPER"],
    },
}

YAHOO_TICKERS: Dict[str, Tuple[str, str]] = {
    "DOW_FUT": ("YM=F", "Dow futures"),
    "NASDAQ_FUT": ("NQ=F", "Nasdaq futures"),
    "SP500_FUT": ("ES=F", "S&P 500 futures"),
    "DOW": ("^DJI", "Dow Jones"),
    "NASDAQ": ("^IXIC", "Nasdaq"),
    "NIFTY": ("^NSEI", "NIFTY"),
    "BANKNIFTY": ("^NSEBANK", "BANKNIFTY"),
    "FINNIFTY": ("NIFTY_FIN_SERVICE.NS", "FINNIFTY"),
    "HANGSENG": ("^HSI", "Hang Seng"),
    "SHANGHAI": ("000001.SS", "Shanghai"),
    "TAIWAN": ("^TWII", "Taiwan Weighted"),
    "KOSPI": ("^KS11", "Kospi"),
    "DAX": ("^GDAXI", "DAX"),
    "FTSE": ("^FTSE", "FTSE"),
    "CAC": ("^FCHI", "CAC"),
    "WTI": ("CL=F", "WTI crude"),
    "NATGAS": ("NG=F", "Natural gas"),
    "SILVER": ("SI=F", "Silver"),
    "COPPER": ("HG=F", "Copper"),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _pct(curr: float, prev: float) -> float:
    return ((curr - prev) / prev * 100.0) if prev else 0.0


def _component_score(change_pct: float, *, effect: int = 1, scale: float = 1.0) -> float:
    scale = max(0.1, abs(float(scale or 1.0)))
    return max(-100.0, min(100.0, effect * (float(change_pct) / scale) * 100.0))


def _avg(values: Iterable[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _asset_row(price: float, prev: float, label: str) -> Dict[str, Any]:
    return {
        "price": round(price, 4),
        "prev": round(prev, 4),
        "change_pct": round(_pct(price, prev), 3),
        "label": label,
    }


def _fetch_yahoo_asset(key: str, ticker: str, label: str) -> Optional[Dict[str, Any]]:
    try:
        from cross_asset import _fetch_yahoo_price

        curr, prev = _fetch_yahoo_price(ticker)
        if curr and curr > 0:
            return _asset_row(float(curr), float(prev or curr), label)
    except Exception as exc:
        logger.debug("macro fetch %s failed: %s", key, exc)
    return None


def fetch_macro_assets(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """Fetch real cross-market inputs with cache-aware reuse of existing modules."""
    assets: Dict[str, Dict[str, Any]] = {}
    try:
        from cross_asset import get_cross_asset_data

        assets.update(get_cross_asset_data(force=force) or {})
    except Exception as exc:
        logger.debug("cross_asset unavailable: %s", exc)

    try:
        from global_market_filter import get_global_filter

        gb = get_global_filter().get_global_bias()
        chg_pct = _safe_float(gb.get("change_pct")) * 100.0
        assets["GIFT"] = {
            "price": _safe_float(gb.get("price")),
            "prev": _safe_float(gb.get("prev")),
            "change_pct": round(chg_pct, 3),
            "label": "GIFT NIFTY",
        }
    except Exception as exc:
        logger.debug("GIFT filter unavailable: %s", exc)

    for key, (ticker, label) in YAHOO_TICKERS.items():
        if key not in assets or not _safe_float(assets.get(key, {}).get("price")):
            row = _fetch_yahoo_asset(key, ticker, label)
            if row:
                assets[key] = row

    return assets


def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(CACHE_PATH.read_text())
        if time.time() - float(raw.get("ts", 0)) <= CACHE_TTL_SEC:
            return raw.get("snapshot")
    except Exception:
        return None
    return None


def _write_cache(snapshot: Dict[str, Any]) -> None:
    try:
        CACHE_PATH.write_text(json.dumps({"ts": time.time(), "snapshot": snapshot}, indent=2))
    except Exception as exc:
        logger.debug("macro cache write failed: %s", exc)


def _bias_label(score: float, india_vix: float = 0.0, us_vix: float = 0.0) -> str:
    if max(india_vix, us_vix) >= 28:
        return "HIGH_RISK"
    if -20 <= score <= 20:
        return "NO_TRADE_ZONE"
    if score > 35:
        return "BULLISH"
    if score < -35:
        return "BEARISH"
    return "NEUTRAL"


def _group_change(assets: Dict[str, Dict[str, Any]], group: Dict[str, Any]) -> float:
    return _avg(
        _safe_float(assets.get(asset, {}).get("change_pct"))
        for asset in group.get("assets", [])
        if asset in assets
    )


def _score_groups(assets: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, float], float]:
    scores: Dict[str, float] = {}
    weighted = 0.0
    total_weight = 0.0
    for name, group in ASSET_GROUPS.items():
        change = _group_change(assets, group)
        score = _component_score(
            change,
            effect=int(group.get("effect", 1)),
            scale=float(group.get("scale", 1.0)),
        )
        scores[name] = round(score, 2)
        if any(asset in assets for asset in group.get("assets", [])):
            w = float(group.get("weight", 0.0))
            weighted += score * w
            total_weight += w
    return scores, round(weighted / total_weight, 2) if total_weight else 0.0


def predict_gap(assets: Dict[str, Dict[str, Any]], global_score: float) -> Dict[str, Any]:
    gift = _safe_float(assets.get("GIFT", assets.get("SGXNIFTY", {})).get("change_pct"))
    expected_points = round((gift * 220.0) + (global_score * 0.8), 1)
    up_raw = max(0.0, global_score) / 100.0 + max(0.0, gift) / 1.5
    down_raw = max(0.0, -global_score) / 100.0 + max(0.0, -gift) / 1.5
    flat_raw = max(0.15, 1.0 - abs(global_score) / 80.0 - abs(gift) / 1.2)
    total = up_raw + down_raw + flat_raw
    probs = {
        "gap_up_probability": round(100 * up_raw / total, 1) if total else 33.3,
        "gap_down_probability": round(100 * down_raw / total, 1) if total else 33.3,
        "flat_probability": round(100 * flat_raw / total, 1) if total else 33.3,
    }
    if probs["gap_up_probability"] >= max(probs["gap_down_probability"], probs["flat_probability"]):
        pred = "GAP_UP"
        p = probs["gap_up_probability"]
    elif probs["gap_down_probability"] >= probs["flat_probability"]:
        pred = "GAP_DOWN"
        p = probs["gap_down_probability"]
    else:
        pred = "FLAT"
        p = probs["flat_probability"]
    return {
        "prediction": pred,
        "probabilities": probs,
        "probability": p,
        "expected_gap_points": expected_points,
        "confidence": "HIGH" if p >= 60 else "MEDIUM" if p >= 45 else "LOW",
    }


def sector_impact_map(changes: Dict[str, float]) -> Dict[str, Any]:
    pos: List[str] = []
    neg: List[str] = []
    why: List[str] = []
    crude = max(_safe_float(changes.get("BRENT")), _safe_float(changes.get("WTI")))
    usdinr = _safe_float(changes.get("USDINR"))
    dxy = _safe_float(changes.get("DXY"))
    nasdaq = _safe_float(changes.get("NASDAQ_FUT", changes.get("NASDAQ")))
    risk = max(_safe_float(changes.get("INDIAVIX")), _safe_float(changes.get("USVIX")))

    if crude >= 1.0:
        neg += ["NIFTY", "Airlines", "Paints", "OMCs"]
        pos += ["Oil & Gas"]
        why.append(f"Crude +{crude:.2f}%")
    elif crude <= -1.0:
        pos += ["Airlines", "Paints", "OMCs"]
        why.append(f"Crude {crude:.2f}%")
    if usdinr >= 0.25 or dxy >= 0.35:
        neg += ["Banks", "Importers"]
        pos += ["IT", "Pharma"]
        why.append(f"USD pressure USDINR={usdinr:+.2f}% DXY={dxy:+.2f}%")
    elif usdinr <= -0.25:
        pos += ["Banks", "Importers"]
        neg += ["IT", "Pharma"]
        why.append(f"INR strength USDINR={usdinr:+.2f}%")
    if nasdaq >= 0.6:
        pos += ["IT"]
        why.append(f"Nasdaq strong {nasdaq:+.2f}%")
    elif nasdaq <= -0.6:
        neg += ["IT"]
        why.append(f"Nasdaq weak {nasdaq:+.2f}%")
    if risk >= 24:
        neg += ["Aggressive CE buying"]
        why.append(f"risk-off VIX={risk:.1f}")

    return {
        "positive_sectors": list(dict.fromkeys(pos)),
        "negative_sectors": list(dict.fromkeys(neg)),
        "reason": "; ".join(why) if why else "No strong macro sector skew",
    }


def detect_market_regimes(
    assets: Dict[str, Dict[str, Any]],
    global_score: float,
    technical_context: Optional[Dict[str, Any]] = None,
) -> List[str]:
    ctx = technical_context or {}
    regimes: List[str] = []
    changes = {k: _safe_float(v.get("change_pct")) for k, v in assets.items() if isinstance(v, dict)}
    vix = max(_safe_float(assets.get("INDIAVIX", {}).get("price")), _safe_float(assets.get("USVIX", {}).get("price")))
    crude = max(_safe_float(changes.get("BRENT")), _safe_float(changes.get("WTI")))
    usd_pressure = _safe_float(changes.get("USDINR")) + _safe_float(changes.get("DXY"))

    if global_score >= 35:
        regimes.append("GLOBAL_RISK_ON")
    if global_score <= -35 or vix >= 24:
        regimes.append("GLOBAL_RISK_OFF")
    if _safe_float(changes.get("GIFT")) > 0.25 and global_score > 20:
        regimes.append("INDIA_POSITIVE")
    if _safe_float(changes.get("GIFT")) < -0.25 and global_score < -20:
        regimes.append("INDIA_WEAK")
    if crude >= 1.0:
        regimes.append("COMMODITY_PRESSURE")
    if usd_pressure >= 0.6:
        regimes.append("USD_PRESSURE")
    if vix >= 20 or abs(global_score) >= 55:
        regimes.append("VOLATILITY_EXPANSION")
    if abs(global_score) <= 20 or str(ctx.get("cpr_bias", "")).upper() in {"INSIDE_CPR", "SIDEWAYS"}:
        regimes.append("SIDEWAYS_DECAY")
    if str(ctx.get("structure_label", "")).upper() in {"BREAKOUT", "BOS"} or ctx.get("camarilla_breakout"):
        regimes.append("BREAKOUT_DAY")
    if str(ctx.get("structure_label", "")).upper() in {"REVERSAL", "CHOCH"}:
        regimes.append("REVERSAL_DAY")
    return list(dict.fromkeys(regimes)) or ["NEUTRAL"]


def detect_trap(
    assets: Dict[str, Dict[str, Any]],
    technical_context: Optional[Dict[str, Any]] = None,
    option_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ctx = technical_context or {}
    opt = option_data or {}
    gift = _safe_float(assets.get("GIFT", assets.get("SGXNIFTY", {})).get("change_pct"))
    cpr = str(ctx.get("cpr_bias", ctx.get("cpr_position", ""))).upper()
    vwap = str(ctx.get("vwap_position", ctx.get("price_vs_vwap", ""))).upper()
    volume_ratio = _safe_float(ctx.get("volume_ratio", opt.get("volume_ratio")), 1.0)
    ce_decay = bool(opt.get("ce_premium_decays") or opt.get("premium_decay_ce"))
    pe_decay = bool(opt.get("pe_premium_decays") or opt.get("premium_decay_pe"))
    failed_resistance = bool(ctx.get("failed_resistance") or ctx.get("failed_camarilla_resistance"))
    reclaimed = bool(ctx.get("reclaimed_cpr") or ctx.get("reclaimed_vwap"))

    if gift > 0.2 and (failed_resistance or "BELOW" in cpr) and volume_ratio < 0.9 and ce_decay:
        return {
            "trap": "BULL_TRAP",
            "warning": "Bull trap: GIFT positive but NIFTY failed CPR/Camarilla with weak volume and CE decay.",
        }
    if gift < -0.2 and (reclaimed or "ABOVE" in cpr or "ABOVE" in vwap) and pe_decay:
        return {
            "trap": "BEAR_TRAP",
            "warning": "Bear trap: GIFT negative but NIFTY reclaimed CPR/VWAP while PE premium decays.",
        }
    return {"trap": "", "warning": ""}


def intraday_bias(
    global_score: float,
    gap: Dict[str, Any],
    regimes: List[str],
    trap: Dict[str, Any],
    technical_context: Optional[Dict[str, Any]] = None,
) -> str:
    ctx = technical_context or {}
    cpr = str(ctx.get("cpr_bias", ctx.get("cpr_position", ""))).upper()
    vwap = str(ctx.get("vwap_position", ctx.get("price_vs_vwap", ""))).upper()
    if trap.get("trap"):
        return "Trap Day"
    if "VOLATILITY_EXPANSION" in regimes:
        return "High Volatility Day"
    if "SIDEWAYS_DECAY" in regimes and abs(global_score) <= 20:
        return "Range-bound Premium Decay Day"
    if global_score >= 55 and ("ABOVE" in cpr or "ABOVE" in vwap):
        return "Strong Bullish Day"
    if global_score >= 30:
        return "Bullish Pullback Buy Day"
    if global_score <= -55 and ("BELOW" in cpr or "BELOW" in vwap):
        return "Bearish Day"
    if global_score <= -30:
        return "Sell-on-rise Day"
    if gap.get("prediction") == "GAP_UP" and global_score < 0:
        return "Trap Day"
    return "Range-bound Premium Decay Day"


def _direction_from_signal(signal: Optional[Dict[str, Any]]) -> str:
    sig = signal or {}
    raw = str(sig.get("allowed_direction") or sig.get("option_type") or sig.get("side") or sig.get("direction") or "").upper()
    if raw in {"CE", "CALL"}:
        return "CE"
    if raw in {"PE", "PUT"}:
        return "PE"
    if raw in {"BUY", "LONG", "BULLISH"}:
        return "CE"
    if raw in {"SELL", "SHORT", "BEARISH"}:
        return "PE"
    return "NONE"


def calculate_profit_quality_score(
    global_score: float,
    direction: str,
    technical_context: Optional[Dict[str, Any]] = None,
    option_data: Optional[Dict[str, Any]] = None,
    regimes: Optional[List[str]] = None,
) -> Tuple[int, List[str]]:
    ctx = technical_context or {}
    opt = option_data or {}
    regimes = regimes or []
    score = 35.0
    reasons: List[str] = []

    if direction == "CE" and global_score > 35:
        score += 16; reasons.append("CE aligned with bullish global score")
    elif direction == "PE" and global_score < -35:
        score += 16; reasons.append("PE aligned with bearish global score")
    elif direction in {"CE", "PE"} and abs(global_score) <= 20:
        score -= 18; reasons.append("weak/conflicting globals")
    elif direction in {"CE", "PE"}:
        score -= 10; reasons.append("direction fighting macro bias")

    cpr = str(ctx.get("cpr_bias", ctx.get("cpr_position", ""))).upper()
    vwap = str(ctx.get("vwap_position", ctx.get("price_vs_vwap", ""))).upper()
    if direction == "CE" and ("ABOVE" in cpr or "ABOVE" in vwap):
        score += 10; reasons.append("above CPR/VWAP")
    if direction == "PE" and ("BELOW" in cpr or "BELOW" in vwap):
        score += 10; reasons.append("below CPR/VWAP")
    if bool(ctx.get("camarilla_breakout")):
        score += 8; reasons.append("Camarilla breakout")
    if _safe_float(ctx.get("volume_ratio", opt.get("volume_ratio")), 1.0) >= 1.3:
        score += 10; reasons.append("volume spike")
    if str(ctx.get("oi_direction", opt.get("oi_direction", ""))).upper() in {direction, "BULLISH" if direction == "CE" else "BEARISH"}:
        score += 8; reasons.append("OI support")
    if _safe_float(opt.get("theta_risk", ctx.get("theta_risk")), 0.0) <= 0.35:
        score += 5; reasons.append("low theta risk")
    if _safe_float(ctx.get("rr", opt.get("rr")), 1.5) >= 1.5:
        score += 5; reasons.append("RR ok")
    if "SIDEWAYS_DECAY" in regimes:
        score -= 20; reasons.append("sideways premium decay risk")
    if "GLOBAL_RISK_OFF" in regimes and direction == "CE":
        score -= 12; reasons.append("risk-off headwind for CE")
    if _safe_float(opt.get("iv_percentile", ctx.get("iv_percentile")), 50) >= 80:
        score -= 8; reasons.append("IV inflated")

    return int(max(0, min(100, round(score)))), reasons


def _allowed_trade_type(
    global_score: float,
    assets: Dict[str, Dict[str, Any]],
    technical_context: Optional[Dict[str, Any]],
    option_data: Optional[Dict[str, Any]],
    regimes: List[str],
    profit_quality_score: int,
    trap: Dict[str, Any],
) -> Tuple[str, str, str]:
    ctx = technical_context or {}
    opt = option_data or {}
    gift = _safe_float(assets.get("GIFT", assets.get("SGXNIFTY", {})).get("change_pct"))
    cpr = str(ctx.get("cpr_bias", ctx.get("cpr_position", ""))).upper()
    vwap = str(ctx.get("vwap_position", ctx.get("price_vs_vwap", ""))).upper()
    volume_ratio = _safe_float(ctx.get("volume_ratio", opt.get("volume_ratio")), 1.0)
    ivp = _safe_float(opt.get("iv_percentile", ctx.get("iv_percentile")), 50.0)

    if trap.get("trap"):
        return "BLOCK", "NONE", trap.get("warning", "trap detected")
    if -20 <= global_score <= 20:
        return "BLOCK", "NONE", "macro score inside no-trade zone"
    if "SIDEWAYS_DECAY" in regimes:
        return "BLOCK", "NONE", "sideways premium decay risk"
    if profit_quality_score < PROFIT_QUALITY_ALERT_THRESHOLD:
        return "WAIT", "NONE", f"profit quality {profit_quality_score}<75"

    ce_ok = (
        global_score > 35
        and gift >= 0
        and ("ABOVE" in cpr or "ABOVE" in vwap or not cpr)
        and volume_ratio >= 1.0
        and ivp < 80
    )
    pe_ok = (
        global_score < -35
        and gift <= 0
        and ("BELOW" in cpr or "BELOW" in vwap or not cpr)
        and volume_ratio >= 1.0
    )
    if ce_ok and pe_ok:
        return "ALLOW", "BOTH", "macro and technical filters aligned"
    if ce_ok:
        return "ALLOW", "CE", "bullish macro + CPR/VWAP/volume aligned"
    if pe_ok:
        return "ALLOW", "PE", "bearish macro + CPR/VWAP/volume aligned"
    return "WAIT", "NONE", "macro context not fully aligned"


def get_macro_global_bias(
    *,
    force: bool = False,
    technical_context: Optional[Dict[str, Any]] = None,
    option_data: Optional[Dict[str, Any]] = None,
    signal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context_specific = bool(technical_context or option_data or signal)
    if not force and not context_specific:
        cached = _load_cache()
        if cached:
            return cached

    assets = fetch_macro_assets(force=force)
    group_scores, final_score = _score_groups(assets)
    changes = {k: _safe_float(v.get("change_pct")) for k, v in assets.items() if isinstance(v, dict)}
    india_vix = _safe_float(assets.get("INDIAVIX", {}).get("price"))
    us_vix = _safe_float(assets.get("USVIX", {}).get("price"))
    bias = _bias_label(final_score, india_vix, us_vix)
    gap = predict_gap(assets, final_score)
    regimes = detect_market_regimes(assets, final_score, technical_context)
    trap = detect_trap(assets, technical_context, option_data)
    direction = _direction_from_signal(signal)
    pqs, pqs_reasons = calculate_profit_quality_score(
        final_score, direction, technical_context, option_data, regimes
    )
    permission, allowed_direction, no_trade_reason = _allowed_trade_type(
        final_score, assets, technical_context, option_data, regimes, pqs, trap
    )
    day_bias = intraday_bias(final_score, gap, regimes, trap, technical_context)
    sectors = sector_impact_map(changes)

    reasons = _top_reasons(assets, group_scores, pqs_reasons, trap)
    snapshot = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "assets": assets,
        "changes": {k: round(v, 3) for k, v in changes.items()},
        "component_scores": group_scores,
        "gift_nifty_change": round(_safe_float(changes.get("GIFT", changes.get("SGXNIFTY"))), 3),
        "us_futures_score": group_scores.get("us_futures", 0.0),
        "asia_score": group_scores.get("asia", 0.0),
        "europe_score": group_scores.get("europe", 0.0),
        "commodity_score": round(_avg([group_scores.get("crude", 0.0), group_scores.get("copper_growth", 0.0)]), 2),
        "currency_score": group_scores.get("currency", 0.0),
        "vix_score": group_scores.get("gold_vix", 0.0),
        "bond_yield_score": group_scores.get("bond_yield", 0.0),
        "global_score": final_score,
        "final_global_score": final_score,
        "bias": bias,
        "gap_prediction": gap["prediction"],
        "gap_probability": gap["probability"],
        "gap": gap,
        "nifty_bias": day_bias,
        "banknifty_bias": _banknifty_bias(day_bias, changes),
        "intraday_bias": day_bias,
        "market_regime": ",".join(regimes),
        "regimes": regimes,
        "sector_impact": sectors,
        "trap": trap,
        "profit_quality_score": pqs,
        "trade_permission": permission,
        "allowed_direction": allowed_direction,
        "allowed_trade_type": allowed_direction,
        "confidence_adjustment": _confidence_adjustment(final_score, pqs, permission),
        "risk_warning": trap.get("warning") or (no_trade_reason if permission != "ALLOW" else ""),
        "no_trade_reason": no_trade_reason if permission != "ALLOW" else "",
        "reasons": reasons,
        "reasons_json": reasons,
    }
    if not context_specific:
        _write_cache(snapshot)
    return snapshot


def get_macro_context(force: bool = False) -> Dict[str, Any]:
    """Backward-compatible alias for older report callers."""
    return get_macro_global_bias(force=force)


def _banknifty_bias(day_bias: str, changes: Dict[str, float]) -> str:
    usd = _safe_float(changes.get("USDINR")) + _safe_float(changes.get("DXY"))
    if usd >= 0.6 and "Bullish" in day_bias:
        return "Bullish but bank headwind from USD pressure"
    if usd >= 0.6:
        return "Weak Banks / avoid aggressive BankNIFTY CE"
    return day_bias


def _confidence_adjustment(score: float, pqs: int, permission: str) -> float:
    if permission == "BLOCK":
        return -0.35
    if permission == "WAIT":
        return -0.15
    return round(min(0.35, max(0.05, abs(score) / 250.0 + (pqs - 75) / 200.0)), 3)


def _top_reasons(
    assets: Dict[str, Dict[str, Any]],
    group_scores: Dict[str, float],
    pqs_reasons: List[str],
    trap: Dict[str, Any],
) -> List[str]:
    group_bits = sorted(group_scores.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
    reasons = [f"{name} score {score:+.1f}" for name, score in group_bits if abs(score) >= 1]
    if trap.get("warning"):
        reasons.append(trap["warning"])
    for reason in pqs_reasons:
        if reason not in reasons:
            reasons.append(reason)
    if not reasons:
        reasons.append("No strong macro driver")
    return reasons[:10]


def apply_macro_profit_filter(
    signal: Optional[Dict[str, Any]] = None,
    technical_context: Optional[Dict[str, Any]] = None,
    option_data: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Return ALLOW/BLOCK/WAIT decision. No order placement happens here."""
    sig = dict(signal or {})
    context = dict(technical_context or {})
    if sig.get("signal_meta") and isinstance(sig.get("signal_meta"), dict):
        context.update(sig.get("signal_meta", {}))
        context.update((sig.get("signal_meta") or {}).get("decision_inputs", {}) or {})
    snapshot = get_macro_global_bias(
        force=force,
        technical_context=context,
        option_data=option_data,
        signal=sig,
    )
    direction = _direction_from_signal(sig)
    permission = snapshot["trade_permission"]
    allowed = snapshot["allowed_direction"]
    if permission == "ALLOW" and allowed not in {"BOTH", direction} and direction in {"CE", "PE"}:
        permission = "WAIT"
        risk_warning = f"macro allows {allowed}, signal wants {direction}"
    else:
        risk_warning = snapshot.get("risk_warning", "")
    return {
        "trade_permission": permission,
        "allowed_direction": allowed,
        "global_score": snapshot["global_score"],
        "profit_quality_score": snapshot["profit_quality_score"],
        "confidence_adjustment": snapshot["confidence_adjustment"],
        "risk_warning": risk_warning,
        "reasons": snapshot["reasons"],
        "snapshot": snapshot,
    }


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_global_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            gift_nifty_change REAL,
            us_futures_score REAL,
            asia_score REAL,
            europe_score REAL,
            commodity_score REAL,
            currency_score REAL,
            vix_score REAL,
            bond_yield_score REAL,
            final_global_score REAL,
            gap_prediction TEXT,
            gap_probability REAL,
            nifty_bias TEXT,
            banknifty_bias TEXT,
            market_regime TEXT,
            profit_quality_score REAL,
            allowed_trade_type TEXT,
            no_trade_reason TEXT,
            reasons_json TEXT
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(macro_global_sentiment)").fetchall()}
    required = {
        "gift_nifty_change": "REAL",
        "us_futures_score": "REAL",
        "asia_score": "REAL",
        "europe_score": "REAL",
        "commodity_score": "REAL",
        "currency_score": "REAL",
        "vix_score": "REAL",
        "bond_yield_score": "REAL",
        "final_global_score": "REAL",
        "gap_prediction": "TEXT",
        "gap_probability": "REAL",
        "nifty_bias": "TEXT",
        "banknifty_bias": "TEXT",
        "market_regime": "TEXT",
        "profit_quality_score": "REAL",
        "allowed_trade_type": "TEXT",
        "no_trade_reason": "TEXT",
        "reasons_json": "TEXT",
    }
    for col, typ in required.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE macro_global_sentiment ADD COLUMN {col} {typ}")
    conn.commit()


def log_sentiment(ctx: Optional[Dict[str, Any]] = None, db_path: str = DB_PATH) -> bool:
    try:
        ctx = ctx or get_macro_global_bias(force=True)
        conn = sqlite3.connect(db_path)
        try:
            _ensure_table(conn)
            conn.execute(
                """
                INSERT INTO macro_global_sentiment
                (timestamp, gift_nifty_change, us_futures_score, asia_score,
                 europe_score, commodity_score, currency_score, vix_score,
                 bond_yield_score, final_global_score, gap_prediction,
                 gap_probability, nifty_bias, banknifty_bias, market_regime,
                 profit_quality_score, allowed_trade_type, no_trade_reason,
                 reasons_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ctx.get("timestamp"),
                    ctx.get("gift_nifty_change"),
                    ctx.get("us_futures_score"),
                    ctx.get("asia_score"),
                    ctx.get("europe_score"),
                    ctx.get("commodity_score"),
                    ctx.get("currency_score"),
                    ctx.get("vix_score"),
                    ctx.get("bond_yield_score"),
                    ctx.get("final_global_score", ctx.get("global_score")),
                    ctx.get("gap_prediction"),
                    ctx.get("gap_probability"),
                    ctx.get("nifty_bias"),
                    ctx.get("banknifty_bias"),
                    ctx.get("market_regime"),
                    ctx.get("profit_quality_score"),
                    ctx.get("allowed_trade_type", ctx.get("allowed_direction")),
                    ctx.get("no_trade_reason", ""),
                    json.dumps(ctx.get("reasons", ctx.get("reasons_json", []))),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("macro sentiment log failed: %s", exc)
        return False


def format_globalbias(ctx: Optional[Dict[str, Any]] = None) -> str:
    ctx = ctx or get_macro_global_bias(force=True)
    gap = ctx.get("gap", {})
    sectors = ctx.get("sector_impact", {})
    lines = [
        f"Global Bias: <b>{ctx.get('bias')}</b>",
        f"Score: {ctx.get('global_score', 0):+.0f}/100 | PQS: {ctx.get('profit_quality_score', 0)}/100",
        f"Permission: {ctx.get('trade_permission')} {ctx.get('allowed_direction')}",
        f"Gap: {ctx.get('gap_prediction')} ({ctx.get('gap_probability', 0):.1f}%), exp {gap.get('expected_gap_points', 0):+.0f} pts",
        f"Intraday: {ctx.get('intraday_bias')}",
        f"Regime: {ctx.get('market_regime')}",
        f"Sector +: {', '.join(sectors.get('positive_sectors', [])) or '-'}",
        f"Sector -: {', '.join(sectors.get('negative_sectors', [])) or '-'}",
    ]
    if ctx.get("risk_warning"):
        lines.append(f"Warning: {ctx.get('risk_warning')}")
    lines.append("")
    lines.append("Reasons:")
    lines.extend(f"- {r}" for r in ctx.get("reasons", [])[:8])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Global macro profit filter for NIFTY options")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    ctx = get_macro_global_bias(force=args.force)
    if args.log:
        log_sentiment(ctx)
    if args.json:
        print(json.dumps(ctx, indent=2, default=str))
    else:
        print(format_globalbias(ctx).replace("<b>", "").replace("</b>", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
