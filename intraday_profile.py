"""
intraday_profile.py  —  30-min bucket performance tracking.

NSE intraday has distinct time-of-day personality:
  09:15-09:45  Opening momentum / gap fill
  09:45-10:30  Trend establishment
  10:30-11:30  Mid-morning mean reversion
  11:30-13:00  Lunch chop — LOW ALPHA
  13:00-14:00  Afternoon session
  14:00-14:30  US pre-market positioning
  14:30-15:00  Power hour — HIGH ALPHA
  15:00-15:25  Position squaring — mean reversion

USAGE: get_time_bucket_weight(strategy, current_time) → float multiplier
"""
from __future__ import annotations
import json, logging
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_PROFILE_FILE = Path("intraday_profile.json")

# Default time bucket weights (1.0 = normal, >1 = boost, <1 = reduce)
_DEFAULT_PROFILE = {
    "09:15": {"label": "gap_fill",          "trend": 1.2, "mean_rev": 0.8, "breakout": 1.3, "default": 1.0},
    "09:45": {"label": "trend_establish",   "trend": 1.4, "mean_rev": 0.7, "breakout": 1.2, "default": 1.1},
    "10:30": {"label": "mid_morning_mr",    "trend": 0.9, "mean_rev": 1.3, "breakout": 0.9, "default": 1.0},
    "11:30": {"label": "lunch_chop",        "trend": 0.6, "mean_rev": 0.7, "breakout": 0.5, "default": 0.6},
    "13:00": {"label": "afternoon",         "trend": 1.0, "mean_rev": 1.0, "breakout": 1.0, "default": 1.0},
    "14:00": {"label": "us_premarket",      "trend": 1.1, "mean_rev": 0.9, "breakout": 1.2, "default": 1.0},
    "14:30": {"label": "power_hour",        "trend": 1.3, "mean_rev": 0.8, "breakout": 1.4, "default": 1.2},
    "15:00": {"label": "squaring",          "trend": 0.7, "mean_rev": 1.2, "breakout": 0.6, "default": 0.9},
}

_STRATEGY_TYPE = {
    "trend":               "trend",
    "ma_cross":            "trend",
    "supertrend_mtf":      "trend",
    "holy_grail":          "trend",
    "breakout":            "breakout",
    "orb":                 "breakout",
    "hour_orb":            "breakout",
    "ttm_squeeze":         "breakout",
    "pivot_boss":          "breakout",
    "failed_breakout":     "breakout",
    "mean_reversion":      "mean_rev",
    "vwap_reversion":      "mean_rev",
    "williams_r":          "mean_rev",
    "candlestick":         "mean_rev",
    "td_sequential":       "mean_rev",
}


def _get_bucket(t: dtime) -> str:
    """Return the bucket key for a given time."""
    buckets = [dtime(15,0), dtime(14,30), dtime(14,0), dtime(13,0),
               dtime(11,30), dtime(10,30), dtime(9,45), dtime(9,15)]
    for b in buckets:
        if t >= b:
            return b.strftime("%H:%M")
    return "09:15"


def load_profile() -> dict:
    try:
        if _PROFILE_FILE.exists():
            return json.loads(_PROFILE_FILE.read_text())
    except Exception:
        pass
    return _DEFAULT_PROFILE


def save_profile(profile: dict) -> None:
    try:
        _PROFILE_FILE.write_text(json.dumps(profile, indent=2))
    except Exception:
        pass


def get_time_bucket_weight(strategy: str, t: Optional[dtime] = None) -> float:
    """
    Get score multiplier for strategy at current time.
    Returns float: 1.0 = normal, 1.3 = boost, 0.6 = suppress.
    """
    if t is None:
        t = datetime.now().time()
    bucket   = _get_bucket(t)
    profile  = load_profile()
    bucket_p = profile.get(bucket, {})
    strat_type = _STRATEGY_TYPE.get(strategy.lower().split("_")[0], "default")
    return float(bucket_p.get(strat_type, bucket_p.get("default", 1.0)))


def update_profile_from_trades(trades: list) -> None:
    """
    Auto-update time bucket weights from actual trade outcomes.
    Called nightly after trades.db analysis.

    Each trade needs: strategy, net_pnl, entry_time (HH:MM format or timestamp).
    """
    profile = load_profile()
    by_bucket: dict = {}

    for t in trades:
        try:
            entry = t.get("entry_time") or t.get("bar_time", "")
            if not entry:
                continue
            # Parse time
            if isinstance(entry, (int, float)):
                import datetime as _dt
                ts = _dt.datetime.fromtimestamp(entry).time()
            else:
                ts = dtime.fromisoformat(str(entry)[:8] if len(str(entry)) >= 8 else "09:15")

            bucket   = _get_bucket(ts)
            strategy = str(t.get("strategy","")).lower()
            stype    = _STRATEGY_TYPE.get(strategy.split("_")[0], "default")
            pnl      = float(t.get("net_pnl", t.get("net", 0)) or 0)

            key = (bucket, stype)
            if key not in by_bucket:
                by_bucket[key] = {"pnl": 0.0, "count": 0}
            by_bucket[key]["pnl"]   += pnl
            by_bucket[key]["count"] += 1
        except Exception:
            pass

    # Adjust weights: positive expectancy → increase weight
    # negative expectancy → decrease weight
    changed = 0
    for (bucket, stype), stats in by_bucket.items():
        if stats["count"] < 5:
            continue
        avg_pnl = stats["pnl"] / stats["count"]
        bucket_p = profile.get(bucket, {})
        cur_w    = float(bucket_p.get(stype, 1.0))

        # Adjust by 5% toward +1.5 or -0.5 based on performance
        if avg_pnl > 0:
            new_w = min(cur_w * 1.05, 1.5)
        else:
            new_w = max(cur_w * 0.95, 0.4)

        if abs(new_w - cur_w) > 0.01:
            profile.setdefault(bucket, {})[stype] = round(new_w, 3)
            changed += 1

    if changed:
        save_profile(profile)
        logger.info("Intraday profile updated: %d bucket weights changed", changed)
