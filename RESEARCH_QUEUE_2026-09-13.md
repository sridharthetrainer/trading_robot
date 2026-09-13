# Research state as of 2026-09-13 — read this before starting new work

Two categories, kept deliberately separate: bug fixes (done, unconditional
improvements to correctness) and research candidates (open, need validation
before any production change). Don't blur them — a research candidate that
looks promising is still not a bug fix.

## Bug fixes shipped today (unconditional — no further validation needed)

All committed and pushed to `origin/main`, all live in the running bot:

1. Manual-trade tracker's algo-order tag detection (`manual_trade_tracker.py`)
   — was misidentifying the bot's own trades as manual ones.
2. Master-contract staleness (`angel.py`) — real GTT stop-loss placement was
   silently failing for any option contract listed after ~June 2026; left a
   real position unprotected for 2 days before being caught. Now self-heals
   on every restart.
3. Confluence-scoring leak (`signal_engine.py`) — two modifiers proven
   harmful (`mtf_pivot_mod`, `sr_level_mod`) were still counting as quality
   confirmations despite their score contribution already being clamped.
4. Scheduler freeze (`idle_engine.py`) — one hung task silently blocked 5
   nightly analysis jobs for 3.5+ weeks. Now has a per-task timeout so a
   future hang can't do this again.

Plus one shipped, evidence-based improvement: BREAKOUT-regime preferred-
strategy penalty (`signal_engine.py`) — validated across two independent
time-halves before shipping, reduces (does not eliminate) loss in that slice.

**Still pending on the user** (needs their sudo, not code): install
`manual-tracker.service` and `daily-pipeline.service`/`.timer` so they
survive a reboot.

## Core finding, unchanged by anything below

No strategy, modifier, or regime slice has validated positive edge. 49/49
strategies with enough data are net-negative after costs. 27/27 confluence
modifiers show zero positive net-of-cost effect. Extending the exit time
window does not fix it (tested and ruled out). System stays in PAPER mode,
no profitability claim, per the project's standing rule.

## Sharpened research thesis (from a three-way review: this session +
## external AI cross-checks, each claim verified against the actual code)

**Entry quality is the dominant, primary problem. Exit/trailing mechanics
are a real but secondary problem.** Evidence: MFE/MAE analysis on existing
signal_log data (winners median MFE=0.85R, MAE=0.214R; losers median
MFE=0.234R, MAE=0.551R; 81.6% of losers never reach even +0.5R). Winners
and losers are measurably different early on (the signal carries real
information), but most losing trades were never close to working, not
good trades ruined by bad exits. Research priority: find what distinguishes
the high-MFE trades *before* entry, not add more indicators or loosen exits.

## Research queue, in priority order — none of these are validated yet

### 1. HTF-alignment gate for the `breakout` strategy
Full pre-registration in `PREREG_BREAKOUT_HTF_ALIGNMENT.md` — read that
file in full before touching this. Summary: `breakout` signals where side
agrees with the already-computed `htf_bias` field show -0.081R vs -0.364R
unaligned (aggregate), and this survives a within-regime confound check
(not just re-discovering the BREAKOUT-regime effect). `trend` shows no
such benefit (control). Must build a causal-slicing helper with a
poisoned-input assertion in `validation_harness.py` BEFORE writing the
gate itself, so the gate is tested against infrastructure already proven
free of lookahead leakage — do not build both at once.

### 2. Hour-of-day pattern
New finding, not yet validated: fraction of trades reaching >=0.5R MFE
by hour_of_day (existing signal_log data, training_eligible=1):
```
hour  9: 0.531   hour 14: 0.441   hour 15: 0.425   hour 10: 0.421
hour 11: 0.369   hour 12: 0.330   hour 13: 0.280
```
Hour 9 (market open) is *better* than midday, the opposite of the common
"avoid the open" assumption — exactly why this needs checking against data
rather than intuition, and exactly why it needs the same rigor before
trusting it. BREAKOUT regime shows the same >=0.5R-MFE-rate pattern
(0.538, highest of any regime) — convergent with the already-shipped
regime fix, not new on its own.
**Required before this goes anywhere**: (a) two-independent-time-halves
consistency check on the hour 9 vs hour 13 gap, (b) confound check
against regime (does the hour-9 effect survive within EARLY_TREND/BREAKOUT
specifically, or does it disappear once regime is controlled for — same
method already used for the HTF-alignment and BREAKOUT-regime checks).

### 3. Winner-giveback / trailing-stop efficiency
Secondary to entry quality — do not let this become the main research
direction even if it looks tractable. Winners' median MFE (0.85R) minus
their actual realized average (~0.63R) implies real value is being left
on the table via current trailing mechanics. Next step: using the
*existing* MFE dataset (no new signal generation needed), quantify what
fraction of winners reach each MFE threshold (0.5R/0.75R/1R/1.25R/1.5R/2R)
and how much they subsequently give back, to get an empirical ceiling on
how much a trailing-stop change could realistically capture — before
writing any new trailing logic. Even fully solving this cannot compensate
for the 16,987-loser vs 9,202-winner population if entry quality is
unchanged.

