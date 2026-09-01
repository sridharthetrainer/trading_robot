# AUDIT REQUEST — NSE algo trading bot deployment (fresh migration to new host)

## CONTEXT
I (an AI assistant, Claude Code) just finished migrating and deploying a live
NSE algorithmic trading system onto a new Linux Mint 22.3 host, and set up a
second, unrelated paper-trading bot's memory limits on the same host. I'm
asking a second AI to independently audit what was done, since this is a real
trading system (currently in PAPER mode, no real capital at risk from these
changes) and I want a second set of eyes on the deployment before trusting it
unattended.

## WHAT THE PROJECT IS
- Repo: `/home/owner/Desktop/trading_robot` — ~228 Python files, NSE (Indian
  stock exchange) algorithmic trading system, months of prior development.
  Entry point: `main_autonomous.py` -> `LiveSignalEngine` (`live_signal_engine.py`)
  -> `generate_signal` in `signal_engine.py` (~57-strategy registry + confluence
  scoring).
- It was COPIED from another machine (previously ran as user "sridhar" at
  `/home/sridhar/Projcts/trading_robot`) onto this machine (user "owner" at
  `/home/owner/Desktop/trading_robot`). This was a file copy, not a fresh
  install — the repo's own project doc (CLAUDE.md) states an earlier AI
  session had previously damaged a copy of this same project by generating
  placeholder/stub files, so this migration was done cautiously: read files
  before changing them, one change at a time, explicit user approval before
  each step, no bulk edits, no rewrites.
- CLAUDE.md (checked into the repo) states: the system's edge has been
  measured via `validation_harness.py` (walk-forward + locked holdout +
  deflated Sharpe) and as of 2026-06-14 NO rule-based strategy passes
  out-of-sample after costs — edge is measured and currently
  negative/absent, not just "unvalidated." The system must stay in PAPER
  mode; no claim of profitability is authorized.

## WHAT WAS FOUND BROKEN ON ARRIVAL (verified by reading/running, not assumed)
1. `.venv` was the old machine's virtualenv, copied byte-for-byte — its
   `pyvenv.cfg` pointed to `/home/sridhar/...`, and `.venv/bin/python3` and pip
   binaries didn't exist at all (just dangling shebang text files).
2. `git` was not installed on the new host at all (the `.git` directory was
   present from the copy, but the git binary was missing from PATH).
3. 8 systemd unit files existed IN the repo (`trading-bot.service`,
   `trading-bot-watchdog.service`, `trade_guardian.service`,
   `manual-tracker.service`, `option-chain-recorder.service`,
   `post-market-ml.service`/`.timer`, `daily-pipeline.service`/`.timer`) but were
   never installed into systemd on this host, and all hardcoded
   `User=sridhar` and `WorkingDirectory=/home/sridhar/Projcts/trading_robot`.
4. `.env` already had `PAPER_TRADING=true` (confirmed safe starting state).

