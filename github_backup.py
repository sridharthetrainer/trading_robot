"""
github_backup.py

Backs up critical trading data to a private GitHub repository.
Uses the GitHub REST API — no git CLI or remote setup needed.

Files backed up (JSON-safe):
  - All JSON state files (strategy, calibration, walk-forward, etc.)
  - trades.db exported as trades_export.json
  - nifty200.csv

Setup (one-time):
  1. Create a private GitHub repo (e.g. your-username/trading-backup)
  2. Generate a Personal Access Token (PAT):
       GitHub → Settings → Developer settings → Personal access tokens
       → Tokens (classic) → Generate new token
       Scopes required: repo (full)
  3. Add to .env:
       GITHUB_BACKUP_TOKEN=your_github_backup_token_here
       GITHUB_BACKUP_REPO=your-username/trading-backup
       GITHUB_BACKUP_BRANCH=main   (optional, default=main)

Usage:
  from github_backup import run_github_backup
  result = run_github_backup()

  Or from CLI:
  python github_backup.py
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Files to push (JSON-serialisable only) ───────────────────────────────────
JSON_FILES: List[str] = [
    "strategy_state.json",
    "walk_forward_results.json",
    "best_params_trend.json",
    "best_params_mr.json",
    "best_params_breakout.json",
    "best_params_scalping.json",
    "best_params_ma.json",
    "validation_results.json",
    "score_calibration.json",
    "strategy_matrix.json",
    "rejection_stats.json",
    "rl_state.json",
    "learning_state.json",
]

DB_FILE    = "trades.db"
CSV_FILES  = ["nifty200.csv"]


# ── Config ────────────────────────────────────────────────────────────────────

def _cfg() -> Dict[str, str]:
    try:
        import config as _c
        return {
            "token":  os.getenv("GITHUB_BACKUP_TOKEN",  getattr(_c, "GITHUB_BACKUP_TOKEN",  "")),
            "repo":   os.getenv("GITHUB_BACKUP_REPO",   getattr(_c, "GITHUB_BACKUP_REPO",   "")),
            "branch": os.getenv("GITHUB_BACKUP_BRANCH", getattr(_c, "GITHUB_BACKUP_BRANCH", "main")),
        }
    except Exception:
        return {
            "token":  os.getenv("GITHUB_BACKUP_TOKEN",  ""),
            "repo":   os.getenv("GITHUB_BACKUP_REPO",   ""),
            "branch": os.getenv("GITHUB_BACKUP_BRANCH", "main"),
        }


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _gh_request(method: str, url: str, token: str, data: dict = None) -> dict:
    """Make a GitHub REST API request."""
    import urllib.request, urllib.error
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "Content-Type":  "application/json",
        "User-Agent":    "trading-backup/1.0",
    }
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True,  "status": r.status, "body": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "body": body}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": str(exc)}


def _push_file(token: str, repo: str, branch: str,
               remote_path: str, content_bytes: bytes,
               message: str) -> bool:
    """Create or update a single file in GitHub repo via API."""
    url = f"https://api.github.com/repos/{repo}/contents/{remote_path}"

    # Get existing SHA if file exists (required for updates)
    existing = _gh_request("GET", url + f"?ref={branch}", token)
    sha = existing["body"].get("sha") if existing["ok"] else None

    payload: Dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode(),
        "branch":  branch,
    }
    if sha:
        payload["sha"] = sha

    result = _gh_request("PUT", url, token, payload)
    if result["ok"]:
        logger.debug("GitHub: pushed %s", remote_path)
        return True
    logger.warning("GitHub push failed for %s: %s", remote_path, result["body"][:120])
    return False


# ── DB export ─────────────────────────────────────────────────────────────────

def _export_trades_db(db_path: str = DB_FILE) -> Optional[bytes]:
    """Export trades table from SQLite to JSON bytes."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        trades = [dict(r) for r in conn.execute(
            "SELECT * FROM trades ORDER BY entry_time DESC LIMIT 5000"
        ).fetchall()]
        conn.close()
        return json.dumps({
            "exported_at": datetime.now().isoformat(),
            "total": len(trades),
            "trades": trades,
        }, indent=2, default=str).encode()
    except Exception as exc:
        logger.warning("DB export failed: %s", exc)
        return None


