#!/bin/bash
# ════════════════════════════════════════════════════════════════
# do_deploy.sh — SAFE deploy: pre-validate before killing bot
#
# Strategy: do not kill the running bot until the new zip is fully
# extracted to a STAGING directory and passes import smoke-test.
# If anything fails, the running bot is untouched.
# ════════════════════════════════════════════════════════════════
set -uo pipefail

BOT_DIR="/home/sridhar/Desktop/trading_robot"
exec >> "$BOT_DIR/deploy.log" 2>&1
echo ""
echo "═══════════════════════════════════════════════"
echo "DEPLOY $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════"

cd "$BOT_DIR" || { echo "FATAL: cannot cd $BOT_DIR"; exit 1; }

# Load .env — robust against quotes/spaces
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env 2>/dev/null || true
    set +a
fi

ZIP="trading_robot_FRESH.zip"
REMOTE="${GDRIVE_REMOTE:-gdrive}"
FOLDER="${GDRIVE_FOLDER:-trading_robot}"
TIMESTAMP_FILE=".last_deploy_timestamp"
LOCK_FILE="/tmp/trading_bot_deploy.lock"
STAGING="$BOT_DIR/.deploy_staging"
BACKUP_DIR="$BOT_DIR/.deploy_backup"
VENV_PY="$BOT_DIR/venv/bin/python3"

TG_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN:-}/sendMessage"

