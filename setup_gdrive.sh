#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_gdrive.sh — One-time Google Drive sync setup
# Run this ONCE from your trading_robot folder
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   GOOGLE DRIVE SYNC — ONE-TIME SETUP                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Step 1: Install rclone
echo "Step 1: Installing rclone..."
if command -v rclone &>/dev/null; then
    echo "  ✅ rclone already installed"
else
    sudo apt-get update -qq && sudo apt-get install -y rclone
    echo "  ✅ rclone installed"
fi

# Step 2: Configure (interactive)
echo ""
echo "Step 2: Configuring Google Drive..."
echo "  → When prompted:"
echo "     Name: gdrive"
echo "     Type: drive (Google Drive)"
echo "     Scope: 1 (full access)"
echo "     Everything else: press Enter (defaults)"
echo "     Auto-config: y (opens browser — login with your Google account)"
echo ""
read -p "Press Enter to start rclone config..."
rclone config

# Step 3: Test connection
echo ""
echo "Step 3: Testing connection..."
if rclone ls gdrive: &>/dev/null; then
    echo "  ✅ Google Drive connected successfully!"
else
    echo "  ❌ Connection failed. Check rclone config and try again."
    exit 1
fi

# Step 4: Create folder structure on Drive
echo ""
echo "Step 4: Creating folder structure on Google Drive..."
FOLDER="trading_robot"
rclone mkdir gdrive:${FOLDER}/code
rclone mkdir gdrive:${FOLDER}/data
rclone mkdir gdrive:${FOLDER}/config
rclone mkdir gdrive:${FOLDER}/reports/daily_pnl
rclone mkdir gdrive:${FOLDER}/reports/weekly
echo "  ✅ Folders created: gdrive:${FOLDER}/"

# Step 5: Add to .env
echo ""
echo "Step 5: Adding to .env..."
ENV_FILE="$(pwd)/.env"
if [ -f "$ENV_FILE" ]; then
    if ! grep -q "GDRIVE_REMOTE" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "# Google Drive Sync" >> "$ENV_FILE"
        echo "GDRIVE_REMOTE=gdrive" >> "$ENV_FILE"
        echo "GDRIVE_FOLDER=trading_robot" >> "$ENV_FILE"
        echo "  ✅ Added to .env"
    else
        echo "  ✅ Already in .env"
    fi
else
    echo "  ⚠️ .env not found — add manually:"
    echo "     GDRIVE_REMOTE=gdrive"
    echo "     GDRIVE_FOLDER=trading_robot"
fi

# Step 6: Initial upload
echo ""
echo "Step 6: Uploading all code to Google Drive..."
# Upload ONLY code files — skip venv, cache, binaries
rclone copy "$(pwd)" gdrive:${FOLDER}/code \
    --include "*.py" \
    --exclude "venv/**" \
    --exclude ".venv/**" \
    --exclude "__pycache__/**" \
    --exclude "*.pyc" \
    --exclude "*.so" \
    --exclude "node_modules/**" \
    --exclude ".git/**" \
    --transfers 8 \
    --progress
echo "  ✅ Python code uploaded to Drive (venv/cache skipped)"

# Upload data files separately
for f in trades.db signal_log.csv nifty200.csv .env; do
    [ -f "$f" ] && rclone copy "$f" gdrive:${FOLDER}/config/ --update
done
echo "  ✅ Data + config files uploaded"

# Step 7: Restart bot
echo ""
echo "Step 7: Restarting bot to enable sync watcher..."
if command -v systemctl &>/dev/null && systemctl is-active trading-bot.service &>/dev/null; then
    sudo systemctl restart trading-bot.service
    echo "  ✅ Bot restarted"
else
    echo "  ℹ️  Run ./bot.sh restart manually"
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ✅ SETUP COMPLETE!                                  ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║                                                       ║"
echo "║  SYSTEM → DRIVE  (automatic every 5 min)             ║"
echo "║  DRIVE  → SYSTEM (automatic every 5 min + /deploy)  ║"
echo "║                                                       ║"
echo "║  TO EDIT CODE REMOTELY:                               ║"
echo "║  1. Open Google Drive on phone/laptop                 ║"
echo "║  2. Go to trading_robot/code/                         ║"
echo "║  3. Edit any .py file                                 ║"
echo "║  4. Send /deploy on Telegram                          ║"
echo "║     OR wait 5 min for auto-deploy                     ║"
echo "║                                                       ║"
echo "║  TELEGRAM COMMANDS:                                   ║"
echo "║  /sync   — manual sync both directions                ║"
echo "║  /deploy — pull from Drive + restart immediately      ║"
echo "║  /cloud  — view sync status                           ║"
echo "╚══════════════════════════════════════════════════════╝"
