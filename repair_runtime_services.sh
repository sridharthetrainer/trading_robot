#!/usr/bin/env bash
# Repair/install systemd services from the checked-in service files.
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${BOT_DIR}/.venv/bin/python3"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: expected Python at $PYTHON"
    exit 1
fi

install_unit() {
    local unit="$1"
    if [ ! -f "${BOT_DIR}/${unit}" ]; then
        echo "ERROR: missing ${BOT_DIR}/${unit}"
        exit 1
    fi
    sudo cp "${BOT_DIR}/${unit}" "/etc/systemd/system/${unit}"
    echo "installed ${unit}"
}

install_unit trading-bot.service
install_unit trading-bot-watchdog.service
install_unit manual-tracker.service
install_unit daily-pipeline.service
install_unit daily-pipeline.timer
install_unit post-market-ml.service
install_unit post-market-ml.timer
install_unit trade_guardian.service

sudo systemctl daemon-reload
sudo systemctl disable --now auto-deploy.service 2>/dev/null || true
sudo systemctl enable trading-bot.service trading-bot-watchdog.service manual-tracker.service
sudo systemctl enable daily-pipeline.timer post-market-ml.timer
sudo systemctl restart trading-bot.service trading-bot-watchdog.service manual-tracker.service
sudo systemctl restart daily-pipeline.timer post-market-ml.timer

echo
echo "Runtime services repaired. Current status:"
systemctl status trading-bot.service manual-tracker.service daily-pipeline.timer post-market-ml.timer --no-pager --lines=8
