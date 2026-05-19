#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# remote_deploy.sh — Deploy from Google Drive (no sudo needed)
# 
# FIXES:
# 1. No sudo — uses ./bot.sh restart (runs as user)
# 2. Fixes PAPER_TRADING=false in .env after restore
# 3. Shows download path for verification
# 4. Keeps zip copy in ~/Desktop/ for manual checking
# ═══════════════════════════════════════════════════════════════

set -e
cd ~/Desktop/trading_robot

# Load env
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep '=' | xargs 2>/dev/null) 2>/dev/null
fi

GDRIVE_REMOTE="${GDRIVE_REMOTE:-gdrive}"
GDRIVE_FOLDER="${GDRIVE_FOLDER:-trading_robot}"
ZIP_NAME="trading_robot_FRESH.zip"
DOWNLOAD_PATH="$HOME/Desktop/${ZIP_NAME}"
TELEGRAM_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"

send_tg() {
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        curl -s -X POST "$TELEGRAM_URL" \
            -d chat_id="$TELEGRAM_CHAT_ID" \
            -d parse_mode="HTML" \
            -d text="$1" > /dev/null 2>&1
    fi
}

# ── Status check ──────────────────────────────────────────────
if [ "$1" = "--status" ]; then
    ANGEL_FIX=$(grep -c "ALWAYS connect for DATA" angel.py 2>/dev/null || echo "0")
    PAPER=$(grep "^PAPER_TRADING=" .env 2>/dev/null | cut -d= -f2)
    MIN_CAP=$(grep "^MIN_LIVE_CAPITAL=" .env 2>/dev/null | cut -d= -f2)
    send_tg "📊 <b>STATUS</b>
  Angel fix: $( [ "$ANGEL_FIX" -ge 1 ] && echo '✅' || echo '❌' )
  PAPER_TRADING: $PAPER
  MIN_LIVE_CAPITAL: $MIN_CAP
  Python files: $(ls *.py 2>/dev/null | wc -l)"
    exit 0
fi

# ── Diagnostics only ─────────────────────────────────────────
if [ "$1" = "--diag" ]; then
    source venv/bin/activate 2>/dev/null || true
    RESULT=$(python3 diag_scan.py 2>&1 | tail -20)
    send_tg "🔧 <b>DIAGNOSTIC</b>
<pre>$RESULT</pre>"
    exit 0
fi

# ── Main deploy ──────────────────────────────────────────────
echo "═══════════════════════════════════════════"
echo "DEPLOY — $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════════"

send_tg "🚀 <b>DEPLOY STARTED</b>
  Pulling from Google Drive..."

# Step 1: Pull from Drive
echo "[1/6] Pulling $ZIP_NAME from Drive..."
rclone copy "${GDRIVE_REMOTE}:${GDRIVE_FOLDER}/${ZIP_NAME}" "$HOME/Desktop/" 2>&1

if [ ! -f "$DOWNLOAD_PATH" ]; then
    echo "  ✗ File not found on Drive"
    send_tg "❌ <b>DEPLOY FAILED</b>
  File not found on Google Drive
  
  Upload steps:
  1. Download zip from Claude
  2. Upload to Google Drive → $GDRIVE_FOLDER/
  3. Send /deploy again"
    exit 1
fi

FILESIZE=$(du -h "$DOWNLOAD_PATH" | cut -f1)
echo "  ✓ Downloaded to: $DOWNLOAD_PATH ($FILESIZE)"

# Step 2: Backup .env
echo "[2/6] Backing up .env..."
cp .env .env.backup_$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
cp .env /tmp/.env_preserve

# Step 3: Extract
echo "[3/6] Extracting..."
unzip -o "$DOWNLOAD_PATH" -d ~/Desktop/trading_robot/ > /dev/null 2>&1
echo "  ✓ Extracted $(ls *.py | wc -l) Python files"

# Step 4: Restore .env AND fix critical settings
echo "[4/6] Restoring .env + fixing settings..."
cp /tmp/.env_preserve .env

# CRITICAL: Fix these settings every deploy
sed -i 's/^PAPER_TRADING=true/PAPER_TRADING=false/' .env
sed -i 's/^PAPER_TRADING=True/PAPER_TRADING=false/' .env

# Ensure MIN_LIVE_CAPITAL=0
if grep -q "^MIN_LIVE_CAPITAL=" .env; then
    sed -i 's/^MIN_LIVE_CAPITAL=.*/MIN_LIVE_CAPITAL=0/' .env
else
    echo "MIN_LIVE_CAPITAL=0" >> .env
fi

echo "  ✓ PAPER_TRADING=false, MIN_LIVE_CAPITAL=0"

# Step 5: Verify
echo "[5/6] Verifying..."
ANGEL_FIX=$(grep -c "ALWAYS connect for DATA" angel.py 2>/dev/null || echo "0")
PAPER=$(grep "^PAPER_TRADING=" .env | cut -d= -f2)
SYNTAX=$(python3 -c "
import ast,os
e=0
for f in os.listdir('.'):
 if f.endswith('.py'):
  try: ast.parse(open(f).read())
  except: e+=1
print(e)" 2>/dev/null || echo "?")

echo "  Angel fix: $ANGEL_FIX"
echo "  PAPER_TRADING: $PAPER"
echo "  Syntax errors: $SYNTAX"

# Step 6: Restart (NO sudo — use ./bot.sh which has sudo built in)
echo "[6/6] Restarting..."
./bot.sh restart 2>&1 | tail -3
sleep 3

echo ""
echo "═══════════════════════════════════════════"
echo "✅ DEPLOY COMPLETE"
echo "  File: $DOWNLOAD_PATH"
echo "  Angel fix: $( [ "$ANGEL_FIX" -ge 1 ] && echo 'YES' || echo 'NO' )"
echo "  PAPER_TRADING: $PAPER"
echo "═══════════════════════════════════════════"

send_tg "✅ <b>DEPLOY COMPLETE</b>

  📁 File: ~/Desktop/$ZIP_NAME
  📄 Files: $(ls *.py | wc -l) Python
  🔧 Angel fix: $( [ "$ANGEL_FIX" -ge 1 ] && echo '✅' || echo '❌' )
  📝 PAPER_TRADING: $PAPER
  📝 MIN_LIVE_CAPITAL: 0
  ⚠️ Syntax errors: $SYNTAX

  Send /fixangel to verify connection"

# Cleanup tmp only (keep Desktop copy)
rm -f /tmp/.env_preserve
