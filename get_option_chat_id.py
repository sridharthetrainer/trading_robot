#!/usr/bin/env python3
"""
get_option_chat_id.py — one-time helper to find a NEW chat_id for the
option-bot Telegram channel (2026-07-20, operator: "have option bot
signals to option bot alone").

Root cause found: OPTION_BOT_TOKEN in .env is genuinely a separate bot
from TELEGRAM_BOT_TOKEN, but OPTION_CHAT_ID is set to the SAME value as
TELEGRAM_CHAT_ID -- so despite a distinct bot, option signals have been
landing in the same chat as everything else, not a separate channel.

Usage:
  1. In Telegram, create a new group or channel (or reuse one you want
     dedicated to option signals only).
  2. Add the option bot to it (search by its username -- BotFather can
     remind you, or check @<the bot you set up for OPTION_BOT_TOKEN>).
     For a CHANNEL, add it as an administrator (channels require bot
     admin rights to post); for a GROUP, a regular member is enough.
  3. Send any message in that chat (e.g. "hi").
  4. Run: python3 get_option_chat_id.py
  5. Copy the chat_id it prints into .env as OPTION_CHAT_ID, then
     restart: sudo systemctl restart trading-bot.service
"""
import json
import os
import urllib.request
from pathlib import Path

env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.getenv("OPTION_BOT_TOKEN", "")
CURRENT_CHAT_ID = os.getenv("OPTION_CHAT_ID", "")


def api(method: str, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    if qs:
        url += f"?{qs}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def main() -> int:
    if not TOKEN:
        print("OPTION_BOT_TOKEN not set in .env — nothing to check.")
        return 1
    print(f"Current OPTION_CHAT_ID in .env: {CURRENT_CHAT_ID or 'NOT SET'}")
    print("Fetching recent updates for the option bot...\n")
    try:
        resp = api("getUpdates", limit=20)
    except Exception as exc:
        print(f"Could not reach Telegram API: {exc}")
        return 1
    if not resp.get("ok"):
        print(f"Telegram API error: {resp}")
        return 1
    results = resp.get("result", [])
    if not results:
        print("No recent messages seen by this bot yet.")
        print("Add it to your new channel/group and send a message there, then re-run.")
        return 0
    seen = {}
    for update in results:
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat", {})
        cid = chat.get("id")
        if cid is not None:
            seen[cid] = {"title": chat.get("title") or chat.get("username") or "(private chat)",
                        "type": chat.get("type", "")}
    if not seen:
        print("No chat info found in recent updates.")
        return 0
    print("Chats this bot has seen:")
    for cid, info in seen.items():
        flag = "  <- SAME AS CURRENT OPTION_CHAT_ID" if str(cid) == CURRENT_CHAT_ID else ""
        print(f"  chat_id={cid}  type={info['type']}  title={info['title']}{flag}")
    print("\nCopy the chat_id for your NEW dedicated channel/group into .env as OPTION_CHAT_ID,")
    print("then: sudo systemctl restart trading-bot.service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
