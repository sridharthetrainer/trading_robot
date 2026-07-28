#!/bin/bash
# bot.sh — Trading bot management (works with or without sudo)
SERVICE="trading-bot"
BOT_DIR="$HOME/Desktop/trading_robot"
MANUAL_TRACKER="$BOT_DIR/manual_trade_tracker.py"
AUTO_DEPLOY="$BOT_DIR/auto_deploy_watcher.py"
WATCHDOG="$BOT_DIR/watchdog.py"
OPTION_RECORDER="$BOT_DIR/option_chain_recorder.py"
OPTION_RECORDER_CTL="$BOT_DIR/run_option_snapshot_recorder.sh"

pick_python() {
    if [ -x "$BOT_DIR/.venv/bin/python3" ]; then
        echo "$BOT_DIR/.venv/bin/python3"
    elif [ -x "$BOT_DIR/venv/bin/python3" ]; then
        echo "$BOT_DIR/venv/bin/python3"
    else
        echo "python3"
    fi
}

main_running() {
    [ -n "$(pid_for_script "main_autonomous.py")" ]
}

pid_for_script() {
    local script="$1"
    ps -eo pid=,comm=,args= | awk -v s="$script" '
        $2 ~ /^python/ && index($0, s) {
            print $1
            exit
        }
    '
}

system_service_enabled() {
    systemctl is-enabled "$1.service" >/dev/null 2>&1
}

start_bot() {
    if main_running; then
        return 0
    fi
    if system_service_enabled "$SERVICE"; then
        sudo -n systemctl start "$SERVICE.service" 2>/dev/null || true
        sleep 3
        main_running && return 0
    else
        systemctl --user start "$SERVICE" 2>/dev/null && return 0
    fi

    cd "$BOT_DIR"
    PYTHON="$(pick_python)"
    nohup "$PYTHON" main_autonomous.py >> trading_bot.log 2>&1 &
    echo "Started PID: $!"
}

# Try systemctl without sudo first (works if user has permissions)
# Then try with sudo (works if password cached or NOPASSWD configured)
# Then fall back to direct python restart (always works)
restart_bot() {
    if system_service_enabled "$SERVICE"; then
        sudo -n systemctl restart "$SERVICE.service" 2>/dev/null && return 0
        # No sudo: terminate the user-owned bot and let systemd Restart=always
        # bring the enabled system service back.
        pkill -f "python3.*main_autonomous.py|python.*main_autonomous.py" 2>/dev/null || true
        sleep 12
        main_running && return 0
    else
        systemctl --user restart "$SERVICE" 2>/dev/null && return 0
    fi

    echo "Using direct restart (no systemd)..."
    pkill -f "python3.*main_autonomous.py|python.*main_autonomous.py" 2>/dev/null || true
    sleep 2
    cd "$BOT_DIR"
    PYTHON="$(pick_python)"
    nohup "$PYTHON" main_autonomous.py >> trading_bot.log 2>&1 &
    echo "Bot PID: $!"
    return 0
}

start_manual_tracker() {
    cd "$BOT_DIR"
    PYTHON="$(pick_python)"
    if [ -n "$(pid_for_script "manual_trade_tracker.py")" ]; then
        return 0
    fi
    if system_service_enabled "manual-tracker"; then
        sudo -n systemctl start manual-tracker.service 2>/dev/null || true
    else
        systemctl --user start manual-tracker 2>/dev/null || true
    fi
    sleep 1
    [ -n "$(pid_for_script "manual_trade_tracker.py")" ] && return 0
    nohup "$PYTHON" "$MANUAL_TRACKER" >> "$BOT_DIR/manual_tracker.log" 2>&1 &
    echo "Manual tracker PID: $!"
}

stop_manual_tracker() {
    systemctl --user stop manual-tracker 2>/dev/null || true
    pkill -f "$MANUAL_TRACKER" 2>/dev/null || true
}

start_auto_deploy() {
    stop_auto_deploy
    echo "Google Drive auto-deploy disabled — use remote_deploy.sh manually"
}

stop_auto_deploy() {
    systemctl --user stop auto-deploy 2>/dev/null || true
    pkill -f "$AUTO_DEPLOY" 2>/dev/null || true
}

start_watchdog() {
    cd "$BOT_DIR"
    PYTHON="$(pick_python)"
    if [ -n "$(pid_for_script "watchdog.py")" ]; then
        return 0
    fi
    if system_service_enabled "trading-bot-watchdog"; then
        sudo -n systemctl start trading-bot-watchdog.service 2>/dev/null || true
    else
        systemctl --user start trading-bot-watchdog 2>/dev/null || true
    fi
    sleep 1
    [ -n "$(pid_for_script "watchdog.py")" ] && return 0
    nohup "$PYTHON" "$WATCHDOG" >> "$BOT_DIR/watchdog.log" 2>&1 &
    echo "Watchdog PID: $!"
}

