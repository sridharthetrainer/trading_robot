"""Read-only, bounded scalping views for the existing options Telegram bot."""
from __future__ import annotations

from datetime import datetime
from html import escape
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "NIFTYNXT50"}
MAX_JOURNAL_BYTES = 8 * 1024 * 1024


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _price(value):
    number = _number(value)
    return f"{number:,.2f}" if number is not None and number > 0 else "unavailable"


def _read_json(path):
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            return {}
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def latest_scalp_setups(path=None, *, symbol=None, limit=3, max_bytes=MAX_JOURNAL_BYTES):
    """Read the recent journal window without creating a journal or emitting alerts."""
    path = Path(path or os.getenv("OPTION_DECISION_JOURNAL", "option_decision_journal.jsonl"))
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - max_bytes)
            stream.seek(start)
            if start:
                stream.readline()  # Discard a potentially partial JSON record.
            lines = stream.read(max_bytes).splitlines()
    except OSError:
        return {"rows": [], "truncated": False, "available": False}
    found, seen = [], set()
    for raw in reversed(lines):
        if b"OPTION_SCALP" not in raw.upper():
            continue
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(row, dict) or str(row.get("strategy", "")).upper() != "OPTION_SCALP":
            continue
        if row.get("decision") != "selected" or (symbol and row.get("symbol") != symbol):
            continue
        key = str(row.get("source_id") or f"{row.get('symbol')}:{row.get('ts')}:{row.get('side')}")
        if key in seen:
            continue
        seen.add(key)
        found.append(row)
        if len(found) >= max(1, min(int(limit), 5)):
            break
    return {"rows": found, "truncated": start > 0, "available": True}


def scalp_navigation(symbol=None):
    suffix = f" {symbol}" if symbol in SYMBOLS else ""
    return {"inline_keyboard": [
        [{"text": "⚡ Status", "callback_data": "cmd:scalpstatus"},
         {"text": "🎯 Setups", "callback_data": "cmd:scalpsignals" + suffix}],
        [{"text": "NIFTY", "callback_data": "cmd:scalpsignals NIFTY"},
         {"text": "BANKNIFTY", "callback_data": "cmd:scalpsignals BANKNIFTY"}],
        [{"text": "📊 Scalp performance", "callback_data": "cmd:optscalp"},
         {"text": "⬅️ Menu", "callback_data": "menu:home"}],
    ]}


def render_scalp_view(*, mode="overview", symbol=None, now=None, journal_path=None,
                      status_path="live_status.json", setting=None):
    if symbol and symbol not in SYMBOLS:
        return "Usage: /scalpsignals NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|SENSEX|BANKEX"
    now = now or datetime.now(IST)
    now = now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    if setting is None:
        from option_scalper import _cfg
        setting = _cfg
    truth = lambda value: str(value).strip().lower() in {"1", "true", "yes", "on"}
    scanner = truth(setting("OPTION_SCALPER_ENABLED", "false"))
    alerts = truth(os.getenv("OPTION_SCALP_ALERTS", "false"))
    interval = escape(str(setting("OPTION_SCALP_INTERVAL", "5m"))[:12])
    lines = ["⚡ <b>OPTION SCALPING</b>", now.strftime("%d %b %Y · %H:%M:%S IST")]
    if mode != "signals":
        status = _read_json(status_path)
        trading = status.get("trading") if isinstance(status.get("trading"), dict) else {}
        kill = status.get("kill_switch") if isinstance(status.get("kill_switch"), dict) else {}
        circuit = status.get("circuit_breaker") if isinstance(status.get("circuit_breaker"), dict) else {}
        locked = trading.get("locked") or kill.get("active") or circuit.get("active")
        lines.extend(["", "<b>Status</b>",
                      f"Scanner: {'enabled' if scanner else 'disabled'} · {interval}",
                      f"Automatic scalp alerts: {'enabled' if alerts else 'disabled'}",
                      f"Bot mode: {escape(str(status.get('mode', 'unknown'))[:24])}",
                      f"Market phase: {escape(str(status.get('market_phase', 'unknown'))[:32])}"])
        lines.append("Entry lock: active" if locked else (
            "Entry lock: inactive in stored status" if "locked" in trading else "Entry lock: unknown"))
        stamp = escape(str(status.get("timestamp") or "unavailable")[:32])
        lines.append(f"Bot status recorded: {stamp}")
        try:
            moment = datetime.fromisoformat(str(status["timestamp"]).replace("Z", "+00:00"))
            moment = moment.replace(tzinfo=IST) if moment.tzinfo is None else moment.astimezone(IST)
            status_age = (now - moment).total_seconds()
            if status_age > 120:
                lines.append(f"⚠️ Status snapshot is stale ({int(status_age / 60)} min old).")
            elif status_age < 0:
                lines.append("⚠️ Status timestamp is ahead of this view's clock.")
        except (KeyError, ValueError, TypeError):
            lines.append("Status freshness: unknown.")
        lines.append("Scalp scanner generates setups only; live eligibility is checked separately.")
    if mode != "status":
        result = latest_scalp_setups(journal_path, symbol=symbol)
        title = "Recorded setups" + (f" · {symbol}" if symbol else "")
        lines.extend(["", f"<b>{title}</b>"])
        if not result["rows"]:
            lines.append("No scalp setup found in the recent journal window." if result["available"]
                         else "Scalp journal unavailable; no setup to display.")
        max_age = _number(setting("MAX_SIGNAL_AGE_SEC", 90)) or 90
        for row in result["rows"]:
            selected = row.get("selected") if isinstance(row.get("selected"), dict) else {}
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            evidence = meta.get("scalp_evidence") if isinstance(meta.get("scalp_evidence"), dict) else {}
            ts = _number(row.get("ts"))
            try:
                recorded = datetime.fromtimestamp(ts, IST) if ts is not None else None
            except (ValueError, OSError, OverflowError):
                recorded = None
            age = now.timestamp() - ts if recorded else None
            label = ("TIME UNKNOWN" if age is None or age < 0 else
                     "HISTORICAL / EXPIRED" if age > max_age else "RECENT · SIGNAL ONLY")
            name = escape(str(row.get("symbol") or "unknown")[:24])
            side = str(row.get("side") or "").upper()
            bias = {"BUY": "bullish", "SELL": "bearish"}.get(side, "unknown")
            option_type = str(selected.get("option_type") or "").upper()
            reference = f"{_price(selected.get('strike'))} {escape(option_type[:4])}" if option_type in {"CE", "PE"} else "unavailable"
            score = _number(row.get("setup_score"))
            lines.extend(["", f"<b>{name}</b> · {bias} · {label}",
                          recorded.strftime("%d %b · %H:%M:%S IST") if recorded else "Recorded time unavailable",
                          f"ATM reference: {reference}",
                          f"Underlying spot {_price(row.get('spot'))} · trigger {_price(evidence.get('trigger'))}",
                          f"Underlying SL {_price(evidence.get('stop'))}",
                          f"Underlying T1 {_price(evidence.get('target_1'))} · T2 {_price(evidence.get('target_2'))}",
                          f"Setup score: {score:g}/10" if score is not None else "Setup score: unavailable"])
        if result["truncated"]:
            lines.append("\nRecent journal window only; older setups may be outside this view.")
        lines.append("\nATM is a reference; expiry and executable premium are not verified by this scalp scanner.")
    lines.append("Win probability: not calibrated. Setup score is not a win probability.")
    return "\n".join(lines)
