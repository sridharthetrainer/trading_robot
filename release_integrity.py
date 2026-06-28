"""Generate and verify an immutable hash manifest for executable project files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict


MANIFEST = Path("release_integrity.json")
PATTERNS = ("*.py", "*.sh", "*.service", "*.timer", "requirements*.txt")
EXCLUDED_PARTS = {".git", ".venv", "venv", "backups", "releases", "__pycache__"}


def _files() -> list[Path]:
    found = set()
    for pattern in PATTERNS:
        for path in Path(".").rglob(pattern):
            if path.is_file() and not EXCLUDED_PARTS.intersection(path.parts):
                found.add(path)
    return sorted(found, key=lambda item: str(item))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def build_manifest(*, write: bool = True) -> Dict[str, Any]:
    files = {str(path): _sha256(path) for path in _files()}
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_head": _git_head(),
        "algorithm": "sha256",
        "file_count": len(files),
        "files": files,
    }
    if write:
        MANIFEST.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def verify_manifest(path: Path = MANIFEST) -> Dict[str, Any]:
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": "manifest_missing_or_invalid", "error": str(exc)}
    current = build_manifest(write=False)
    old_files = expected.get("files", {}) if isinstance(expected.get("files"), dict) else {}
    new_files = current["files"]
    missing = sorted(set(old_files) - set(new_files))
    added = sorted(set(new_files) - set(old_files))
    changed = sorted(name for name in set(old_files) & set(new_files) if old_files[name] != new_files[name])
    return {
        "ok": not missing and not added and not changed,
        "reason": "verified" if not missing and not added and not changed else "release_files_changed",
        "manifest_git_head": expected.get("git_head", ""),
        "current_git_head": current.get("git_head", ""),
        "file_count": len(new_files),
        "missing": missing[:25], "added": added[:25], "changed": changed[:25],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify_manifest() if args.verify else build_manifest(write=True)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
