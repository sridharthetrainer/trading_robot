#!/bin/bash
# fix_webhook.sh — Clear Telegram webhook so polling works
# Run this if bot sends messages but doesn't respond to commands
# Usage: ./fix_webhook.sh

set -e

# Read token from .env
if [ ! -f ".env" ]; then
  echo "❌ .env not found — run from trading_robot folder"
  exit 1
fi

TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")

if [ -z "$TOKEN" ] || [ "$TOKEN" = "REPLACE_WITH_YOUR_TOKEN_FROM_BOTFATHER" ]; then
  echo "❌ TELEGRAM_BOT_TOKEN not set in .env"
  echo "   Edit .env and set your bot token, then retry"
  exit 1
fi

echo "Token: ${TOKEN:0:25}..."
echo ""

# Check current webhook
echo "Checking webhook status..."
WH_INFO=$(curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo")
WH_URL=$(echo "$WH_INFO" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('url',''))" 2>/dev/null)

if [ -n "$WH_URL" ]; then
  echo "⚠️  Webhook is SET to: $WH_URL"
  echo "   This BLOCKS getUpdates (polling) — deleting it now..."
  
  DEL=$(curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook?drop_pending_updates=true")
  OK=$(echo "$DEL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ok','false'))" 2>/dev/null)
  
  if [ "$OK" = "True" ] || [ "$OK" = "true" ]; then
    echo "✅ Webhook deleted — polling mode now active"
  else
    echo "❌ Failed to delete webhook: $DEL"
    exit 1
  fi
else
  echo "✅ No webhook set — polling mode is already active"
  echo "   If commands still don't work, the issue is the token"
fi

echo ""
echo "Restarting bot to apply changes..."
./bot.sh restart

echo ""
echo "✅ Done! Now send /health to your bot"
echo "   You should get a response within 3 seconds"
