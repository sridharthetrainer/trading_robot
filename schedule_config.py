"""
Shared morning off-hours task schedule.

main_autonomous.py's _after_hours_tasks() (trading-day path) and
_run_holiday_off_hours_tasks() (holiday/weekend path) used to each
hand-maintain their own copy of this morning schedule, and had already
drifted (e.g. morning brief fired at >=08:28 in one, 08:30-08:45 in the
other). Single source of truth from here on - see git history for the
drift evidence (2026-09-02 off-hours scheduling reconciliation).

Only tasks that were genuinely duplicated in BOTH paths are listed here.
Sector rotation refresh and the subscription-expiry check are
deliberately NOT included: they only ever existed in the holiday/weekend
path (_run_holiday_off_hours_tasks), so pulling them into a table both
paths read from would silently add them to every trading day too - a
behavior change, not a drift fix. They stay where they are.
"""
from __future__ import annotations

from datetime import time
from typing import Any, Callable

# (task_id, start_time, end_time, OffHoursEngine method name to call)
#
# Each window is the UNION of the two original (pre-consolidation) windows,
# not an arbitrary pick of one side. The trading-day path only polls this
# schedule every ~300s (after-hours sleep interval) vs. the holiday path's
# ~60s, so a window that was safely generous on one side must stay that
# generous here - narrowing to the tighter of the two would have quietly
# shrunk the trading-day path's safety margin against a missed send.
MORNING_OFF_HOURS_SCHEDULE: list[tuple[str, time, time, str]] = [
    ("gap_warn",      time(7, 43),  time(8, 0),   "_run_swing_gap_warning"),
    ("morning_video", time(8, 0),   time(8, 15),  "_run_morning_video"),
    ("morning_brief", time(8, 28),  time(8, 59),  "_run_morning_brief"),
    ("fno_ban_check", time(9, 0),   time(9, 59),  "_run_fno_ban_check"),
    ("live_pos_1000", time(10, 0),  time(10, 5),  "_run_live_position_update"),
    ("live_pos_1130", time(11, 30), time(11, 35), "_run_live_position_update"),
    ("live_pos_1300", time(13, 0),  time(13, 5),  "_run_live_position_update"),
    ("live_pos_1430", time(14, 30), time(14, 35), "_run_live_position_update"),
]


def run_morning_schedule(
    flag_owner: Any,
    off_hours_engine: Any,
    today_str: str,
    now_time: time,
    on_error: Callable[[str, Exception], None] | None = None,
) -> None:
    """Dispatch any due tasks from MORNING_OFF_HOURS_SCHEDULE, each at most
    once per calendar day.

    `flag_owner` holds the per-day done-flags as instance attributes,
    date-keyed (f"_sched_{task_id}_{today_str}") so they self-reset daily
    without needing a separate reset-at-7am step. Each task gets its own
    try/except, so one task raising can't block a later one in the same
    window (previously sector-rotation and fno-ban-check shared one
    try/except in the holiday path; fno-ban-check silently never ran if
    sector-rotation raised first).
    """
    for task_id, start, end, method_name in MORNING_OFF_HOURS_SCHEDULE:
        flag = f"_sched_{task_id}_{today_str}"
        if getattr(flag_owner, flag, False):
            continue
        if start <= now_time <= end:
            setattr(flag_owner, flag, True)
            method = getattr(off_hours_engine, method_name, None)
            if method is None:
                continue
            try:
                method()
            except Exception as e:
                if on_error is not None:
                    on_error(task_id, e)
