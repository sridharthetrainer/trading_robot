"""
event_calendar.py

High-impact economic event calendar for NSE trading.

Tracks events that cause NIFTY to move >1%:
  - RBI Monetary Policy Committee (MPC) — 6 times per year
  - US Federal Reserve FOMC meetings
  - India CPI / WPI inflation data
  - India GDP quarterly data
  - India Union Budget
  - Quarterly earnings (NIFTY 50 heavy-weights)
  - US Non-Farm Payrolls (NFP)

On high-impact days:
  - Reduce position sizes by 50%
  - Block scalping strategies
  - Widen stop-losses
  - Alert via Telegram at 8:30 AM
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Known high-impact dates for 2026 (update annually)
HIGH_IMPACT_EVENTS_2026 = [
    # RBI MPC decisions
    {"date":"2026-02-07","event":"RBI MPC Decision","impact":"EXTREME","direction":"UNKNOWN"},
    {"date":"2026-04-09","event":"RBI MPC Decision","impact":"EXTREME","direction":"UNKNOWN"},
    {"date":"2026-06-06","event":"RBI MPC Decision","impact":"EXTREME","direction":"UNKNOWN"},
    {"date":"2026-08-06","event":"RBI MPC Decision","impact":"EXTREME","direction":"UNKNOWN"},
    {"date":"2026-10-08","event":"RBI MPC Decision","impact":"EXTREME","direction":"UNKNOWN"},
    {"date":"2026-12-05","event":"RBI MPC Decision","impact":"EXTREME","direction":"UNKNOWN"},
    # US Federal Reserve (impacts global markets including India)
    {"date":"2026-01-29","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-03-19","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-05-07","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-06-18","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-07-30","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-09-17","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-11-05","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-12-17","event":"US Fed FOMC","impact":"HIGH","direction":"UNKNOWN"},
    # India Budget
    {"date":"2026-02-01","event":"India Union Budget","impact":"EXTREME","direction":"UNKNOWN"},
    # India CPI (monthly, around 12th)
    {"date":"2026-01-13","event":"India CPI Inflation","impact":"MEDIUM","direction":"UNKNOWN"},
    {"date":"2026-02-12","event":"India CPI Inflation","impact":"MEDIUM","direction":"UNKNOWN"},
    {"date":"2026-03-12","event":"India CPI Inflation","impact":"MEDIUM","direction":"UNKNOWN"},
    {"date":"2026-04-14","event":"India CPI Inflation","impact":"MEDIUM","direction":"UNKNOWN"},
    {"date":"2026-05-12","event":"India CPI Inflation","impact":"MEDIUM","direction":"UNKNOWN"},
    {"date":"2026-06-12","event":"India CPI Inflation","impact":"MEDIUM","direction":"UNKNOWN"},
    # US NFP (first Friday of month)
    {"date":"2026-01-02","event":"US Non-Farm Payrolls","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-02-06","event":"US Non-Farm Payrolls","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-03-06","event":"US Non-Farm Payrolls","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-04-03","event":"US Non-Farm Payrolls","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-05-01","event":"US Non-Farm Payrolls","impact":"HIGH","direction":"UNKNOWN"},
    {"date":"2026-06-05","event":"US Non-Farm Payrolls","impact":"HIGH","direction":"UNKNOWN"},
]


class EventCalendar:
    """
    Checks if today or tomorrow has a high-impact economic event.
    Provides risk multipliers to reduce position sizes on event days.
    """

    IMPACT_MULTIPLIERS = {
        "EXTREME": 0.0,    # no new positions on extreme impact days
        "HIGH":    0.5,    # half size on high impact days
        "MEDIUM":  0.75,   # 75% size on medium impact days
        "LOW":     1.0,    # full size
    }

    def __init__(self, events: list = None) -> None:
        self._events = events or HIGH_IMPACT_EVENTS_2026
        # Parse dates
        self._parsed = []
        for e in self._events:
            try:
                d = date.fromisoformat(e["date"])
                self._parsed.append({**e, "_date": d})
            except Exception:
                pass

    def get_today_events(self) -> list:
        """Return events happening today."""
        today = date.today()
        return [e for e in self._parsed if e["_date"] == today]

    def get_tomorrow_events(self) -> list:
        """Return events happening tomorrow."""
        tomorrow = date.today() + timedelta(days=1)
        return [e for e in self._parsed if e["_date"] == tomorrow]

    def get_size_multiplier(self) -> float:
        """
        Returns position size multiplier for today.
        1.0 = normal, 0.5 = half size, 0.0 = no new trades.
        """
        events = self.get_today_events()
        if not events:
            return 1.0
        # Use the most restrictive event
        min_mult = 1.0
        for e in events:
            mult = self.IMPACT_MULTIPLIERS.get(e.get("impact","LOW"), 1.0)
            min_mult = min(min_mult, mult)
        return min_mult

    def should_block_new_trades(self) -> tuple:
        """Returns (block, reason)."""
        events = self.get_today_events()
        for e in events:
            if e.get("impact") == "EXTREME":
                return True, f"high_impact_event_{e['event'].replace(' ','_')}"
        return False, ""

    def get_morning_alert(self) -> Optional[str]:
        """
        Returns Telegram alert message if there are events today/tomorrow.
        Call at 8:30 AM.
        """
        today_events    = self.get_today_events()
        tomorrow_events = self.get_tomorrow_events()

        if not today_events and not tomorrow_events:
            return None

        msg = "📅 <b>ECONOMIC CALENDAR ALERT</b>\n\n"

        if today_events:
            msg += "⚠️ <b>TODAY:</b>\n"
            for e in today_events:
                impact = e.get("impact","?")
                icon   = "🔴" if impact=="EXTREME" else "🟠" if impact=="HIGH" else "🟡"
                msg   += f"  {icon} {e['event']} ({impact})\n"
            mult = self.get_size_multiplier()
            if mult == 0.0:
                msg += "\n🚫 <b>No new trades today</b> — extreme event\n"
            else:
                msg += f"\n📉 Position sizes reduced to {mult:.0%}\n"

        if tomorrow_events:
            msg += "\n📋 <b>TOMORROW:</b>\n"
            for e in tomorrow_events:
                msg += f"  • {e['event']} ({e.get('impact','?')})\n"

        return msg.strip()


_calendar: Optional[EventCalendar] = None
def get_event_calendar() -> EventCalendar:
    global _calendar
    if _calendar is None:
        _calendar = get_auto_event_calendar()
    return _calendar


def auto_fetch_rbi_events() -> list:
    """
    Auto-fetch upcoming RBI MPC dates from NSE press releases page.
    Falls back to hardcoded dates if fetch fails.
    Called on bot startup and first of each month.
    """
    try:
        import requests, re as _re
        from datetime import date
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})

        # NSE publishes RBI calendar on their website
        r = session.get(
            "https://www.rbi.org.in/Scripts/MonetaryPolicyCalendar.aspx",
            timeout=8
        )
        text = r.text

        # Extract dates — RBI page lists them as DD-MM-YYYY
        dates_found = _re.findall(r"\b(\d{2})-(\d{2})-(\d{4})\b", text)
        events = []
        today  = date.today()
        for d, m, y in dates_found:
            try:
                event_date = date(int(y), int(m), int(d))
                if event_date >= today:
                    events.append({
                        "date":    event_date.isoformat(),
                        "event":   "RBI MPC Decision",
                        "impact":  "EXTREME",
                        "direction": "UNKNOWN",
                    })
            except Exception:
                pass

        if events:
            import logging
            logging.getLogger(__name__).info(
                "Auto-fetched %d RBI events from RBI website", len(events)
            )
            return events[:10]  # next 10 dates
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("RBI calendar fetch: %s", e)
    return []


def get_auto_event_calendar() -> "EventCalendar":
    """
    Returns EventCalendar with auto-fetched + hardcoded events combined.
    Auto-fetch runs once on first call, then uses cache.
    """
    import time, json
    from pathlib import Path
    _CACHE = Path("event_calendar_cache.json")
    _TTL   = 86400 * 7   # refresh weekly

    # Try cache first
    live_events = []
    if _CACHE.exists():
        try:
            data = json.loads(_CACHE.read_text())
            if time.time() - data.get("ts", 0) < _TTL:
                live_events = data.get("events", [])
        except Exception:
            pass

    if not live_events:
        live_events = auto_fetch_rbi_events()
        if live_events:
            try:
                _CACHE.write_text(json.dumps({"ts": time.time(), "events": live_events}))
            except Exception:
                pass

    # Merge live + hardcoded (hardcoded as fallback)
    all_events = list(HIGH_IMPACT_EVENTS_2026) + live_events
    # Deduplicate by date
    seen = set()
    unique = []
    for e in all_events:
        if e["date"] not in seen:
            seen.add(e["date"])
            unique.append(e)
    return EventCalendar(unique)

