# AUDIT — Strategy Registry & ML/Validation Pipeline (NSE algo trading bot)

## CONTEXT
Follow-up to `AUDIT_HANDOFF_2026-09-02.md` (deployment) and
`TELEGRAM_AUDIT_HANDOFF_2026-09-02.md` (Telegram UI) on the same repo:
`/home/owner/Desktop/trading_robot`, running in PAPER mode. This covers
the strategy registry and ML/validation pipeline, compiled by reading
actual code and querying the live database/JSON files directly (file:line
cited throughout) — not inferred from names or docstrings. A prior
external audit in this project made confident but false claims by
inferring from names instead of reading code; this document is
explicit about what was verified vs. not.

**Governing constraint (from CLAUDE.md, still true):** no rule-based
strategy currently passes out-of-sample validation after costs. The
project's own documented next step is DATA-GATED — wait for live trading
days to accrue, then run `modifier_edge_analyzer.py` and prune based on
evidence. Nothing below should be read as license to pre-emptively cull
or rewrite strategies today.

---

## 1. Strategy registry structure

**Mechanism**: `signal_engine.py` builds `STRATEGIES` as a plain Python
list of function references (not a dict, not a class registry, no
decorator) — 22 "core" functions always included, the rest appended
conditionally on import-success flags (`signal_engine.py:2326-2392`).

**Actual count — measured, not assumed**: imported `signal_engine` live
and measured `len(STRATEGIES) == 79` (15 from `strategies_new.py`,
`signal_engine.py:2267-2287`). **CLAUDE.md's "~57" does not match current
code** — `grep -n "57\b" signal_engine.py` returns nothing. Independent
corroboration: `cluster_risk_gate.py:4-6` (dated 2026-08-19) already
states "~79 live strategies (signal_engine.STRATEGIES)," matching. This
is a discrepancy worth resolving, not a claim about which number is
"correct" historically.

**Concrete finding — 5 "core" strategies share one computation**:
`run_trend_strategy`, `run_mean_reversion_strategy`, `run_breakout_strategy`,
`run_scalping_strategy`, `run_ma_cross_strategy` (`signal_engine.py:
1288-1339`) **all call the identical function**
`calculate_signal_score(df, df_htf, option_data)` (`signal_score.py:57`),
differing only by a hardcoded score offset (0, −0.25, 0, −0.50, +0.10)
— same direction, every time. These are counted as up to 5 separate
"votes" in confluence scoring despite being one computation.

**Confluence scoring — verified as a hybrid, not a simple vote**:
1. Each candidate's raw score passes through a long chain of additive
   modifiers (Hurst, market-profile, market-quality, pivot-boss,
   condition multiplier, HTF-misalignment penalty, Weinstein-stage,
   pruning) — `signal_engine.py:3006-4222`.
2. Majority direction wins, with a conflict penalty if both sides fire
   (`:4274-4297`).
3. Raw vote count is de-correlated into an "effective factor count" via
   `strategy_clusters.effective_confluence` (§2) — `:4302-4320`.
4. A boost table keyed on that effective count adds +1.5 (2 factors) up
   to +7.0 (6+ factors) — `:4333-4342`.
5. A post-boost minimum-score gate (default 3.5) applies, then the
   single highest-scoring candidate is picked (`:4361-4391`).

---

## 2. Correlation between strategies

Three distinct mechanisms exist — do not conflate them:

**a) Live, in the vote path**: `strategy_clusters.py` — by its own
docstring, explicitly **not** a statistical correlation measure ("a
correlation-matrix... is the fuller solution but needs per-strategy
output history; clustering is the robust version available today,"
`:9-11`). It's a hand-authored keyword-to-bucket lookup
(`_FACTOR_KEYWORDS`, `:20-50`, 11 buckets). **Limitation directly
confirmed by §1's finding**: because it's name-based, it correctly
merges `trend`+`ma_cross` into one TREND factor, but still counts
`breakout`, `mean_reversion`, and `scalping` as 3 separate "factors"
despite them sharing the identical underlying computation.

**b) Offline, unwired**: `strategy_pair_edge_miner.py` — its own
docstring confirms "no run_nightly / no pipeline wiring." Mines actual
co-firing combinations for real edge via day-holdout + Bonferroni
correction; last documented run (2026-07-23) had only 17 days of data.

**c) Real correlation, but of symbols not strategies**:
`cluster_risk_gate.py` reads `correlation_matrix.json`, populated by
`idle_engine.run_correlation_update()` (30-day Pearson correlation of
daily closes across 50 NIFTY200 symbols, `idle_engine.py:488-537`),
scheduled nightly since 2026-08-19 (`daily_pipeline.py:163-173`). **The
output file does not exist on disk** at time of audit — wired in, but
either never successfully run yet or writing elsewhere.

**Bottom line**: no statistical strategy-signal correlation check is
live in the trading path today. What's live is a coarse keyword proxy.

---

## 3. Position sizing

`adaptive_position_sizer.size_position()` (`:352-487`), the live path
(`live_signal_engine.py:4478-4494, 4597`):

