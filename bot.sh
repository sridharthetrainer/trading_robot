#!/bin/bash
# bot.sh — Trading bot control script
# Usage: ./bot.sh [start|stop|restart|logs|status|test]

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$BOT_DIR/venv/bin/python3"
SERVICE="trading-bot"

case "${1:-restart}" in

  start)
    echo "Starting trading bot..."
    sudo systemctl start $SERVICE
    sleep 3
    if systemctl is-active --quiet $SERVICE; then
      echo "✓ Bot started successfully"
    else
      echo "✗ Start failed — check: journalctl -u $SERVICE -n 20"
    fi
    ;;

  stop)
    echo "Stopping trading bot..."
    sudo systemctl stop $SERVICE trading-bot-watchdog 2>/dev/null || true
    echo "✓ Bot stopped"
    ;;

  restart)
    echo "Restarting trading bot..."
    sudo systemctl reset-failed $SERVICE 2>/dev/null || true
    sudo systemctl stop $SERVICE trading-bot-watchdog 2>/dev/null || true
    sleep 2
    sudo systemctl start $SERVICE
    sleep 4
    if systemctl is-active --quiet $SERVICE; then
      echo "✓ Bot restarted successfully"
    else
      echo "✗ Restart failed. Last 20 lines:"
      journalctl -u $SERVICE -n 20 --no-pager
    fi
    ;;

  logs)
    echo "Live logs (Ctrl+C to stop):"
    journalctl -u $SERVICE -f --no-pager
    ;;

  status)
    systemctl status $SERVICE --no-pager
    ;;

  test)
    # Quick syntax + import test without systemd
    echo "Testing bot startup..."
    cd "$BOT_DIR"
    $VENV -c "
import sys, os
os.chdir('$BOT_DIR')
sys.path.insert(0, '$BOT_DIR')
from dotenv import load_dotenv
load_dotenv('.env', override=True)
print('✅ dotenv loaded')

# Test critical imports
import config; print(f'✅ config | PAPER={config.PAPER_TRADING}')
import angel;  print(f'✅ angel  | class={angel.AngelOne.__name__}')
import yf_compat; print('✅ yf_compat loaded')
import data_fetcher; print('✅ data_fetcher loaded')
import signal_engine; print('✅ signal_engine loaded')
print()
print('✅ All critical imports OK — bot can start')
" 2>&1
    ;;

  *)
    echo "Usage: ./bot.sh [start|stop|restart|logs|status|test]"
    ;;

esac
