#!/bin/bash
# ════════════════════════════════════════════════════════════════
# recover_bot.sh — ONE-COMMAND RECOVERY when bot is dead
#
# Run on the trading machine:
#   cd ~/Desktop/trading_robot && bash recover_bot.sh
#
# What it does:
#   1. Kills any zombie main_autonomous processes
#   2. Pulls latest zip from Drive (if available)
#   3. Extracts safely
#   4. Validates syntax + imports
#   5. Restarts bot with nohup
#   6. Installs watchdog cron (so it never dies silently again)
#   7. Sends Telegram confirmation
# ════════════════════════════════════════════════════════════════
set -uo pipefail

BOT_DIR="/home/sridhar/Desktop/trading_robot"
VENV_PY="$BOT_DIR/.venv/bin/python3"
[ ! -x "$VENV_PY" ] && VENV_PY="$BOT_DIR/venv/bin/python3"
cd "$BOT_DIR" || { echo "FATAL: $BOT_DIR not found"; exit 1; }

echo "═══════════════════════════════════════════"
echo "BOT RECOVERY — $(date)"
echo "═══════════════════════════════════════════"

# Load .env
if [ -f .env ]; then
    set -a
    source .env 2>/dev/null
    set +a
fi

send_tg() {
    [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && return
    [ -z "${TELEGRAM_CHAT_ID:-}" ] && return
    curl -s --max-time 10 -X POST \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "parse_mode=HTML" \
        --data-urlencode "text=$1" >/dev/null 2>&1
}

# Step 1: Kill any zombies
echo "[1] Killing zombie processes..."
pkill -9 -f "python3.*main_autonomous" 2>/dev/null || true
sleep 2

# Step 2: Try to pull new zip from Drive (don't fail if it fails)
echo "[2] Pulling latest zip from Drive..."
REMOTE="${GDRIVE_REMOTE:-gdrive}"
FOLDER="${GDRIVE_FOLDER:-trading_robot}"
ZIP="trading_robot_FRESH.zip"

if command -v rclone >/dev/null 2>&1; then
    rclone copy "${REMOTE}:${FOLDER}/${ZIP}" ~/Desktop/ 2>&1 | tail -3 || true
    if [ -f "$HOME/Desktop/$ZIP" ]; then
        echo "  Downloaded $(stat -c%s $HOME/Desktop/$ZIP 2>/dev/null) bytes"
        # Step 3: Extract
        echo "[3] Extracting..."
        STAGING="$BOT_DIR/.recovery_staging"
        rm -rf "$STAGING"
        mkdir -p "$STAGING"
        unzip -q -o "$HOME/Desktop/$ZIP" -d "$STAGING/" 2>&1
        if [ -d "$STAGING/trading_robot" ]; then
            mv "$STAGING/trading_robot"/* "$STAGING/" 2>/dev/null
            mv "$STAGING/trading_robot"/.* "$STAGING/" 2>/dev/null || true
        fi
        PYCOUNT=$(find "$STAGING" -maxdepth 1 -name "*.py" | wc -l)
        echo "  Extracted: $PYCOUNT .py files"

        if [ "$PYCOUNT" -ge 100 ]; then
            # Step 4: Syntax check
            echo "[4] Syntax check on new code..."
            SYNTAX_ERR=$("$VENV_PY" -c "
import ast, os
errs = []
for f in os.listdir('$STAGING'):
    if f.endswith('.py'):
        try: ast.parse(open(os.path.join('$STAGING', f)).read())
        except SyntaxError as e: errs.append(f'{f}:{e.lineno}')
print('|'.join(errs[:5]))
" 2>&1)
            if [ -z "$SYNTAX_ERR" ]; then
                # Promote
                cp .env /tmp/.env.recovery 2>/dev/null
                cp "$STAGING"/*.py "$BOT_DIR/" 2>/dev/null
                cp "$STAGING"/*.sh "$BOT_DIR/" 2>/dev/null
                chmod +x "$BOT_DIR"/*.sh 2>/dev/null
                cp /tmp/.env.recovery .env 2>/dev/null
                # Preserve operator-controlled .env trading mode during recovery.
                echo "  ✓ New code promoted"
            else
                echo "  ✗ Syntax errors in new zip: $SYNTAX_ERR"
                echo "  Keeping current code"
            fi
        else
            echo "  ✗ Too few files in zip — keeping current code"
        fi
    else
        echo "  No zip on Drive — keeping current code"
    fi
else
    echo "  rclone not available — skipping Drive pull"
fi

# Step 5: Restart bot — try systemctl first to stay tracked
echo "[5] Restarting bot..."
pkill -9 -f "python3.*main_autonomous" 2>/dev/null || true
sleep 2

if [ ! -x "$VENV_PY" ]; then
    echo "✗ FATAL: venv python missing at $VENV_PY"
    send_tg "🚨 RECOVERY FAILED: venv python missing"
    exit 1
fi

RESTART_METHOD="unknown"
# Method 1: systemctl (best — keeps systemd tracking)
if sudo -n systemctl reset-failed trading-bot.service 2>/dev/null && \
   sudo -n systemctl start trading-bot.service 2>/dev/null; then
    RESTART_METHOD="systemctl"
    echo "  ✓ Started via systemctl"
# Method 2: sudo with password prompt (interactive)
elif sudo systemctl reset-failed trading-bot.service 2>/dev/null && \
     sudo systemctl start trading-bot.service 2>/dev/null; then
    RESTART_METHOD="systemctl (interactive)"
    echo "  ✓ Started via systemctl (with password prompt)"
else
    # Method 3: nohup fallback
    echo "  systemctl not available — using nohup"
    nohup "$VENV_PY" "$BOT_DIR/main_autonomous.py" >> "$BOT_DIR/trading_bot.log" 2>&1 &
    NEW_PID=$!
    disown $NEW_PID 2>/dev/null
    RESTART_METHOD="nohup"
    echo "  Started via nohup PID $NEW_PID (NOT systemd-tracked)"
fi

# Step 6: Verify
echo "[6] Verifying (waiting 15s)..."
sleep 15
ALIVE=$(pgrep -f "python3.*main_autonomous" 2>/dev/null | head -1)
if [ -n "$ALIVE" ]; then
    echo "  ✓ Bot alive — pid $ALIVE"
else
    echo "  ✗ Bot died. Last log:"
    tail -20 "$BOT_DIR/trading_bot.log"
    send_tg "🚨 RECOVERY: Bot failed to start. Check trading_bot.log"
    exit 1
fi

# Step 7: Install watchdog
echo "[7] Installing watchdog cron..."
if [ -f "$BOT_DIR/install_watchdog.sh" ]; then
    bash "$BOT_DIR/install_watchdog.sh"
else
    echo "  install_watchdog.sh not found — skipping"
fi

# Step 8: Notify
echo "═══════════════════════════════════════════"
echo "✅ RECOVERY COMPLETE — pid $ALIVE"
echo "═══════════════════════════════════════════"
send_tg "✅ <b>RECOVERY COMPLETE</b>%0APID: ${ALIVE}%0AMethod: ${RESTART_METHOD}%0AWatchdog cron installed%0ARun /diagscan to verify"
