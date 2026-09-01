#!/usr/bin/env bash
# Reinstall systemd service files so the bot uses the verified .venv first.
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Repairing trading-bot systemd Python path..."
echo "Project: $BOT_DIR"

if [ ! -x "$BOT_DIR/.venv/bin/python3" ]; then
    echo "ERROR: .venv/bin/python3 not found or not executable"
    exit 1
fi

sudo cp "$BOT_DIR/trading-bot.service" /etc/systemd/system/trading-bot.service
sudo cp "$BOT_DIR/trading-bot-watchdog.service" /etc/systemd/system/trading-bot-watchdog.service
sudo systemctl daemon-reload
sudo systemctl restart trading-bot.service
sudo systemctl restart trading-bot-watchdog.service 2>/dev/null || true

echo "Done. Current ExecStart:"
systemctl cat trading-bot.service | grep -E "Environment=PATH|ExecStart="
