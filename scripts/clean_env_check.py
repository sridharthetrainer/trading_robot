#!/usr/bin/env python3
"""
clean_env_check.py — local stand-in for the GitHub CI dependency check.

The one thing a local test run can't catch (because your .venv already has
everything) is a third-party package that's imported in the code but MISSING from
requirements.txt — a fresh deploy would then crash on import. This walks every
.py file, extracts top-level third-party imports, and flags any that aren't:
  - the Python standard library, or
  - a local module/package in this repo, or
  - listed in requirements.txt.

Zero network, runs in ~1s. Exit 0 = clean, 1 = missing deps found.
Run:  python3 scripts/clean_env_check.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# import-name → pip-package-name (where they differ)
IMPORT_TO_PKG = {
    "cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "yaml": "pyyaml",
    "dateutil": "python-dateutil", "telegram": "python-telegram-bot",
    "google": "google-api-python-client", "git": "gitpython",
    "pandas_ta": "pandas-ta", "smartapi": "smartapi-python",
    "SmartApi": "smartapi-python", "apscheduler": "apscheduler",
}
# third-party packages used optionally / via try-except (don't fail the check)
OPTIONAL = {"talib", "tensorflow", "torch", "xgboost", "lightgbm"}


def _req_packages() -> set[str]:
    req = ROOT / "requirements.txt"
    out = set()
    if req.exists():
        for line in req.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~\[ ]", line)[0].strip().lower().replace("_", "-")
            if name:
                out.add(name)
    return out


def _local_modules() -> set[str]:
    mods = set()
    for p in ROOT.glob("*.py"):
        mods.add(p.stem)
    for p in ROOT.iterdir():
        if p.is_dir() and (p / "__init__.py").exists():
            mods.add(p.name)
    return mods


def _guarded_linenos(tree: ast.AST) -> set[int]:
    """Line numbers inside any `try:` body — imports there are optional
    (the code handles ImportError), so they need not be in requirements.txt."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if hasattr(sub, "lineno"):
                        guarded.add(sub.lineno)
    return guarded


def _top_imports() -> dict[str, str]:
    """{import_name: first_file_using_it} for HARD (unguarded) imports across the
    repo (excl. .venv). An import that is ALWAYS inside a try/except is optional
    and excluded."""
    found: dict[str, str] = {}
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "site-packages" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        guarded = _guarded_linenos(tree)
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) in guarded:
                continue  # optional (try/except-guarded) import
            if isinstance(node, ast.Import):
                for a in node.names:
                    found.setdefault(a.name.split(".")[0], str(path.relative_to(ROOT)))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    found.setdefault(node.module.split(".")[0], str(path.relative_to(ROOT)))
    return found


def main() -> int:
    stdlib = set(getattr(sys, "stdlib_module_names", set())) | {"__future__"}
    reqs = _req_packages()
    local = _local_modules()
    imports = _top_imports()

    missing = []
    for name, where in sorted(imports.items()):
        if name in stdlib or name in local or name in OPTIONAL:
            continue
        pkg = IMPORT_TO_PKG.get(name, name).lower().replace("_", "-")
        if pkg in reqs or name.lower().replace("_", "-") in reqs:
            continue
        missing.append((name, pkg, where))

    if missing:
        print("❌ clean-env check: third-party imports NOT in requirements.txt:")
        for name, pkg, where in missing:
            print(f"   import {name:20} → add '{pkg}'   (first used in {where})")
        print(f"\n{len(missing)} missing dependency(ies). A fresh install would crash on these.")
        return 1
    print(f"✅ clean-env check: all third-party imports covered by requirements.txt "
          f"({len(reqs)} pkgs, {len(imports)} distinct imports scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
