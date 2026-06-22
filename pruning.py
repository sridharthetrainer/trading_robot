#!/usr/bin/env python3
"""
pruning.py — safe, REVERSIBLE gate-off of strategies/modifiers.

EDGE_STRATEGY says "reduce, don't add" — but per hard rule 3 we NEVER delete code.
This disables strategies/modifiers BY NAME via config + a JSON list, so reduction
is one reversible edit and nothing is destroyed.

  • DISABLED_STRATEGIES / DISABLED_MODIFIERS — env (comma-separated)
  • pruned.json — {"strategies": [...], "modifiers": [...]}  (operator-controlled)
  • prune_suggestions.json — written by suggest_from_analyzers() from the nightly
    edge analyzers' HURTS/NOISE verdicts. SUGGESTIONS ONLY — promotion to pruned.json
    is a deliberate operator step (data-gated; never auto-disable on noisy early data).

A disabled strategy is skipped in the registry; a disabled modifier has its captured
contribution subtracted from the score (the instrumentation makes this clean).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Set, Tuple

PRUNED_FILE = "pruned.json"
SUGGEST_FILE = "prune_suggestions.json"


def _from_env(name: str) -> Set[str]:
    return {x.strip() for x in os.getenv(name, "").split(",") if x.strip()}


def load_pruned(path: str = PRUNED_FILE) -> Tuple[Set[str], Set[str]]:
    """Return (disabled_strategies, disabled_modifiers) from env + pruned.json."""
    strat = _from_env("DISABLED_STRATEGIES")
    mods = _from_env("DISABLED_MODIFIERS")
    try:
        d = json.loads(Path(path).read_text())
        strat |= set(d.get("strategies", []) or [])
        mods |= set(d.get("modifiers", []) or [])
    except Exception:
        pass
    return strat, mods


# Loaded once at import (operator restarts the bot to apply a prune — same as other
# config). Cheap to reload via refresh() if needed.
_DISABLED_STRATEGIES, _DISABLED_MODIFIERS = load_pruned()


def refresh() -> None:
    global _DISABLED_STRATEGIES, _DISABLED_MODIFIERS
    _DISABLED_STRATEGIES, _DISABLED_MODIFIERS = load_pruned()


def strategy_disabled(name: str) -> bool:
    return name in _DISABLED_STRATEGIES


def disabled_modifiers() -> Set[str]:
    return set(_DISABLED_MODIFIERS)


def suggest_from_analyzers() -> dict:
    """Build prune SUGGESTIONS from the nightly edge analyzers (report-only).
    Modifiers: HURTS + NOISE. Strategies: those the strategy edge analyzer flags
    as significantly losing / unstable, if that report exists. Writes
    prune_suggestions.json. Does NOT disable anything."""
    from datetime import datetime
    sug = {"generated": datetime.now().isoformat(timespec="seconds"),
           "modifiers": [], "strategies": [],
           "note": "SUGGESTIONS ONLY — promote to pruned.json deliberately, data-gated"}
    try:
        m = json.loads(Path("modifier_edge_report.json").read_text())
        s = m.get("summary", {}) if isinstance(m, dict) else {}
        sug["modifiers"] = sorted(set((s.get("hurts", []) or []) + (s.get("noise", []) or [])))
    except Exception:
        pass
    try:
        e = json.loads(Path("edge_analysis_last_run.json").read_text())
        if isinstance(e, dict):
            losing = [k for k, v in e.items()
                      if isinstance(v, dict) and str(v.get("verdict", "")).upper().startswith(("HURT", "LOSE", "AVOID"))]
            sug["strategies"] = sorted(set(losing))
    except Exception:
        pass
    try:
        Path(SUGGEST_FILE).write_text(json.dumps(sug, indent=2))
    except Exception:
        pass
    return sug


def status() -> dict:
    return {
        "disabled_strategies": sorted(_DISABLED_STRATEGIES),
        "disabled_modifiers": sorted(_DISABLED_MODIFIERS),
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Prune mechanism (gate off, reversible)")
    ap.add_argument("--suggest", action="store_true", help="Build prune_suggestions.json from analyzers")
    args = ap.parse_args()
    if args.suggest:
        print(json.dumps(suggest_from_analyzers(), indent=2))
    else:
        print(json.dumps(status(), indent=2))
        print("\nTo prune: add names to pruned.json (or DISABLED_* env) and restart. Reversible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
