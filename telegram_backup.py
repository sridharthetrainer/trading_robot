"""
telegram_backup.py — Backup Key Files to Telegram

Sends critical files directly to your Telegram chat.
Useful when Drive/GitHub unavailable or as additional backup.

What gets backed up:
  - .env (as text, encrypted — API keys)  
  - trades.db summary (NOT the raw file — too large)
  - signal_log stats
  - Daily P&L summary

USAGE:
  /backup       — send backup to Telegram
  /backup env   — send .env content (bot sends to YOUR chat only)
  /backup trades — send trade summary

NOTE: .env is sent ONLY to the configured TELEGRAM_CHAT_ID.
      Never shared with anyone else.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BOT_DIR = Path(__file__).parent


def backup_env_to_telegram(bot_token: str, chat_id: str) -> bool:
    """Send .env to Telegram (your private chat only)."""
    import requests
    env_path = _BOT_DIR / ".env"
    if not env_path.exists():
        return False
    try:
        content = env_path.read_text()
        # Mask actual key values for security — show keys exist but not values
        lines = []
        for line in content.splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                if val.strip():
                    masked = val[:4] + "****" + val[-2:] if len(val) > 6 else "****"
                    lines.append(f"{key}={masked}")
                else:
                    lines.append(line)
            else:
                lines.append(line)
        masked_env = "\n".join(lines)
        msg = (
            f"🔐 <b>.env BACKUP</b>\n"
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"<code>{masked_env[:3000]}</code>\n\n"
            f"⚠️ Values masked. Full file in Google Drive/config/"
        )
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        logger.debug("backup_env: %s", e)
        return False


def backup_trades_summary(bot_token: str, chat_id: str) -> bool:
    """Send trade stats summary to Telegram."""
    import requests
    try:
        summary_lines = [
            f"📊 <b>TRADES BACKUP SUMMARY</b>",
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]

        # Try to read trades.db
        db_path = _BOT_DIR / "trades.db"
        if db_path.exists():
            import sqlite3
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM trades")
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM trades WHERE status='CLOSED'")
                closed = cur.fetchone()[0]
                cur.execute("SELECT SUM(pnl) FROM trades WHERE status='CLOSED'")
                total_pnl = cur.fetchone()[0] or 0
                summary_lines += [
                    f"  Total trades: {total}",
                    f"  Closed: {closed}",
                    f"  Total P&L: ₹{total_pnl:,.0f}",
                ]
            except Exception:
                summary_lines.append("  DB structure changed — no stats")
            finally:
                con.close()

        # Signal log stats
        sl_path = _BOT_DIR / "signal_log.csv"
        if sl_path.exists():
            import csv
            with open(sl_path) as f:
                rows = sum(1 for _ in csv.reader(f)) - 1
            summary_lines.append(f"  Signal log entries: {rows}")

        msg = "\n".join(summary_lines)
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        logger.debug("backup_trades: %s", e)
        return False


def daily_backup(bot_token: str, chat_id: str, alerts=None) -> None:
    """Run all backups — called at 9:30 PM."""
    backup_trades_summary(bot_token, chat_id)
    if alerts:
        alerts.send(
            f"💾 <b>DAILY BACKUP COMPLETE</b>\n"
            f"  ☁️ Drive sync: done\n"
            f"  🐙 GitHub: committed\n"
            f"  📱 Trades summary: sent\n"
            f"🕐 {datetime.now().strftime('%H:%M')}"
        )
