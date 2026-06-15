#!/bin/bash
# bot.sh — Trading bot management (works with or without sudo)
SERVICE="trading-bot"
BOT_DIR="$HOME/Desktop/trading_robot"

# Try systemctl without sudo first (works if user has permissions)
# Then try with sudo (works if password cached or NOPASSWD configured)
# Then fall back to direct python restart (always works)
restart_bot() {
    # Method 1: systemctl --user (no sudo)
    systemctl --user restart $SERVICE 2>/dev/null && return 0
    
    # Method 2: sudo systemctl (needs password or NOPASSWD)
    sudo -n systemctl restart $SERVICE 2>/dev/null && return 0
    
    # Method 3: Direct kill + restart (always works, no sudo)
    echo "Using direct restart (no systemd)..."
    pkill -f "python3.*main_autonomous" 2>/dev/null || true
    sleep 2
    cd "$BOT_DIR"
    source venv/bin/activate 2>/dev/null || true
    nohup python3 main_autonomous.py >> trading_bot.log 2>&1 &
    echo "Bot PID: $!"
    return 0
}

# Pre-launch smoke check: catch import-time crashes (missing imports, broken
# class-body code, etc.) BEFORE launching, so a bad commit fails loudly instead
# of crash-looping the service. main_autonomous's run() is gated under __main__,
# so importing the module is side-effect-free (no Angel connect, no trading).
smoke_check() {
    cd "$BOT_DIR"
    source venv/bin/activate 2>/dev/null || true
    if ! python3 -c "import main_autonomous" 2>/tmp/bot_smoke_err.txt; then
        echo "❌ Smoke check FAILED — import error; NOT (re)starting the bot:"
        tail -6 /tmp/bot_smoke_err.txt | sed 's/^/    /'
        return 1
    fi
    echo "✓ Smoke check passed (imports clean)"
    return 0
}

case "$1" in
    start)
        echo "Starting trading bot..."
        smoke_check || exit 1
        systemctl --user start $SERVICE 2>/dev/null ||         sudo -n systemctl start $SERVICE 2>/dev/null ||         { cd "$BOT_DIR"; source venv/bin/activate 2>/dev/null; nohup python3 main_autonomous.py >> trading_bot.log 2>&1 & echo "Started PID: $!"; }
        echo "✓ Bot started"
        ;;
    stop)
        echo "Stopping trading bot..."
        systemctl --user stop $SERVICE 2>/dev/null ||         sudo -n systemctl stop $SERVICE 2>/dev/null ||         pkill -f "python3.*main_autonomous" 2>/dev/null
        echo "✓ Bot stopped"
        ;;
    restart)
        echo "Restarting trading bot..."
        smoke_check || exit 1
        restart_bot
        echo "✓ Bot restarted successfully"
        ;;
    logs)
        echo "Live logs (Ctrl+C to stop):"
        journalctl -u $SERVICE -f 2>/dev/null || tail -f "$BOT_DIR/trading_bot.log"
        ;;
    status)
        systemctl --user status $SERVICE 2>/dev/null ||         sudo -n systemctl status $SERVICE 2>/dev/null ||         { pgrep -f "main_autonomous" > /dev/null && echo "Bot is running (PID: $(pgrep -f main_autonomous))" || echo "Bot is NOT running"; }
        ;;
    test)
        echo "Running system tests..."
        cd "$BOT_DIR"
        source venv/bin/activate 2>/dev/null || true
        python3 -c "
import ast, os
errs = 0
for f in os.listdir('.'):
    if f.endswith('.py'):
        try: ast.parse(open(f).read())
        except: errs += 1; print(f'  ❌ {f}')
print(f'  Files: {len([f for f in os.listdir(".") if f.endswith(".py")])}')
print(f'  Errors: {errs}')
print('  ✅ All tests passed' if errs == 0 else '  ❌ Fix errors above')
"
        ;;
    *)
        echo "Usage: ./bot.sh {start|stop|restart|logs|status|test}"
        ;;
esac
