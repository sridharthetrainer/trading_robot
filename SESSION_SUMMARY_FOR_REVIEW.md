# Session summary — for external AI review (updated)

Context: a live NSE algo-trading system (Python, ~230+ files), already running in
PAPER mode. This session covered several distinct threads, then went through a
round of external AI review that surfaced both useful critique and one
significant factual error — recorded here so it isn't rediscovered blind or,
worse, recirculated as true. Written for a second AI opinion — flagging
methodology choices and open questions explicitly rather than presenting
conclusions as settled.

## 1. A real production bug found and fixed

A manual trade lost ₹6,362.85 (-29.8%) when its broker-side stop-loss (GTT
order) was placed with `producttype="CARRYFORWARD"` while the actual position
was `INTRADAY` — the broker couldn't net the close order against the held
position, so the stop never fired despite price falling far through the
trigger. Root cause confirmed via broker logs + an identical, already-documented
prior incident in the same codebase (a different code path, fixed 2026-07-08,
same mismatch). Fix: added a `producttype` parameter to the GTT-placement
function and threaded the position's actual product type through all 4 call
sites. Regression test added. **Fairly confident this one is correct** — root
cause matched a known pattern, fix is small and targeted, tests pass.

## 2. Seminar-sourced option strategies — built, tested, and stress-tested

The user provided 6 real "seminar" trading strategy descriptions (Bollinger
Band reversal/momentum OTM buying, SMA20 crossover ATM buying, ADX-triggered
long straddle, DI+Momentum ATM call buying, a rolling short straddle). None
had existing code. Built from scratch:

- **`option_intraday_pricer.py`** — Black-Scholes option pricer anchored to
  REAL NIFTY EOD settlement prices (2020-2026 real bhavcopy data). Since only
  EOD (not intraday tick) option data exists, each day's implied vol is solved
  from the PREVIOUS trading day's real settle price, then used to reprice
  intraday off the real 5-min NIFTY underlying path. **This is the most
  important methodology choice to scrutinize** — it's an approximation, not
  real intraday option prices. Verified via round-trip sanity check and
  directionally-correct sensitivity. One day of IV staleness, no lookahead.
- **`single_leg_intraday_option_backtest.py`** / **`multi_leg_intraday_option_backtest.py`**
  — shared engines, real transaction costs, every unpriceable day counted and
  reported (not silently dropped).
- **`backtest_rolling_short_straddle.py`** — short premium, multiple
  re-entries per day, its own day-loop.
- **`seminar_param_search.py`** — grid search + locked chronological holdout +
  Deflated Sharpe Ratio (Bailey & López de Prado) + a buy-and-hold benchmark
  gate.

**Result: 0 of 6 pass.** One near-miss: Bollinger OTM reversal (14-period,
1.5 std-dev) has a positive, benchmark-beating holdout (+₹88,609, Sharpe
3.44, n=50, 50% win rate) but fails the Deflated Sharpe gate (0.77 vs. the
0.95 bar).

**Follow-up stress testing on that one near-miss** (`bollinger_reversal_sensitivity.py`,
`bollinger_reversal_robustness_check.py`), prompted by external review:

- **IV/cost sensitivity**: 28 scenarios (±10/5/2 vol-point IV shocks ×
  4 extra-cost levels up to +0.20%/leg) — **all 28 stay net-positive**, though
  P&L/Sharpe degrade smoothly as the shock grows (Sharpe 3.44 → 0.99 at +10
  vol points). Note: this strategy specifically enters at volatility extremes
  (a 2-std Bollinger breach), so a *positive* IV shock — true intraday IV
  being higher than the prior day's calmer close — is the structurally more
  plausible direction of pricing error here, and that's the direction tested
  down to +10 points without flipping negative.
- **Asymmetric IV shock** (`bollinger_reversal_asymmetric_iv_shock.py`,
  follow-up on a fair review critique that uniform ±shocks give false comfort
  where real IV error is skewed, not symmetric): the strategy's own entry
  logic gives a non-arbitrary way to apply the skew — a CE entry is triggered
  by a down-move (lower band breach), which is exactly where the leverage
  effect predicts IV understatement is largest; a PE entry is triggered by an
  up-move, where IV typically doesn't spike as much. Tested CE-side shocks up
  to +15 vol points against 0/-3 on the PE side (7 scenarios) — **all 7 stay
  net-positive**, Sharpe degrading from 3.44 to 0.97 at the most extreme skew
  tested, never flipping negative.
