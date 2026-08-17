# System Infrastructure Audit — 2026-08-13

Prompted by an external "Autonomous Adaptive Trading Engine" spec (50 sections, 18 phases)
asking for a from-scratch build of strategy discovery, regime detection, correlation-aware
ensembling, Monte Carlo robustness, drift detection, adaptive sizing, and risk governance.
Before building anything, this audit checks what already exists — via three parallel
read-only codebase investigations — so new work fills real gaps instead of duplicating or
destabilizing working infrastructure.

**Headline finding:** almost everything the spec asks for already exists in some form. The
real problem isn't absence, it's fragmentation — several capabilities exist as two
independent, non-integrated implementations, one is a live-wired stand-in that presents as
the real thing, and one is genuinely disabled. Only one capability (drift detection) is
genuinely absent.

## Regime engine

**Two independent, non-integrated regime engines exist.**

- `regime.py` — live-wired into `signal_engine.py:741-769,1263-1277` (reached by
  `live_signal_engine.py`/`main_autonomous.py`). `detect_market_regime(df) -> str`
  (`regime.py:107-129`) returns one hard label: `TREND / EARLY_TREND / RANGE / BREAKOUT /
  VOLATILE / NO_TRADE` (`regime.py:6-12`). Dimensions: ADX trend strength (`:41-43,149`),
  EMA20/50 alignment (`:150-151,182-185`), ATR% volatility (`:48,170-173`), single-bar
  breakout magnitude (`:49,206-208`).
- `market_regime.py` — offline-analysis only, used by `regime_breakout_analysis.py`. Also
  hard-classified (`classify_regime()` returns a label + scalar confidence,
  `market_regime.py:114,197`), but adds two dimensions `regime.py` lacks: R²-based trend
  quality and volume ratio (`market_regime.py:~180-201`).
- Neither produces the spec's multi-dimensional regime *probabilities* (simultaneous
  trend/range/high-vol/breakout scores) — both are single hard labels.
- `regime_meta_labeler.py` uses **neither** module — a standalone time-feature model, a
  third, fully separate notion of "regime."

## Correlation-aware ensemble

**A live-wired fake alongside a real-but-disconnected real implementation.**

- `strategy_clusters.py` computes **no statistical correlation at all**. It's a hardcoded
  keyword→factor lookup table mapping strategy *names* to one of 11 fixed buckets
  (`:20-50`, substring matching in `factor_of()`, `:53-59`). Its own docstring admits this:
  *"A correlation-matrix / 1-over-correlation weighting is the fuller solution but needs
  per-strategy output history; clustering is the robust version available today"* (`:9-11`)
  — a deliberate stand-in, not real correlation. Despite that, `effective_confluence()`
  (`:62-64`) drives a live confidence boost applied to the signal score
  (`signal_engine.py:4295-4335`, via `_strategy_factor` at `:1240-1241`) — it reaches live
  trading.