## WHAT WAS DONE THIS SESSION (in order, each step shown to/approved by the user)
1. Installed `git` and `python3.12-venv` via apt (required sudo; user ran the
   command themselves — I don't have/use sudo credentials).
2. Rebuilt `.venv` from scratch (`python3 -m venv .venv`), installed all 84
   packages from `requirements.txt` successfully (includes pandas, numpy,
   scikit-learn, TensorFlow 2.21.0, SmartAPI/Angel broker SDK, python-telegram-
   bot, etc.). No install errors.
3. `git status` showed ~100 modified tracked files (all runtime-generated:
   JSON reports, logs, `.db` files, caches — not source code) plus some
   untracked report/audit snapshots, pre-existing from before this session.
   With explicit user approval, committed this as a single baseline snapshot
   commit (message notes it's runtime-state drift, not code changes) so the
   tree is clean going forward. Working tree is now clean
   ("nothing to commit, working tree clean").
4. Edited ONLY 2 of the 8 service files (user chose "core only: trading-bot +
   watchdog" over installing all 8): replaced `User=sridhar` -> `owner`,
   `/home/sridhar/Projcts/trading_robot` -> `/home/owner/Desktop/trading_robot`
   in WorkingDirectory/Environment=PATH/ExecStart. Diff was shown to the user
   before applying. Validated both with `systemd-analyze verify` (no errors).
5. Added systemd memory accounting to both (per explicit user-approved
   values):
   - `trading-bot.service`: `MemoryHigh=1300M` `MemoryMax=1536M`
   - `trading-bot-watchdog.service`: `MemoryMax=256M`

   Rationale given to user: main bot imports pandas/numpy/TensorFlow so gets
   the larger budget; the watchdog (`watchdog.py`) only does
   os/psutil/sqlite3/signal-level monitoring, so gets a much smaller cap.
6. User ran (with sudo): copied both units to `/etc/systemd/system/`,
   `daemon-reload`, `systemctl enable --now` on both. Confirmed via
   `systemctl status`: both "active (running)", enabled (will auto-start on
   boot and be restarted by systemd on crash — `Restart=always` on both units).
7. Observed live: `trading-bot.service` Main PID logging Upstox data fetches
   (e.g. "Upstox V2 ✅ INFY 1m: 750 bars") — actively scanning, no crash
   loop observed in the ~1 minute watched.

## SEPARATE SYSTEM ON THE SAME HOST (unrelated project, found while checking overall host RAM pressure)
- `/home/owner/Desktop/delta_quant_autonomous` — a different, unrelated
  crypto (BTCUSD) paper-trading research system. Already running BEFORE this
  session started, already properly deployed as a systemd `--user` service
  (`delta-quant-live-loop.service`) with `loginctl enable-linger` (so it
  already auto-started at boot independent of login). I did not build this
  — I found it already correctly set up, just missing a memory cap.
- Added, live, without restarting the running process (used
  `systemctl --user set-property … MemoryHigh=400M MemoryMax=512M`, which
  writes a persistent drop-in under `~/.config/systemd/user.control/` — verified
  this is the persistent variant, not the runtime-only one under `/run`):
  confirmed same PID/start-time before and after, so zero interruption.

## CURRENT VERIFIED STATE (all read directly from systemctl/free, not inferred)
As of 2026-09-02 04:31 IST:
- `trading-bot.service`: active, ~350M used / 1.5G cap
- `trading-bot-watchdog.service`: active, ~14M used / 256M cap
- `delta-quant-live-loop.service`: active, ~116M used / 512M cap
- Host: 7.7GB RAM total, ~3.7GB "available" (reclaimable), swap in modest use
  (~623Mi/1.9GB, attributed to Firefox/VSCode, not the bots).
- Both trading services independently confirmed to auto-start at boot/power
  restore (systemd system service `enable`d + user service `linger`).

## WHAT I HAVE NOT DONE / EXPLICITLY OUT OF SCOPE THIS SESSION
- Did NOT install/enable the other 6 service files (`trade_guardian`,
  `manual-tracker`, `option-chain-recorder`, `post-market-ml`, `daily-pipeline`) —
  user chose core-only for now.
- Did NOT touch any Python source/strategy code, signal logic, risk config,
  or `.env` values.
- Did NOT assert or verify profitability/edge — CLAUDE.md is explicit that
  no rule strategy currently passes out-of-sample validation.
- Did NOT restart or modify `delta-quant-live-loop.service` beyond the live
  memory-cap property change.
- Shell scripts (`bot.sh`, `deploy.sh`, etc.) lost their executable bit somewhere
  in the original host-to-host copy (mode 755->644, visible in the baseline
  commit's diff) — noted but NOT fixed, since nothing in this session's
  path (systemd calling python3 directly) depends on them being executable.

## PLEASE AUDIT
1. Are there any systemd unit-file mistakes (ordering, restart-loop risk
   between `trading-bot.service` and its watchdog, missing hardening flags,
   the `MemoryMax` values being unsafe too low/high for a pandas+TensorFlow
   process under real intraday load) that I should have caught?
2. Is treating ~100 modified runtime-state files as a single "baseline
   snapshot" commit defensible, or should some of those files (logs, `.db`
   files, caches) have been added to `.gitignore` and untracked instead of
   committed? (`.gitignore` already excludes `.env` and `.venv` and `*.db` in most
   cases, but the repo evidently has some tracked `.db`/`.log` files predating
   this session.)
3. Any risk from the `MemoryMax` hard caps causing an OOM-kill of the live bot
   mid-signal-generation during market hours, and whether `MemoryHigh` alone
   (soft, throttling) would have been the safer choice over pairing it with
   a hard `MemoryMax` on a live trading process, given `Restart=always` masks
   the kill as just a restart but still means a live gap.
4. Anything about the cross-project RAM isolation (two independent systemd
   scopes, system vs user manager) that could still let one process's I/O
   or CPU contention affect the other, despite memory being capped
   separately.