- **Parameter plateau**: all 9 points in the (period∈{12,14,16} ×
  std∈{1.4,1.5,1.7}) grid are holdout-positive (₹75,686 to ₹133,948, Sharpe
  2.93–4.98) — not an isolated spike.
- **Temporal stability**: holdout split into 3 chronological thirds, all 3
  positive, but unevenly: the middle third carried 68% of total P&L, the
  last third only 7%. Recency-weighted, that reads as decaying toward zero
  — the early-warning shape of exactly the pattern that killed the
  score-inversion candidate (see section 5) before its forward ledger caught
  up to it. (Note: the holdout window is 2026-05-19 to 2026-08-17, ~63 days
  — an external review characterized this as "2020-2026, ~8 trades/year" and
  drew conclusions about a 2020-21 volatility regime; that premise is wrong,
  checked directly against `load_nifty_5m()`'s actual date range. The
  decay-shape observation itself doesn't depend on that wrong premise and
  still holds on the real, much shorter timeline.)
- **Minimum Detectable Edge** (`minimum_detectable_edge.py`, built after three
  separate reviews converged on wanting this): at n=50, net mean ₹1,772/trade
  doesn't clear its own MDE (₹3,237 at 80% power) — **this result cannot be
  statistically distinguished from zero given the sample size.** Verdict:
  `INSUFFICIENT_POWER`, not evidence of edge.
- **Realistic OTM bid-ask spread stress** (extending `extra_cost_pct`, which
  is a fraction of premium not underlying notional, past the 0.20% ceiling
  tested earlier up to a review-cited realistic 5-15% range for thin OTM
  options during volatility spikes): **the result flips negative between 8%
  and 10% extra cost**, and is decisively negative at 15% (-₹49,665, Sharpe
  -2.31). The earlier IV-shock robustness (all scenarios positive) tested
  pricing-model risk; it never tested execution-cost risk at a realistic
  magnitude for this instrument, and that's the one that broke it.

- **Effective number of trials for DSR** (`bollinger_effective_trials.py`,
  eigenvalue/Li-Ji-style estimator on the 9 grid points' daily-P&L
  correlation matrix, per external review): the 9 grid points are NOT 9
  independent bets — mean pairwise correlation 0.66, **N_effective ≈ 1.9**.
  Recomputing DSR with the correct effective count rather than the raw grid
  size flips it: **0.77 (raw N=9) → 1.0 (N_effective≈1.9)** — the DSR gate
  specifically would now pass. **This does not mean the candidate should be
  promoted.** MDE (`INSUFFICIENT_POWER`) and the realistic OTM cost stress
  (flips negative at 8-15%/premium) are both completely independent of trial
  count and are untouched by this correction — fixing one miscalibrated gate
  doesn't retroactively validate a candidate that two other, unrelated gates
  still reject on their own terms.

**Revised verdict on this candidate, downgraded from "most extensively
vetted" to "thoroughly tested and found wanting":** the DSR objection turned
out to be the weakest of the four checks (it was measuring correlated trials
as if independent), but the candidate is still correctly rejected — on
statistical power (MDE) and execution economics (realistic bid-ask stress),
neither of which the trial-count correction touches. Also shows a
within-holdout decay pattern. Not a near-miss; not promotable; not worth
further stress-testing without a fundamentally different data source (real
intraday option quotes) to re-run against.

## 3. A 50-section "autonomous adaptive trading engine" spec — scoped down, not built as-is

Declined to execute an external 50-section/18-phase master-prompt as literally
instructed. A 3-agent parallel codebase audit found almost everything it asked
for **already exists**, fragmented: two independent regime engines, a
live-wired "correlation" module that's actually a hardcoded keyword lookup
next to a real-but-disconnected correlation calc, two independent evidence-gated
promotion state machines, a real but disabled-by-default Monte Carlo module.
Genuinely missing: only drift detection. Built `drift_monitor.py` (report-only,
wired into the nightly pipeline) and extended Monte Carlo to trade-level +
ruin probability. Full audit in `SYSTEM_INFRASTRUCTURE_AUDIT.md`. The two
duplicate systems (regime, promotion) were deliberately left untouched.

