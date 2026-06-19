#!/bin/bash
set -e
echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║        🚀 TRADING BOT - FIRST TIME SETUP (AUTO)               ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Verify we're in correct directory
if [ ! -f "symbols.py" ]; then
    echo "❌ ERROR: Not in trading_robot directory"
    exit 1
fi

echo "Step 1: Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "Step 2: Installing dependencies..."
pip install -q --break-system-packages -r requirements.txt

echo "Step 3: Creating directories..."
mkdir -p data logs .deploy_backups

echo "Step 4: Verifying symbols..."
python3 -c "from symbols import get_symbol_count; assert get_symbol_count() == 198; print('✅ Verified: 198 symbols')"

echo "Step 5: Testing autonomous bot..."
python3 autonomous_bot.py

echo "Step 6: Starting systemd services..."
sudo bash -c 'cat > /etc/systemd/system/autonomous-bot.service << "EOF"
[Unit]
Description=Trading Autonomous Bot
After=network.target

[Service]
Type=simple
User=sridhar
WorkingDirectory=/home/sridhar/Desktop/trading_robot
ExecStart=/home/sridhar/Desktop/trading_robot/venv/bin/python3 autonomous_bot.py
Restart=always
RestartSec=10
StandardOutput=append:logs/autonomous_bot.log
StandardError=append:logs/autonomous_bot.log

[Install]
WantedBy=multi-user.target
EOF'

sudo bash -c 'cat > /etc/systemd/system/system-bot.service << "EOF"
[Unit]
Description=Trading System Bot
After=network.target

[Service]
Type=simple
User=sridhar
WorkingDirectory=/home/sridhar/Desktop/trading_robot
ExecStart=/home/sridhar/Desktop/trading_robot/venv/bin/python3 system_bot.py
Restart=always
RestartSec=10
StandardOutput=append:logs/system_bot.log

[Install]
WantedBy=multi-user.target
EOF'

sudo bash -c 'cat > /etc/systemd/system/trades-bot.service << "EOF"
[Unit]
Description=Trading Trades Bot
After=network.target

[Service]
Type=simple
User=sridhar
WorkingDirectory=/home/sridhar/Desktop/trading_robot
ExecStart=/home/sridhar/Desktop/trading_robot/venv/bin/python3 trades_bot.py
Restart=always
RestartSec=10
StandardOutput=append:logs/trades_bot.log

[Install]
WantedBy=multi-user.target
EOF'

sudo systemctl daemon-reload
sudo systemctl enable autonomous-bot.service system-bot.service trades-bot.service
sudo systemctl start autonomous-bot.service system-bot.service trades-bot.service

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║              ✅ SETUP COMPLETE - ALL RUNNING                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "✅ Services running:"
sudo systemctl status autonomous-bot.service --no-pager | grep Active
sudo systemctl status system-bot.service --no-pager | grep Active
sudo systemctl status trades-bot.service --no-pager | grep Active
echo ""
echo "Commands:"
echo "  /deploy   - Deploy new version (in System Bot)"
echo "  /status   - Check status (in System Bot)"
echo "  /trades   - Show trades (in Trades Bot)"
echo "  /pnl      - Show profit/loss (in Trades Bot)"
echo ""