```
adjusted_risk_pct = base_risk_pct × confidence_scale × regime_scale
                    × volatility_scale × drawdown_scale × ml_conviction_scale
risk_amount = capital × adjusted_risk_pct  (clipped to [min,max])
lots = floor(risk_amount / (stop_distance × lot_size))  (clipped to [min,max])
```

- **Confidence**: driven by `signal["confidence"]`, traced to
  `live_signal_engine.py:2235` (`= ai_prob`, an AI/ML probability) —
  **not** the confluence WEAK/MEDIUM/STRONG/VERY_STRONG label, and not
  the numeric confluence-boosted score. The score-band fallback
  (`:91-124`) is dead code on this call path since confidence is never
  negative in practice.
- **Regime + strategy category**: genuinely regime-aware, crossed with
  a coarse TREND/MEAN_REVERSION/BREAKOUT strategy-type string (`:126-147`).
- **Volatility**: genuinely ATR/price-ratio-aware, 1.05× down to 0.55×
  (`:149-173`).
- **Drawdown**: 1.00× down to 0.40× as drawdown deepens (`:197-221`).
- **ML conviction**: only non-1.0 with a promoted model; per an
  in-code comment (2026-08-05) no strategy has one yet — not
  independently re-verified for today.

**Answer**: sizing is volatility- and regime-aware, but keyed off an AI
probability estimate, not the confluence tier label itself.

---

## 4. ML pipeline components

- **`self_learning_engine.py`** (1125 lines): trains an XGBoost binary
  classifier on closed trades, 80/20 **time-ordered** split, reports OOS
  `val_accuracy` (`:725-805`); maintains RL strategy-preference bias with
  watermark-based dedup so trades aren't double-counted (`:810-882`).
  Gated to a 07:00-21:00 window, skips heavy work during market hours.
- **`score_calibrator.py`** (170 lines): win rate by (score bucket,
  confluence level); `has_min_samples(strategy, min_outcomes=30)`
  (`:74-87`) blocks weight-updates below 30 recorded outcomes — the
  guard CLAUDE.md cites.
- **`modifier_edge_analyzer.py`** (290 lines): Welch t-test per modifier
  (endorsed vs. silent mean return), Bonferroni-corrected, plus a
  same-sign-across-time-halves stability check. Verdicts: HELPS / HURTS
  / NOISE / DEAD (&lt;2% fire rate) / INSUFFICIENT / UNSTABLE_OOS.
  **Reports only — explicitly does not re-weight or prune anything**
  (`:18`).
- **`validation_harness.py`** (898 lines): walk-forward + Deflated
  Sharpe Ratio, min-trades-per-window guard, parameter-stability CV;
  only evaluates the locked holdout once if DSR&gt;0.5 and min-trade/
  stability pass, and requires it to beat a buy-and-hold benchmark.
  `INSUFFICIENT_DATA` if &lt;3 walk-forward windows exist, else PASS only
  if every gate clears (`:569-574`).
- **`strategy_performance_matrix.py`** (286 lines): win rate by
  (day_type, time_bucket, VIX regime, market regime), 0.0-1.5× score
  multiplier once ≥30 trades exist in-bucket (`:74-120`), gated by
  `score_calibrator.has_min_samples` (`:105` — CLAUDE.md cites `:86`,
  worth a quick recheck, minor line-number drift). **Unconfirmed
  caveat found**: this class appears to have two incompatible data
  schemas sharing one `self._data` attribute — list-of-outcome-dicts
  (`record_trade`/`get_condition_multiplier`, `:44-131`) vs. flat
  `wins`/`losses` counters keyed `"strategy:symbol"`
  (`record_result`/`should_run_strategy`, `:217-257`). Both are called
  live (`signal_engine.py:3031` and `:4079`). **Not traced far enough to
  confirm an actual runtime collision** — flagged as an open question.

**Data flow**: `signal_engine.generate_signal` → `live_signal_engine.py`
harvests + sets `confidence` from an AI-probability call + writes
`signal_log` (incl. modifier columns) → nightly, `modifier_edge_analyzer`
and `self_learning_engine` consume `signal_log` with resolved outcomes.
`validation_harness.py` is **separate and standalone** — runs its own
walk-forward against historical OHLCV (not `signal_log`), writes
`validation_results.json`. No code found feeding validation results back
into `signal_engine.py` automatically — e.g. `DISABLE_SCALPING_STRATEGY`
(`signal_engine.py:2305-2311`) shows a human acting manually on a
validation finding, not an automatic loop.

---

## 5. Current validation status

`validation_results.json` read directly (`last_run: "2026-08-21"`),
**10 of 79 strategies tested**:

