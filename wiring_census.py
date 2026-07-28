"""
wiring_census.py — static wiring audit: which modules and public functions
are actually connected to anything? (2026-07-12, operator-requested after
spread_strategy.py was found fully built and imported by NOTHING.)

Two sweeps:
  1. MODULE census — every repo-root module, classified:
       entrypoint  — run directly (systemd unit, cron, .sh, __main__ CLI)
       imported    — imported by at least one other module
       ORPHAN      — imported by nothing and never run: dead weight or a
                     forgotten feature (the spread_strategy class)
  2. FUNCTION census — every top-level public function/class, classified:
       external    — referenced from another module
       internal    — referenced only within its own module (often fine)
       UNREFERENCED— referenced nowhere at all (not even its own module)

Static analysis has known blind spots — dynamic dispatch (getattr,
registries, Telegram handler registration) resolves at runtime — so names
matching the repo's known dynamic patterns are tagged, not flagged.
Report: wiring_census_report.json. The nightly wiring_watchdog diffs the
orphan-module list against a baseline and alerts only on NEW orphans.
"""
from __future__ import annotations

import ast
from collections import Counter
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

REPORT_FILE = Path("wiring_census_report.json")

# Names resolved dynamically at runtime — referenced by pattern, not import.
_DYNAMIC_NAME_RE = re.compile(
    r"^(run_.*_strategy|_cmd_.*|backtest_.*|s_[a-z_]+|test_.*|main)$")

# Modules that are deliberately shelved/dead — documented decisions, not
# accidents. Keep in sync with CLAUDE.md / memory notes.
KNOWN_SHELVED = {
    "sahi_strategy", "backtest_sahi_strategy",   # loses in-sample; shelved
    "cvar_optimizer",                            # documented dead risk module
    "trade_guardian",                            # dead duplicate of manual tracker
}


def _repo_py_files() -> List[Path]:
    return sorted(p for p in Path(".").glob("*.py") if p.name != "__init__.py")


def _entrypoint_names() -> Set[str]:
    """Modules invoked directly: systemd units, crontab, shell scripts, or
    referenced as 'python x.py' anywhere."""
    names: Set[str] = set()
    hay = []
    for pat in ("*.sh", "*.service", "*.timer"):
        for p in Path(".").glob(pat):
            try:
                hay.append(p.read_text(errors="replace"))
            except Exception:
                continue
    # systemd units live outside the repo too
    for p in Path("/etc/systemd/system").glob("*.service"):
        try:
            hay.append(p.read_text(errors="replace"))
        except Exception:
            continue
    try:
        import subprocess
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                              timeout=5)
        hay.append(cron.stdout or "")
    except Exception as exc:
        logger.debug("crontab read: %s", exc)
    blob = "\n".join(hay)
    for m in re.finditer(r"([A-Za-z0-9_]+)\.py\b", blob):
        names.add(m.group(1))
    return names


def build_census() -> Dict[str, Any]:
    files = _repo_py_files()
    module_names = {p.stem for p in files}
    sources: Dict[str, str] = {}
    trees: Dict[str, ast.AST] = {}
    for p in files:
        try:
            sources[p.stem] = p.read_text(errors="replace")
            trees[p.stem] = ast.parse(sources[p.stem])
        except Exception as exc:
            logger.debug("parse %s: %s", p.name, exc)

    # 1. Import graph
    imported_by: Dict[str, Set[str]] = {m: set() for m in module_names}
    for mod, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    root = a.name.split(".")[0]
                    if root in module_names and root != mod:
                        imported_by[root].add(mod)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                if root in module_names and root != mod:
                    imported_by[root].add(mod)

    entrypoints = _entrypoint_names()
    has_main = {m for m, src in sources.items()
                if "__name__" in src and "__main__" in src}

    # Dynamic imports (importlib.import_module("backtest_cpr") etc.) are
    # invisible to the AST import graph — a module whose NAME appears as a
    # string literal in someone else's source is dynamically referenced.
    all_source = "\n".join(src for m, src in sources.items())
    def _dyn_referenced(name: str) -> bool:
        own = sources.get(name, "")
        others = all_source.replace(own, "", 1)
        return f'"{name}"' in others or f"'{name}'" in others

    modules: Dict[str, Dict[str, Any]] = {}
    orphans: List[str] = []
    for m in sorted(module_names):
        is_test = m.startswith("test_")
        importers = sorted(imported_by.get(m, set()))
        status = ("test" if is_test
                  else "imported" if importers
                  else "entrypoint" if (m in entrypoints or m in has_main)
                  else "dynamic" if _dyn_referenced(m)
                  else "ORPHAN")
        if m in KNOWN_SHELVED and status == "ORPHAN":
            status = "shelved(known)"
        modules[m] = {"status": status, "imported_by": importers[:8],
                      "n_importers": len(importers)}
        if status == "ORPHAN":
            orphans.append(m)

    # 2. Function census (public top-level defs in non-test modules)
    #
    # This used to run one full-repo regex scan per symbol.  As the strategy
    # surface grew, the nightly post-market tail could spend a long time in this
    # static audit after the expensive ML result was already complete.  Count
    # identifiers once instead; the word-boundary semantics are equivalent for
    # Python names and keep the census cheap enough for every post-market run.
    ident_re = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
    all_name_counts = Counter()
    own_name_counts: Dict[str, Counter[str]] = {}
    for mod, src in sources.items():
        counts = Counter(ident_re.findall(src))
        own_name_counts[mod] = counts
        all_name_counts.update(counts)
    unreferenced: List[Dict[str, str]] = []
    internal_only: List[Dict[str, str]] = []
    for mod, tree in trees.items():
        if mod.startswith("test_"):
            continue
        for node in getattr(tree, "body", []):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = node.name
            if name.startswith("_") or _DYNAMIC_NAME_RE.match(name):
                continue
            total = all_name_counts.get(name, 0)
            own = own_name_counts.get(mod, Counter()).get(name, 0)
            # one hit is the definition itself
            if total <= 1:
                unreferenced.append({"module": mod, "name": name})
            elif total == own:
                internal_only.append({"module": mod, "name": name})

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_modules": len(module_names),
        "orphan_modules": orphans,
        "shelved_known": sorted(m for m, v in modules.items()
                                 if v["status"] == "shelved(known)"),
        "unreferenced_functions": sorted(unreferenced,
                                         key=lambda d: (d["module"], d["name"])),
        "internal_only_functions_count": len(internal_only),
        "modules": modules,
    }
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("census report write: %s", exc)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    r = build_census()
    print(f"modules: {r['n_modules']}")
    print(f"ORPHANS ({len(r['orphan_modules'])}): {', '.join(r['orphan_modules'])}")
    print(f"known-shelved: {', '.join(r['shelved_known'])}")
    print(f"unreferenced public functions: {len(r['unreferenced_functions'])}")
    for f in r["unreferenced_functions"][:40]:
        print(f"  {f['module']}.{f['name']}")
