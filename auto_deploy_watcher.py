"""Legacy Google Drive deploy helper — automatic watching is disabled.

Runs as a systemd service. Every 5 minutes checks if a new
trading_robot_FRESH.zip exists on Google Drive. If newer than
last deploy, auto-pulls, extracts, restarts bot, runs diagnostics.

Setup:
  sudo cp auto-deploy.service /etc/systemd/system/
  sudo systemctl enable auto-deploy
  sudo systemctl start auto-deploy

How to deploy from Claude mobile:
  1. Download trading_robot_FRESH.zip from Claude
  2. Upload to Google Drive → trading_robot/ folder
  3. Within 5 minutes, bot auto-updates and sends Telegram confirmation
"""
from __future__ import annotations
import os, time, subprocess, logging, json
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.expanduser("~/Desktop/trading_robot/auto_deploy.log")),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("auto_deploy")

BOT_DIR    = Path.home() / "Desktop" / "trading_robot"
STATE_FILE = BOT_DIR / ".last_deploy_timestamp"
CHECK_INTERVAL = 300  # 5 minutes
ZIP_NAME   = "trading_robot_FRESH.zip"


def load_env():
    env_file = BOT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def send_tg(text: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            "chat_id": chat_id, "text": text, "parse_mode": "HTML"
        }).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, timeout=10
        )
    except Exception as e:
        logger.debug("TG send: %s", e)


def get_drive_zip_time() -> float:
    """Check modification time of zip on Google Drive."""
    try:
        remote = os.getenv("GDRIVE_REMOTE", "gdrive")
        folder = os.getenv("GDRIVE_FOLDER", "trading_robot")
        r = subprocess.run(
            ["rclone", "lsjson", f"{remote}:{folder}/{ZIP_NAME}"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout.strip():
            items = json.loads(r.stdout)
            if items:
                mod_time = items[0].get("ModTime", "")
                if mod_time:
                    from dateutil.parser import parse as dp
                    return dp(mod_time).timestamp()
    except Exception as e:
        logger.debug("Drive check: %s", e)
    return 0


def get_last_deploy_time() -> float:
    try:
        return float(STATE_FILE.read_text().strip())
    except Exception:
        return 0


def deploy():
    logger.info("New zip detected on Drive — deploying...")
    send_tg("🚀 <b>AUTO-DEPLOY STARTED</b>\n  New zip detected on Google Drive")
    
    os.chdir(str(BOT_DIR))
    r = subprocess.run(
        ["bash", "remote_deploy.sh"],
        capture_output=True, text=True, timeout=300
    )
    
    logger.info("Deploy output:\n%s", r.stdout[-500:])
    if r.returncode != 0:
        logger.error("Deploy failed:\n%s", r.stderr[-300:])
        send_tg(f"❌ <b>AUTO-DEPLOY FAILED</b>\n<pre>{r.stderr[-200:]}</pre>")
    
    # Record timestamp
    STATE_FILE.write_text(str(time.time()))


def main():
    import sys
    load_env()
    if "--manual-deploy" in sys.argv:
        logger.warning("Manual Google Drive deployment explicitly requested")
        deploy()
        return
    logger.warning("Google Drive auto-deploy is DISABLED. Use ./remote_deploy.sh manually.")


if __name__ == "__main__":
    main()
