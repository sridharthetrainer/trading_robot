#!/bin/bash
# ════════════════════════════════════════════════════════════════
# bot_watchdog.sh — runs every minute via cron, restarts dead bot
#
# Install once:   bash install_watchdog.sh
# Uninstall:      crontab -e   (delete the bot_watchdog line)
# Logs:           ~/Desktop/trading_robot/watchdog.log
# ════════════════════════════════════════════════════════════════
BOT_DIR="/home/sridhar/Desktop/trading_robot"
BOT_CTL="$BOT_DIR/bot.sh"
MANUAL_TRACKER="$BOT_DIR/manual_trade_tracker.py"
AUTO_DEPLOY="$BOT_DIR/auto_deploy_watcher.py"
OPTION_RECORDER="$BOT_DIR/option_chain_recorder.py"
if [ -x "$BOT_DIR/.venv/bin/python3" ]; then
    VENV_PY="$BOT_DIR/.venv/bin/python3"
else
    VENV_PY="$BOT_DIR/venv/bin/python3"
fi
LOG="$BOT_DIR/watchdog.log"
STATE="$BOT_DIR/.watchdog_state"

cd "$BOT_DIR" 2>/dev/null || exit 0

# Load .env for Telegram
if [ -f .env ]; then
    set -a
    source .env 2>/dev/null
    set +a
fi

now() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(now)] $*" >> "$LOG"; }

send_tg() {
    [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && return
    [ -z "${TELEGRAM_CHAT_ID:-}" ] && return
    curl -s --max-time 10 -X POST \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "parse_mode=HTML" \
        --data-urlencode "text=$1" >/dev/null 2>&1
}

# Throttle alerts — don't spam if restart loop happens
last_restart_ts() { [ -f "$STATE" ] && cat "$STATE" 2>/dev/null || echo "0"; }
record_restart()  { date +%s > "$STATE"; }
seconds_since_last() {
    local last
    last=$(last_restart_ts)
    echo $(( $(date +%s) - last ))
}

# Is the bot running?
BOT_PID=$(pgrep -f "python3.*main_autonomous" 2>/dev/null | head -1)
if [ -n "$BOT_PID" ]; then
    # Keep companion services alive too.  Manual option protection and
    # auto-deploy are part of the autonomous runtime, not optional extras.
    if ! pgrep -f "$MANUAL_TRACKER" >/dev/null 2>&1; then
        log "Manual tracker is DEAD — starting via bot.sh"
        bash "$BOT_CTL" start >> "$LOG" 2>&1 || true
    fi
    if ! pgrep -f "$AUTO_DEPLOY" >/dev/null 2>&1; then
        log "Auto-deploy watcher is DEAD — starting via bot.sh"
        bash "$BOT_CTL" start >> "$LOG" 2>&1 || true
    fi
    if ! pgrep -f "$OPTION_RECORDER" >/dev/null 2>&1; then
        log "Option-chain recorder is DEAD — starting via bot.sh"
        bash "$BOT_CTL" start >> "$LOG" 2>&1 || true
    fi
    # Healthy — just touch heartbeat
    echo "$(now) alive pid=$BOT_PID" > "$BOT_DIR/.watchdog_heartbeat"
    exit 0
fi

# Bot is dead
log "Bot is DEAD — attempting restart"

# Throttle: don't restart more than once every 90 seconds
SINCE=$(seconds_since_last)
if [ "$SINCE" -lt 90 ]; then
    log "Skipping restart — last attempt was ${SINCE}s ago (cooldown 90s)"
    exit 0
fi

# Validate venv python exists
if [ ! -x "$VENV_PY" ]; then
    log "FATAL: venv python missing at $VENV_PY"
    send_tg "🚨 <b>WATCHDOG: venv python missing</b>%0AExpected: ${VENV_PY}%0ASSH required."
    exit 1
fi

# Quick syntax check before launching — don't restart broken code
SYNTAX=$("$VENV_PY" -c "
import ast, os
errs = 0
for f in os.listdir('$BOT_DIR'):
    if f.endswith('.py'):
        try: ast.parse(open(os.path.join('$BOT_DIR', f)).read())
        except SyntaxError: errs += 1
print(errs)
" 2>/dev/null)

if [ "$SYNTAX" != "0" ]; then
    log "Cannot restart — $SYNTAX syntax errors in bot dir"
    send_tg "🚨 <b>WATCHDOG: ${SYNTAX} syntax errors</b>%0ABot cannot restart. SSH required."
    record_restart
    exit 1
fi

# Restart the whole runtime. bot.sh handles systemd when available and falls
# back to direct nohup launches; it also starts the manual tracker and deploy
# watcher.
record_restart
log "Bot is dead — attempting restart"

RESTART_METHOD="unknown"

if bash "$BOT_CTL" restart >> "$LOG" 2>&1; then
    RESTART_METHOD="bot.sh"
    log "Restarted via bot.sh"
else
    log "bot.sh restart failed — launching nohup ${VENV_PY} main_autonomous.py"
    nohup "$VENV_PY" "$BOT_DIR/main_autonomous.py" >> "$BOT_DIR/trading_bot.log" 2>&1 &
    disown $! 2>/dev/null
    RESTART_METHOD="nohup-main-only"
fi

# Verify after 10 seconds
sleep 10
ALIVE=$(pgrep -f "python3.*main_autonomous" 2>/dev/null | head -1)
if [ -n "$ALIVE" ]; then
    log "Restart SUCCESS — pid $ALIVE method=$RESTART_METHOD"
    send_tg "🤖 <b>WATCHDOG: Bot restarted</b>%0APID: ${ALIVE}%0AMethod: ${RESTART_METHOD}"
else
    LAST_ERR=$(tail -15 "$BOT_DIR/trading_bot.log" 2>/dev/null | tail -c 500)
    log "Restart FAILED — bot died"
    log "Last error:"
    log "$LAST_ERR"
    send_tg "🚨 <b>WATCHDOG: Restart FAILED</b>%0A<pre>${LAST_ERR}</pre>%0ASSH required."
fi