## 4. A second, similarly-styled spec (MA "Parameter Zoo" + regime ensemble) — declined outright

Asked to deploy a moving-average ensemble live with weekly autonomous
re-parameterization based on in-sample Sortino, no out-of-sample gate.
Declined — its core claim (ADX-gated regime switching helps) was already
checkable: `backtest_trend.py`'s existing 648-trial grid search already
swept an ADX threshold, and the optimizer's own best result rejected it
(`adx_threshold: null` beat every gated variant). Verdict on that check is
INSUFFICIENT_DATA (only 1 walk-forward window currently available), not a
clean rejection — but the point stands that the claim didn't need a new build
to check.

## 5. External AI review round — convergence, and one significant error corrected

The summary above (an earlier version) was sent to multiple external AI
reviewers. Worth recording both what they agreed on and what one of them got
wrong, since the error was substantive enough to matter if left uncorrected.

**Where reviewers converged (multiple independent reviews, consistent):**
keep the DSR≥0.95 promotion gate rather than lowering it for the Bollinger
result; don't deploy Bollinger; the 30-day drift-monitor window is a
reasonable default given only 27 clean signal-log days exist, but should be
made sample-size-aware (minimum trade counts, not just calendar days) rather
than shortened; declining both master-prompt specs was correct; stop adding
strategies and prioritize validation/data quality instead; reframe "0/17
profitable" as "0/17 meet the system's predefined validated-edge promotion
criteria" — positive in-sample or single-holdout P&L is not validated edge.

**A genuinely sharp, distinct critique from one review** (worth keeping
regardless of the error below): real IV mis-calibration is skewed, not
symmetric, so the uniform ±shock sensitivity test gives false comfort at
exactly the point where the model is most likely wrong; the H1/H2/H3
temporal split is underpowered at these trade counts; if the 9 grid trials
are correlated, naively feeding N=9 into DSR can *over*-deflate it (0.77
could understate true significance) — but the more important point is that
the real multiple-testing exposure is arguably program-wide (the 648-trial
MA search, the full strategy catalog, modifier pruning across signal_log),
not per-session; "INSUFFICIENT_DATA forever" is itself a drift-monitor
failure mode for sparse strategies, not just a safe default; and — the
sharpest point — the 0/17 result conflates "these strategies lack edge" with
"the pipeline's cost/sensitivity floor sits above whatever edge might exist,"
and there's no way currently to tell which explanation is true.

**The error, and why it mattered**: that same review asserted the process was
ignoring "your strongest, most-validated lead: the score-inversion /
magnitude finding (rho ~-0.20, n>9,000, survived a pre-registered
falsification battery)" and that continued-directional-strategy testing was
close to a category error given that stronger lead. This was checked directly
against the codebase's own pre-existing files (`score_inverse_falsification.py`,
`score_inverse_falsification.json`, `option_signal_research_ledger.json` — all
predate this session, dated 2026-07-17 through 2026-07-29):

- The falsification battery's own recorded verdict is `FLAGS_TRIPPED`, not
  "survived": the day-clustered bootstrap 95% CI on the correlation is
  [-0.449, +0.141] (crosses zero), only 10 of 18 days show the effect's sign
  (sign-test p=0.81), and per-day rho swings from -0.74 to +0.76 — day to
  day, not just noisy but sign-flipping. The correlation also flips sign
  across time-of-day buckets (+0.06 in the first 75 minutes, -0.35 and -0.41
  later).
- The forward research ledger — named in the falsification script's own
  docstring as *the sole promotion authority* — recorded `verdict: REJECTED`
  on 2026-07-29 after 8 forward days: low-scored signals underperformed
  (-13.56 bps, t=-22.99) and high-scored signals outperformed (+4.44 bps,
  t=10.62) — both highly significant, but in the **opposite direction**
  needed to confirm the "inverse" hypothesis that was found in the discovery
  sample. The effect's sign reversed out-of-sample.
- The pooled headline number the review cited (ρ≈-0.20, n=9,208) is real and
  accurately quoted — it's citing it without the file's own verdict field
  that was the error, and it's exactly the "impressive pooled statistic that
  dissolves on decomposition" pattern this whole session's validation
  discipline exists to catch.

Confidence in this correction is high — it's read directly from JSON files
this session didn't create or modify, with an explicit machine-computed
verdict field in each, not an inference or a re-analysis.

