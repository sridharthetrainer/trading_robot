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
    """Imports are optional only when a handler actually catches ImportError."""
    guarded: set[int] = set()

    def catches_import_error(handler):
        if handler.type is None:
            return True
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        return any(isinstance(kind, ast.Name) and kind.id in {
            "ImportError", "ModuleNotFoundError", "Exception", "BaseException"
        } for kind in types)

    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(catches_import_error(handler) for handler in node.handlers):
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if hasattr(sub, "lineno"):
                        guarded.add(sub.lineno)
    return guarded


def _script_import_roots(tree: ast.AST, path: pathlib.Path) -> set[pathlib.Path]:
    """Resolve literal Path-based sys.path additions without executing code."""
    values = {"__file__": path}
    roots = {ROOT, path.parent}

    def resolve(node):
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"Path", "str"} and len(node.args) == 1:
                return resolve(node.args[0])
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"resolve", "absolute"} and not node.args:
                value = resolve(node.func.value)
                return pathlib.Path(value).resolve() if value is not None else None
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            value = resolve(node.value)
            return pathlib.Path(value).parent if value is not None else None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
            value, index = resolve(node.value.value), resolve(node.slice)
            if value is not None and isinstance(index, int) and index >= 0:
                return pathlib.Path(value).parents[index]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = resolve(node.left), resolve(node.right)
            if left is not None and isinstance(right, str):
                return pathlib.Path(left) / right
        return None

    for statement in tree.body:
        try:
            if isinstance(statement, ast.Assign):
                value = resolve(statement.value)
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        values[target.id] = value
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                if (isinstance(call.func, ast.Attribute)
                        and ast.unparse(call.func.value) == "sys.path"
                        and call.func.attr in {"insert", "append"} and call.args):
                    value = resolve(call.args[-1])
                    if isinstance(value, (str, pathlib.Path)):
                        directory = pathlib.Path(value).resolve()
                        if directory.is_relative_to(ROOT):
                            roots.add(directory)
        except (TypeError, ValueError, IndexError):
            continue  # Dynamic paths remain unresolved; never run repository code.
    return roots


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
        roots = _script_import_roots(tree, path)

        def record(name):
            if not any((root / f"{name}.py").is_file() or (root / name).is_dir() for root in roots):
                found.setdefault(name, str(path.relative_to(ROOT)))

        for node in ast.walk(tree):
            if getattr(node, "lineno", None) in guarded:
                continue  # optional (try/except-guarded) import
            if isinstance(node, ast.Import):
                for a in node.names:
                    record(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    record(node.module.split(".")[0])
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
