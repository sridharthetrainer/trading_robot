"""
option_quality.py

Reusable option-chain health and expected-move gates.

This module is deliberately pure: callers pass an already-fetched option chain
and optional market data, and get a deterministic quality decision back.
"""

from __future__ import annotations

import time
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


DEFAULT_MAX_CHAIN_AGE_SEC = 120
DEFAULT_MIN_ATM_LEG_VOLUME = 100
DEFAULT_MIN_ATM_LEG_OI = 100
DEFAULT_MAX_ATM_SPREAD_PCT = 0.20
DEFAULT_MIN_EXPECTED_MOVE_PCT_FOR_BUY = 0.35
DEFAULT_EXPECTED_MOVE_USAGE_LIMIT = 0.70


@dataclass
class OptionQualityResult:
    ok: bool
    reason: str
    spot: float = 0.0
    atm_strike: float = 0.0
    straddle_price: float = 0.0
    implied_move_pct: float = 0.0
    session_move_pct: float = 0.0
    move_used_ratio: float = 0.0
    ce_premium: float = 0.0
    pe_premium: float = 0.0
    ce_oi: float = 0.0
    pe_oi: float = 0.0
    ce_volume: float = 0.0
    pe_volume: float = 0.0
    ce_spread_pct: Optional[float] = None
    pe_spread_pct: Optional[float] = None
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_chain_rows(option_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(option_data, dict):
        return []
    for key in ("chain", "option_chain", "rows", "data", "strikes_data"):
        value = option_data.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if "strikePrice" in value[0]:
                return [row for row in value if isinstance(row, dict)]
    for key in ("records", "filtered"):
        value = option_data.get(key)
        if isinstance(value, dict) and isinstance(value.get("data"), list):
            return [row for row in value["data"] if isinstance(row, dict)]
    return []


def extract_spot(option_data: Optional[Dict[str, Any]], fallback: float = 0.0) -> float:
    try:
        fallback = float(fallback or 0.0)
        fallback = fallback if math.isfinite(fallback) and fallback > 0 else 0.0
    except (TypeError, ValueError):
        fallback = 0.0
    if not isinstance(option_data, dict):
        return float(fallback or 0.0)
    for key in ("spot", "underlyingValue", "underlying_value"):
        try:
            value = float(option_data.get(key, 0) or 0)
            if math.isfinite(value) and value > 0:
                return value
        except Exception:
            pass
    for key in ("records", "filtered"):
        value = option_data.get(key)
        if isinstance(value, dict):
            try:
                spot = float(value.get("underlyingValue", 0) or 0)
                if math.isfinite(spot) and spot > 0:
                    return spot
            except Exception:
                pass
    return float(fallback or 0.0)


def chain_age_sec(option_data: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(option_data, dict):
        return None
    ages = []
    now = time.time()
    # Exchange time remains authoritative even when cached data was fetched
    # again just now. Never refresh stale quotes by replacing their clock.
    containers = [option_data, option_data.get("records", {})]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("timestamp", "ts", "fetched_at", "last_updated"):
            value = container.get(key)
            if value is None:
                continue
            try:
                ts = float(value)
                if ts > 10_000_000_000:
                    ts /= 1000.0
            except (TypeError, ValueError):
                try:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                except ValueError:
                    try:
                        parsed = datetime.strptime(str(value), "%d-%b-%Y %H:%M:%S")
                    except ValueError:
                        continue
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                ts = parsed.timestamp()
            if not math.isfinite(ts) or ts <= 0 or ts > now + 5:
                ages.append(float("inf"))
            else:
                ages.append(max(0.0, now - ts))
    return max(ages) if ages else None


def normalize_expiry(value: Any) -> str:
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def filter_expiry_rows(rows: List[Dict[str, Any]], expiry: str) -> List[Dict[str, Any]]:
    """Keep only the requested expiry; untagged legacy rows remain usable.

    A tagged chain never falls back to an arbitrary expiry at the same strike.
    Nested leg tags must agree with the row and requested expiry.
    """
    wanted = normalize_expiry(expiry)
    tagged_chain = any(isinstance(row, dict) and (row.get("expiryDate") or row.get("expiry") or any(
        isinstance(row.get(ot), dict) and (
            row[ot].get("expiryDate") or row[ot].get("expiry"))
        for ot in ("CE", "PE"))) for row in rows)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_tag = row.get("expiryDate", row.get("expiry", ""))
        if row_tag and (not wanted or normalize_expiry(row_tag) != wanted):
            continue
        copy = dict(row)
        for ot in ("CE", "PE"):
            leg = copy.get(ot)
            if isinstance(leg, dict):
                tag = leg.get("expiryDate", leg.get("expiry", row_tag))
                if (tagged_chain and not tag) or (tag and (
                        not wanted or normalize_expiry(tag) != wanted)):
                    copy.pop(ot, None)
        if tagged_chain and not row_tag and not any(ot in copy for ot in ("CE", "PE")):
            continue
        result.append(copy)
    return result


def _leg_float(leg: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            value = float(leg.get(key, 0) or 0)
            if math.isfinite(value) and value > 0:
                return value
        except Exception:
            pass
    return 0.0


def _spread_pct(leg: Dict[str, Any]) -> Optional[float]:
    bid = _leg_float(leg, "bidprice", "bidPrice", "bestBid")
    ask = _leg_float(leg, "askPrice", "askprice", "bestAsk")
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid


def extract_contract_liquidity(
    option_data: Optional[Dict[str, Any]],
    strike: float,
    option_type: str,
) -> Dict[str, Any]:
    """Per-strike CE/PE liquidity from nested or flat chain rows
    ({strikePrice, <CE/PE>_lastPrice/_openInterest/_totalTradedVolume/_bidPrice/_askPrice}).
    Returns {last_price, oi, volume, spread_pct}. Used by option_multistrike_signals."""
    rows = extract_chain_rows(option_data)
    ot = str(option_type or "").upper()
    for r in rows:
        try:
            k = float(r.get("strikePrice", r.get("strike", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(k) or abs(k - float(strike)) > 1e-6:
            continue
        nested = r.get(ot)
        if isinstance(nested, dict):
            lp = _leg_float(nested, "lastPrice", "ltp")
            bid = _leg_float(nested, "bidprice", "bidPrice", "bestBid")
            ask = _leg_float(nested, "askPrice", "askprice", "bestAsk")
            oi = _leg_float(nested, "openInterest", "oi")
            volume = _leg_float(nested, "totalTradedVolume", "volume")
        else:
            lp = _leg_float(r, f"{ot}_lastPrice", f"{ot}_lastprice")
            bid = _leg_float(r, f"{ot}_bidPrice", f"{ot}_bidprice")
            ask = _leg_float(r, f"{ot}_askPrice", f"{ot}_askprice")
            oi = _leg_float(r, f"{ot}_openInterest", f"{ot}_oi")
            volume = _leg_float(r, f"{ot}_totalTradedVolume", f"{ot}_volume")
        mid = (bid + ask) / 2.0
        # Fraction (e.g. 0.009 = 0.9%), matching _spread_pct() convention.
        spread = round((ask - bid) / mid, 6) if (mid > 0 and ask >= bid > 0) else None
        return {
            "last_price": lp,
            "oi": oi,
            "volume": volume,
            "spread_pct": spread,
            "bid": bid, "ask": ask,
        }
    return {"last_price": 0.0, "oi": 0.0, "volume": 0.0, "spread_pct": None}


def _session_move_pct(df: Any, spot: float) -> float:
    if df is None or spot <= 0:
        return 0.0
    try:
        index = getattr(df, "index", None)
        if index is not None and hasattr(index, "date") and len(index):
            local_index = index.tz_convert("Asia/Kolkata") if index.tz is not None else index
            df = df.loc[local_index.date == max(local_index.date)]
        high_col = "high" if "high" in df.columns else "High"
        low_col = "low" if "low" in df.columns else "Low"
        high = float(df[high_col].max())
        low = float(df[low_col].min())
        return max(0.0, (high - low) / spot * 100.0)
    except Exception:
        return 0.0


def evaluate_option_buy_quality(
    option_data: Optional[Dict[str, Any]],
    spot: float = 0.0,
    df: Any = None,
    max_chain_age_sec: int = DEFAULT_MAX_CHAIN_AGE_SEC,
    min_atm_leg_volume: float = DEFAULT_MIN_ATM_LEG_VOLUME,
    min_atm_leg_oi: float = DEFAULT_MIN_ATM_LEG_OI,
    max_atm_spread_pct: float = DEFAULT_MAX_ATM_SPREAD_PCT,
    min_expected_move_pct: float = DEFAULT_MIN_EXPECTED_MOVE_PCT_FOR_BUY,
    expected_move_usage_limit: float = DEFAULT_EXPECTED_MOVE_USAGE_LIMIT,
    require_timestamp: bool = True,
    expiry: str = "",
) -> OptionQualityResult:
    """
    Gate option buying using chain health and ATM expected move.

    Blocks when:
    - option chain is missing/stale,
    - ATM CE/PE legs are missing,
    - ATM liquidity is too thin,
    - bid/ask spread is too wide,
    - market-priced move is too small for option buying,
    - current session range has already consumed too much expected move.
    """
    try:
        limits = [float(value) for value in (
            max_chain_age_sec, min_atm_leg_volume, min_atm_leg_oi,
            max_atm_spread_pct, min_expected_move_pct, expected_move_usage_limit)]
        if any(not math.isfinite(value) or value < 0 for value in limits):
            return OptionQualityResult(False, "invalid_option_quality_limits")
    except (TypeError, ValueError):
        return OptionQualityResult(False, "invalid_option_quality_limits")
    rows = extract_chain_rows(option_data)
    if not rows:
        return OptionQualityResult(False, "option_chain_missing")

    age = chain_age_sec(option_data)
    if require_timestamp and age is None:
        return OptionQualityResult(False, "option_chain_timestamp_missing")
    if age is not None and max_chain_age_sec > 0 and age > max_chain_age_sec:
        return OptionQualityResult(False, "option_chain_stale")

    spot = extract_spot(option_data, fallback=spot)
    if spot <= 0:
        return OptionQualityResult(False, "spot_missing")

    valid_rows = [row for row in rows if _leg_float(row, "strikePrice") > 0]
    expiry = expiry or str((option_data or {}).get("expiry", ""))
    tagged = any(row.get("expiryDate") or row.get("expiry") or any(
        isinstance(row.get(ot), dict) and (row[ot].get("expiryDate") or row[ot].get("expiry"))
        for ot in ("CE", "PE")) for row in valid_rows)
    if tagged:
        if not normalize_expiry(expiry):
            return OptionQualityResult(False, "option_chain_expiry_missing")
        valid_rows = filter_expiry_rows(valid_rows, expiry)
    if not valid_rows:
        return OptionQualityResult(False, "option_chain_expiry_or_strikes_missing")
    atm_row = min(valid_rows, key=lambda row: abs(float(row["strikePrice"]) - spot))
    atm_strike = float(atm_row.get("strikePrice", 0) or 0)
    ce = atm_row.get("CE", {}) or {}
    pe = atm_row.get("PE", {}) or {}

    ce_premium = _leg_float(ce, "lastPrice")
    pe_premium = _leg_float(pe, "lastPrice")
    if ce_premium <= 0 or pe_premium <= 0:
        return OptionQualityResult(False, "atm_premium_missing", spot=spot, atm_strike=atm_strike)

    ce_oi = _leg_float(ce, "openInterest")
    pe_oi = _leg_float(pe, "openInterest")
    ce_volume = _leg_float(ce, "totalTradedVolume")
    pe_volume = _leg_float(pe, "totalTradedVolume")
    if min(ce_oi, pe_oi) < min_atm_leg_oi:
        return OptionQualityResult(False, "atm_oi_too_low", spot=spot, atm_strike=atm_strike)
    if min(ce_volume, pe_volume) < min_atm_leg_volume:
        return OptionQualityResult(False, "atm_volume_too_low", spot=spot, atm_strike=atm_strike)

    ce_spread = _spread_pct(ce)
    pe_spread = _spread_pct(pe)
    if ce_spread is None or pe_spread is None:
        return OptionQualityResult(False, "atm_quotes_missing_or_crossed", spot=spot,
                                   atm_strike=atm_strike)
    for spread in (ce_spread, pe_spread):
        if spread is not None and spread > max_atm_spread_pct:
            return OptionQualityResult(
                False, "atm_spread_too_wide", spot=spot, atm_strike=atm_strike,
                ce_spread_pct=ce_spread, pe_spread_pct=pe_spread,
            )

    straddle = ce_premium + pe_premium
    implied_pct = (straddle / spot * 100.0) if spot > 0 else 0.0
    if implied_pct < min_expected_move_pct:
        return OptionQualityResult(False, "expected_move_too_small", spot=spot, atm_strike=atm_strike,
                                   straddle_price=straddle, implied_move_pct=implied_pct)

    session_move = _session_move_pct(df, spot)
    move_used = session_move / implied_pct if implied_pct > 0 else 0.0
    if expected_move_usage_limit > 0 and move_used > expected_move_usage_limit:
        return OptionQualityResult(False, "expected_move_already_consumed", spot=spot,
                                   atm_strike=atm_strike, straddle_price=straddle,
                                   implied_move_pct=implied_pct, session_move_pct=session_move,
                                   move_used_ratio=move_used)

    source = str(option_data.get("source", "")) if isinstance(option_data, dict) else ""
    return OptionQualityResult(
        True,
        "ok_to_buy_options",
        spot=round(spot, 2),
        atm_strike=atm_strike,
        straddle_price=round(straddle, 2),
        implied_move_pct=round(implied_pct, 4),
        session_move_pct=round(session_move, 4),
        move_used_ratio=round(move_used, 4),
        ce_premium=ce_premium,
        pe_premium=pe_premium,
        ce_oi=ce_oi,
        pe_oi=pe_oi,
        ce_volume=ce_volume,
        pe_volume=pe_volume,
        ce_spread_pct=round(ce_spread, 4) if ce_spread is not None else None,
        pe_spread_pct=round(pe_spread, 4) if pe_spread is not None else None,
        source=source,
    )
