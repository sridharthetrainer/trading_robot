"""
regime_detector.py -- 8:45 AM regime resolution from real raw indicators.

Follow-up gap from the cluster_risk_gate build (2026-08-19): its regime
resolution was defaulting to weak_trend_low_vol whenever ambiguous --
dangerous on an actual crash morning or RBI day, since that regime doesn't
disable any high-risk clusters. This module computes the raw indicator
inputs cluster_risk_gate.ClusterRiskGate.resolve_regime_key() needs directly
from NIFTY daily candles + live VIX + the real NSE holiday calendar +
config.HIGH_IMPACT_DATES, instead of inferring from the existing
single-label regime.py/market_regime.py engines (retrofitting those is a
separate, larger task, deliberately not touched here).

Priority order (matches ClusterRiskGate.resolve_regime_key exactly):
  market_crash > holiday_week > event_day > expiry_week > indicator-derived

Every computation returns None/False rather than a guessed value when the
underlying data isn't available -- resolve_regime() then passes 0.0/False
defaults into resolve_regime_key(), which is the same conservative
(non-trending, not-obviously-high/low-vol) fallback ClusterRiskGate already
uses when a raw signal is missing.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("regime_detector")

CRASH_THRESHOLD_PCT = -0.03      # NIFTY spot -3% or worse
CRASH_LOOKBACK_TRADING_DAYS = 2
HOLIDAY_LOOKAHEAD_DAYS = 2


def _nifty_daily(days: int = 120):
    try:
        from data_fetcher import DataFetcher
        df = DataFetcher().get_market_data("NIFTY", interval="1d", days=days)
        if df is not None:
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
        return df
    except Exception as e:
        logger.debug("regime_detector: NIFTY daily fetch failed: %s", e)
        return None


def compute_raw_indicators(df=None) -> Dict[str, Optional[float]]:
    """ADX(14), 50-day EMA slope (price vs EMA), Bollinger BandWidth(20,2) --
    all on NIFTY daily candles. None for anything that couldn't be computed
    from real data (insufficient history, fetch failure, NaN result)."""
    if df is None:
        df = _nifty_daily()
    result: Dict[str, Optional[float]] = {
        "adx": None, "price_above_50ema": None, "bb_bandwidth_pct": None,
    }
    if df is None or len(df) < 55 or "close" not in df.columns:
        return result

    try:
        from indicators import calculate_adx
        adx_series = calculate_adx(df, period=14)
        adx = float(adx_series.iloc[-1])
        if adx == adx:  # not NaN
            result["adx"] = adx
    except Exception as e:
        logger.debug("regime_detector: ADX failed: %s", e)

    try:
        from indicators import calculate_ema
        ema50 = calculate_ema(df["close"], 50)
        last_close = float(df["close"].iloc[-1])
        last_ema = float(ema50.iloc[-1])
        if last_ema == last_ema:
            result["price_above_50ema"] = last_close > last_ema
    except Exception as e:
        logger.debug("regime_detector: 50EMA failed: %s", e)

    try:
        from indicators import calculate_bollinger_bands
        lower, mid, upper = calculate_bollinger_bands(df["close"], period=20, std_mult=2.0)
        m = float(mid.iloc[-1])
        if m and m == m:
            bw = (float(upper.iloc[-1]) - float(lower.iloc[-1])) / m * 100.0
            result["bb_bandwidth_pct"] = bw
    except Exception as e:
        logger.debug("regime_detector: BandWidth failed: %s", e)

    return result


def detect_crash(df=None) -> bool:
    """NIFTY spot -3% or worse over the trailing 2 TRADING days
    (close-to-close), NOT the 15-minute window -- that's market_shock_monitor's
    job. Requires at least 3 daily closes; returns False (never guesses) if
    data is insufficient."""
    if df is None:
        df = _nifty_daily(days=10)
    if df is None or len(df) < CRASH_LOOKBACK_TRADING_DAYS + 1 or "close" not in df.columns:
        return False
    try:
        closes = df["close"]
        recent = float(closes.iloc[-1])
        base = float(closes.iloc[-(CRASH_LOOKBACK_TRADING_DAYS + 1)])
        if base <= 0:
            return False
        move = (recent - base) / base
        return move <= CRASH_THRESHOLD_PCT
    except Exception as e:
        logger.debug("regime_detector: crash check failed: %s", e)
        return False


def detect_holiday_week(today: Optional[date] = None) -> bool:
    """True if tomorrow is an NSE holiday, or today is within
    HOLIDAY_LOOKAHEAD_DAYS calendar days of one (weekends don't count as
    the trigger, but ARE skipped over when checking the window)."""
    today = today or date.today()
    try:
        from nse_master import get_nse_master
        master = get_nse_master()
    except Exception as e:
        logger.debug("regime_detector: nse_master unavailable: %s", e)
        return False
    for offset in range(1, HOLIDAY_LOOKAHEAD_DAYS + 1):
        d = today + timedelta(days=offset)
        try:
            if master.is_trading_holiday(d):
                return True
        except Exception as e:
            logger.debug("regime_detector: holiday check failed for %s: %s", d, e)
    return False


def detect_event_day(today: Optional[date] = None) -> bool:
    today = today or date.today()
    try:
        import config
        return today in set(getattr(config, "HIGH_IMPACT_DATES", set()) or set())
    except Exception as e:
        logger.debug("regime_detector: event-day check failed: %s", e)
        return False


def get_days_to_expiry(today: Optional[date] = None, symbol: str = "NIFTY") -> Optional[int]:
    try:
        from expiry_regime import get_expiry_regime
        info = get_expiry_regime(today, symbol)
        dte = info.get("days_to_expiry")
        return int(dte) if dte is not None else None
    except Exception as e:
        logger.debug("regime_detector: DTE lookup failed: %s", e)
        return None


def get_india_vix(angel=None) -> float:
    try:
        from market_data_feeds import get_market_feeds
        angel_obj = getattr(angel, "obj", None) if angel is not None else None
        return float(get_market_feeds().vix.get(angel_obj) or 0.0)
    except Exception as e:
        logger.debug("regime_detector: VIX fetch failed: %s", e)
        return 0.0


GAP_OVERRIDE_THRESHOLD_PCT = 0.015  # 1.5%


def compute_gap_override(spot: float, prev_close: float,
                          threshold: float = GAP_OVERRIDE_THRESHOLD_PCT) -> Optional[str]:
    """'Poor man's news feed' (2026-08-19): the 8:45AM regime resolver uses
    yesterday's EOD indicators plus a static known-event-date list, so a
    surprise announcement between 8:45 and 9:15 (e.g. an unscheduled RBI
    move) is invisible to it. A >1.5% NIFTY gap at the open IS the news,
    regardless of cause. Returns an override regime key -- reusing the
    already-built, already-tested regime keys rather than inventing a new
    one -- or None if the gap isn't large enough to override anything.
    market_crash for a gap down (active=["G"] only in cluster_matrix.json),
    event_day for a gap up (active=["F","G","H"], 1.5% max risk)."""
    if not prev_close:
        return None
    gap_pct = (spot - prev_close) / prev_close
    if gap_pct <= -threshold:
        return "market_crash"
    if gap_pct >= threshold:
        return "event_day"
    return None


def resolve_regime(*, angel=None, today: Optional[date] = None) -> Dict[str, Any]:
    """Full regime resolution from real data. Returns {"regime_key": ...,
    "inputs": {...}} -- the inputs dict is for logging/the 5-trading-day
    manual-label validation the user asked for, not just the final answer."""
    today = today or date.today()
    df = _nifty_daily()
    raw = compute_raw_indicators(df)
    is_crash = detect_crash(df)
    is_holiday = detect_holiday_week(today)
    is_event = detect_event_day(today)
    dte = get_days_to_expiry(today)
    vix = get_india_vix(angel)

    from cluster_risk_gate import ClusterRiskGate
    regime_key = ClusterRiskGate.resolve_regime_key(
        adx=raw["adx"] or 0.0,
        price_above_50ema=bool(raw["price_above_50ema"]),
        bb_bandwidth_pct=raw["bb_bandwidth_pct"] or 0.0,
        india_vix=vix,
        days_to_expiry=dte,
        is_event_day=is_event,
        is_market_crash=is_crash,
        is_holiday_week=is_holiday,
    )
    return {
        "regime_key": regime_key,
        "resolved_at": today.isoformat(),
        "inputs": {
            "adx": raw["adx"],
            "price_above_50ema": raw["price_above_50ema"],
            "bb_bandwidth_pct": raw["bb_bandwidth_pct"],
            "india_vix": vix,
            "days_to_expiry": dte,
            "is_event_day": is_event,
            "is_market_crash": is_crash,
            "is_holiday_week": is_holiday,
        },
    }
