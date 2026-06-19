# CLAUDE.md — Project Context & Rules

## What this project is
A real, working live NSE algorithmic trading system (~228 Python files), built over
months. This is NOT a scaffold or a demo. A previous AI session damaged an earlier
copy by generating placeholder/stub files and overwriting real ones. Do not repeat
that. Your job is to audit, understand, and improve this system with small,
reviewable changes — never to rewrite or replace it.

## Hard rules (follow strictly)
1. Read before you touch. Before changing anything, read the relevant files, explain
   what they do and what you intend to change, and wait for my approval.
2. One change at a time. Show me the diff. Never bulk-edit without approval.
3. Never delete files, never mass-replace, never create a "fresh"/"clean" rewrite of
   an existing module. If something looks like a duplicate, ask before acting.
4. Never fabricate. If you are unsure something works, say so and verify by reading
   or running it. Do not claim it works without evidence.
5. Keep the system in PAPER mode and do not assert it is profitable — there is no
   validated backtest yet. Capital preservation comes first.
6. We use git. Confirm the working tree is committed before starting. After each
   accepted change, prompt me to commit.
7. On phone / Remote Control: review every diff before accepting. Do not enable
   auto-accept of edits on mobile.

## Verified architecture (correct only with file/line evidence)
- Entry point: `main_autonomous.py`
- Signal flow: `main_autonomous.py` -> `LiveSignalEngine` (`live_signal_engine.py`,
  orchestrator) -> `generate_signal` in `signal_engine.py` (holds the ~57-strategy
  registry + confluence scoring). BOTH engines are needed; neither is a duplicate.
- Manual-trade protection: `manual_trade_tracker.py` + `manual-tracker.service`.
- Data sources (priority): Angel `getCandleData` is PRIMARY for intraday candles;
  direct NSE APIs for option chain / indices / FII-DII; bhavcopy for EOD history;
  yfinance is fallback only (treat as unreliable; prefer demoting it).
- Risk layer: `value_at_risk.py`, `cvar_optimizer.py`, `daily_loss_limit.py`,
  `gap_risk_manager.py`, `portfolio_risk.py`, `kill_switch.py`,
  `adaptive_position_sizer.py`.
- Persistence: SQLite (tables include `trades`, `manual_trades`,
  `manual_trade_updates`, `strategy_scores`, `eod_ml_feedback`).
- Capital: fetched LIVE from Angel `rmsLimit()` (availablecash/net). `REAL_CAPITAL`
  in `.env` is only a fallback if the API call fails.
- Thresholds (env-configurable): `MIN_CONFLUENCE_SCORE`, `POST_CONFLUENCE_MIN_SCORE`,
  `SWING_MIN_SCORE`, `AI_MIN_SCORE_THRESHOLD`.

## Known state
- The old "paper_trade blocks Angel connection -> Scanned: 0" bug is FIXED:
  `angel.py` connects unconditionally for data (search "ALWAYS connect for DATA").
- EDGE IS NOW MEASURED (was "#1 GAP: UNVALIDATED"): `validation_harness.py`
  (walk-forward + locked holdout + deflated Sharpe + parameter-stability +
  min-trade) saves to `validation_results.json`. Result (2026-06-14): NO rule
  strategy passes OOS after costs — every one FAILs; the lone high Sharpe
  (supertrend_mtf) is correctly rejected as overfit on <30 trades/window. The
  edge is measured and currently NEGATIVE/absent, not merely unvalidated.
- Modifier instrumentation was DEAD until 2026-06-14: the ~17 confluence score
  modifiers were computed then DISCARDED before logging, so `signal_log` stored 0
  for all of them and the ML trained on dead-constant features. Now captured
  end-to-end (`signal_engine` -> `live_signal_engine` harvest -> `signal_log`);
  `modifier_edge_analyzer.py` measures each nightly. Verdicts stay DEAD until data
  accrues over trading days.
- Diagnostics that already exist: `diag.py`, `diag_scan.py`, `validate_env.py`,
  `check_connections.py`. `_rejection_stats` in the live engine records
  total/passed/reasons per scan.

## Pending improvements
The original #1-#6 list is DONE — do NOT re-do it:
1. validate a strategy + save → `validation_harness.py` + `validation_results.json`
2. capital fail-safe → balance-fetch failure blocks REAL orders + falls to PAPER
   (`main_autonomous.py` ~3504), never trades real on a guessed number
3. demote yfinance behind a flag → `DISABLE_YFINANCE` (config/data_sources/data_fetcher)
4. standalone overfitting validation harness → `validation_harness.py`
5. `ARCHITECTURE.md` → present
6. calibrator min-sample guard → `score_calibrator.has_min_samples`, wired at
   `strategy_performance_matrix.py:86`

Real next step (DATA-GATED — needs live trading days to populate, then act on
EVIDENCE, not guesswork):
1. After ~3 trading days, run `modifier_edge_analyzer.py` (also runs nightly in
   `post_market_ml`) and PRUNE the confluence modifiers that measure NOISE/HURTS.
   Removing dilution from the few real signals beats adding more.
2. Widen instrumentation to still-unlogged inputs. STATUS (audit 2026-06-14):
   `rl_bias` NOW has a live producer (`_rl_score_adjustment` in
   live_signal_engine, fed at ~1436/1798 — note was stale). `ai_score` is NOT
   model-driven: `signal_log` writes `ai_score = signal.confidence`
   (signal_log.py:279), so as an ML feature it is redundant with confidence,
   not an independent AI signal. `weinstein_mod` still has no producer (logged
   as DEFAULT 0). Still unlogged: sector-rotation score; inline
   connors_rsi/nr7/volume bonuses.
3. The binding constraint is EDGE/INFORMATION, not feature count, breadth, or
   latency. Do NOT expand the universe (nifty_200->500) or add a streaming scan
   until a strategy/modifier set shows a validated edge worth scaling.

## Security note
`.env` / `.env.template` has historically contained real broker credentials, a
GitHub token, and API keys. Never commit `.env` (it must be in `.gitignore`). These
keys should be rotated. Do not print secret values in chat or logs.
