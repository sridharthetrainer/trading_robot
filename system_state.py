"""
system_state.py — Persistent system state across restarts.

Saves: current mode, what was running, last activity timestamp.
On restart: reads this file and sends meaningful "resuming..." alert.

States: TRADING, BACKTEST, ML_TRAINING, LEARNING, AFTER_HOURS,
        HOLIDAY, WEEKEND, STARTUP, IDLE
"""
from __future__ import annotations
import json, logging, time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_STATE_FILE = Path("system_state.json")

STATES = {
    "TRADING":     {"icon":"📈","desc":"Live market scanning + signal generation"},
    "BACKTEST":    {"icon":"📐","desc":"Running nightly backtest on all 199 symbols"},
    "ML_TRAINING": {"icon":"🧠","desc":"AI model training on signal log data"},
    "LEARNING":    {"icon":"📚","desc":"Strategy learning + parameter optimization"},
    "AFTER_HOURS": {"icon":"🌙","desc":"After-hours intelligence gathering"},
    "HOLIDAY":     {"icon":"🎉","desc":"Market holiday — running extended maintenance"},
    "WEEKEND":     {"icon":"🏖️","desc":"Weekend deep analysis + model improvement"},
    "STARTUP":     {"icon":"🚀","desc":"System initializing"},
    "IDLE":        {"icon":"⏸️","desc":"Waiting for next scheduled task"},
    "BACKUP":      {"icon":"💾","desc":"Backing up trades and model to Google Drive"},
    "DATA_FETCH":  {"icon":"📥","desc":"Downloading market data"},
    "PAPER":       {"icon":"📄","desc":"Paper trading mode — recording signals"},
    "LIVE":        {"icon":"💰","desc":"Live trading mode — executing real orders"},
}


class SystemState:
    def __init__(self) -> None:
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if _STATE_FILE.exists():
                return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
        return {"state": "STARTUP", "since": time.time(), "detail": ""}

    def _save(self) -> None:
        try:
            _STATE_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as e:
            logger.debug("SystemState save: %s", e)

    def set(self, state: str, detail: str = "") -> None:
        prev = self._data.get("state","?")
        self._data = {
            "state":       state,
            "since":       time.time(),
            "detail":      detail,
            "prev_state":  prev,
            "updated":     datetime.now().isoformat(),
        }
        self._save()
        logger.info("State: %s → %s", prev, state)

    def get(self) -> dict:
        return dict(self._data)

    def get_state(self) -> str:
        return self._data.get("state","STARTUP")

    def resume_message(self) -> str:
        """Build 'resuming...' message for restart alert."""
        state  = self._data.get("state","STARTUP")
        detail = self._data.get("detail","")
        since_ts = float(self._data.get("since", time.time()))
        mins_ago = int((time.time() - since_ts) / 60)
        info   = STATES.get(state, {"icon":"🔄","desc":state})

        lines = [
            f"🔄 <b>SYSTEM RESTARTED</b>",
            f"{'─'*34}",
            f"  Resuming from: <b>{state}</b>",
            f"  {info['icon']} {info['desc']}",
        ]
        if detail:
            lines.append(f"  Detail: {detail}")
        if mins_ago > 1:
            lines.append(f"  Was running for: {mins_ago} min before restart")
        lines += [
            f"{'─'*34}",
            f"  System is now re-initializing.",
            f"  Trades and positions will be restored.",
            f"  All scheduled tasks will resume.",
            f"🕐 {datetime.now().strftime('%d-%b %H:%M:%S')}",
        ]
        return "\n".join(lines)

    def schedule_message(self) -> str:
        """Show what's running and what's next."""
        now = datetime.now()
        h   = now.hour
        state = self.get_state()

        schedule = [
            (8, 28,  "Pre-market intelligence brief",  "AFTER_HOURS"),
            (9, 10,  "Daily trading plan",             "AFTER_HOURS"),
            (9, 15,  "Market open → TRADING starts",   "TRADING"),
            (15, 25, "EOD squareoff → positions close","TRADING"),
            (15, 30, "Market closes → AFTER_HOURS",    "AFTER_HOURS"),
            (15, 35, "Daily P&L journal report",       "AFTER_HOURS"),
            (16, 30, "BACKTEST starts (199 symbols)",  "BACKTEST"),
            (17, 30, "Backtest ends + report sent",    "AFTER_HOURS"),
            (18, 0,  "ML_TRAINING starts",             "ML_TRAINING"),
            (18, 30, "ML training ends",               "AFTER_HOURS"),
            (20, 0,  "Daily download report sent",     "AFTER_HOURS"),
            (23, 0,  "Signal log TB labelling",        "ML_TRAINING"),
        ]

        lines = [f"📅 <b>SYSTEM SCHEDULE</b>  Now: {now.strftime('%H:%M')} ({state})"]
        now_mins = h * 60 + now.minute
        for sh, sm, desc, mode in schedule:
            task_mins = sh * 60 + sm
            diff = task_mins - now_mins
            if -5 <= diff <= 0:
                lines.append(f"  🔴 NOW  {sh:02d}:{sm:02d} — {desc}")
            elif 0 < diff <= 120:
                lines.append(f"  ⏰ +{diff}m  {sh:02d}:{sm:02d} — {desc}")
            elif diff > 120:
                lines.append(f"  ⬜ {sh:02d}:{sm:02d} — {desc}")
        return "\n".join(lines[:12])


_state: Optional[SystemState] = None
def get_state() -> SystemState:
    global _state
    if _state is None:
        _state = SystemState()
    return _state
