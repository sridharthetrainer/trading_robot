#!/usr/bin/env python3
"""
pre_commit_lint.py — the repo's pre-commit safety gate.

Blocks a commit if any STAGED .py file has a SYNTAX error or an UNDEFINED NAME —
this repo's #1 outage cause (a NameError swallowed by a broad try/except leaves a
feature silently dead, e.g. the multiple "Scanned: 0" incidents). Cosmetic lint
(unused imports, empty f-strings) is intentionally NOT blocked — too noisy.

Checks the working-tree version of staged files (standard simple-hook behaviour).
Bypass in an emergency with:  git commit --no-verify

Wired via .git/hooks/pre-commit (install with scripts/install_hooks.sh).
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys


def _staged_py_files() -> list:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True,
    ).stdout
    return [f for f in out.splitlines() if f.endswith(".py") and os.path.exists(f)]


def main() -> int:
    files = _staged_py_files()
    if not files:
        return 0

    errors: list = []

    # 1) Syntax — would crash on import.
    for f in files:
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            msg = (e.msg or str(e)).splitlines()[0]
            errors.append(f"  SYNTAX    {f}: {msg}")
        except Exception as e:  # pragma: no cover
            errors.append(f"  SYNTAX    {f}: {e}")

    # 2) Undefined names — the swallowed-NameError class.
    pf = subprocess.run(
        [sys.executable, "-m", "pyflakes", *files],
        capture_output=True, text=True,
    )
    for line in (pf.stdout + pf.stderr).splitlines():
        if "undefined name" in line:
            errors.append(f"  UNDEFINED {line.strip()}")

    if errors:
        print("\n".join([
            "",
            "❌ pre-commit lint gate FAILED — commit blocked.",
            "   (syntax error or undefined name — this repo's #1 outage cause)",
            "",
            *errors,
            "",
            "Fix the above, or bypass in an emergency:  git commit --no-verify",
            "",
        ]))
        return 1

    print(f"✅ lint gate: {len(files)} staged .py file(s) clean (syntax + undefined-name)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
