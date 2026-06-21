# New System Deploy Commands

Use this on a fresh Ubuntu/Linux system to restore and run the trading robot.
Do not commit `.env`, broker tokens, Google tokens, or Telegram tokens.

## 1. Install OS Packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip unzip zip rclone sqlite3
```

## 2. Clone This Version

Primary repo:

```bash
mkdir -p ~/Desktop
cd ~/Desktop
git clone https://github.com/sridharthetrainer/trading_robot.git trading_robot
cd trading_robot
git fetch origin version-20260619-220346
git checkout version-20260619-220346
```

Backup repo/version branch:

```bash
mkdir -p ~/Desktop
cd ~/Desktop
git clone --branch version-20260619-220346 https://github.com/sridharthetrainer/trading-backup.git trading_robot
cd trading_robot
```

## 3. Optional: Restore From Zip Inside Git

```bash
cd ~/Desktop/trading_robot
mkdir -p /tmp/trading_robot_restore
unzip -q backups/trading_robot_code_20260619_232135.zip -d /tmp/trading_robot_restore
rsync -a --exclude ".git/" /tmp/trading_robot_restore/ ~/Desktop/trading_robot/
```

## 4. Create Python Environment

```bash
cd ~/Desktop/trading_robot
python3 -m venv .venv
./.venv/bin/python3 -m pip install --upgrade pip wheel setuptools
./.venv/bin/python3 -m pip install -r requirements.txt
```

## 5. Add Private Config

Create `.env` locally on the new system. Minimum required keys usually include:

```bash
cd ~/Desktop/trading_robot
nano .env
```

Required private values:

```text
API_KEY=
CLIENT_ID=
PASSWORD=
TOTP_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
NEWS_API_KEY=
GITHUB_BACKUP_TOKEN=
GITHUB_BACKUP_REPO=sridharthetrainer/trading-backup
GITHUB_BACKUP_BRANCH=main
```

For first run, keep live trading disabled:

```text
PAPER_TRADING=true
ENABLE_REAL_TRADING=false
```

## 6. Validate

```bash
cd ~/Desktop/trading_robot
./.venv/bin/python3 -m py_compile config.py live_signal_engine.py main_autonomous.py option_bot_audit.py
./.venv/bin/python3 option_bot_audit.py --json
./.venv/bin/python3 data_pipeline_audit.py
```

## 7. Install/Restart Services

```bash
cd ~/Desktop/trading_robot
chmod +x ensure_autonomous_runtime.sh
./ensure_autonomous_runtime.sh
sudo systemctl daemon-reload
sudo systemctl enable trading-bot.service daily-pipeline.timer post-market-ml.timer manual-tracker.service
sudo systemctl restart trading-bot.service daily-pipeline.timer post-market-ml.timer manual-tracker.service
```

## 8. Check Runtime

```bash
systemctl status trading-bot.service --no-pager --lines=30
systemctl status manual-tracker.service --no-pager --lines=30
journalctl -u trading-bot.service -f
```

## 9. Google Drive Backup Setup

```bash
rclone config
```

Use remote name:

```text
gdrive
```

Then test:

```bash
cd ~/Desktop/trading_robot
rclone mkdir gdrive:trading_robot
rclone copy backups/trading_robot_code_20260619_232135.zip gdrive:trading_robot/backups/
```
