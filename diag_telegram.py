#!/usr/bin/env python3
"""
diag_telegram.py — Run this on your machine to diagnose Telegram issue
It will:
1. Test your token
2. Clear webhook
3. Do a 30-second test to see if messages are being received
4. Show you EXACTLY what the bot sees when you send /health

Run: python3 diag_telegram.py
Then send /health from Telegram while it runs
"""
import os, sys, json, time, urllib.request, urllib.parse
from pathlib import Path

# Load .env
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k,_,v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN","")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","")

print("="*55)
print("TELEGRAM DIAGNOSTIC")  
print("="*55)
print(f"Token:   {'SET' if TOKEN else 'NOT SET'}")
print(f"Chat ID: {'SET' if CHAT_ID else 'NOT SET'}")
print()

def api(method, **params):
    data = json.dumps(params).encode() if params else None
    req  = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=data,
        headers={"Content-Type":"application/json"} if data else {}
    )
    r = urllib.request.urlopen(req, timeout=15)
    return json.loads(r.read())

# Test 1: Token
print("1. Testing token...")
try:
    me = api("getMe")
    if me.get("ok"):
        print(f"   ✅ Token valid — @{me['result']['username']}")
    else:
        print(f"   ❌ Token rejected: {me.get('description')}")
        sys.exit(1)
except Exception as e:
    print(f"   ❌ FAILED: {e}")
    sys.exit(1)

# Test 2: Webhook
print("2. Checking webhook...")
try:
    wh = api("getWebhookInfo")
    url = wh.get("result",{}).get("url","")
    if url:
        print(f"   ⚠️  Webhook set: {url}")
        print("      Deleting...")
        api("deleteWebhook", drop_pending_updates=False)
        print("   ✅ Webhook deleted")
    else:
        print("   ✅ No webhook — polling mode OK")
except Exception as e:
    print(f"   ⚠️  {e}")

# Test 3: Get pending updates
print("3. Checking pending updates...")
try:
    resp = api("getUpdates", timeout=1, limit=10)
    updates = resp.get("result",[])
    print(f"   Pending updates: {len(updates)}")
    for u in updates[-3:]:
        msg = u.get("message",{})
        print(f"   - chat={msg.get('chat',{}).get('id')} "
              f"from={msg.get('from',{}).get('id')} "
              f"text={msg.get('text','')!r}")
        # Check if chat_id matches
        from_id = str(msg.get('from',{}).get('id',''))
        chat_id = str(msg.get('chat',{}).get('id',''))
        owner   = str(CHAT_ID)
        match   = chat_id==owner or from_id==owner
        print(f"     Owner match: {'✅ YES' if match else f'❌ NO (owner={owner})'}")
except Exception as e:
    print(f"   ❌ {e}")

# Test 4: Live 30-second polling test
print()
print("4. LIVE TEST — send /health from Telegram NOW")
print("   Waiting 30 seconds for your message...")
print()

offset = 0
deadline = time.time() + 30
found = False
while time.time() < deadline:
    try:
        resp = api("getUpdates", offset=offset, timeout=5, 
                   allowed_updates=["message"])
        for upd in resp.get("result",[]):
            offset = upd["update_id"] + 1
            msg    = upd.get("message",{})
            text   = msg.get("text","")
            fid    = str(msg.get("from",{}).get("id",""))
            cid    = str(msg.get("chat",{}).get("id",""))
            print(f"   📨 Got message: chat={cid} from={fid} text={text!r}")
            print(f"      Owner({CHAT_ID}) match: {'✅ YES' if (cid==CHAT_ID or fid==CHAT_ID) else '❌ NO'}")
            if text.startswith("/"):
                print(f"      → Sending reply to chat {cid}...")
                try:
                    data = urllib.parse.urlencode({
                        "chat_id":    cid,
                        "text":       f"✅ Diagnostic reply to: {text}",
                        "parse_mode": "HTML"
                    }).encode()
                    urllib.request.urlopen(
                        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                        data=data, timeout=10
                    )
                    print("      ✅ Reply sent!")
                except Exception as e:
                    print(f"      ❌ Reply failed: {e}")
            found = True
    except Exception as e:
        print(f"   Error: {e}")
    remaining = int(deadline - time.time())
    print(f"   Waiting... {remaining}s", end="\r")

print()
if not found:
    print("   ❌ No messages received in 30 seconds")
    print()
    print("   POSSIBLE CAUSES:")
    print("   a) You sent from a DIFFERENT account (not chat_id 8257513231)")
    print("   b) The bot is not started (check ./bot.sh logs)")  
    print("   c) You need to send /start to the bot first")
    print("   d) The bot has been blocked — unblock it in Telegram")
    print()
    print("   TRY: Open your bot in Telegram → Send: /start")
else:
    print("   ✅ Messages ARE being received by the polling loop")
    print("   The bot should respond. Check ./bot.sh logs for errors")
