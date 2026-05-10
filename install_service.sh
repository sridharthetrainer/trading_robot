#!/usr/bin/env bash
# =============================================================================
# install_service.sh
# One-time setup — installs both systemd services and starts them.
# Run with: sudo ./install_service.sh
# =============================================================================
set -euo pipefail

# ── Auto-detect project directory ─────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_USER="$(stat -c '%U' "$PROJECT_DIR")"
VENV_PATH="$PROJECT_DIR/venv"
PYTHON="$VENV_PATH/bin/python"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC}  $1"; }
err()  { echo -e "${RED}✗${NC} $1"; exit 1; }

echo ""
echo "======================================================"
echo "  Trading Bot — systemd Service Installer"
echo "  Project : $PROJECT_DIR"
echo "  User    : $BOT_USER"
echo "======================================================"
echo ""

# ── Pre-flight checks ─────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Run with sudo: sudo ./install_service.sh"
[[ -f "$PROJECT_DIR/main_autonomous.py" ]] || err "main_autonomous.py not found"
[[ -d "$VENV_PATH" ]] || err "virtualenv not found at $VENV_PATH"
[[ -f "$PYTHON" ]]    || err "python not found at $PYTHON"

ok "Project directory found"
ok "Virtualenv found"

[[ -f "$PROJECT_DIR/.env" ]] && ok ".env file found" \
    || warn ".env missing — create it before starting (copy from .env.example)"

# ── Write service files with correct paths ────────────────────────────────────
write_service() {
    local SRC="$PROJECT_DIR/$1"
    local DEST="/etc/systemd/system/$1"
    [[ -f "$SRC" ]] || err "$1 not found in $PROJECT_DIR"
    # Replace placeholder username/path with actual values
    sed \
        -e "s|User=sridhar|User=$BOT_USER|g" \
        -e "s|Group=sridhar|Group=$BOT_USER|g" \
        -e "s|/home/sridhar/Desktop/trading_robot|$PROJECT_DIR|g" \
        "$SRC" > "$DEST"
    ok "Installed $DEST"
}

write_service "trading-bot.service"
write_service "trading-bot-watchdog.service"

# ── Reload and enable ─────────────────────────────────────────────────────────
systemctl daemon-reload
ok "systemd daemon reloaded"

systemctl enable trading-bot.service
systemctl enable trading-bot-watchdog.service
ok "Both services enabled (will start on every boot)"

# ── Start now ─────────────────────────────────────────────────────────────────
echo ""
read -rp "Start the bot now? (y/n): " START_NOW
if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
    systemctl start trading-bot.service
    sleep 3
    systemctl start trading-bot-watchdog.service
    sleep 2

    BOT_OK=false; WD_OK=false
    systemctl is-active --quiet trading-bot.service         && BOT_OK=true
    systemctl is-active --quiet trading-bot-watchdog.service && WD_OK=true

    $BOT_OK && ok "Trading bot is running" \
            || warn "Bot may not have started — check: journalctl -u trading-bot -n 30"
    $WD_OK  && ok "Watchdog is running" \
            || warn "Watchdog may not have started"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Useful commands"
echo "======================================================"
echo ""
echo "  ./bot.sh start          Start the bot"
echo "  ./bot.sh stop           Stop the bot"
echo "  ./bot.sh restart        Restart after updating files"
echo "  ./bot.sh status         Show status + open positions"
echo "  ./bot.sh logs           Live log stream"
echo "  ./bot.sh pnl            Today's P&L"
echo "  ./bot.sh trades         Today's trade list"
echo "  ./bot.sh telegram-test  Send test Telegram message"
echo ""
echo "  journalctl -u trading-bot -f          Live bot logs"
echo "  journalctl -u trading-watchdog -f     Live watchdog logs"
echo ""