- Real pairwise return-correlation **does** exist: `idle_engine.py:run_correlation_update()`
  (`:488-557`, a nightly 17:55 job) computes it for the top-50 symbols and writes
  `correlation_matrix.json`, consumed by `portfolio_heat.py:30,52-60`. Not imported by
  `live_signal_engine.py` or `signal_engine.py` — not wired into live signal generation —
  and `correlation_matrix.json` does not currently exist on disk (the job's output isn't
  present, whether from not running or a path mismatch wasn't further diagnosed).
- A separate, unrelated "`_correlation_cluster`" also exists in
  `live_signal_engine.py:2915-2932` — a hardcoded symbol→sector/index map used only for a
  max-1-position-per-correlated-group risk cap (`:3214,4412,4491`, consumed by
  `portfolio_risk.py:183-189`). `test_correlation_cluster.py` tests *this*, not
  `strategy_clusters.py`, despite the similar name — a naming trap for future sessions.

## Strategy promotion / lifecycle pipeline

**Two independent, real, evidence-based state machines** (not stubs) plus a separate
manual hard-block list.

- `autonomous_edge_policy.py:84-132` — 4-state machine: `QUARANTINED / VALIDATING /
  PAPER_PROMISING / LIVE_EVIDENCE_READY`. Promotion requires real thresholds, e.g.
  `promising = n>=30 and days>=3 and avg_r>0 and pf>=1.20 and recent_positive`;
  `live_ready = n>=500 and days>=15 and avg_r>0 and pf>=1.20 and recent_n>=100 and
  recent_avg_r>0 and recent_pf>=1.10`. Recomputed live on every call from a rolling
  recent-9-day window, so **demotion is automatic** — a strategy can silently fall back to
  `QUARANTINED` if recent performance degrades. Checked live at
  `live_signal_engine.py:522-523,3294-3300`. Reimplemented separately for options in
  `option_live_edge_policy.py:181,263`.
- `live_eligibility.py:189-238` — a separate binary `live_ready`/`paper_training_only` gate:
  deflated Sharpe ≥0.95, ≥60% profitable windows, ≥4 validation windows, min-trade and
  stability flags, restricted to a frozen 5-strategy cohort (`:25,210-216`).
- `pruned.json` / `pruning.py` — actively read live (`signal_engine.py:2316-2319` loads it;
  gates execution at `:3118` and modifier scoring at `:4175-4183` — not just an audit
  trail). Unlike the two systems above, promotion into this list is **explicitly manual** —
  `pruning.py:12-13,46-48`: *"a deliberate operator step... never auto-disable"* — matching
  every prune entry added this session and in `pruned.json`'s own history (2026-07-07
  through 2026-08-06).

## Monte Carlo / bootstrap robustness

**A real Monte Carlo trade-sequence permutation exists**, closer to complete than the other
categories, but narrower than the spec's ask and disabled by default.

- `walk_forward_backtest.py:559-636` (`monte_carlo_trade_sequence()`) — genuinely shuffles
  the observed P&L sequence 5,000 times and computes distributions of total P&L, max
  drawdown (p95/p99), and max-consecutive-losses. `bootstrap_confidence()` (`:521-556`)
  separately computes a bootstrap CI on mean OOS P&L across walk-forward windows.
- Caveat (`:347` comment): operates on **per-window** P&L as a proxy for the trade list, not
  individual trade-level P&L — coarser than true trade-level Monte Carlo. No slippage or
  entry-timing randomization, no parameter-perturbation sensitivity, no ruin-probability
  (probability of breaching a capital floor).
- Wired into `daily_pipeline.py:230-244` (step 9) but **gated behind `--run-wf`, skipped by
  default**. Also called from `strategy_selector.py:31` (after-hours mode) and
  `self_learning.py:229-237`. Results persist to `walk_forward_results.json`.
- Everything else that matched a "bootstrap"/"Monte Carlo" grep is narrower and unrelated:
  `ml_effective_n_bootstrap.py` bootstraps AUC for a multiple-testing correction only (no
  trade-sequence/drawdown/ruin element), wired into `post_market_ml.py:635-654`.
  `score_inverse_falsification.py:251-264` is a one-off, hard-coded falsification script for
  a single candidate signal, not general-purpose. `meta_learner.py`/`self_learning.py` use
  "bootstrap" to mean system cold-start initialization — unrelated to statistics.

## Already real and already used this session — no gap

- **Indicator/modifier incremental-value testing** (spec §8) — `modifier_edge_analyzer.py`
  and `strategy_edge_analyzer.py`, both run this session against live data.
- **Cost-aware ranking** (spec §12) — `nse_cost_model.py`, used net-of-cost throughout this
  session's seminar-strategy validation.
- **Walk-forward + locked holdout + deflated Sharpe + parameter-stability** (spec §21) —
  `validation_harness.py`.

## Genuine gap

- **Drift detection** (spec §20) — no file, no nightly step, nothing. This is the one
  capability with no existing implementation anywhere in the codebase.

## What this audit is used for

Per the approved plan (`/home/sridhar/.claude/plans/precious-prancing-parasol.md`), this
audit gates scope: build only the genuinely-missing, low-risk pieces (drift detection;
extending the existing Monte Carlo to trade-level + ruin probability), and explicitly avoid
touching the fragmented-but-live-wired regime and promotion systems, or building new
allocation machinery, in this pass. Those live-wired duplicates are real technical debt
worth resolving eventually, but that's a separate, dedicated, higher-risk review — not
something to fold into an additive drift-detection change.