send_tg() {
    [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && return
    [ -z "${TELEGRAM_CHAT_ID:-}" ] && return
    curl -s -X POST "$TG_URL" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "parse_mode=HTML" \
        --data-urlencode "text=$1" >/dev/null 2>&1
}

bot_pid() { pgrep -f "python3.*main_autonomous" 2>/dev/null | head -1; }

# ── Concurrency lock ────────────────────────────────────────────
if [ -e "$LOCK_FILE" ]; then
    PID_IN_LOCK=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$PID_IN_LOCK" ] && kill -0 "$PID_IN_LOCK" 2>/dev/null; then
        echo "DEPLOY ALREADY RUNNING (pid $PID_IN_LOCK) — exiting"
        send_tg "ℹ️ Deploy already in progress (pid $PID_IN_LOCK)"
        exit 0
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ── Step 1: Check Drive timestamp ───────────────────────────────
echo "[1] Checking Drive timestamp..."
DRIVE_MOD=$(rclone lsjson "${REMOTE}:${FOLDER}/${ZIP}" 2>/dev/null | "$VENV_PY" -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d[0]['ModTime'] if d else '')
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$DRIVE_MOD" ]; then
    echo "  ✗ File not on Drive"
    send_tg "❌ <b>DEPLOY FAILED</b>%0AFile not found on Google Drive%0AUpload <code>${ZIP}</code> to ${FOLDER}/"
    exit 1
fi
echo "  Drive timestamp: $DRIVE_MOD"

LAST_DEPLOY=""
[ -f "$TIMESTAMP_FILE" ] && LAST_DEPLOY=$(cat "$TIMESTAMP_FILE" 2>/dev/null)
echo "  Last deploy:     ${LAST_DEPLOY:-never}"

if [ "$DRIVE_MOD" = "$LAST_DEPLOY" ]; then
    echo "  SAME VERSION — skipping"
    BOT_PID=$(bot_pid)
    if [ -n "$BOT_PID" ]; then
        send_tg "ℹ️ <b>ALREADY UP TO DATE</b>%0AVersion: ${DRIVE_MOD}%0ABot running (pid ${BOT_PID})"
    else
        # Same version but bot dead — restart with current code
        echo "  Bot is dead — restarting with current code"
        send_tg "⚠️ Same version but bot is dead — restarting..."
        nohup "$VENV_PY" "$BOT_DIR/main_autonomous.py" >> "$BOT_DIR/trading_bot.log" 2>&1 &
        disown
        sleep 8
        NEW_PID=$(bot_pid)
        if [ -n "$NEW_PID" ]; then
            send_tg "✅ Bot restarted (pid $NEW_PID)"
        else
            send_tg "❌ Bot failed to restart with current code"
        fi
    fi
    exit 0
fi

echo "  NEW VERSION DETECTED — deploying..."

# ── Step 2: Download to staging ─────────────────────────────────
echo "[2] Downloading..."
mkdir -p "$STAGING"
rm -rf "$STAGING"/* 2>/dev/null

rclone copy "${REMOTE}:${FOLDER}/${ZIP}" ~/Desktop/ 2>&1
ZIP_PATH="$HOME/Desktop/$ZIP"
if [ ! -f "$ZIP_PATH" ]; then
    echo "  ✗ Download failed"
    send_tg "❌ Download failed from Drive"
    exit 1
fi
ZIP_SIZE=$(stat -c%s "$ZIP_PATH" 2>/dev/null || echo 0)
echo "  Downloaded: $ZIP_SIZE bytes"

# ── Step 3: Extract to staging (NOT live dir yet!) ──────────────
echo "[3] Extracting to staging..."
unzip -q -o "$ZIP_PATH" -d "$STAGING/" 2>&1
# Most zips put files in a subfolder trading_robot/ — flatten
if [ -d "$STAGING/trading_robot" ]; then
    mv "$STAGING/trading_robot"/* "$STAGING/" 2>/dev/null
    mv "$STAGING/trading_robot"/.* "$STAGING/" 2>/dev/null || true
    rmdir "$STAGING/trading_robot" 2>/dev/null || true
fi
PYCOUNT=$(find "$STAGING" -maxdepth 1 -name "*.py" | wc -l)
echo "  Extracted: $PYCOUNT .py files"
if [ "$PYCOUNT" -lt 50 ]; then
    echo "  ✗ Suspiciously few files — aborting"
    send_tg "❌ <b>DEPLOY ABORTED</b>%0AOnly $PYCOUNT .py files in zip (expected 200+)%0ABot NOT touched"
    exit 1
fi

# ── Step 4: Pre-flight syntax check on STAGING ──────────────────
echo "[4] Syntax check..."
SYNTAX_ERR=$("$VENV_PY" -c "
import ast, os
errors = []
for f in os.listdir('$STAGING'):
    if f.endswith('.py'):
        try:
            ast.parse(open(os.path.join('$STAGING', f)).read())
        except SyntaxError as e:
            errors.append(f'{f}:{e.lineno}')
print('|'.join(errors[:5]))
" 2>&1)
if [ -n "$SYNTAX_ERR" ]; then
    echo "  ✗ Syntax errors: $SYNTAX_ERR"
    send_tg "❌ <b>DEPLOY ABORTED</b>%0ASyntax errors:%0A<pre>${SYNTAX_ERR}</pre>%0ABot NOT touched"
    exit 1
fi
echo "  ✓ All files parse"

# ── Step 5: Smoke test — can main_autonomous import? ────────────
echo "[5] Smoke test — import main_autonomous..."
# Copy current .env to staging so imports that read env work
cp "$BOT_DIR/.env" "$STAGING/.env" 2>/dev/null || true
# Symlink directories that imports may need (data files, master contract csv, etc.)
for item in MasterContract_NFO.csv MasterContract_ALL.csv nifty200.csv venv; do
    [ -e "$BOT_DIR/$item" ] && ln -sfn "$BOT_DIR/$item" "$STAGING/$item"
done

SMOKE=$(cd "$STAGING" && timeout 30 "$VENV_PY" -c "
import sys
sys.path.insert(0, '.')
try:
    import config
    # Don't actually instantiate the engine — just check imports resolve
    import main_autonomous  # this imports almost everything transitively
    print('OK')
except Exception as e:
    import traceback
    print('FAIL:', str(e)[:200])
    print(traceback.format_exc()[-600:])
" 2>&1)

if ! echo "$SMOKE" | head -1 | grep -q "^OK$"; then
    echo "  ✗ Smoke test failed:"
    echo "$SMOKE"
    SMOKE_ESC=$(echo "$SMOKE" | tail -10 | head -c 800)
    send_tg "❌ <b>DEPLOY ABORTED</b>%0AImport smoke test failed:%0A<pre>${SMOKE_ESC}</pre>%0ABot NOT touched"
    exit 1
fi
echo "  ✓ Smoke test passed"

# ── Step 6: Backup current dir (atomic-ish) ─────────────────────
echo "[6] Backing up current code..."
rm -rf "$BACKUP_DIR" 2>/dev/null
mkdir -p "$BACKUP_DIR"
cp "$BOT_DIR"/*.py "$BACKUP_DIR/" 2>/dev/null
cp "$BOT_DIR/.env" "$BACKUP_DIR/.env.backup" 2>/dev/null
echo "  ✓ Backup at $BACKUP_DIR"

# ── Step 7: Promote staging → live ──────────────────────────────
echo "[7] Promoting staging to live..."
# Save .env first (paranoid)
cp "$BOT_DIR/.env" /tmp/.env.preserve 2>/dev/null
# Copy all .py and supporting files from staging
cp "$STAGING"/*.py "$BOT_DIR/" 2>/dev/null
cp "$STAGING"/*.sh "$BOT_DIR/" 2>/dev/null
chmod +x "$BOT_DIR"/*.sh 2>/dev/null
# Restore .env
cp /tmp/.env.preserve "$BOT_DIR/.env" 2>/dev/null

# Preserve operator-controlled .env trading mode. Deploy must never force live
# trading or lower the live-capital threshold.

echo "  ✓ Code promoted"

# ── Step 8: Run test_core ───────────────────────────────────────
echo "[8] Running test_core..."
TEST_OUT=$(cd "$BOT_DIR" && timeout 60 "$VENV_PY" test_core.py 2>&1 | tail -3)
echo "$TEST_OUT"

# ── Step 9: Restart bot — try systemctl first, fall back to nohup ─
echo "[9] Restarting bot..."
RESTART_METHOD="unknown"

# Method 1: systemctl reset-failed + restart (works if NOPASSWD sudo OR polkit allows it)
echo "  Trying: systemctl restart trading-bot.service"
if sudo -n systemctl reset-failed trading-bot.service 2>/dev/null && \
   sudo -n systemctl restart trading-bot.service 2>/dev/null; then
    RESTART_METHOD="systemctl"
    echo "  ✓ systemctl restart succeeded"
else
    # Method 2: SIGKILL the old bot — systemd's Restart=on-failure will pick it up
    echo "  systemctl restart not permitted (no NOPASSWD sudo); trying SIGKILL trick"
    OLD_PID=$(bot_pid)
    if [ -n "$OLD_PID" ]; then
        echo "  SIGKILL old bot pid $OLD_PID — systemd should auto-restart"
        kill -9 "$OLD_PID" 2>/dev/null
        sleep 3
        # Check if systemd auto-restarted it
        AUTO=$(bot_pid)
        if [ -n "$AUTO" ] && [ "$AUTO" != "$OLD_PID" ]; then
            RESTART_METHOD="sigkill+systemd"
            echo "  ✓ systemd auto-restarted bot (new pid $AUTO)"
        fi
    fi

    # Method 3: nohup direct launch (last resort — bot won't be tracked by systemd)
    if [ "$RESTART_METHOD" = "unknown" ]; then
        echo "  Falling back to nohup direct launch"
        pkill -9 -f "python3.*main_autonomous" 2>/dev/null
        sleep 2
        cd "$BOT_DIR"
        nohup "$VENV_PY" "$BOT_DIR/main_autonomous.py" >> "$BOT_DIR/trading_bot.log" 2>&1 &
        NEW_PID=$!
        disown $NEW_PID 2>/dev/null
        RESTART_METHOD="nohup"
        echo "  Launched nohup pid $NEW_PID (NOT systemd-tracked)"
    fi
fi

# ── Step 10: Verify with multiple checks ────────────────────────
echo "[10] Verifying (10s, 20s, 30s checks)..."
sleep 10
ALIVE=$(bot_pid)
if [ -n "$ALIVE" ]; then
    sleep 10
    ALIVE2=$(bot_pid)
    if [ -n "$ALIVE2" ]; then
        # Third check at +30s catches slow-startup-then-crash scenarios
        sleep 10
        ALIVE3=$(bot_pid)
        if [ -n "$ALIVE3" ]; then
            echo "$DRIVE_MOD" > "$TIMESTAMP_FILE"
            ANGEL=$(grep -c "ALWAYS connect for DATA" "$BOT_DIR/angel.py" 2>/dev/null || echo 0)
            PAPER=$(grep "^PAPER_TRADING=" "$BOT_DIR/.env" | cut -d= -f2)
            # Auto-install watchdog cron on first successful deploy
            WD_STATUS="not_installed"
            if [ -f "$BOT_DIR/install_watchdog.sh" ]; then
                if bash "$BOT_DIR/install_watchdog.sh" >/dev/null 2>&1; then
                    WD_STATUS="installed"
                fi
            fi
            send_tg "✅ <b>DEPLOY SUCCESS</b>%0A%0A📄 Files: ${PYCOUNT}%0A🔧 Angel fix: ${ANGEL}%0A📝 PAPER_TRADING: ${PAPER}%0A🔢 PID: ${ALIVE3}%0A🚀 Restart: ${RESTART_METHOD}%0A🐕 Watchdog cron: ${WD_STATUS}%0A🕐 Version: ${DRIVE_MOD}%0A🧪 ${TEST_OUT}%0A%0ARun /diagscan to verify"
            echo "DEPLOY SUCCESS — pid $ALIVE3 method=$RESTART_METHOD"
            exit 0
        fi
    fi
fi

# ── Failure path: ROLLBACK ─────────────────────────────────────
echo "  ✗ Bot died after restart — ROLLING BACK"
LAST_ERR=$(tail -30 "$BOT_DIR/trading_bot.log" 2>/dev/null | tail -c 600)

# Restore .py files from backup
if [ -d "$BACKUP_DIR" ]; then
    cp "$BACKUP_DIR"/*.py "$BOT_DIR/" 2>/dev/null
    [ -f "$BACKUP_DIR/.env.backup" ] && cp "$BACKUP_DIR/.env.backup" "$BOT_DIR/.env"
    echo "  ✓ Code restored from backup"
fi

# Restart with old code
pkill -f "python3.*main_autonomous" 2>/dev/null
sleep 2
nohup "$VENV_PY" "$BOT_DIR/main_autonomous.py" >> "$BOT_DIR/trading_bot.log" 2>&1 &
ROLLBACK_PID=$!
disown $ROLLBACK_PID 2>/dev/null
sleep 8
ALIVE3=$(bot_pid)

if [ -n "$ALIVE3" ]; then
    send_tg "⚠️ <b>DEPLOY FAILED — ROLLED BACK</b>%0ANew code crashed bot. Old code restored.%0A%0A<pre>${LAST_ERR}</pre>%0A%0ABot running with OLD code (pid $ALIVE3)"
    echo "ROLLBACK SUCCESS — pid $ALIVE3"
else
    send_tg "🚨 <b>DEPLOY FAILED — ROLLBACK ALSO FAILED</b>%0A%0A<pre>${LAST_ERR}</pre>%0A%0ABot is DEAD. SSH required to recover."
    echo "ROLLBACK FAILED — bot DEAD"
fi
exit 1