| strategy | run_date | verdict | dev_windows | DSR | dev_avg_trades |
|---|---|---|---|---|---|
| trend | 2026-08-21 | INSUFFICIENT_DATA | 1 | 1.0 | 83.0 |
| breakout | 2026-08-21 | INSUFFICIENT_DATA | 1 | 1.0 | 81.0 |
| mean_reversion | 2026-08-21 | INSUFFICIENT_DATA | 1 | 0.0 | 55.0 |
| orb | 2026-08-05 | INSUFFICIENT_DATA | 0 | 0.0 | 0.0 |
| ma_cross | 2026-07-30 | FAIL | — | 0.0 | 93.5 |
| scalping | 2026-07-30 | FAIL | — | 0.0 | 250.8 |
| ema_5min | 2026-07-30 | FAIL | — | 0.0 | 80.8 |
| cpr | 2026-08-05 | FAIL | — | 0.0 | 36.7 |
| vwap_reversion | 2026-08-05 | FAIL | — | 0.0 | 0.0 |
| supertrend_mtf | 2026-08-05 | FAIL | — | 0.0 | 28.1 |

Zero PASS — matches CLAUDE.md. `supertrend_mtf`'s 28.1 avg trades
(&lt;30 guard) matches CLAUDE.md's specific overfit-rejection claim.

**Nuance CLAUDE.md's phrasing glosses over**: none of these 10 have a
non-null `holdout_sharpe`/`holdout_pnl` — the code only evaluates the
holdout when dev-stage gates pass (`validation_harness.py:529`). Three
(`trend`/`breakout`/`mean_reversion`) got `INSUFFICIENT_DATA` from
having only 1 walk-forward window (need ≥3) — never fairly evaluated at
all. The other 6 FAILed on DSR=0.0 at the dev stage. **Zero of the 10
tested strategies have actually reached the locked holdout stage** —
every verdict is a dev-stage rejection, not an observed negative OOS
P&amp;L. "No strategy passes" is accurate; "strategies have been shown to
lose money out-of-sample" would not be, yet — most simply haven't been
evaluated far enough to say either way.

**Freshness**: today is 2026-09-02; `last_run` is 2026-08-21 (12 days
stale), individual `run_date`s span back to 2026-07-30 (33 days stale).

---

## 6. Modifier instrumentation status

Queried `signal_log.db` directly (35,065 rows, 156 columns).

**Confirmed real variation** (supports CLAUDE.md's claim) for
`weinstein_mod`, `sector_mod`, `crsi_mod`, `nr_mod`, `volume_mod` over
the last ~2000 rows: 3-7 distinct non-zero values each, non-zero rates
1.5%-94%.

**`rl_bias`**: technically varies (142 distinct values) but every
observed value is tiny (−0.0049 to +0.031), always below the analyzer's
own "fired" threshold (EPS=0.05). Consistent with `modifier_edge_report.json`
(2026-08-20) classifying it **DEAD, coverage 0.0** — logging real
values, but too small to ever count as an endorsement.

**`ai_score` — new finding, not previously documented anywhere seen**:
constant at exactly 0.5 for **every row since 2026-07-28** (over a
month of live data, ~2600 rows). Traced by date: varied normally through
2026-07-24, briefly varied again on 2026-07-27 (0 of 856 rows at 0.5),
then pinned at 0.5 for every row 2026-07-28 onward through today. Over
the table's full lifetime ~62% of all rows are 0.5, so this is not
"dead since inception" — something changed specifically around
2026-07-28. **Root cause not investigated** (would require tracing
`ai_score`'s producer, out of scope for this pass) — worth a focused
follow-up, especially since `ai_score` mirrors `signal["confidence"]`
(`signal_log.py:279`), which also drives position sizing (§3).

**Separate finding**: `signal_log` has a **12-day gap, 2026-08-21 to
2026-09-02**, and only 24 rows so far today. Not investigated whether
this reflects the bot being stopped, a holiday gap, or a logging issue.

---

## THINGS NOT VERIFIED (explicitly out of scope this pass)
- Root cause of the `ai_score` pinned-at-0.5 regression since 2026-07-28.
- Whether `strategy_performance_matrix.py`'s dual-schema issue causes an
  actual runtime key collision.
- Why `signal_log` has a 12-day gap (2026-08-21 to 2026-09-02).
- Whether `correlation_matrix.json`'s absence means the nightly job has
  never successfully run, or writes elsewhere.

## PLEASE AUDIT / SUGGESTED FOLLOW-UPS
1. Reconcile the "~57" vs. measured 79 strategy count — is CLAUDE.md
   stale, or is there a narrower "core" count meant by "57" that isn't
   `len(STRATEGIES)`?
2. Investigate the `ai_score`/confidence regression (constant 0.5 since
   2026-07-28) — this feeds live position sizing, not just a cosmetic
   logging field.
3. Investigate the `signal_log` 12-day gap.
4. Decide whether the 5 strategies sharing `calculate_signal_score`
   should be de-duplicated in `strategy_clusters.py`'s factor map (a
   targeted, small fix) rather than treated as 5 independent votes.
5. Confirm or rule out the `strategy_performance_matrix.py` dual-schema
   collision risk.
6. None of this changes the core constraint: no strategy has reached a
   holdout evaluation yet, so any "which strategies to keep" decision
   remains data-gated, per CLAUDE.md's own documented plan.
