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

import ast
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


def _added_lines(path: str) -> set:
    """New-file line numbers added/changed for this file in the staged diff."""
    out = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--", path],
        capture_output=True, text=True,
    ).stdout
    added, new_ln = set(), 0
    for line in out.splitlines():
        if line.startswith("@@"):
            # @@ -a,b +c,d @@  → new-file hunk starts at c
            try:
                plus = line.split("+", 1)[1].split("@@", 1)[0].strip()
                new_ln = int(plus.split(",", 1)[0])
            except Exception:
                new_ln = 0
        elif line.startswith("+") and not line.startswith("+++"):
            added.add(new_ln)
            new_ln += 1
        elif line.startswith("-") and not line.startswith("---"):
            pass  # removed line — does not advance new-file counter
    return added


def _silent_except_findings(path: str, added: set):
    """Return (blocking_bare, warn_silent) for except-handlers ON ADDED lines:
    newly-added bare `except:` (also swallows SystemExit/KeyboardInterrupt) and
    newly-added `except …: pass` (a silent swallow — this repo's recurring
    failure-hiding pattern)."""
    blocking, warn = [], []
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return blocking, warn          # syntax errors handled separately
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.lineno not in added:
            continue                   # only NEW handlers
        is_silent = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
        if node.type is None:          # bare `except:`
            blocking.append(f"  BARE-EXCEPT {path}:{node.lineno}  (use `except Exception:` + log)")
        elif is_silent:
            warn.append(f"  SILENT-PASS {path}:{node.lineno}  (add logger.debug(..., exc_info=True))")
    return blocking, warn


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

    # 3) Newly-added silent failure patterns (visibility for this repo's #1
    #    incident shape: errors swallowed with no log). Bare except blocks;
    #    `except …: pass` warns (non-blocking — many legitimate uses exist).
    warnings: list = []
    for f in files:
        added = _added_lines(f)
        if not added:
            continue
        bare, silent = _silent_except_findings(f, added)
        errors.extend(bare)
        warnings.extend(silent)

    if warnings:
        print("\n".join([
            "⚠️  pre-commit: newly-added silent `except …: pass` (consider logging):",
            *warnings, "",
        ]))

    # 4) Dependency coverage (local stand-in for CI's clean-install check) — if a
    #    staged .py adds a third-party import missing from requirements.txt, warn
    #    so a fresh deploy won't crash. Non-blocking (run only when .py staged).
    try:
        dep = subprocess.run(
            [sys.executable, "scripts/clean_env_check.py"],
            capture_output=True, text=True,
        )
        if dep.returncode != 0:
            print("⚠️  pre-commit: dependency-coverage gap (fresh install would crash):")
            print(dep.stdout.strip() + "\n")
    except Exception:
        pass

    if errors:
        print("\n".join([
            "",
            "❌ pre-commit lint gate FAILED — commit blocked.",
            "   (syntax error, undefined name, or new bare `except:` — outage causes)",
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
