"""
option_strategy_regime.py — detect_regime() for the 2026-07-16 option
strategy catalog, composed entirely from existing, already-tested
primitives (no new regime math):

  - market_regime.classify_regime(df, vix, df_daily)  -> trend/vol regime
  - expiry_regime.get_expiry_regime(today, symbol)     -> expiry/rollover
  - time_regime.get_time_zone(now)                     -> intraday session zone
  - greeks_live.get_event_playbook(symbol)             -> RBI/Budget/FOMC calendar

Plus one genuinely new primitive, _gap_pct(), since a same-day
open-vs-previous-close gap check exists nowhere else in this repo.

Returns the spec's own category vocabulary (STRONG_TREND/MILD_TREND/RANGE/
HIGH_VOL/LOW_VOL/EVENT/GAP/EXPIRY/CHOP) as a dict of overlay flags rather
than one mutually-exclusive label -- a day can legitimately be EXPIRY *and*
HIGH_VOL at the same time, and the spec's own catalog gates on combinations
("skip if ADX>25, gap>0.6%, VIX opened>8%" etc.), not a single enum.
"""
from __future__ import annotations

import logging
import time
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TTL = 300  # 5-minute cache, matching greeks_live.py's own regime-adjacent cache
_CACHE: Dict[tuple, Dict[str, Any]] = {}

# market_regime.classify_regime()'s 6 outputs -> this catalog's primary label.
# BREAKOUT is folded into STRONG_TREND (both are directional-momentum reads);
# the spec has no separate BREAKOUT category.
_PRIMARY_REMAP = {
    "STRONG_TREND": "STRONG_TREND",
    "WEAK_TREND": "MILD_TREND",
    "MEAN_REVERT": "RANGE",
    "HIGH_VOL": "HIGH_VOL",
    "LOW_VOL_CHOP": "CHOP",
    "BREAKOUT": "STRONG_TREND",
}


def _gap_pct(df=None, df_daily=None) -> float:
    """|today's open - previous close| / previous close, as a fraction
    (0.006 == 0.6%). Best-effort: returns 0.0 if either side can't be
    determined rather than raising -- this is an overlay flag, not a
    load-bearing trading decision in this shadow-only pass."""
    try:
        if df is None or len(df) == 0:
            return 0.0
        cols_today = {c.lower(): c for c in df.columns}
        dates = _frame_dates(df)
        session = df
        prev_close = None
        if dates is not None:
            session_date = max(dates)
            session = df.loc[dates == session_date].sort_index()
            previous = df.loc[dates < session_date].sort_index()
            if len(previous):
                prev_close = float(previous.iloc[-1][cols_today["close"]])
        if df_daily is not None and len(df_daily):
            cols_daily = {c.lower(): c for c in df_daily.columns}
            daily = df_daily
            daily_dates = _frame_dates(daily)
            if dates is not None and daily_dates is not None:
                daily = daily.loc[daily_dates < max(dates)].sort_index()
            if len(daily):
                prev_close = float(daily.iloc[-1][cols_daily["close"]])
        today_open = float(session.iloc[0][cols_today["open"]])
        if prev_close is None or not math.isfinite(prev_close) or prev_close <= 0 or not math.isfinite(today_open):
            return 0.0
        return abs(today_open - prev_close) / prev_close
    except Exception as exc:
        logger.debug("_gap_pct: %s", exc)
        return 0.0


def _frame_dates(df):
    index = getattr(df, "index", None)
    if index is not None and hasattr(index, "date"):
        local = index.tz_convert("Asia/Kolkata") if index.tz is not None else index
        return local.date
    return None


def _frame_fingerprint(df):
    if df is None or len(df) == 0:
        return None
    try:
        from pandas.util import hash_pandas_object
        return (len(df), int(hash_pandas_object(df, index=True).sum()))
    except Exception:
        # An unsupported frame must not reuse another input's regime.
        return time.time_ns()


def detect_regime(df, df_daily=None, vix: float = 15.0, symbol: str = "NIFTY",
                   now=None) -> Dict[str, Any]:
    """Cache classification for unchanged inputs, refresh overlays each call.

    New candles, a VIX change, or a new trading date invalidate classification.
    """
    now = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo("Asia/Kolkata"))
    cache_key = (symbol.upper(), float(vix), now.date(),
                 _frame_fingerprint(df), _frame_fingerprint(df_daily))
    cached = _CACHE.get(cache_key)
    if cached is not None and (time.time() - cached["_computed_at"]) < _TTL:
        primary_raw, confidence, description = cached["primary_raw"], cached["confidence"], cached["description"]
        adx = cached.get("adx")
    else:
        try:
            from market_regime import classify_regime
            primary_raw, confidence, description = classify_regime(df, vix=vix, df_daily=df_daily)
        except Exception as exc:
            logger.debug("classify_regime: %s", exc)
            primary_raw, confidence, description = "UNKNOWN", 0.0, "unavailable"
        try:
            from indicators import calculate_adx
            adx = float(calculate_adx(df).iloc[-1])
            adx = adx if math.isfinite(adx) else None
        except Exception:
            adx = None

    try:
        from expiry_regime import get_expiry_regime
        expiry = get_expiry_regime(today=now.date(), symbol=symbol)
    except Exception as exc:
        logger.debug("get_expiry_regime: %s", exc)
        expiry = {"is_expiry_day": False, "days_to_expiry": -1, "regime_label": "UNKNOWN"}

    try:
        from time_regime import get_time_zone
        zone = get_time_zone(now)
        zone_value = zone.value
    except Exception as exc:
        logger.debug("get_time_zone: %s", exc)
        zone_value = "UNKNOWN"

    try:
        from greeks_live import get_event_playbook
        event = get_event_playbook(symbol)
    except Exception as exc:
        logger.debug("get_event_playbook: %s", exc)
        event = {"events": [], "has_event": False}

    gap_pct = _gap_pct(df, df_daily)

    result = {
        "primary": _PRIMARY_REMAP.get(primary_raw, "UNKNOWN"),
        "primary_raw": primary_raw,
        "confidence": confidence,
        "description": description,
        "low_vol": bool(vix < 12),
        "vix": float(vix),
        "adx": adx,
        "gap": bool(gap_pct > 0.006),
        "gap_pct": round(gap_pct, 5),
        "event": bool(event.get("has_event")),
        "events": event.get("events", []),
        "expiry_day": bool(expiry.get("is_expiry_day")),
        "days_to_expiry": expiry.get("days_to_expiry", -1),
        "next_expiry": expiry.get("next_expiry", ""),
        "expiry_regime_label": expiry.get("regime_label", "UNKNOWN"),
        "time_zone": zone_value,
        "_computed_at": time.time(),
    }
    # Cache only the expensive classification. Time, event, gap and expiry
    # overlays are refreshed each call and callers receive independent dicts.
    for key in list(_CACHE):
        if time.time() - _CACHE[key]["_computed_at"] >= _TTL or key[0] == symbol.upper():
            del _CACHE[key]
    _CACHE[cache_key] = dict(result)
    return result
