"""Small, dependency-free NSE session calendar helpers."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time as _time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any


HOLIDAY_FILE = Path("trading_holidays.json")


def _parse_hhmm(text: str, default: str) -> _time:
    try:
        hh, mm = str(text).split(":", 1)
        return _time(int(hh), int(mm[:2]))
    except Exception:
        hh, mm = default.split(":")
        return _time(int(hh), int(mm))


def ml_training_window() -> tuple[str, str]:
    """(start, end) as 'HH:MM' for the allowed ML-training window.
    Env-configurable (ML_TRAINING_WINDOW_START / _END); default 07:00–21:00."""
    return (os.getenv("ML_TRAINING_WINDOW_START", "07:00"),
            os.getenv("ML_TRAINING_WINDOW_END", "21:00"))


def in_ml_training_window(now: Any = None) -> tuple[bool, str]:
    """Whether `now` is inside the ML-training window. Returns
    (allowed, 'HH:MM-HH:MM'). All ML training must run between these hours
    (default 7am–9pm) so heavy jobs never fire overnight."""
    start_s, end_s = ml_training_window()
    start, end = _parse_hhmm(start_s, "07:00"), _parse_hhmm(end_s, "21:00")
    moment = now if isinstance(now, datetime) else datetime.now()
    allowed = start <= moment.time() <= end
    return allowed, f"{start_s}-{end_s}"


def _coerce_date(value: Any = None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@lru_cache(maxsize=8)
def _holidays(path_text: str, mtime_ns: int) -> frozenset[date]:
    del mtime_ns
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return frozenset()
    if isinstance(payload, dict):
        payload = payload.get("holidays") or payload.get("dates") or []
    output = set()
    for item in payload or []:
        raw = item
        if isinstance(item, dict):
            raw = item.get("date") or item.get("tradingDate") or item.get("holidayDate")
        try:
            output.add(date.fromisoformat(str(raw)[:10]))
        except Exception:
            continue
    return frozenset(output)


def trading_holidays(path: Path = HOLIDAY_FILE) -> frozenset[date]:
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return _holidays(str(path.resolve()), mtime_ns)


def is_trading_day(value: Any = None, *, holiday_path: Path = HOLIDAY_FILE) -> bool:
    day = _coerce_date(value)
    return day.weekday() < 5 and day not in trading_holidays(holiday_path)


def latest_expected_session(value: Any = None, *, holiday_path: Path = HOLIDAY_FILE) -> date:
    day = _coerce_date(value)
    while not is_trading_day(day, holiday_path=holiday_path):
        day -= timedelta(days=1)
    return day


def previous_session(value: Any = None, *, holiday_path: Path = HOLIDAY_FILE) -> date:
    day = _coerce_date(value) - timedelta(days=1)
    return latest_expected_session(day, holiday_path=holiday_path)


def session_lag(last_timestamp: Any, value: Any = None) -> int:
    """Count missing expected sessions after ``last_timestamp`` through ``value``."""
    try:
        last_day = _coerce_date(last_timestamp)
    except Exception:
        return 9999
    expected = latest_expected_session(value)
    if last_day >= expected:
        return 0
    lag = 0
    cursor = last_day + timedelta(days=1)
    while cursor <= expected:
        if is_trading_day(cursor):
            lag += 1
        cursor += timedelta(days=1)
    return lag
