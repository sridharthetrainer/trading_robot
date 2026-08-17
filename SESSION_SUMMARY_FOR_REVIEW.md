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

**Revised verdict on this candidate, downgraded from "most extensively
vetted" to "thoroughly tested and found wanting":** four independent checks
now point the same direction — statistically underpowered (MDE), doesn't
survive realistic execution costs, shows a within-holdout decay pattern, and
fails the DSR gate outright. The IV-shock robustness that looked reassuring
tested the wrong risk relative to what actually breaks it. Not a near-miss;
not promotable; not worth further stress-testing without a fundamentally
different data source (real intraday option quotes) to re-run against.

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
tell either way at this sample size. Scoped to the 6 seminar strategies only;
extending it to the original 11 rule strategies would require care around
their stored (already-annualized) Sharpe figures to avoid the same
unit-conflation error caught in review this round.

## Things a reviewer should push on

1. The 0/17-vs-detection-floor question is now partially, not fully,
   resolved: `minimum_detectable_edge.py` answers it for the 6 seminar
   strategies (5 of 6 are power-limited, not edge-absent), but the same
   analysis hasn't been run on the original 11 rule strategies, and there's
   no single pipeline-wide detection-floor number — `pipeline_sensitivity_
   floor.py`'s ~10bps figure is scoped to a different research pipeline
   entirely (see section 5) and doesn't transfer.
2. The Bollinger reversal case is likely closed pending real intraday option
   data (a Black-Scholes-on-synthetic-premium result that dies under a
   realistic bid-ask assumption isn't worth further stress-testing on the
   same proxy) — is that the right place to stop, or is there still value in
   re-testing it once/if real option quotes are captured?
3. What is the right way to estimate the *effective* number of independent
   trials for DSR when trials are correlated (9 in one grid, but the honest
   program-wide count is much larger across the MA search, full strategy
   catalog, and modifier pruning) — is per-session DSR ever a defensible
   unit, or is that itself a methodological gap in this system's validation
   discipline?
4. Should `drift_monitor.py` be redesigned so sparse strategies can eventually
   produce a real verdict (trade-count-based window, or a sequential test
   like CUSUM/SPRT that accumulates evidence at whatever rate trades arrive,
   rather than a fixed calendar window), so "INSUFFICIENT_DATA forever"
   doesn't become a blind spot for exactly the low-frequency strategies most
   prone to undetected decay?
