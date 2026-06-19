#!/bin/bash
# ════════════════════════════════════════════════════════════════
# install_watchdog.sh — install bot_watchdog as user crontab entry
#
# Run once:  bash install_watchdog.sh
# No sudo needed — uses user crontab.
# ════════════════════════════════════════════════════════════════
set -e
BOT_DIR="/home/sridhar/Desktop/trading_robot"
WATCHDOG="$BOT_DIR/bot_watchdog.sh"

if [ ! -f "$WATCHDOG" ]; then
    echo "✗ Watchdog not found at $WATCHDOG"
    exit 1
fi
chmod +x "$WATCHDOG"

# Check if already installed
if crontab -l 2>/dev/null | grep -q "bot_watchdog.sh"; then
    echo "✓ Watchdog already installed in crontab"
    echo ""
    echo "Current cron entries:"
    crontab -l 2>/dev/null | grep bot_watchdog
    exit 0
fi

# Add cron entry — every minute
( crontab -l 2>/dev/null; echo "* * * * * $WATCHDOG >/dev/null 2>&1" ) | crontab -

echo "✓ Watchdog installed — will check bot every minute"
echo ""
echo "Verify:"
crontab -l 2>/dev/null | grep bot_watchdog
echo ""
echo "Logs: $BOT_DIR/watchdog.log"
echo "Disable: crontab -e   (delete the bot_watchdog line)"