**A second review round, two more math errors caught (same discipline
applied)**: a later review proposed a genuinely useful framework (a
minimum-detectable-edge score per strategy, which speaks directly to open
question #1 below) and one genuinely good, actionable idea — the asymmetric
IV shock test above, which it correctly identified as the right way to
address the earlier symmetric-shock critique. But it also contained two
concrete errors, checked against the actual code rather than accepted on
authority:

- It computed an illustrative `mean/σ ≈ 3.44/√50 ≈ 0.49` for the Bollinger
  result. This codebase's Sharpe is `mean(pnl)/std(pnl) × √252` — an
  *annualization* factor assuming roughly one trade/day, not `√n_trades`.
  Using the correct factor: `3.44/√252 ≈ 0.22`, less than half the reviewer's
  number. The MDE-framework concept is still worth building properly; this
  particular worked example wasn't reliable.
- It quoted a Deflated Sharpe Ratio formula — `(Sharpe−threshold)/√((1+Sharpe²/2)(1−SR_benchmark²)/(n−1)(1+N·something))`
  — that doesn't match what's actually implemented (`validation_harness.py:146-193`,
  the real Bailey & López de Prado formula: an Euler-Mascheroni expected-max-
  Sharpe-under-the-null term via `norm.ppf`, a skew/kurtosis-adjusted variance
  term, `norm.cdf(z)`). No `SR_benchmark` term exists in the real DSR
  calculation at all — that's a separate, different check (the buy-and-hold
  benchmark gate) that got conflated with DSR itself. The dangling
  "`N·something`" in the quoted formula is its own tell.

Neither error was a fabricated finding the way the score-inversion claim was
— both are the more mundane failure mode of a plausible-sounding formula
produced without checking it against the actual implementation. Recorded for
the same reason: so a future session (or a future review) doesn't inherit
either number as if it were verified.

**A third review round — the sharpest and most valuable so far, one wrong
premise, one genuinely important catch.** One review applied the exact
"impressive pooled statistic, check the decomposition" lens from the
score-inversion correction directly to the Bollinger near-miss, and pushed
three points that materially changed the conclusion (see the revised verdict
in section 2): the temporal-decay reading, a base-rate argument (a *sold
seminar* strategy showing a positive holdout should raise suspicion, not
lower it — if it cleared real retail costs it wouldn't be economical to
teach), and — the most concrete and ultimately correct catch — that the
cost-sensitivity test never went past 0.20% of premium, far short of the
5-15% real OTM bid-ask spread at volatility extremes. Re-testing at that
realistic range is what actually flipped the result negative (section 2).

Two things in that same review didn't hold up, checked directly rather than
accepted:
- It stated the Bollinger holdout was "n=50 over 2020–2026, ~8 trades/year"
  and built a narrative around a 2020-21 volatility regime. The actual data
  (`load_nifty_5m()`) spans 2025-05-19 to 2026-08-17; the holdout is
  2026-05-19 to 2026-08-17, about 63 days. Wrong premise, wrong regime
  narrative — though, notably, the *decay-shape* conclusion built on top of
  it turned out to be right anyway, just for a much shorter and more
  recent timeline than claimed.
- It cited a "recorded ~10bps pipeline sensitivity floor" as if it were a
  ready-made answer to the detection-floor question. The file
  (`pipeline_sensitivity_floor.py`) is real and predates this session — but
  it's a positive control calibrated specifically to
  `option_underlying_decomposition.py`'s data structure (the score-inversion
  research), not to the seminar option-buying strategies. The review's own
  caveat alongside this claim — "that 10bps is on the underlying; an
  OTM-buying strategy's floor is set by premium bid-ask, likely much
  higher" — was the correct instinct, and the follow-up cost-stress test
  confirmed it: the effective floor for this strategy class is far above
  10bps of the underlying.

Net effect of this round: got two facts wrong, got the two things that
actually mattered right, and the thing it got right is what actually
resolved the open question. Worth remembering when weighing review output —
correctness of the specific claims and value of the overall critique aren't
the same axis.

## 6. Standing state

