#!/usr/bin/env python3
"""
validate_telegram.py — Test Telegram bot + send test message
Run: python3 validate_telegram.py
Run: python3 validate_telegram.py <new_token>  (to test a new token)
"""
import os, sys, json, urllib.request, urllib.parse
from pathlib import Path

# Load from .env
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

token   = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TELEGRAM_BOT_TOKEN","")
chat_id = os.getenv("TELEGRAM_CHAT_ID","")


def _print_token_fix():
    print()
    print("  HOW TO GET NEW TOKEN:")
    print("  1. Open Telegram → search @BotFather")
    print("  2. Send: /mybots")
    print("  3. Select: NIFTY Algo Bot")
    print("  4. Tap: API Token → Copy the token")
    print("  5. Run: ./update_token.sh <paste_token_here>")


print("=" * 55)
print("TELEGRAM CONNECTIVITY TEST")
print("=" * 55)
print(f"Token:   {token[:25]}..." if len(token) > 25 else f"Token:   {token or 'NOT SET ❌'}")
print(f"Chat ID: {chat_id or 'NOT SET ❌'}")
print()

if not token:
    print("❌ TELEGRAM_BOT_TOKEN not set")
    print("   Edit .env and set TELEGRAM_BOT_TOKEN=<your_token>")
    sys.exit(1)

# ── Test 1: getMe ────────────────────────────────────────────
print("Test 1: Checking token...")
try:
    r = urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/getMe", timeout=10
    )
    d = json.loads(r.read())
    if d.get("ok"):
        bot = d["result"]
        print(f"  ✅ Token valid")
        print(f"     Bot name: {bot.get('first_name','?')}")
        print(f"     Username: @{bot.get('username','?')}")
        print(f"     Bot ID:   {bot.get('id','?')}")
    else:
        print(f"  ❌ Token rejected: {d.get('description','?')}")
        _print_token_fix()
        sys.exit(1)
except urllib.error.HTTPError as e:
    if e.code == 403:
        print(f"  ❌ HTTP 403 — Token is REVOKED")
    elif e.code == 401:
        print(f"  ❌ HTTP 401 — Token is INVALID")
    else:
        print(f"  ❌ HTTP {e.code} {e.reason}")
    _print_token_fix()
    sys.exit(1)
except Exception as e:
    print(f"  ❌ Connection failed: {e}")
    sys.exit(1)

# ── Test 2: getUpdates ───────────────────────────────────────
print("\nTest 2: Checking updates...")
try:
    r = urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/getUpdates?timeout=1&limit=5",
        timeout=15
    )
    d = json.loads(r.read())
    updates = d.get("result", [])
    print(f"  ✅ getUpdates OK")
    print(f"     Pending: {len(updates)} updates")
    if updates:
        last = updates[-1]
        msg = last.get("message",{})
        from_chat = str(msg.get("chat",{}).get("id","?"))
        text      = msg.get("text","?")
        print(f"     Last msg: chat={from_chat} text={text!r}")
        if from_chat != chat_id:
            print(f"  ⚠️  chat_id mismatch!")
            print(f"     .env has:     {chat_id}")
            print(f"     Message from: {from_chat}")
            print(f"     → Update TELEGRAM_CHAT_ID={from_chat} in .env")
except Exception as e:
    print(f"  ⚠️  getUpdates: {e}")

# ── Test 3: Send message ─────────────────────────────────────
print(f"\nTest 3: Sending test message to {chat_id}...")
if not chat_id:
    print("  ⚠️  TELEGRAM_CHAT_ID not set — skipping send test")
else:
    try:
        msg_text = (
            "✅ <b>BOT CONNECTIVITY TEST</b>\n\n"
            "  Token:   Valid ✅\n"
            "  Chat ID: Verified ✅\n"
            "  Commands: Ready ✅\n\n"
            "  Type /health to verify full system status"
        )
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": msg_text,
            "parse_mode": "HTML"
        }).encode()
        r = urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, timeout=10
        )
        d = json.loads(r.read())
        if d.get("ok"):
            print(f"  ✅ Message sent — check your Telegram!")
        else:
            desc = d.get("description","?")
            print(f"  ❌ Send failed: {desc}")
            if "chat not found" in desc.lower():
                print(f"     → Send any message to the bot first, then retry")
            elif "blocked" in desc.lower():
                print(f"     → You have blocked the bot — unblock it in Telegram")
    except Exception as e:
        print(f"  ❌ Send failed: {e}")

print()
print("=" * 55)
print("NEXT STEPS:")
print("=" * 55)
if len(sys.argv) > 1:
    new_token = sys.argv[1]
    print(f"  Token tested:  {new_token[:25]}...")
    print(f"  Update .env:   nano .env")
    print(f"  Change line:   TELEGRAM_BOT_TOKEN={new_token}")
    print(f"  Restart:       ./bot.sh restart")
else:
    print("  If all tests passed: ./bot.sh restart")
    print("  If token failed:     ./update_token.sh <new_token>")
    print("  Get new token:       @BotFather → /mybots → API Token")