# ── Main backup function ──────────────────────────────────────────────────────

def run_github_backup(force: bool = False) -> Dict[str, Any]:
    """
    Push all backup files to the configured GitHub repository.

    Returns dict with 'ok', 'pushed', 'skipped', 'errors' keys.
    """
    cfg = _cfg()
    if not cfg["token"] or not cfg["repo"]:
        return {
            "ok": False,
            "error": "GITHUB_BACKUP_TOKEN and GITHUB_BACKUP_REPO not set in .env",
            "setup": (
                "1. Create private GitHub repo\n"
                "2. Generate PAT with 'repo' scope\n"
                "3. Add to .env:\n"
                "   GITHUB_BACKUP_TOKEN=your_github_backup_token_here\n"
                "   GITHUB_BACKUP_REPO=username/trading-backup"
            ),
        }

    today      = date.today().isoformat()
    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    commit_msg = f"backup: {today} {datetime.now().strftime('%H:%M')} IST"
    pushed     = []
    skipped    = []
    errors     = []

    # Push JSON state files
    for fname in JSON_FILES:
        fpath = Path(fname)
        if not fpath.exists():
            skipped.append(fname)
            continue
        try:
            content = fpath.read_bytes()
            ok = _push_file(cfg["token"], cfg["repo"], cfg["branch"],
                            f"data/{fname}", content, commit_msg)
            (pushed if ok else errors).append(fname)
        except Exception as exc:
            errors.append(f"{fname}: {exc}")

    # Push CSV files
    for fname in CSV_FILES:
        fpath = Path(fname)
        if not fpath.exists():
            skipped.append(fname)
            continue
        try:
            ok = _push_file(cfg["token"], cfg["repo"], cfg["branch"],
                            f"data/{fname}", fpath.read_bytes(), commit_msg)
            (pushed if ok else errors).append(fname)
        except Exception as exc:
            errors.append(f"{fname}: {exc}")

    # Export and push trades.db
    db_bytes = _export_trades_db()
    if db_bytes:
        ok = _push_file(cfg["token"], cfg["repo"], cfg["branch"],
                        "data/trades_export.json", db_bytes, commit_msg)
        (pushed if ok else errors).append("trades_export.json")
    else:
        skipped.append("trades.db (empty/not found)")

    # Push a backup manifest
    manifest = json.dumps({
        "backup_time":  datetime.now().isoformat(),
        "pushed":       pushed,
        "skipped":      skipped,
        "errors":       errors,
        "total_files":  len(pushed),
    }, indent=2).encode()
    _push_file(cfg["token"], cfg["repo"], cfg["branch"],
               "backup_manifest.json", manifest, commit_msg)

    result = {
        "ok":      len(pushed) > 0,
        "pushed":  pushed,
        "skipped": skipped,
        "errors":  errors,
    }
    if result["ok"]:
        logger.info("GitHub backup: %d files pushed to %s/%s",
                    len(pushed), cfg["repo"], cfg["branch"])
    else:
        logger.warning("GitHub backup failed: %s", errors[:3])
    return result


# ── Telegram command integration ──────────────────────────────────────────────

def backup_status_message() -> str:
    """Return a Telegram-formatted backup status."""
    cfg = _cfg()
    if not cfg["token"] or not cfg["repo"]:
        return (
            "⚠️ <b>GitHub Backup</b> not configured\n"
            "Add to .env:\n"
            "  GITHUB_BACKUP_TOKEN=your_github_backup_token_here\n"
            "  GITHUB_BACKUP_REPO=username/trading-backup"
        )
    return f"✅ GitHub Backup configured → <code>{cfg['repo']}</code> / {cfg['branch']}"


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    import sys
    result = run_github_backup(force=True)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
