# DEAD_CODE.md — intentionally-kept dead code (DO NOT "clean up")

> This project exists partly because a prior AI session "cleaned up" / rewrote real
> files and broke the system (see CLAUDE.md). Per **hard rule 3**, the items below are
> **documented, not removed**. Do **not** delete them, and do **not** "fix" them by
> ripping out logic — several are deliberate, and others are harmless. Verify with
> `git blame` + the owner before touching anything here.
> Last verified: 2026-06-20 (pyflakes clean: 0 undefined names across 414 files).

## Intentionally-kept dead MODULES (keep — may be re-wired later)
- **`cvar_optimizer.py`** — a portfolio CVaR optimizer that is present but **never wired**
  into the execution path. Non-critical (it's an optimizer, not a hard gate). Keep; if
  ever wired, validate first.
- **`trade_guardian.py`** (+ `trade_guardian.service`, `trade_guardian_bot.py`) — a SECOND,
  `/in`-driven manual-trade manager. **Dead** because the owner never types `/in`
  (`trade_guardian.db` has 0 trades); the live manual path is `manual_trade_tracker.py`.
  Kept intentionally (owner's choice). Do not delete or re-activate without approval.

## Verified-BENIGN unused locals (cosmetic — NOT discarded risk-controls)
Confirmed 2026-06-20 by reading each site. These are dead *reads/imports*, not safety
controls — the real controls fire regardless. Safe to leave; if cleaned, do it as an
isolated, reviewed lint pass (never alongside logic changes):
- `main_autonomous.py:3065` `pnl` — computed for an alert but not passed; breaker still fires (3067).
- `main_autonomous.py:3366` `side` — extra read in the open-positions loop; loop uses tid/sym/ep.
- `main_autonomous.py:3581` `_cfg_pm = __import__("config")` — leftover from old direct
  paper-flag setting; the actual order block IS applied at 3583 (`_apply_order_block`).
- `main_autonomous.py:4514` `_feeds` — unused feed context in the 14:30 overnight-protection
  block (minor; `_vix`/`_has_event` are used). Low value to wire in; not a safety gap.

## Known `in dir()` guard FALSE-POSITIVES (do NOT "fix")
`if 'x' in dir():` is a systemic anti-pattern here (dir() is locals-only → usually False),
BUT these specific ones are intentional no-ops and removing them changes nothing / risks a
real NameError:
- `live_signal_engine.py` ~2771/2778 (`pnl` / `_alpha_factors`) — P&L is recorded at trade
  CLOSE via the trade_manager callback, not here. Leaving the guard is correct.
- `pattern_engine/base.py` ~94 `EngineConfig` — a `# noqa` forward reference.
- All `pd` "undefined" pyflakes hits are STRING type annotations (`def f(df: "pd.DataFrame")`)
  — never evaluated; pandas is imported locally where used. Do not add a module-level import.

## How to find/treat dead code here
- Detection: `python3 -m pyflakes <file>` (undefined-name = real bug; unused local =
  candidate). The dangerous class is a **risk control computed then never applied**
  (this bit the VIX option-buy gate and gap sizing before) — those are FIXED; re-scan
  with `grep F841` semantics if adding new sizing/gating code.
- Policy: dead ≠ delete. Document here, leave in place, and only remove with explicit
  owner approval and a reviewed diff.
