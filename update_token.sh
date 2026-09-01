#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# update_token.sh — Update Telegram bot token
# Usage: ./update_token.sh <NEW_TOKEN_FROM_BOTFATHER>
# ═══════════════════════════════════════════════════════════════

set -e

if [ -z "$1" ]; then
  echo "Usage: ./update_token.sh <NEW_BOT_TOKEN>"
  echo ""
  echo "How to get new token:"
  echo "  1. Open Telegram → @BotFather"
  echo "  2. Send: /mybots"
  echo "  3. Select your bot"
  echo "  4. Tap: API Token → copy the token"
  echo "  5. Run: ./update_token.sh <paste_token_here>"
  exit 1
fi

NEW_TOKEN="$1"

# ── Validate token ────────────────────────────────────────────
echo "Validating token with Telegram..."
RESPONSE=$(curl -s --max-time 10 "https://api.telegram.org/bot${NEW_TOKEN}/getMe")

if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('ok') else 1)" 2>/dev/null; then
  BOT_NAME=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['username'])" 2>/dev/null)
  BOT_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['id'])" 2>/dev/null)
  echo "✅ Token valid — @${BOT_NAME} (ID: ${BOT_ID})"
else
  echo "❌ Token invalid: $RESPONSE"
  echo ""
  echo "Make sure you copied the full token from BotFather"
  exit 1
fi

# ── Update .env ───────────────────────────────────────────────
if [ -f ".env" ]; then
  # Replace existing token line
  if grep -q "^TELEGRAM_BOT_TOKEN=" .env; then
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${NEW_TOKEN}|" .env
    echo "✅ .env updated (TELEGRAM_BOT_TOKEN replaced)"
  else
    echo "TELEGRAM_BOT_TOKEN=${NEW_TOKEN}" >> .env
    echo "✅ .env updated (TELEGRAM_BOT_TOKEN added)"
  fi
else
  echo "⚠️  .env not found — creating it"
  echo "TELEGRAM_BOT_TOKEN=${NEW_TOKEN}" > .env
fi

# ── Send test message ─────────────────────────────────────────
CHAT_ID=$(grep "^TELEGRAM_CHAT_ID=" .env 2>/dev/null | cut -d'=' -f2 | tr -d '"')
if [ -n "$CHAT_ID" ]; then
  echo "Sending test message to chat ${CHAT_ID}..."
  TEST_RESP=$(curl -s --max-time 10 \
    -X POST "https://api.telegram.org/bot${NEW_TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\":\"${CHAT_ID}\",\"text\":\"✅ New token activated — bot is ready\",\"parse_mode\":\"HTML\"}")
  if echo "$TEST_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('ok') else 1)" 2>/dev/null; then
    echo "✅ Test message sent — check your Telegram!"
  else
    echo "⚠️  Test message failed (chat may not exist yet)"
    echo "   Send any message to the bot and retry"
  fi
fi

# ── Restart bot ───────────────────────────────────────────────
if [ -f "bot.sh" ]; then
  echo ""
  echo "Restarting bot..."
  ./bot.sh restart
  echo "✅ Done! Bot is running with new token"
  echo ""
  echo "Test it: send /health to your bot"
else
  echo ""
  echo "⚠️  bot.sh not found — restart manually"
  echo "   systemctl restart trading-bot"
fi
