#!/bin/bash
# Add to crontab: */5 * * * * /home/user/trading_robot/check_standalone.sh
# This runs standalone_runner every 5 min when main bot is offline

cd /home/$(whoami)/Desktop/trading_robot || exit 1
python3 standalone_runner.py >> standalone.log 2>&1
