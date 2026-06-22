"""
market_context_builder.py

Build one normalized market-context payload for signal scoring.
This keeps VIX, IV, PCR, global bias, news, volume and option-chain state from
getting split between config, option_data, logs and final signal metadata.
"""
from __future__ import annotations

from typing import Any, Dict

import math
import pandas as pd


INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return default if math.isnan(out) else out
    except Exception:
        return default


def _last_volume_ratio(df: pd.DataFrame | None) -> tuple[float, str]:
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return 0.0, "missing"
    lookup = {str(c).lower(): c for c in df.columns}
    for name in ("volume_ratio", "vol_ratio"):
        if name in lookup:
            vr = _safe_float(df[lookup[name]].iloc[-1], 0.0)
            return vr, "ok" if vr > 0 else "zero"
    if "volume" not in lookup:
        return 0.0, "missing"
    try:
        vol = pd.to_numeric(df[lookup["volume"]], errors="coerce").fillna(0)
        avg = vol.tail(20).mean()
        if avg <= 0:
            return 0.0, "zero"
        vr = float(vol.iloc[-1]) / float(avg)
        return round(vr, 3), "ok" if vr > 0 else "zero"
    except Exception:
        return 0.0, "missing"


def _atm_iv_from_option_result(option_result: Any) -> float:
    try:
        df = getattr(option_result, "dataframe", None)
        atm = _safe_float(getattr(option_result, "atm_strike", 0), 0.0)
        if df is None or len(df) == 0 or atm <= 0:
            return 0.0
        row = df.iloc[(pd.to_numeric(df["strikePrice"], errors="coerce") - atm).abs().argsort()[:1]]
        if row.empty:
            return 0.0
        ce_iv = _safe_float(row.iloc[0].get("CE_impliedVolatility"), 0.0)
        pe_iv = _safe_float(row.iloc[0].get("PE_impliedVolatility"), 0.0)
        vals = [v for v in (ce_iv, pe_iv) if v > 0]
        return round(sum(vals) / len(vals), 2) if vals else 0.0
    except Exception:
        return 0.0


def build_market_context(
    *,
    symbol: str,
    df: pd.DataFrame | None = None,
    intel: Dict[str, Any] | None = None,
    option_result: Any = None,
    global_bias: Dict[str, Any] | None = None,
    iv_percentile: float | None = None,
    feed_degraded: bool = False,
    feed_degraded_reasons: list | None = None,
) -> Dict[str, Any]:
    intel = intel or {}
    summary = getattr(option_result, "summary", None) or {}
    option_signal = getattr(option_result, "signal", None) or {}
    raw_json = getattr(option_result, "raw_json", None) or {}
    records = raw_json.get("records", {}) if isinstance(raw_json, dict) else {}
    symbol_u = str(symbol or "").upper()

    volume_ratio, volume_quality = _last_volume_ratio(df)
    if volume_quality != "ok" and symbol_u in INDEX_SYMBOLS:
        volume_quality = "missing_index_volume"

    pcr = (
        _safe_float(summary.get("pcr_oi"), 0.0)
        or _safe_float(summary.get("pcr_change_oi"), 0.0)
        or _safe_float(summary.get("pcr_volume"), 0.0)
    )
    vix = _safe_float(intel.get("vix"), 15.0)
    iv = _atm_iv_from_option_result(option_result)
    ivp = _safe_float(iv_percentile, 0.0)
    if ivp <= 0:
        ivp = _safe_float(intel.get("iv_percentile"), 0.0)

    ctx = {
        "symbol": symbol_u,
        "vix": vix,
        "india_vix": vix,
        "vix_source": "intel_cache" if vix else "missing",
        "iv": iv,
        "atm_iv": iv,
        "implied_volatility": iv,
        "iv_percentile": ivp,
        "ivp": ivp,
        "pcr": pcr,
        "put_call_ratio": pcr,
        "pcr_oi": _safe_float(summary.get("pcr_oi"), 0.0),
        "pcr_change_oi": _safe_float(summary.get("pcr_change_oi"), 0.0),
        "pcr_volume": _safe_float(summary.get("pcr_volume"), 0.0),
        "option_chain_signal": option_signal.get("signal"),
        "option_chain_bias": summary.get("net_bias"),
        "option_summary": summary,
        "option_signal": option_signal,
        "spot": _safe_float(getattr(option_result, "spot", 0.0), 0.0)
            or _safe_float(records.get("underlyingValue"), 0.0),
        "atm_strike": _safe_float(getattr(option_result, "atm_strike", 0.0), 0.0),
        "volume_ratio": volume_ratio,
        "volume_data_quality": volume_quality,
        "global_bias": (global_bias or {}).get("bias") or intel.get("cross_asset_bias") or "NEUTRAL",
        "global_change_pct": _safe_float((global_bias or {}).get("change_pct"), 0.0),
        "global_source": (global_bias or {}).get("source", ""),
        "cross_asset_bias": intel.get("cross_asset_bias", "NEUTRAL"),
        "news_score": _safe_float(intel.get("news_score"), 0.0),
        "expiry_dte": int(_safe_float(intel.get("expiry_dte"), 5)),
        "expiry_regime": intel.get("expiry_regime", "NORMAL"),
        "whale_mod": _safe_float((intel.get("whale_index") or {}).get(symbol_u), 0.0),
        "feed_degraded": bool(feed_degraded),
        "feed_degraded_reasons": list(feed_degraded_reasons or []),
    }
    ctx["data_confidence"] = 1.0
    if ctx["volume_data_quality"] not in ("ok", "missing_index_volume"):
        ctx["data_confidence"] -= 0.20
    if ctx["iv"] <= 0 and symbol_u in INDEX_SYMBOLS:
        ctx["data_confidence"] -= 0.10
    if ctx["pcr"] <= 0 and symbol_u in INDEX_SYMBOLS:
        ctx["data_confidence"] -= 0.10
    if ctx["feed_degraded"]:
        ctx["data_confidence"] -= 0.20
    ctx["data_confidence"] = round(max(0.0, min(1.0, ctx["data_confidence"])), 2)
    return ctx
