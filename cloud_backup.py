"""
cloud_backup.py

Automatic cloud backup of trades.db and strategy state.

Backup targets (in order of availability):
  1. Google Drive folder (if rclone installed)
  2. Local external drive (if mounted)
  3. Email attachment (if configured)
  4. Local backup folder (always — already exists)

Setup options:

OPTION A — Google Drive (recommended, free):
  1. Install rclone: sudo apt install rclone
  2. Configure: rclone config (follow prompts, name it "gdrive")
  3. Add to .env: BACKUP_GDRIVE_FOLDER=trading_robot_backup
  4. Done — auto-backs up at 4 PM daily

OPTION B — Local external drive:
  1. Add to .env: BACKUP_LOCAL_PATH=/media/sridhar/external_drive/trading

OPTION C — Email (if rclone not available):
  1. Add to .env:
     BACKUP_EMAIL=your@gmail.com
     BACKUP_EMAIL_PASSWORD=app_password (not your main password)
"""
from __future__ import annotations
import logging, os, shutil, subprocess, time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CloudBackup:
    """
    Backs up critical trading data to cloud/external storage.
    
    Files backed up:
      - trades.db          (trade history, AI training data)
      - strategy_state.json (current strategy weights)
      - ai_model.pkl       (trained XGBoost model)
      - .env               (configuration, without secrets exposed)
    """

    FILES_TO_BACKUP = [
        # Core trade data
        "trades.db",
        # Strategy state and ML
        "strategy_state.json",
        "ai_model.pkl",
        "rl_state.json",
        "learning_state.json",
        # Validation and backtest results
        "walk_forward_results.json",
        "best_params_trend.json",
        "best_params_mr.json",
        "best_params_breakout.json",
        "best_params_scalping.json",
        "best_params_ma.json",
        "validation_results.json",
        # Calibration and performance
        "score_calibration.json",
        "strategy_matrix.json",
        "rejection_stats.json",
        # Symbol lists
        "nifty200.csv",
    ]

    def __init__(self) -> None:
        try:
            import config as cfg
            self._gdrive_folder  = getattr(cfg, "BACKUP_GDRIVE_FOLDER", "")
            self._local_path     = getattr(cfg, "BACKUP_LOCAL_PATH",    "")
            self._email          = getattr(cfg, "BACKUP_EMAIL",         "")
            self._email_pwd      = getattr(cfg, "BACKUP_EMAIL_PASSWORD","")
        except Exception:
            self._gdrive_folder = os.getenv("BACKUP_GDRIVE_FOLDER", "")
            self._local_path    = os.getenv("BACKUP_LOCAL_PATH",    "")
            self._email         = os.getenv("BACKUP_EMAIL",         "")
            self._email_pwd     = os.getenv("BACKUP_EMAIL_PASSWORD","")

        self._last_backup_ts = 0.0
        self._backup_interval = 3600   # hourly during session, daily otherwise

    def run_backup(self, force: bool = False) -> dict:
        """
        Run backup. Returns result dict.
        force=True skips interval check.
        """
        now_ts = time.time()
        if not force and (now_ts - self._last_backup_ts) < self._backup_interval:
            return {"status": "skipped", "reason": "too_soon"}

        results = []
        ts_str  = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Always do local backup first
        local_result = self._backup_local(ts_str)
        results.append(("local", local_result))

        # Try Google Drive — API method first, rclone as fallback
        from pathlib import Path as _P
        if _P("gdrive_token.json").exists():
            gdrive_result = self._backup_gdrive_api(ts_str)
            results.append(("gdrive_api", gdrive_result))
            if not gdrive_result.get("ok") and self._gdrive_folder:
                # API failed — try rclone fallback
                rclone_result = self._backup_gdrive(ts_str)
                results.append(("gdrive_rclone", rclone_result))
        elif self._gdrive_folder:
            gdrive_result = self._backup_gdrive(ts_str)
            results.append(("gdrive_rclone", gdrive_result))

        # Try external drive
        if self._local_path:
            ext_result = self._backup_external(ts_str)
            results.append(("external_drive", ext_result))

        self._last_backup_ts = now_ts
        success = any(r[1].get("ok") for r in results)

        summary = {
            "status":     "ok" if success else "failed",
            "timestamp":  ts_str,
            "targets":    results,
            "files":      [],
        }

        for f in self.FILES_TO_BACKUP:
            if Path(f).exists():
                size_kb = Path(f).stat().st_size // 1024
                summary["files"].append(f"{f} ({size_kb}KB)")

        if success:
            logger.info("Backup completed: %s", ", ".join(r[0] for r in results if r[1].get("ok")))
        else:
            logger.warning("All backup targets failed")

        return summary

    def _backup_local(self, ts_str: str) -> dict:
        """Backup to local backup/ folder (always available)."""
        try:
            backup_dir = Path("backup") / f"full_{ts_str}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            copied = []
            for fname in self.FILES_TO_BACKUP:
                src = Path(fname)
                if src.exists():
                    shutil.copy2(src, backup_dir / fname)
                    copied.append(fname)
            # Keep only last 7 full backups
            full_backups = sorted(Path("backup").glob("full_*"))
            for old in full_backups[:-7]:
                shutil.rmtree(old, ignore_errors=True)
            return {"ok": True, "path": str(backup_dir), "files": copied}
        except Exception as e:
            return {"ok": False, "error": str(e)}


    def _backup_gdrive_api(self, ts_str: str) -> dict:
        """
        Backup to Google Drive using Google API directly.
        Requires setup_gdrive_backup.py to have been run once.
        No rclone needed — uses gdrive_token.json + gdrive_config.json.
        """
        try:
            from pathlib import Path
            import json

            token_path  = Path("gdrive_token.json")
            config_path = Path("gdrive_config.json")

            if not token_path.exists():
                return {"ok": False, "error": "gdrive_token.json not found — run setup_gdrive_backup.py"}
            if not config_path.exists():
                return {"ok": False, "error": "gdrive_config.json not found — run setup_gdrive_backup.py"}

            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            creds = Credentials.from_authorized_user_info(
                json.loads(token_path.read_text()),
                ["https://www.googleapis.com/auth/drive.file"]
            )

            # Refresh token if expired
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json())

            service   = build("drive", "v3", credentials=creds, cache_discovery=False)
            cfg       = json.loads(config_path.read_text())
            folder_id = cfg["folder_id"]

            # Create dated subfolder
            subfolder_name = f"{date.today().isoformat()}_{ts_str}"
            subfolder_meta = {
                "name":     subfolder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents":  [folder_id],
            }
            subfolder = service.files().create(
                body=subfolder_meta, fields="id"
            ).execute()
            sub_id = subfolder["id"]

            uploaded = []
            errors   = []

            for fname in self.FILES_TO_BACKUP:
                fpath = Path(fname)
                if not fpath.exists():
                    continue
                try:
                    media = MediaFileUpload(str(fpath), resumable=False)
                    service.files().create(
                        body={"name": fname, "parents": [sub_id]},
                        media_body=media,
                        fields="id",
                    ).execute()
                    uploaded.append(fname)
                except Exception as _fe:
                    errors.append(f"{fname}: {_fe}")

            ok = len(uploaded) > 0
            logger.info(
                "Google Drive backup: %d files uploaded to %s/%s",
                len(uploaded), cfg.get("folder_name"), subfolder_name
            )
            return {
                "ok":       ok,
                "uploaded": uploaded,
                "errors":   errors,
                "folder":   subfolder_name,
            }

        except ImportError:
            return {"ok": False, "error": "google-api-python-client not installed — run setup_gdrive_backup.py"}
        except Exception as e:
            logger.warning("Google Drive API backup failed: %s", e)
            return {"ok": False, "error": str(e)}


    def _backup_gdrive(self, ts_str: str) -> dict:
        """Backup to Google Drive using rclone (fallback if API not set up)."""
        try:
            # Check rclone available
            r = subprocess.run(["which", "rclone"], capture_output=True, timeout=5)
            if r.returncode != 0:
                return {"ok": False, "error": "rclone not installed"}

            dest = f"gdrive:{self._gdrive_folder}/{date.today().isoformat()}"
            errors = []
            for fname in self.FILES_TO_BACKUP:
                if Path(fname).exists():
                    r = subprocess.run(
                        ["rclone", "copy", fname, dest,
                         "--retries", "2", "--timeout", "30s"],
                        capture_output=True, timeout=60,
                    )
                    if r.returncode != 0:
                        errors.append(fname)

            ok = len(errors) == 0
            return {"ok": ok, "dest": dest, "errors": errors}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _backup_external(self, ts_str: str) -> dict:
        """Backup to external drive/path."""
        try:
            dest_dir = Path(self._local_path) / date.today().isoformat()
            dest_dir.mkdir(parents=True, exist_ok=True)
            copied = []
            for fname in self.FILES_TO_BACKUP:
                src = Path(fname)
                if src.exists():
                    shutil.copy2(src, dest_dir / fname)
                    copied.append(fname)
            return {"ok": True, "path": str(dest_dir), "files": copied}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def setup_instructions() -> str:
        return """
CLOUD BACKUP SETUP
==================

OPTION A — Google Drive (recommended):
  1. sudo apt install rclone
  2. rclone config
     → Choose: n (new remote)
     → Name: gdrive
     → Type: drive (Google Drive)
     → Follow prompts to authenticate
  3. Add to .env:
     BACKUP_GDRIVE_FOLDER=TradingRobotBackup

OPTION B — External drive:
  1. Plug in USB drive
  2. Add to .env:
     BACKUP_LOCAL_PATH=/media/sridhar/YourDriveName/trading

Test backup:
  python -c "from cloud_backup import CloudBackup; print(CloudBackup().run_backup(force=True))"
"""


_backup: Optional[CloudBackup] = None
def get_backup() -> CloudBackup:
    global _backup
    if _backup is None:
        _backup = CloudBackup()
    return _backup
