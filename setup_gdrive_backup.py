#!/usr/bin/env python3
"""
setup_gdrive_backup.py

One-time setup for Google Drive backup.
Run this ONCE to authenticate. After that, backup runs automatically.

TWO OPTIONS:
  A) Google Drive API (recommended — fully automated after setup)
  B) rclone (alternative — requires one-time browser auth)

Usage:
    cd ~/Desktop/trading_robot
    source venv/bin/activate
    python setup_gdrive_backup.py

Follow the prompts — it opens a browser once for Google auth,
then everything is automatic forever.
"""
import os, sys, json, subprocess
from pathlib import Path

HERE = Path(__file__).parent
os.chdir(HERE)

print("""
╔══════════════════════════════════════════════════════════╗
║         GOOGLE DRIVE BACKUP SETUP                        ║
║         One-time setup — takes ~5 minutes                ║
╚══════════════════════════════════════════════════════════╝
""")

# ── Step 1: Check / install required packages ─────────────────────────────────
print("[1] Checking required packages...")

def install_package(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        __import__(import_name)
        print(f"  ✅ {pkg} already installed")
        return True
    except ImportError:
        print(f"  📦 Installing {pkg}...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            capture_output=True
        )
        if r.returncode == 0:
            print(f"  ✅ {pkg} installed")
            return True
        else:
            print(f"  ❌ Failed to install {pkg}: {r.stderr.decode()[:200]}")
            return False

ok1 = install_package("google-auth-oauthlib",  "google_auth_oauthlib")
ok2 = install_package("google-auth-httplib2",   "google_auth_httplib2")
ok3 = install_package("google-api-python-client", "googleapiclient")

if not all([ok1, ok2, ok3]):
    print("\n❌ Package installation failed. Try manually:")
    print("   pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
    sys.exit(1)

# ── Step 2: Check for credentials.json ───────────────────────────────────────
print("\n[2] Checking Google credentials...")

creds_path = HERE / "gdrive_credentials.json"
token_path  = HERE / "gdrive_token.json"

if token_path.exists():
    print("  ✅ Already authenticated (gdrive_token.json found)")
    print("  ℹ  To re-authenticate, delete gdrive_token.json and run again")

elif creds_path.exists():
    print("  ✅ gdrive_credentials.json found — will authenticate now")

else:
    print("""
  ❌ gdrive_credentials.json not found.

  To set up Google Drive access:

  1. Go to: https://console.cloud.google.com/
  2. Create a new project (or select existing)
  3. Enable 'Google Drive API':
       APIs & Services → Library → Google Drive API → Enable
  4. Create credentials:
       APIs & Services → Credentials → Create Credentials → OAuth client ID
       Application type: Desktop app
       Name: TradingRobot
       → Download JSON
  5. Rename downloaded file to 'gdrive_credentials.json'
  6. Copy to: ~/Desktop/trading_robot/gdrive_credentials.json
  7. Run this script again

  ALTERNATIVE (simpler — uses rclone):
  Run: python setup_gdrive_backup.py --rclone
""")
    if "--rclone" in sys.argv:
        _setup_rclone()
    sys.exit(0)

# ── Step 3: Authenticate ──────────────────────────────────────────────────────
if not token_path.exists():
    print("\n[3] Authenticating with Google...")
    print("  A browser window will open. Sign in and grant access.")
    print("  (If no browser opens, copy the URL shown and paste in your browser)")
    input("\n  Press Enter to continue...")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        SCOPES = ["https://www.googleapis.com/auth/drive.file"]
        flow   = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        creds  = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
        print("  ✅ Authentication successful! Token saved.")
    except Exception as e:
        print(f"  ❌ Authentication failed: {e}")
        sys.exit(1)

# ── Step 4: Create backup folder on Google Drive ──────────────────────────────
print("\n[4] Setting up Google Drive folder...")

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery  import build

    creds   = Credentials.from_authorized_user_info(
        json.loads(token_path.read_text()),
        ["https://www.googleapis.com/auth/drive.file"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Check if folder exists
    FOLDER_NAME = "TradingRobotBackup"
    results = service.files().list(
        q=f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive",
        fields="files(id, name)"
    ).execute()

    folders = results.get("files", [])
    if folders:
        folder_id = folders[0]["id"]
        print(f"  ✅ Folder '{FOLDER_NAME}' already exists (id={folder_id[:8]}...)")
    else:
        meta = {
            "name":     FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder"
        }
        folder = service.files().create(body=meta, fields="id").execute()
        folder_id = folder["id"]
        print(f"  ✅ Created folder '{FOLDER_NAME}' on Google Drive")

    # Save folder_id for use by cloud_backup.py
    config_data = {
        "folder_id":   folder_id,
        "folder_name": FOLDER_NAME,
    }
    (HERE / "gdrive_config.json").write_text(json.dumps(config_data, indent=2))

except Exception as e:
    print(f"  ❌ Drive API error: {e}")
    sys.exit(1)

# ── Step 5: Test upload ────────────────────────────────────────────────────────
print("\n[5] Testing backup upload...")

try:
    from googleapiclient.http import MediaFileUpload

    # Upload a test file
    test_content = json.dumps({"test": True, "setup_date": str(__import__("datetime").date.today())})
    test_path    = HERE / "_backup_test.json"
    test_path.write_text(test_content)

    media = MediaFileUpload(str(test_path), mimetype="application/json")
    result = service.files().create(
        body={"name": "_backup_test.json", "parents": [folder_id]},
        media_body=media,
        fields="id"
    ).execute()
    test_path.unlink(missing_ok=True)
    print(f"  ✅ Test upload successful! File ID: {result['id'][:12]}...")

except Exception as e:
    print(f"  ❌ Test upload failed: {e}")

# ── Step 6: Update .env ───────────────────────────────────────────────────────
print("\n[6] Updating .env...")

env_path = HERE / ".env"
if env_path.exists():
    env_content = env_path.read_text()
    if "BACKUP_GDRIVE_FOLDER" not in env_content:
        with open(env_path, "a") as f:
            f.write(f"\nBACKUP_GDRIVE_FOLDER=TradingRobotBackup\n")
        print("  ✅ Added BACKUP_GDRIVE_FOLDER=TradingRobotBackup to .env")
    else:
        print("  ✅ BACKUP_GDRIVE_FOLDER already in .env")

print(f"""
╔══════════════════════════════════════════════════════════╗
║  ✅ GOOGLE DRIVE BACKUP READY                            ║
║                                                          ║
║  Folder: TradingRobotBackup (on your Google Drive)       ║
║  Files:  trades.db, ai_model.pkl, strategy files         ║
║  Schedule: Daily at 3:15 PM automatically                ║
║                                                          ║
║  Restart the bot to activate:                            ║
║    ./bot.sh restart                                       ║
╚══════════════════════════════════════════════════════════╝
""")


def _setup_rclone():
    """Fallback: set up rclone for Google Drive."""
    print("\n[RCLONE SETUP]")
    r = subprocess.run(["which", "rclone"], capture_output=True)
    if r.returncode != 0:
        print("  Installing rclone...")
        subprocess.run(["sudo", "apt-get", "install", "-y", "rclone"])
    print("  Running: rclone config")
    print("  → Choose: n (new remote)")
    print("  → Name:   gdrive")
    print("  → Type:   drive (Google Drive)")
    print("  → Follow prompts, authenticate in browser")
    subprocess.run(["rclone", "config"])