start_option_recorder() {
    cd "$BOT_DIR"
    PYTHON="$(pick_python)"
    if [ -n "$(pid_for_script "option_chain_recorder.py")" ]; then
        return 0
    fi
    if [ -x "$OPTION_RECORDER_CTL" ]; then
        nohup "$OPTION_RECORDER_CTL" >> "$BOT_DIR/option_chain_recorder.log" 2>&1 &
    else
        nohup "$PYTHON" "$OPTION_RECORDER" --loop >> "$BOT_DIR/option_chain_recorder.log" 2>&1 &
    fi
    echo "Option-chain recorder PID: $!"
}

stop_option_recorder() {
    pkill -f "$OPTION_RECORDER" 2>/dev/null || true
    pkill -f "$OPTION_RECORDER_CTL" 2>/dev/null || true
}

# Pre-launch smoke check: catch import-time crashes (missing imports, broken
# class-body code, etc.) BEFORE launching, so a bad commit fails loudly instead
# of crash-looping the service. main_autonomous's run() is gated under __main__,
# so importing the module is side-effect-free (no Angel connect, no trading).
smoke_check() {
    cd "$BOT_DIR"
    PYTHON="$(pick_python)"
    if ! "$PYTHON" -c "import main_autonomous" 2>/tmp/bot_smoke_err.txt; then
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
        start_bot
        start_manual_tracker
        start_option_recorder
        start_watchdog
        echo "✓ Bot started"
        ;;
    stop)
        echo "Stopping trading bot..."
        systemctl --user stop $SERVICE 2>/dev/null ||         sudo -n systemctl stop $SERVICE 2>/dev/null ||         pkill -f "python3.*main_autonomous" 2>/dev/null
        stop_manual_tracker
        stop_auto_deploy
        stop_option_recorder
        systemctl --user stop trading-bot-watchdog 2>/dev/null || true
        pkill -f "$WATCHDOG" 2>/dev/null || true
        echo "✓ Bot stopped"
        ;;
    restart)
        echo "Restarting trading bot..."
        smoke_check || exit 1
        restart_bot
        stop_manual_tracker
        stop_auto_deploy
        stop_option_recorder
        sleep 1
        start_manual_tracker
        start_option_recorder
        start_watchdog
        echo "✓ Bot restarted successfully"
        ;;
    logs)
        echo "Live logs (Ctrl+C to stop):"
        if system_service_enabled "$SERVICE"; then
            journalctl -u "$SERVICE.service" -f 2>/dev/null || tail -f "$BOT_DIR/trading_bot.log"
        else
            journalctl --user -u "$SERVICE.service" -f 2>/dev/null || tail -f "$BOT_DIR/trading_bot.log"
        fi
        ;;
    status)
        if system_service_enabled "$SERVICE"; then
            systemctl status "$SERVICE.service" --no-pager 2>/dev/null || true
        else
            systemctl --user status "$SERVICE.service" --no-pager 2>/dev/null || true
        fi

        MAIN_PID="$(pid_for_script "main_autonomous.py")"
        MANUAL_PID="$(pid_for_script "manual_trade_tracker.py")"
        OPTION_REC_PID="$(pid_for_script "option_chain_recorder.py")"
        WATCHDOG_PID="$(pid_for_script "watchdog.py")"

        [ -n "$MAIN_PID" ] && echo "Bot: running (PID: $MAIN_PID)" || echo "Bot: NOT running"
        [ -n "$MANUAL_PID" ] && echo "Manual tracker: running (PID: $MANUAL_PID)" || echo "Manual tracker: NOT running"
        echo "Auto-deploy: DISABLED (manual deployment only)"
        [ -n "$OPTION_REC_PID" ] && echo "Option recorder: running (PID: $OPTION_REC_PID)" || echo "Option recorder: NOT running"
        [ -n "$WATCHDOG_PID" ] && echo "Watchdog: running (PID: $WATCHDOG_PID)" || echo "Watchdog: NOT running"
        ;;
    test)
        echo "Running system tests..."
        cd "$BOT_DIR"
        PYTHON="$(pick_python)"
        "$PYTHON" - <<'PY'
import ast
import os

errs = 0
files = [f for f in os.listdir(".") if f.endswith(".py")]
for f in files:
    try:
        ast.parse(open(f, encoding="utf-8").read())
    except SyntaxError:
        errs += 1
        print(f"  ❌ {f}")
print(f"  Files: {len(files)}")
print(f"  Errors: {errs}")
print("  ✅ All tests passed" if errs == 0 else "  ❌ Fix errors above")
PY
        ;;
    *)
        echo "Usage: ./bot.sh {start|stop|restart|logs|status|test}"
        ;;
esac