Total tally, all methods combined: **0 of 17 tested strategies meet the
system's predefined validated-edge promotion criteria** (11 original rule
strategies + 6 seminar strategies). The Bollinger reversal near-miss — the
one candidate that looked interesting for several rounds of review — is now
better described as thoroughly rejected than as a near-miss: underpowered
(MDE), doesn't survive realistic OTM execution costs, shows within-holdout
decay, and fails DSR outright. The score-inversion candidate remains
separately and independently rejected (section 5). System remains in PAPER
mode. Nothing here asserts or implies profitability.

`minimum_detectable_edge.py` (new, built after three reviews converged on
wanting it) reframes the 0/6 seminar-strategy tally usefully: only 1 of 6
(`rolling_short_straddle`) can be said with statistical confidence to lack
edge. The other 5 — including ones earlier reported as clean FAILs — are
`INSUFFICIENT_POWER`: not evidence of no edge, just not enough data/trades to
tell either way at this sample size.

`minimum_detectable_edge_original11.py` extends the identical (unit-safe,
recomputed-from-raw-trades) approach to the original 10 rule strategies:
4 get a genuinely confident `NO_EDGE` (`ma_cross` n=143, `scalping` n=233,
`ema_5min` n=75, `cpr` n=44 — independent confirmation of their earlier FAIL
verdicts, not just a restatement), 3 are `INSUFFICIENT_POWER` (`trend`,
`mean_reversion`, `breakout`), and 3 fired zero trades on this holdout slice
(`orb`, `vwap_reversion`, `supertrend_mtf` — a data/parameter issue, not a
power problem; `vwap_reversion`'s matches the known index-zero-volume issue
found earlier this session).

