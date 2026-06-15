#!/bin/bash
# ════════════════════════════════════════════════════════════════
# remote_deploy.sh — called by auto_deploy_watcher when new zip
# appears on Drive. Delegates to do_deploy.sh which has the safe
# pre-validate + rollback logic.
# ════════════════════════════════════════════════════════════════
BOT_DIR="/home/sridhar/Desktop/trading_robot"
cd "$BOT_DIR" || exit 1

if [ -f .env ]; then
    set -a
    source .env 2>/dev/null
    set +a
fi

# Status check
if [ "${1:-}" = "--status" ]; then
    ANGEL_FIX=$(grep -c "ALWAYS connect for DATA" angel.py 2>/dev/null || echo "0")
    PAPER=$(grep "^PAPER_TRADING=" .env 2>/dev/null | cut -d= -f2)
    MIN_CAP=$(grep "^MIN_LIVE_CAPITAL=" .env 2>/dev/null | cut -d= -f2)
    PID=$(pgrep -f "python3.*main_autonomous" 2>/dev/null | head -1)
    BOT_STATUS=$( [ -n "$PID" ] && echo "running (pid $PID)" || echo "DEAD" )
    MSG="📊 <b>STATUS</b>%0ABot: ${BOT_STATUS}%0AAngel fix: $( [ \"$ANGEL_FIX\" -ge 1 ] && echo '✅' || echo '❌' )%0APAPER_TRADING: ${PAPER}%0AMIN_LIVE_CAPITAL: ${MIN_CAP}%0APython files: $(ls *.py 2>/dev/null | wc -l)"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "parse_mode=HTML" \
            --data-urlencode "text=${MSG}" >/dev/null 2>&1
    fi
    exit 0
fi

# Diag only
if [ "${1:-}" = "--diag" ]; then
    OUT=$("$BOT_DIR/venv/bin/python3" "$BOT_DIR/diag_scan.py" 2>&1 | tail -25)
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "parse_mode=HTML" \
            --data-urlencode "text=🔧 <b>DIAGNOSTIC</b>%0A<pre>${OUT}</pre>" >/dev/null 2>&1
    fi
    exit 0
fi

# Main deploy — delegate to safe do_deploy.sh
exec bash "$BOT_DIR/do_deploy.sh"
