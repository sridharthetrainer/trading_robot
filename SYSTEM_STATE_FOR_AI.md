# SYSTEM_STATE_FOR_AI.md — Portable Project Context

> **Purpose.** A single, self-contained briefing you can paste into *any* AI assistant so it
> understands this project without re-deriving it. It consolidates `CLAUDE.md`, the long-running
> memory log, and verified code state.
> **Last updated:** 2026-06-20. **Mode:** PAPER. **Author of record:** Sridhar.
> **Honesty contract:** every claim below was measured or read from code. Where something is
> *unverified* or *data-gated*, it says so. Do not upgrade a "measured negative" into "promising."

---

## 0. The one-paragraph truth (read this first)

This is a **real, working, ~358-Python-file live NSE algorithmic trading system**, built over
months, running in **PAPER mode**. It scans ~190 NSE symbols, runs a **~57–75 strategy confluence
engine**, has a full **risk layer**, **ML/learning loop**, **manual-trade protection**, and
**nightly evidence pipelines**. The engineering is sound. **The unsolved problem is EDGE:** after a
rigorous, exhaustive edge hunt (10 intraday + 5 daily price-indicator families, options strategies
on real premia, participant-OI, cross-sectional momentum, vol-surface, and the system's own ML), **no
strategy or signal shows a validated, cost-surviving edge.** The binding constraint is
**information/edge**, not features, breadth, latency, or model sophistication. The evidence-driven
next steps are **data-gated**: they need weeks of live trading days to accrue labelled outcomes
before they produce trustworthy verdicts. Capital preservation comes first; do **not** assert
profitability and do **not** flip to live.

---

## 1. Working agreement (hard rules — follow strictly)

1. **Read before you touch.** Read the relevant files, explain what they do and what you'll change,
   wait for Sridhar's approval.
2. **One change at a time.** Show the diff. Never bulk-edit without approval.
3. **Never delete files, never mass-replace, never "rewrite clean."** A previous AI session damaged
   an earlier copy by generating placeholder/stub files over real ones. If something looks like a
   duplicate, **ask** — don't act.
4. **Never fabricate.** If unsure, say so and verify by reading/running. No "it works" without evidence.
5. **Stay PAPER. Don't claim profitability.** There is no validated profitable backtest. Capital
   preservation is priority #1.
6. **Use git.** Confirm a clean/committed tree before starting; prompt to commit after each accepted change.
7. **On phone / Remote Control:** review every diff; never auto-accept edits on mobile.
8. **Never `git add -A` in this repo.** `.env.template` / `.env.example` are tracked and hold real
   secrets — stage code files explicitly. Never print secret values.

---

## 2. Verified architecture

- **Entry point:** `main_autonomous.py` (single process; `trading-bot.service`).
- **Signal flow:** `main_autonomous.py` → `LiveSignalEngine` (`live_signal_engine.py`, orchestrator)
  → `generate_signal` in `signal_engine.py` (holds the strategy registry + confluence scoring).
  **Both engines are needed; neither is a duplicate.**
- **Strategy registry:** ~57–75 strategies invoked via a **signature-aware adapter** (`_invoke_strategy`)
  — fixes a bug where 16/75 strategies silently `TypeError`'d on every call and never voted.
- **Manual-trade protection:** `manual_trade_tracker.py` + `manual-tracker.service` (the live, canonical
  manual manager). `trade_guardian.py` is a parallel `/in`-driven manager that is **dead** (0 trades —
  user never types `/in`; kept, not removed).
- **Data sources (priority):** Angel `getCandleData` is **PRIMARY** for intraday candles (chunked for
  long spans); direct NSE APIs for option chain / indices / FII-DII; bhavcopy for EOD history; **yfinance
  is fallback only** and demoted behind `DISABLE_YFINANCE` (currently true).
- **Risk layer:** `value_at_risk.py`, `cvar_optimizer.py` (DEAD — never wired), `daily_loss_limit.py`,
  `gap_risk_manager.py`, `portfolio_risk.py`, `kill_switch.py`, `adaptive_position_sizer.py`.
- **Persistence:** SQLite. Key DBs: `signal_log.db` (logged signals + triple-barrier labels),
  `manual_trades.db`, `trades.db`, `options_nifty.db` (~3.1M rows real option EOD), `participant_oi.db`
  (~6yr participant OI + `nifty_daily`).
- **Capital:** fetched **LIVE** from Angel `rmsLimit()` (availablecash/net). `REAL_CAPITAL` in `.env`
  is only a fallback if the API call fails. (Live account balance has been ~₹77, which correctly forces PAPER.)
- **Thresholds (env-configurable):** `MIN_CONFLUENCE_SCORE`, `POST_CONFLUENCE_MIN_SCORE`,
  `SWING_MIN_SCORE`, `AI_MIN_SCORE_THRESHOLD`.

---

## 3. Deployment / services

Active systemd units (all healthy as of last audit): **`trading-bot`**, **`trading-bot-watchdog`**,
**`manual-tracker`**, **`auto-deploy`**. The old crash-looping stub units
(`autonomous-bot` / `system-bot` / `trades-bot` — leftover 5–8 line print-and-exit files from the
AI-damage era) are **disabled/gone**. `trade_guardian.service` is no longer active.

- `auto-deploy.service` redeploys on file changes → edits trigger bot restarts (a transient Telegram
  "another getUpdates consumer" conflict can appear during restart overlap — harmless).
- Passwordless restart is enabled only for `sudo systemctl restart trading-bot` and
  `manual-tracker.service` (NOPASSWD); installing new systemd timers needs a password → scheduling is
  done via **crontab**, not systemd timers.
- Nightly evidence pipeline runs via crontab: `16:30 IST Mon–Fri → post_market_ml.py` (runs
  `modifier_edge_analyzer`, `strategy_edge_analyzer`, `meta_labeler`, `eod_market_capture`, training).

---

## 4. THE EDGE VERDICT (most important section)

A rigorous harness exists: `validation_harness.py` (walk-forward + **locked holdout** + **deflated
Sharpe (DSR)** + parameter-stability + min-trade floor). Results in `validation_results.json`.
**PASS bar:** DSR ≥ 0.95, ≥ 30 trades/window, stability CV < 0.5, locked-holdout P&L > 0 & Sharpe > 0.

### Scoreboard: 0 validated edges, everywhere

| Edge source tested | Result |
|---|---|
| **10 intraday price-indicator families** (trend, mean_reversion, breakout, ma_cross, scalping, ema_5min, cpr/pivots, orb, vwap_reversion, supertrend_mtf) on NIFTY 5m, 420d | **0/10 PASS.** Best (breakout) hit dev Sharpe +0.40 best-of-360-trials → **DSR ≈ 0.00** (indistinguishable from luck). |
| **5 daily families** (trend/momentum) on ~7y daily NIFTY | All mildly positive dev Sharpe but **all FAIL** (DSR 0, or unstable, or too few trades). The one apparent PASS (supertrend_mtf, DSR 1.00) was a **bug**: `backtest_supertrend_mtf.py:184` hardcodes the 5-min annualization `√(252*75)` and applied it to daily bars → over-annualized Sharpe by ~8.66×. Corrected → mediocre (~+0.65), fails. **Fixed & committed** (`17713c28`, interval-aware annualization; confirms the FAIL, unlocks nothing). |
| **Option BUYING** (long straddle / CE-PE directional — what the user does manually) | **≈ −67%/yr**, win 29% — structural theta drag. Negative edge. |
| **Naked short strangle** | Passes OOS *within the model* (Sharpe ~6) but it's a **mirage**: one −5% gap = ~50 weeks of income; −7% = ~1.7 years in a day. **Not deployable.** |
| **Iron condor (defined-risk)** on **REAL** 6yr NIFTY premia (335 weekly condors) | ~71% win rate but **expectancy −0.036R**, PF 0.78, OOS −0.046R, and **−0.021R even at ZERO slippage**. Defined-risk ≠ profitable. The high win rate is the seller's trap. |
| **hero_zero (0DTE deep-OTM lottery)** real 54 expiries | win 11%, 89% worthless, PF 0.37. **Alert-only** (zero confluence vote unless `HERO_ZERO_LIVE_VOTE=true`). |
| **Participant-wise OI** (FII futures, Client contrarian) | **No edge** OOS. CRITICAL: OI is published ~8 PM (post-close) → must use `entry_lag ≥ 1`; the apparent edge was lookahead correlating with the same day's realized move. |
| **Cross-sectional equity momentum** (59 large-caps, 54 months) | **No alpha.** Long-short ≈ 0/negative every lookback (3m = short-term reversal); long-only top quintile is **bull beta**, collapses OOS. |
| **Vol-surface** (ATM IV, skew, term-ratio, PCR via BS inversion, ~2.4y) | **No directional alpha** (all ICs insignificant). Defensible only as a **bounded risk/context modifier** (already how it's designed), not a directional signal. |
| **SAHI tipster-log strategy** (892 lines, fully built) | Loses **in-sample** (PF 0.36, −₹358k, 39.9% win rate). Shelved — backtest-only. |
| **The system's own ML** | cross-symbol GradientBoosting CV **AUC 0.568** (< 0.62 usage gate → **not used live**). Univariate test of all 63 logged features: **none predictive**; the system's own confluence score has **AUC ≈ 0.50** vs outcomes — the selection layer is uncorrelated with whether its trades win. |

### What this means (and what it does NOT mean)
- **Parameters are NOT the bottleneck.** The grid already searched the textbook region
  (RSI14, ADX25, BB 2σ, Donchian20) and its best pick still gives DSR ≈ 0. "Just tune the indicators"
  cannot pass — demonstrated, not asserted (more grid combos → lower DSR, not higher).
- **More ML algorithms won't help.** The base signals are edgeless, so any model trained on their
  outcomes scores ~random and is correctly gated out. Edge must come from **new information**, validated
  under the same locked-holdout + DSR discipline, *then* ML can refine.
- **Caveat on magnitudes:** large |Sharpe| (e.g. −39) in the index-points backtests is **leverage**
  (fixed NIFTY 75-lot ≈ 18× notional on ₹100k), not a formula bug. The **sign** and
  **0%-profitable-windows** are reliable. The index-points tests are a **futures proxy**; the live bot
  trades **options** (theta + spreads = worse).
- **Honest conclusion:** the edge search across price-indicators (intraday+daily), ML, all logged
  features, equity momentum, and the options surface is **exhausted — none found**. The evidence-based
  posture is **paper / monitoring / manual-assist**, OR test a genuinely **new edge source** (order-flow,
  events, microstructure) under the same discipline before any live promotion.

---

## 5. ML / learning loop

**Engineering is sound** (likely the "overfitting prevention" work): TimeSeriesSplit CV (no shuffle),
StandardScaler inside the sklearn Pipeline (no scaling leak), GradientBoosting depth ≤ 3, feature count
capped, ML applied live **only if CV AUC ≥ 0.62** (fails safe), `learned_filters` clamped to
[0.80, 1.50] (bounded blast radius), `failure_autopsy` danger zones require ≥ 15 samples/bin.

**The loop was broken in 3 places, now fixed (2026-06-12):**
1. Triple-barrier **labeller was starved** (built `DataFetcher` with no Angel client) → all labels stayed −99.
2. **`executed` flag never set** → `mark_executed()` added and called at the trade-executed site.
3. **`angel.get_token` shadowing** — both token resolvers consulted the NFO master *before* the NSE
   cash map, so every NSE stock resolved to a derivative token and `getCandleData` returned SUCCESS with
   **0 candles**. The system silently survived on daily-bar fallbacks. Fixed: NSE requests consult
   `_load_nse_eq_tokens()` first. (A second variant — odd series overwriting the `-EQ` row — also fixed;
   `-EQ` always wins.)

After fixes: label backlog drained; nightly pipeline runs end-to-end. **`post_market_ml` keystone bug**
also fixed (commit 5e9eecd): `build_feature_matrix(executed_only=True)` returned 0 rows → pipeline aborted
every night; now defaults False.

**`edge_report.py`** (Wilson-bounded WR breakdowns) finding to watch: at the top end, **engine score ≥ 18
is the WORST bucket** — the confluence score is *inversely* related to outcomes there → score-based gating
may be anti-predictive; needs recalibration once the calibrator trains. (Refresh `edge_report.json`; don't
trust the snapshot.)

---

## 6. Confluence-modifier instrumentation (recently completed)

**The bug:** ~30 confluence modifiers were computed and folded into the score but **discarded before
logging** — `signal_log` stored the schema DEFAULT (0) for every one, so the ML trained on dead-constant
features. This was a self-inflicted part of the information bottleneck.

**Fixed end-to-end** (producer → harvest → persistence). Verified 2026-06-20:
- **Producers** (`signal_engine.py`): write into `_sig_meta` / `_cand_meta`.
- **Harvest** (`live_signal_engine.py`): key-list collects them.
- **Persistence** (`signal_log.py`): schema DDL + self-heal `_new_cols` ALTER migration + write path.

Modifiers now logged include: `bhav_delivery, cross_asset, time_bucket, participant_oi, expiry_regime,
sip_boost, bulk_deal, theta, rebalancing, news, mtf_pivot, gex_mod, skew_mod, whale_mod, sr_level_mod,
pivot_boss_mod, oi_mod`, and (commits ab29b8fe / 6b18662d / 3b2985d8, 2026-06-20)
**`weinstein_mod, sector_mod, crsi_mod, nr_mod, volume_mod`**.

> **Instrumentation removes blind spots; it does NOT create edge.** `modifier_edge_analyzer.py`
> (significance-gated: Welch t-test + Bonferroni + temporal stability) measures each nightly and reports
> HELPS / HURTS / DEAD / NOISE → `modifier_edge_report.json`. Verdicts stay **DEAD until live days accrue**.

Still not independent signals: `rl_bias` (has a live producer), `ai_score` (redundant — `signal_log`
writes `ai_score = signal.confidence`). `news_mod` was dead at source (needed an unset `NEWS_API_KEY`) →
rewired to `omnisource_news_engine` (30 sources, no key).

---

## 7. Safety / order-path posture

**Status (2026-06-20):** PAPER is now a **config guarantee**. `.env` has `PAPER_TRADING=true` and
`ENABLE_REAL_TRADING=false`; verified at the config layer (`config.PAPER_TRADING == True`,
`config.ENABLE_REAL_TRADING == False`). [config.py:59](config.py#L59) forces real trading off whenever
`PAPER_TRADING` is true regardless of the other flag, so the guarantee is self-reinforcing. Data/scanning
is unaffected ([angel.py:320](angel.py#L320) "ALWAYS connect for DATA"). The running bot must be restarted
to load the change. *(History: previously the bot was configured for LIVE and ran PAPER only because a
runtime order-block fired when the startup balance check couldn't confirm a real Angel balance
(`_apply_order_block(True)` → `config.PAPER_ORDERS_ONLY=True`) — i.e. paper safety leaned on a runtime flag.
That fragile dependency is now removed.)*

Order-path hardening done (all strictly safety-additive — block more, never fewer):
- Primary path (`angel.place_order`) now honors `block_real_orders` (was enforced only on the SL/GTT path).
- `place_sl_order` / `place_gtt_order` now honor `block_real_orders` / `PAPER_ORDERS_ONLY` too.
- Live-engine kill-switch check was **dead** (built a throwaway in-memory `KillSwitch` that can never fire);
  now reads `trade_manager.trading_locked` with a kill-switch lock reason.
- VIX option-buy gate and gap-risk sizing were **computed-then-discarded locals** (logged a reduction that
  never applied) — both now stored on `self` and enforced.

Risk layer otherwise sound (all enforced with evidence): `daily_loss_limit.can_trade()`, VaR size-down,
`PortfolioRiskManager` qty override, `AdaptivePositionSizer`, `gap_risk`. **DEAD:** `cvar_optimizer`
(listed but never wired — non-critical).

**Systemic anti-patterns to watch** (this codebase's dominant failure mode): a `NameError` inside a
`try/except` is swallowed → a line/feature goes silently DEAD. Run `pyflakes <hotpath>.py | grep "undefined name"`
regularly. Also treat every `if 'X' in dir():` guard as suspect — `dir()` returns locals only, so the guard
is permanently False for a module global → silently disables the feature while looking safe.

---

## 8. Production-incident history ("Scanned: 0" family)

When you see **Scanned: 0 + Angel reachable**, suspect **token-resolution / rate-limit**, not connection:
- **Original 2-month bug:** `get_historical_data` called a non-existent `self._get_token()` →
  silent AttributeError → all fetches None → <50 bars → 0 candidates. Fixed.
- **2026-06-13 (caught pre-Monday):** `generate_signal` called `time.time()` but `time` was only
  imported function-locally → would NameError every signal. Fixed (module-level import).
- **2026-06-15 rate-limit storm:** stale/renamed universe symbols → `searchScrip` storm (which bypassed
  the rate-limit breaker) → Angel account-wide throttle → valid symbols also failed. Fixed: negative token
  cache + breaker on `_search_scrip_safe`; `nifty200.csv` cleaned (200→192 rows).
- **2026-06-16:** `_passes_1h_filter` was *called but never defined* (half-built uncommitted WIP) →
  AttributeError on every symbol with ≥10 1h bars. Fixed (method defined, fails safe). End-to-end proof
  (Scanned > 0) was pending the next open at the time of writing.

---

## 9. Data sources & databases

- **Intraday candles:** Angel `getCandleData`, chunked (`get_historical_data` stitches spans past Angel's
  ~100-calendar-day/request cap for 5m; verified 365d → ~18,392 bars). yfinance demoted.
- **Option chain:** NSE `api/option-chain-indices` 404s persistently (since ~2026-06-10). **Angel fallback
  is fixed & wired** (`NSEOptionChainFetcher.fetch()` chain: resilience → Sensibull → direct NSE → Angel
  → cache); real OI via `getMarketData("FULL", ...)` (`opnInterest` field). `fetch()` is cache-only outside
  market hours. Open: verify the live engine path actually uses `fetch()` during market hours.
- **FII/DII CASH:** `fii_data_fetcher.fetch_nse_fii_dii_today()` → `fii_history.csv` (90-day cap).
- **FII F&O positioning:** `participant_oi.db` (overlaid from the `fao_participant_oi_DDMMYYYY` archive;
  the live JSON endpoints are dead → all-zeros). Backfill is kept fresh nightly by `eod_market_capture`.
  **Limitation:** intraday FII positioning is **BLIND** — only EOD data exists (published ~8pm,
  `entry_lag ≥ 1`); never treat it as a live intraday signal (documented at `participant_oi.get_participant_data`).
- **Sector rotation:** `sector_rotation_engine` → `sector_history.csv` (180-day cap).
- These three new historical series are joined into ML by date (`market_context_by_date()` →
  hist_fii_net / hist_fii_cum5 / sector_breadth); they fill in as trading days accrue.

---

## 10. Manual-trade system (what Sridhar actually trades)

`manual_trade_tracker.py` auto-discovers real manual trades and protects them with a **hybrid option stop**:
broker **GTT = deep catastrophe floor** (60% premium) + **primary intraday stop = the underlying breaking
structure** (long CALL exits when the underlying closes below its recent swing low; long PUT above swing
high; 5-min candles). The 60% floor only **arms** when the trade's underlying candles are fetchable;
otherwise the trade safely keeps the tight 30% premium GTT (no trade is under-protected). Env:
`MANUAL_STRUCT_STOP`, `MANUAL_CATASTROPHE_SL_PCT`, `MANUAL_STRUCT_CHECK_SECS`, `MANUAL_STRUCT_SWING`.

`manual_book_risk.py` adds a read-only portfolio-risk snapshot over open manual trades (the manual path
previously imported none of the risk modules). `main_autonomous._check_manual_book_risk()` runs daily
post-close (15:45) and Telegram-alerts on unstopped positions / portfolio risk > 5% (report-only, no auto-close).

---

## 11. Other bots & sibling project

- **`nifty_scalper_bot.py`** — standalone **signal-only** Telegram bot (never places orders): 5-vote
  confluence on Angel 1m/5m, full trade cards (entry/SL −30%/target +45%/time-stop/lot sizing). Env
  `SCALPER_BOT_TOKEN` + `SCALPER_CHAT_ID`. Not a service yet. **Unvalidated** like everything — keep labelled PAPER.
- **`advisory_engine/`** — additive package: parses advisory texts → validates vs MTF data → risk-gates →
  paper-default OCO. `python -m advisory_engine.selftest` (21 checks). **NOT wired** into the live loop.
- **`~/Desktop/options_platform`** — a **separate, standalone** institutional NIFTY/BANKNIFTY options
  research platform (own git repo), built evidence-first / phase-gated per a "FINAL MASTER DIRECTIVE."
  All phases (0→11) built, 261 tests green; positive-control proves its DSR gate detects edge when it
  exists (so the INVALID verdicts are honest). It **reuses trading_robot as a vetted library**, does NOT
  extend the monolith. ⚠️ **Requirement:** it must use a **SEPARATE Telegram bot/channel** — never reuse
  trading_robot's token/chat id.

---

## 12. Security state (act on these)

- **`.env` must never be committed** (in `.gitignore`). The `.env.template` / `.env.example` files are
  **no longer tracked and removed from disk** (commits `411514d5`, `d8736406`); `git ls-files` is clean.
- **Credential rotation — DECLINED by the user (2026-06-20):** the user has chosen to **keep the same
  credentials and not modify `.env`**, consciously accepting the residual exposure. Do **not** re-raise
  rotation. *(Risk record for context only: the historically-leaked values — Angel
  `API_KEY/CLIENT_ID/PASSWORD/TOTP_SECRET`, `GITHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TWITTER_*`,
  `FYERS_TOKEN` — still exist in OLD local git history (commit `5407640`); `origin/main` is a clean
  squashed snapshot and the repo is private/local. A non-secret `SECRETS_ROTATION_CHECKLIST.md` exists as
  reference if that decision is ever reversed.)*
- **Never `git add -A`** here — always stage code files explicitly.
- **Git remote:** `origin = github.com/sridharthetrainer/trading_robot.git`. Local history (176 commits) is
  **unrelated** to origin by design — origin/main is a clean squashed snapshot (v1.5.0). Future pushes need
  the same clean-snapshot dance (commit-tree + force-push) or a reset. Auth via `GITHUB_BACKUP_TOKEN` in
  `.env` through a `GIT_ASKPASS` script (token never in argv). Never use secret-scanning bypass URLs (they
  publish the secret).

---

## 13. Pending / next steps (all DATA-GATED — act on evidence, not guesswork)

1. **After dozens of distinct labelled trading days accrue**, run `modifier_edge_analyzer.py` (and
   `meta_labeler.py`, `strategy_edge_analyzer.py` — all run nightly in `post_market_ml`) and **PRUNE the
   confluence modifiers that measure NOISE/HURTS**. Removing dilution from the few real signals beats
   adding more. Today `signal_log` has only a handful of clean labelled days → every evidence report is
   premature; the MIN_DAYS guards correctly refuse. **The binding constraint right now is DATA ACCRUAL.**
2. **Do NOT** expand the universe (nifty_200 → 500) or add a streaming scan until a strategy/modifier set
   shows a validated edge worth scaling. The constraint is edge, not breadth or latency.
3. **Decision already on the table for Sridhar:** the price-indicator/ML/feature/momentum/options-surface
   edge space is exhausted with **zero** validated edges → either (a) accept the system as
   **paper / monitoring / manual-assist**, or (b) test a genuinely **new edge source** (order-flow, events,
   microstructure) under the **same** locked-holdout + deflated-Sharpe discipline.
4. **DONE (committed `17713c28`):** interval-aware annualization in `backtest_supertrend_mtf.py:184`
   (only confirmed the FAIL; unlocked nothing).

---

## 14. Key files & where to look

| Concern | Files |
|---|---|
| Orchestration | `main_autonomous.py`, `live_signal_engine.py` |
| Strategy registry + confluence | `signal_engine.py`, `_invoke_strategy` adapter |
| Validation (the harness that matters) | `validation_harness.py` → `validation_results.json` |
| Weak harness (data-snoops; do NOT trust) | `walk_forward_backtest.py` → `walk_forward_results.json` |
| Options edge | `options_backtest.py`, `condor_backtest_real.py`, `options_bhavcopy_backfill.py` |
| Other edge probes | `participant_oi_edge.py`, vol-surface reconstruction, cross-sectional momentum |
| ML / learning | `ml_trainer.py`, `post_market_ml.py`, `ml_feature_builder.py`, `learned_filters.py`, `failure_autopsy.py`, `meta_labeler.py` |
| Evidence analyzers (nightly) | `modifier_edge_analyzer.py`, `strategy_edge_analyzer.py`, `edge_report.py`, `eod_market_capture.py` |
| Logging | `signal_log.py` → `signal_log.db` |
| Risk | `value_at_risk.py`, `daily_loss_limit.py`, `gap_risk_manager.py`, `portfolio_risk.py`, `kill_switch.py`, `adaptive_position_sizer.py` |
| Manual book | `manual_trade_tracker.py`, `manual_book_risk.py` |
| Broker/data | `angel.py`, `data_fetcher.py`, `option_chain_fetcher.py`, `fii_data_fetcher.py`, `participant_oi.py` |
| Diagnostics | `diag.py`, `diag_scan.py`, `validate_env.py`, `check_connections.py` |

**Companion docs already in the repo** (this file summarizes/links them; it does not replace them):
`CLAUDE.md` (rules + verified architecture), `ARCHITECTURE.md`, `PROJECT_HANDOFF.md`,
`VALIDATION_FINDINGS.md`, `COMPETITOR_AUDIT.md`, `DATA_PIPELINE_AUDIT.md`, `MANUAL_TRADE_SYSTEM.md`,
`TWO_SYSTEMS_ARCHITECTURE.md`, `DEPLOY.md`.

---

## 15. How to onboard a new AI with this file

1. Paste this file. Then state the immediate task.
2. The AI must obey Section 1 (read-before-touch, one-change-at-a-time, never delete/rewrite, never
   fabricate, stay PAPER, explicit git staging, never print secrets).
3. Treat Section 4 as settled: **no validated edge exists**; do not "find" one without passing
   `validation_harness.py` (DSR ≥ 0.95 + positive locked holdout). "Correct" ≠ "profitable."
4. Most "improve the signals" work is **data-gated** (Section 13) — it needs live trading days, not code.
