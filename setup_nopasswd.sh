#!/bin/bash
# setup_nopasswd.sh — Run ONCE to allow bot restart without password
# Usage: sudo bash setup_nopasswd.sh

echo "Setting up passwordless restart for trading-bot service..."
echo "sridhar ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart trading-bot" | sudo tee /etc/sudoers.d/trading-bot
echo "sridhar ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop trading-bot" | sudo tee -a /etc/sudoers.d/trading-bot
echo "sridhar ALL=(ALL) NOPASSWD: /usr/bin/systemctl start trading-bot" | sudo tee -a /etc/sudoers.d/trading-bot
sudo chmod 440 /etc/sudoers.d/trading-bot
echo "✅ Done — ./bot.sh restart will work without password"
