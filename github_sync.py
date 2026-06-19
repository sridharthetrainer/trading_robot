"""
github_sync.py — GitHub Sync for Code Backup & Version Control

Syncs all trading bot .py files to a private GitHub repository.
Use as permanent code backup — every version tracked forever.

SETUP (one-time, 5 min):
  1. Create GitHub account at github.com
  2. Create NEW private repo: "trading-robot"
  3. Go to Settings → Developer Settings → Personal Access Tokens
  4. Generate token → select "repo" scope → copy token
  5. Add to .env:
       GITHUB_TOKEN=your_github_token_here
       GITHUB_REPO=yourusername/trading-robot

WHAT IT DOES:
  - Daily commit at 9:30 PM with all changed .py files
  - /github command to push manually any time
  - Tags releases: v1.0, v2.0 etc
  - You can browse full history at github.com
  - Works alongside Google Drive sync (different purpose)

WHY GITHUB vs DRIVE:
  Google Drive = latest files only (operational backup)
  GitHub       = full version history (if you break something, roll back)
"""
from __future__ import annotations

import logging
import os
import subprocess
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_BOT_DIR  = Path(__file__).parent
_TOKEN    = os.getenv("GITHUB_TOKEN", "")
_REPO     = os.getenv("GITHUB_REPO", "")   # e.g. "sridhar123/trading-robot"


def _git(args: list, timeout: int = 30) -> tuple:
    """Run git command in BOT_DIR."""
    try:
        r = subprocess.run(
            ["git"] + args, cwd=str(_BOT_DIR),
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, str(e)


def _git_available() -> bool:
    ok, _ = _git(["--version"])
    return ok


def _repo_initialized() -> bool:
    return (_BOT_DIR / ".git").exists()


def setup_github_repo(token: str = "", repo: str = "") -> Dict:
    """
    One-time setup: initialize local git repo and link to GitHub.
    """
    token = token or _TOKEN
    repo  = repo  or _REPO
    if not token or not repo:
        return {"ok": False, "error": "Set GITHUB_TOKEN and GITHUB_REPO in .env"}

    if not _git_available():
        return {"ok": False, "error": "git not installed. Run: sudo apt install git"}

    steps = []

    # Init local repo
    if not _repo_initialized():
        _git(["init"])
        _git(["config", "user.email", "trading-bot@local"])
        _git(["config", "user.name",  "Trading Bot"])
        steps.append("Git repo initialized")

    # Create .gitignore
    gitignore = _BOT_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "venv/\n.venv/\n__pycache__/\n*.pyc\n*.pyo\n*.so\n"
            ".env\n*.db\n*.log\nsync_state.json\nheartbeat.json\n"
            "oi_tracker_state.json\ndedup_state.json\n"
            "gdrive_token.json\ngdrive_config.json\n"
            "node_modules/\n.git/\n*.zip\n"
        )
        steps.append(".gitignore created (protects .env and API keys)")

    # Set remote
    remote_url = f"https://{token}@github.com/{repo}.git"
    _git(["remote", "remove", "origin"])
    ok, out = _git(["remote", "add", "origin", remote_url])
    steps.append(f"Remote set: {repo}")

    return {"ok": True, "steps": steps}


def push_to_github(
    message: str = "",
    tag: str = "",
    alerts = None,
) -> Dict:
    """
    Commit and push all changed .py files to GitHub.
    Safe — never commits .env or sensitive files.
    """
    if not _TOKEN or not _REPO:
        return {"ok": False, "error": "GITHUB_TOKEN / GITHUB_REPO not in .env"}
    if not _repo_initialized():
        setup_github_repo()

    # Stage only .py files (never .env, never .db)
    ok1, _ = _git(["add", "*.py", "requirements.txt", "nifty200.csv",
                   "bot.sh", "*.sh", "*.service"])

    # Check if anything changed
    ok_status, status = _git(["status", "--porcelain"])
    if not status.strip():
        return {"ok": True, "message": "Nothing to commit — all files up to date"}

    changed_files = [l[3:].strip() for l in status.strip().splitlines()
                     if l.strip() and not l.startswith("??")]

    # Commit
    msg = message or f"Bot update {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ok2, out = _git(["commit", "-m", msg])
    if not ok2 and "nothing to commit" not in out:
        return {"ok": False, "error": out[:100]}

    # Push
    ok3, out3 = _git(["push", "-u", "origin", "main", "--force-with-lease"], timeout=60)
    if not ok3:
        # Try creating main branch first
        _git(["checkout", "-b", "main"])
        ok3, out3 = _git(["push", "-u", "origin", "main", "--force"], timeout=60)

    # Optional tag
    if tag:
        _git(["tag", tag, "-m", f"Release {tag}"])
        _git(["push", "origin", tag])

    if alerts and ok3:
        alerts.send(
            f"🐙 <b>GITHUB SYNC</b>\n"
            f"  ✅ {len(changed_files)} files committed\n"
            f"  Repo: github.com/{_REPO}\n"
            f"  Message: {msg[:40]}\n"
            f"🕐 {datetime.now().strftime('%H:%M')}"
        )

    return {
        "ok":           ok3,
        "files":        changed_files,
        "message":      msg,
        "error":        out3[:100] if not ok3 else "",
    }


def pull_from_github() -> Dict:
    """Pull latest from GitHub (for restoring on new machine)."""
    if not _repo_initialized():
        return {"ok": False, "error": "No local repo. Run setup first."}
    ok, out = _git(["pull", "origin", "main"], timeout=60)
    return {"ok": ok, "output": out[:100]}


def github_status() -> str:
    """Status summary for /github Telegram command."""
    if not _TOKEN:
        return ("🐙 <b>GITHUB SYNC</b>\n"
                "  ❌ Not configured\n"
                "  Add GITHUB_TOKEN + GITHUB_REPO to .env\n"
                "  See github_sync.py for setup guide")
    if not _repo_initialized():
        return "🐙 GitHub: repo not initialized. Send /github setup"

    ok, log = _git(["log", "--oneline", "-5"])
    ok2, status = _git(["status", "--short"])
    changed = len([l for l in status.strip().splitlines() if l.strip()])

    return (
        f"🐙 <b>GITHUB SYNC</b>\n"
        f"  Repo: github.com/{_REPO}\n"
        f"  Uncommitted: {changed} files\n"
        f"  Last 5 commits:\n"
        + "\n".join(f"    {l}" for l in log.strip().splitlines()[:5])
    )