`time_to_power.py` (new, per a review's point that `INSUFFICIENT_POWER` was
never actually a decision -- just "keep shadowing forever," the same failure
mode already named for `drift_monitor.py`'s calendar window): for every
`INSUFFICIENT_POWER` strategy, computes n* (trades needed at 80% power to
detect the OBSERVED effect size) and time-to-power = n*/firing_rate. Verdict
`DEAD_ON_ARRIVAL` if that exceeds 5 years (functionally unvalidatable, retire
it) vs. `WORTH_WAITING`. **3 are `DEAD_ON_ARRIVAL`**
(`bollinger_otm_momentum` at 173.6 years, `sma20_atm_option` at 6.6 years,
`trend` at 15.7 years) and 4 are `WORTH_WAITING` (`bollinger_otm_reversal`
0.8yr, `mean_reversion` 0.6yr, `breakout` 1.0yr, `di_momentum_call` 0.5yr —
the last a low-confidence extrapolation from only 3 observed trades).
`bollinger_otm_momentum`'s 173-year figure is the clearest case in the whole
session of an "INSUFFICIENT_POWER" that should actually be read as dead, not
pending. (`adx_long_straddle` and `di_momentum_call`, both n<10, correctly
report `SAMPLE_TOO_SMALL_TO_EXTRAPOLATE` rather than a number built on too
few points to trust — this includes a since-fixed bug where ADX's first MDE
pass used a split date predating its 1-min data entirely, now corrected at
the source in `minimum_detectable_edge.py` itself.)

Two more refinements added directly on `minimum_detectable_edge.py`'s output
and on `drift_monitor.py`, both per external review:

- **95% confidence intervals** on per-trade net edge, alongside the binary
  verdict — a bare `INSUFFICIENT_POWER` label can't distinguish a CI tight
  around zero (genuinely dead) from one that's wide and mostly positive
  (worth waiting on). E.g. `adx_long_straddle`'s CI is [3325, 25871] —
  entirely positive despite only n=2 — versus `sma20_atm_option`'s
  [-7424, 4252], which straddles zero. Same verdict, very different
  information content.
- **`cusum_check()`** in `drift_monitor.py` — a sequential (CUSUM) drift test
  alongside the existing calendar-window check, needing only a 15-observation
  bootstrap reference rather than 2×30=60 split into disjoint windows.
  Building this surfaced two real miscalibrations, found and fixed only by
  validating against actual `signal_log.db` data rather than synthetic tests
  alone: (1) using the small reference window's own std for the decision
  boundary made it depend on whatever that tiny sample happened to draw (a
  15-point window drew std=0.58 against the true full-series std of 1.14 on
  real data); (2) even after fixing that, a fixed boundary still gave price_
  structure (n=3657) and similar high-volume strategies a near-certain false
  alarm — a positive-control simulation (pure noise, same method as
  `pipeline_sensitivity_floor.py`) found the false-alarm rate climbing from
  6.5% at n=30 to 75.5% at n=3500, since a long enough random walk
  eventually crosses any fixed finite boundary. Fixed by scaling the
  boundary with √(n_evaluated), holding the simulated false-alarm rate to
  roughly 3-7% across all tested lengths — same Bonferroni-style principle
  used everywhere else in this pipeline, applied to a sequential test's
  boundary instead of a fixed test's alpha. On real data, the largest-sample
  strategies (`price_structure`, `trend`, `ma_cross`, `elder_triple_screen`)
  now correctly read `STABLE` instead of universal false positives — though
  the real-data flag rate (~24% of tested strategies) still runs above the
  synthetic control's ~3-7%, an open question (see below).

Finally, `bollinger_effective_trials.py` (eigenvalue/Li-Ji-style effective-
number-of-trials, per external review) found the 9-point Bollinger grid's
trials are far from independent — mean pairwise correlation 0.66,
**N_effective≈1.9** — and recomputing DSR with that corrected count instead
of the raw grid size flips the DSR verdict: **0.77→1.0**, meaning the DSR
gate specifically would now pass. This does NOT reopen the case: MDE
(`INSUFFICIENT_POWER`) and the realistic OTM cost stress (flips negative at
8-15%/premium) are both independent of trial count and untouched by this —
the strategy is still correctly rejected, just for reasons unrelated to the
one gate whose calibration turned out to be wrong. See the revised section 2
verdict above for the full reasoning.

## Things a reviewer should push on

1. The 0/17-vs-detection-floor question is now resolved for 16 of 17
   strategies. `minimum_detectable_edge_original11.py` extends the same
   (unit-safe, recomputed-from-raw-trades) methodology to the original 10
   rule strategies, reusing `validation_harness.py`'s own `split_holdout()`
   and each strategy's real `best_params` rather than touching any stored
   annualized Sharpe figure:
   - **4 strategies get a genuinely confident `NO_EDGE`** on decent sample
     sizes: `ma_cross` (n=143), `scalping` (n=233), `ema_5min` (n=75),
     `cpr` (n=44) — statistically distinguishable from zero, and not
     positive. Independent confirmation of their earlier FAIL verdicts, not
     just a re-statement.
   - **3 are `INSUFFICIENT_POWER`**: `trend` (n=89), `mean_reversion`
     (n=63), `breakout` (n=81).
   - **3 produced zero trades on this holdout slice** (`orb`,
     `vwap_reversion`, `supertrend_mtf`) — a different, more basic failure
     mode than statistical power (matches the known index-zero-volume issue
     for `vwap_reversion` found earlier this session), not yet decomposed
     further.
   Not yet done: `fibonacci` (tested via a separate `run_extended_
   validation.py` pathway with a different report shape) and any
   pipeline-wide single detection-floor number — `pipeline_sensitivity_
   floor.py`'s ~10bps figure remains scoped to a different research
   pipeline (see section 5) and still doesn't transfer here.
2. The Bollinger reversal case is likely closed pending real intraday option
   data (a Black-Scholes-on-synthetic-premium result that dies under a
   realistic bid-ask assumption isn't worth further stress-testing on the
   same proxy) — is that the right place to stop, or is there still value in
   re-testing it once/if real option quotes are captured?
3. Effective trials for DSR is now solved LOCALLY (`bollinger_effective_
   trials.py`, eigenvalue-based, N_eff≈1.9 for the 9-point grid — see
   section 2) but not PROGRAM-WIDE: the honest exposure across the 648-trial
   MA search, full strategy catalog, and modifier pruning is still
   uncorrected. Is per-session (even effective-N-corrected) DSR ever a
   defensible final gate, or does it need a persistent, program-wide trial
   registry before promotion decisions can trust it?
4. `drift_monitor.py` now has both the calendar-window check and a CUSUM
   sequential test (`cusum_check()`, new — see below) — sparse strategies no
   longer sit in permanent INSUFFICIENT_DATA. Still open: the CUSUM
   false-alarm rate on real signal_log data (~24% of tested strategies
   flagged) runs higher than its synthetic positive-control rate (~3-7%) —
   is that genuine drift in several strategies, or does real return data
   violate the i.i.d.-Gaussian assumption the control test relies on,
   warranting a fatter-tailed reference distribution?
   prone to undetected decay?