### 4. ML: predict MFE threshold, not final win/loss
Current meta-labeler (`meta_labeler.py`/`regime_meta_labeler.py`) predicts
final WIN/LOSS. Latest state (2026-09-13): AUC=0.7217 (single split),
CPCV mean=0.619 (15 paths, min=0.554), bootstrap 95% CI=[0.506, 0.822]
excludes 0.5 (`ml_effective_n_bootstrap_report.json` verdict:
`REAL_SIGNAL`) — so the model has genuine, non-noise predictive
information. But every threshold tested still nets negative after costs
(best -0.143R vs baseline -0.1654R, ~13% relative improvement, not a
flip to positive), and `ml_pipeline_last_run.json` explicitly states no
edge survives multiple-testing correction. `model_saved: False` in both
reports — correctly not promoted.
Given today's MFE finding (81.6% of losers never reach +0.5R, winners
vs losers clearly separated by median MFE 0.85R vs 0.234R), the more
economically meaningful question is whether the model can predict
**favorable excursion** rather than final outcome. Proposed next
experiment: train the same feature set against `MFE >= 0.5R` / `>= 0.75R`
/ `>= 1.0R` as separate binary labels (not final win/loss), using the
existing `max_favorable_move` column (94% populated already, no new
instrumentation needed) as ground truth. Same discipline as everything
else: purged CPCV, locked holdout, cost-adjusted R, and — critically —
do not optimize the classification threshold against the holdout (choose
it on the training/CV side only, then apply once to holdout). Compare
against the current WIN/LOSS-label model's economics, not just AUC.
Do not promote to live under any circumstance until it clears the same
full validation gate as everything else.

### 5. Option-level P&L translation
Currently unmeasured. Every R-multiple discussed today (49-strategy table,
MFE/MAE, BREAKOUT-regime fix, HTF-alignment finding) is computed on the
**underlying's price movement as a proxy** — `triple_barrier.py`'s own
docstring is explicit that this excludes real option execution costs
(bid/ask spread, IV, theta, delta/DTE selection effects), which are
"measured separately." This means today's already-negative numbers are
an *upper bound* on real tradability, not a realistic estimate — actual
option execution can only be worse, not better, once those costs are
correctly modeled. No specific next step defined yet beyond: eventually
build the underlying-signal -> option-selection -> executable-option-P&L
translation layer, and check whether it changes which signals look
promising, before trusting any of today's findings as directly tradable.

## Correction: Bollinger OTM reversal is NOT an open lead

An external AI review (2026-09-13) recommended promoting a "Bollinger OTM
reversal" option strategy to top research priority, citing a positive
holdout (+₹88,609, Sharpe 3.44, n=50) and a failed DSR gate (0.77 vs 0.95)
as if that were the current, live obstacle. Checked against
`SESSION_SUMMARY_FOR_REVIEW.md` (predates this session, untouched) — this
candidate already went through extensive multi-round investigation and is
correctly, conclusively REJECTED on two grounds independent of DSR:
1. `minimum_detectable_edge.py`: net mean ₹1,772/trade at n=50 doesn't
   clear its own MDE (₹3,237 at 80% power) — statistically indistinguishable
   from zero.
2. Realistic OTM bid-ask spread stress (5-15% of premium, not the 0.20%
   originally tested): flips negative between 8-10%, decisively negative
   at 15% (-₹49,665, Sharpe -2.31).
Also: within-holdout decay (last third of holdout carried only 7% of
total P&L vs 68% in the middle third). The DSR effective-trials correction
(0.77→1.0, since the 9 grid points are correlated at 0.66 mean pairwise,
not independent) is real and was applied — it does not reopen the case,
since MDE and the cost-stress result don't depend on trial count at all.
**Do not add this to the research queue.** The one legitimately open
question is whether it's worth re-testing if real intraday option quotes
(not the current Black-Scholes-on-EOD-settlement proxy) ever become
available — not something to prioritize now.
This is worth remembering as a pattern, not just a one-off: an external
review can cite real files and real numbers accurately while still being
wrong about current status, if it's missing later work in the same
project. Verify against the project's own most recent documents, not just
whether individual cited numbers check out.

## Ground rule for all four items

Same standard as everything shipped today: purged k-fold CV + deflated
Sharpe + locked holdout, holdout untouched, two-independent-halves
consistency check, confound-check against regime where relevant, and
outlier-robust reporting (median/trimmed-mean alongside mean) given how
easily a handful of large trades can dominate a small subgroup's average
(seen directly in both the TREND-regime HTF-alignment cell and the earlier
iron condor forward test). Don't loosen these standards to make a
promising-looking finding clear the bar faster.
