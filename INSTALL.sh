#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# NIFTY ALGO TRADING BOT — COMPLETE FRESH INSTALL
# Run: bash INSTALL.sh
# ═══════════════════════════════════════════════════════════════

set -e
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; exit 1; }
info() { echo -e "   $1"; }

echo "═══════════════════════════════════════════════════════"
echo "  NIFTY ALGO BOT — FRESH INSTALL"
echo "  $(date '+%d-%b-%Y %H:%M')"
echo "═══════════════════════════════════════════════════════"

INSTALL_DIR="$HOME/Desktop/trading_robot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── STEP 1: System packages ─────────────────────────────────
echo -e "\n[1/10] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    git curl wget unzip sqlite3 \
    build-essential libssl-dev libffi-dev \
    rclone 2>/dev/null || true
ok "System packages installed"

# ── STEP 2: Create install directory ────────────────────────
echo -e "\n[2/10] Setting up directory..."
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Copy all files from script location
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    cp -r "$SCRIPT_DIR"/*.py "$INSTALL_DIR/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR"/*.sh "$INSTALL_DIR/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR"/*.service "$INSTALL_DIR/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR"/*.csv "$INSTALL_DIR/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR"/*.txt "$INSTALL_DIR/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR"/.env.example "$INSTALL_DIR/" 2>/dev/null || true
fi
PY_COUNT=$(ls *.py 2>/dev/null | wc -l)
ok "Directory ready: $INSTALL_DIR ($PY_COUNT Python files)"

# ── STEP 3: Python virtual environment ──────────────────────
echo -e "\n[3/10] Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
ok "Virtual environment created"

# ── STEP 4: Python packages ─────────────────────────────────
echo -e "\n[4/10] Installing Python packages (this takes 3-5 min)..."
pip install -q \
    pandas numpy scipy scikit-learn \
    requests pyotp python-dotenv \
    smartapi-python pyzmq websockets \
    ta-lib 2>/dev/null || \
pip install -q \
    pandas numpy scipy scikit-learn \
    requests pyotp python-dotenv \
    smartapi-python pyzmq websockets
pip install -q \
    hmmlearn cvxpy 2>/dev/null || warn "hmmlearn/cvxpy optional — skipping"
pip install -q \
    psutil schedule APScheduler \
    beautifulsoup4 lxml \
    SQLAlchemy alembic \
    pyarrow fastparquet 2>/dev/null || true

# Install from requirements.txt if present
if [ -f requirements.txt ]; then
    pip install -q -r requirements.txt 2>/dev/null || true
fi
ok "Python packages installed"

# ── STEP 5: .env configuration ──────────────────────────────
echo -e "\n[5/10] Setting up configuration..."
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        warn ".env created from template — EDIT IT NOW with your keys!"
        info "Required keys:"
        info "  API_KEY=           (Angel One API key)"
        info "  CLIENT_ID=         (Angel One client ID)"
        info "  PASSWORD=          (Angel One password)"
        info "  TOTP_SECRET=       (Angel One TOTP secret)"
        info "  TELEGRAM_BOT_TOKEN=(from @BotFather)"
        info "  TELEGRAM_CHAT_ID=  (your chat ID)"
        info "  TIINGO_KEY=        (from tiingo.com — free)"
        info "  TWELVE_DATA_KEY=   (from twelvedata.com — free)"
        info "  GITHUB_TOKEN=      (from github.com/settings/tokens)"
        info "  GITHUB_REPO=       (your_username/trading_robot)"
    else
        cat > .env << 'ENVEOF'
# Angel One SmartAPI
API_KEY=your_angel_api_key
CLIENT_ID=your_client_id
PASSWORD=your_password
TOTP_SECRET=your_totp_secret

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Capital
PAPER_TRADING=false
ENABLE_REAL_TRADING=true
REAL_CAPITAL=26964
CAPITAL=26964
MIN_LIVE_CAPITAL=1
LIVE_BALANCE_USE_PCT=0.95
ALLOW_VALIDATION_BLOCKED_LIVE=true
PAPER_CAPITAL=100000

# Data APIs (free registration)
TIINGO_KEY=your_tiingo_key
TWELVE_DATA_KEY=your_twelve_data_key
ALPHA_VANTAGE_KEY=your_av_key

# GitHub backup
GITHUB_TOKEN=your_github_token
GITHUB_REPO=your_username/trading_robot

# AI (optional — enables LLM signal filter)
ANTHROPIC_API_KEY=your_claude_api_key
ENVEOF
        warn ".env template created — fill in your keys before starting!"
    fi
else
    ok ".env already exists — keeping your settings"
fi

# ── STEP 6: Download MasterContract ─────────────────────────
echo -e "\n[6/10] Downloading Angel One MasterContract..."
python3 - << 'PYEOF'
import sys, os
sys.path.insert(0, '.')
try:
    from dotenv import load_dotenv
    load_dotenv('.env', override=True)
    import pyotp, config as cfg
    from SmartApi import SmartConnect
    
    obj = SmartConnect(api_key=cfg.API_KEY)
    totp = pyotp.TOTP(cfg.TOTP_SECRET).now()
    resp = obj.generateSession(cfg.CLIENT_ID, cfg.PASSWORD, totp)
    
    if resp.get('status') == True:
        import requests, pandas as pd
        r = requests.get(
            'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json',
            timeout=30
        )
        if r.status_code == 200:
            df = pd.DataFrame(r.json())
            df.to_csv('MasterContract_ALL.csv', index=False)
            print(f"✅ MasterContract: {len(df):,} instruments")
        else:
            print(f"⚠️  Download failed (HTTP {r.status_code}) — will retry on first run")
    else:
        print(f"⚠️  Angel One login failed: {resp.get('message','?')} — add keys to .env first")
except Exception as e:
    print(f"⚠️  MasterContract skipped: {e}")
    print("   Run: python3 download_master_contract.py after adding .env keys")
PYEOF

# ── STEP 7: Seed Bhavcopy cache ─────────────────────────────
echo -e "\n[7/10] Seeding NSE Bhavcopy cache (60 days)..."
python3 seed_cache.py 2>/dev/null || warn "Bhavcopy cache skipped — run: python3 seed_cache.py"

# ── STEP 8: Install systemd services ────────────────────────
echo -e "\n[8/10] Installing systemd services..."
chmod +x *.sh 2>/dev/null || true

# Fix service file paths
if [ -f trading-bot.service ]; then
    sed -i "s|/home/sridhar/Desktop|$HOME/Desktop|g" trading-bot.service
    sed -i "s|User=sridhar|User=$USER|g" trading-bot.service
    sudo cp trading-bot.service /etc/systemd/system/
    ok "trading-bot.service installed"
fi

if [ -f trading-bot-watchdog.service ]; then
    sed -i "s|/home/sridhar/Desktop|$HOME/Desktop|g" trading-bot-watchdog.service
    sed -i "s|User=sridhar|User=$USER|g" trading-bot-watchdog.service
    sudo cp trading-bot-watchdog.service /etc/systemd/system/
    ok "trading-bot-watchdog.service installed"
fi

sudo systemctl daemon-reload
sudo systemctl enable trading-bot trading-bot-watchdog 2>/dev/null || true
ok "Services enabled (auto-start on boot)"

# ── STEP 9: Validate ────────────────────────────────────────
echo -e "\n[9/10] Running system validation..."
python3 validate_scan.py 2>/dev/null || warn "Validation skipped — add .env keys first"

# ── STEP 10: Start bot ──────────────────────────────────────
echo -e "\n[10/10] Starting bot..."
if grep -q "your_angel_api_key" .env 2>/dev/null; then
    warn "BOT NOT STARTED — edit .env with real keys first, then: ./bot.sh restart"
else
    ./bot.sh restart
    sleep 3
    ./bot.sh logs &
    sleep 8
    kill %1 2>/dev/null || true
    ok "Bot started!"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  INSTALL COMPLETE"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo "  1. Edit .env with your keys: nano $INSTALL_DIR/.env"
echo "  2. Start bot: cd $INSTALL_DIR && ./bot.sh restart"
echo "  3. Watch logs: ./bot.sh logs"
echo "  4. Validate: python3 validate_scan.py"
echo ""
echo "  Telegram commands:"
echo "  /health   /morning   /signals   /pnl"
echo "  /fii      /downloads /backup    /help"
echo "═══════════════════════════════════════════════════════"
