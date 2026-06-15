#!/bin/bash
echo "🔄 Deploying from ZIP..."
echo ""

# Find latest ZIP
LATEST_ZIP=$(ls -t ~/Downloads/trading_robot_*.zip 2>/dev/null | head -1)

if [ -z "$LATEST_ZIP" ]; then
    echo "❌ No ZIP found in ~/Downloads/"
    exit 1
fi

echo "📦 Found: $LATEST_ZIP"
echo "⏸️  Stopping services..."
sudo systemctl stop autonomous-bot.service system-bot.service trades-bot.service 2>/dev/null || true

echo "💾 Backing up database..."
mkdir -p .deploy_backups
cp data/manual_trades.json .deploy_backups/backup_$(date +%Y%m%d_%H%M%S).json 2>/dev/null || true

echo "📥 Extracting new version..."
TMPEXT="/tmp/extract_$$"
mkdir -p "$TMPEXT"
unzip -q "$LATEST_ZIP" -d "$TMPEXT"

# Copy files (preserve .env, data, logs, venv)
for item in "$TMPEXT"/trading_robot/*; do
    FNAME=$(basename "$item")
    if [[ ! "$FNAME" =~ ^(\.env|data|logs|venv|\.deploy_backups)$ ]]; then
        rm -rf "$FNAME"
        cp -r "$item" .
    fi
done

rm -rf "$TMPEXT"

echo "🔄 Restarting services..."
sudo systemctl start autonomous-bot.service system-bot.service trades-bot.service

echo ""
echo "✅ Deployment complete!"
echo "   - Database preserved"
echo "   - Services restarted"
echo ""
