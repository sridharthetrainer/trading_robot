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

### 1. CLOSED 2026-09-17 — VOID. HTF-alignment gate for `breakout` (was item 1)
Built exactly per `PREREG_BREAKOUT_HTF_ALIGNMENT.md`'s sequence: causal
slicing helper (`causal_htf.py`) → poisoned-input test (PASSED, byte-
identical pre-cutoff trades) → trend-control re-run under the real causal
definition, on `candle_cache.db`'s full 16-month history (24,913 bars,
not the original small exploratory sample). Result: **trend shows the
same benefit breakout does** (aligned avg +819.49 vs unaligned +337.59,
n=247/594, large sample) — this is the pre-registration's own explicit
void condition. The effect isn't breakout-specific; do not build the
gate or run full validation on this candidate as scoped. Full detail and
reasoning in the pre-reg file's resolution header — read it before
considering a broader "general HTF-alignment" hypothesis, which would
need its own fresh pre-registration and ideally different data, not a
reuse of the data that just falsified this narrower claim.

**Reusable infrastructure this build still produced** (keep, don't
discard just because the candidate voided): `causal_htf.py` — causally-
correct HTF resampling + strict truncation, verified via poisoned-input
test — is real, tested infrastructure for any future multi-timeframe
work. Also discovered `candle_cache.db` (24,913 5-min NIFTY bars,
2025-05-19 onward) as a far better backtest data source than live-API
pulls (~30-day cap) — use it by default for any future backtest needing
real historical depth, as `rerun_trend_control_causal.py` now does.

**FIXED 2026-09-18** — the `backtest_supertrend_mtf.py` lookahead bug
found above: reused `causal_htf.resample_to_htf()`'s close-time labeling
in place of the leaky default resample. Verified via a new poisoned-input
test (`test_supertrend_mtf_poisoned_input.py`, PASSED — 148 pre-cutoff
trades byte-identical). Compared old vs fixed on the full 16-month
history: old (leaky) 245 trades/-₹203,316.71/Sharpe -0.92, fixed 211
trades/-₹152,201.87/Sharpe -0.86. **The fix does not flip the verdict —
`supertrend_mtf` remains clearly net-negative either way.** Real
data-integrity fix, not a strategy rescue; nothing further to do here.

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
Specific trail-shape variants worth testing once the ceiling estimate is
done (per an external AI review, checked as reasonable engineering
proposals, not yet validated): trail only activates after +1R rather
than from entry; trail behind structure (swing high/low, VWAP, or the
Supertrend line) instead of a second ATR multiple; scale out ~50% near
the realized average win (~0.8-1.0R) with a structural trail on the
remainder, rather than a single fixed 2R target that only 3.3% of trades
ever reach. Test one changed element at a time against the locked
holdout, same as everything else — do not combine multiple exit changes
in one experiment or a passing result won't say which change mattered.

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

## External repos for the volatility-surface idea (verified real, not yet reviewed)

An AI response (source not identified) proposed a concrete methodology
for the volatility-surface research direction below: fit a per-expiry
SVI (raw stochastic volatility inspired) smile to the option chain,
measure each option's residual deviation from the fitted smile, and test
whether that residual shows repeatable convergence — before ever
constructing a trade. It cited six specific GitHub repos as references.
All six checked directly (`gh repo view`) and are REAL, with descriptions
matching exactly what was cited — no hallucinated URLs this time:
- `crollila/implied-vol-surface-svi` — SVI surface calibration + static-
  arbitrage diagnostics, from-scratch Black-Scholes/IV inversion. The
  one worth reading first for methodology.
- `darshkale/nse-options-data-pipeline` — NSE option data
  normalization/enrichment (IV, Greeks, liquidity filters).
- `oracl8/OptionsAnalytics` — implied vs. forecast-realized volatility
  research methodology.
- `builditwithgk/dhan-nifty-algo-trading-lab`, `chandrunit/nifty-options-
  backtester`, `aaryansinha16/AI-trader` — reviewed and correctly
  assessed as NOT worth pulling in: redundant with infrastructure this
  project already has, or more variants of the directional-strategy
  approach already tested and rejected six times.

**"Verified to exist" is not "safe to import."** None of this code has
been read, license-checked, or security-reviewed. This project holds
real broker credentials and will eventually handle real capital —
pulling in any third-party code is a fundamentally different risk class
than everything else in this queue, which is all internal analysis of
data and code already in this repo. Before cloning or adapting anything:
read the actual source (not just the README), check the license, and
treat it the same as reviewing any other unaudited third-party
dependency being added to a system with financial and credential access
— a materially higher bar than "the methodology looks sound."

The one sound, generalizable principle from this exchange, worth keeping
regardless of whether any of this gets used: **borrow methodology and
ideas from public research; never borrow a public repo's claimed alpha
figures at face value** (one repo cited claimed +₹53,715 over ~49 trades
in a month — nowhere near enough sample to mean anything, exactly the
same insufficient-power failure mode already found in this project's own
`minimum_detectable_edge.py` work).

## Options-side audit (2026-09-13): one new research direction
(volatility-surface relative-value idea and the pivot_scalping diagnostic
matrix below came from an external review by ChatGPT, checked for
internal consistency against this project's own files before being
recorded — no factual error found this round, unlike the earlier
Bollinger recommendation from a different ChatGPT exchange the same
night)

Compiled a full accounting of the four separate option-related systems
(42-strategy shadow catalog, iron condor forward test, `pivot_scalping`,
six seminar strategies) for external review. All six seminar strategies
are REJECTED or INSUFFICIENT_DATA on a chronological 70/30 holdout t-test
(`seminar_strategy_validation.json`): `bollinger_otm_reversal` p=0.143,
`sma20_atm_option` -₹5,096/trade holdout p=0.041 (significantly negative),
`bollinger_otm_momentum` p=0.651, `di_momentum_call` p=0.666,
`adx_long_straddle` n=4 insufficient, `rolling_short_straddle` -₹2.9M
full-sample (58% of cycles whipsawed out via leg-level stop). The condor
forward test's true win/loss split, recomputed: ~₹3,465 average win (9
wins) vs. one -₹17,719 loss — confirms the tail-risk read, not "promising."

**New research direction worth queuing, genuinely different from
everything tried**: instead of predicting underlying direction and
buying/selling an option accordingly (what all six seminar strategies,
`pivot_scalping`, and the 42-strategy catalog all do in different forms),
test whether the **option market's own cross-sectional structure**
contains exploitable, mean-reverting information — skew changes, ATM/OTM
IV term-structure relationships, realized-vs-implied volatility spread,
or temporary IV distortion following a large underlying shock. Requires,
before any code: one specific hypothesis (not several tested at once),
a defined observable, an explicit statement of whether it depends on
real intraday IV repricing (the current Black-Scholes-on-EOD-settlement
pricer cannot represent this — see the epistemic asymmetry note below),
realistic execution costs, a genuinely untouched chronological holdout,
and a pre-committed rejection threshold. If this fails, the reasonable
conclusion is to freeze NIFTY-options strategy research generally, not
attempt an eighth variant.

**Epistemic asymmetry worth keeping as a standing rule for the options
pricer**: a NEGATIVE result from `option_intraday_pricer.py` (Black-
Scholes anchored to real EOD settlement, no lookahead) is meaningful
evidence against a strategy — the engine is generous (accurate underlying,
no stale quotes, smooth pricing), so a loss under those favorable
conditions is informative. A POSITIVE result is much weaker evidence,
since the synthetic price may not reflect a real, executable option
price. Exception: if the tested hypothesis's edge specifically depends on
real intraday IV repricing (e.g. "IV expands after a shock and that's the
edge"), a negative result is also weaker, since the pricer holds IV fixed
to the prior day's settlement and may be removing the exact phenomenon
being tested. Check this before trusting any negative result on such a
hypothesis.

**Refinement to the pivot_scalping debug plan**: build the two-step trace
already specified as a full diagnostic checklist (bar exists → session
accepted → CPR/Camarilla computed → EMA state → directional condition →
option contract selected → expiry/strike valid → liquidity filter →
cooldown/state → generic dispatcher → final signed signal) and run both
the known pre-07-29 signal and a synthetic post-07-29 candidate through
it side by side to find the exact first point of divergence. Specifically
watch for a caught exception that silently returns "no signal" rather
than raising — that failure mode looks externally identical to "the
strategy stopped finding setups" while actually being a broken filter/
data dependency further downstream, unrelated to the direction question.

**Regime-shift hypothesis added and verified (2026-09-13, from an
unlabeled AI response — source not identified despite asking twice)**:
proposed that pivot_scalping's active window coincided with a higher-VIX
period than its silent window, and specifically cited real market data
(Nifty +264.85 points/+1.10% on 2026-07-29, VIX moderating to 12-13 by
late July) as supporting context. Both specific factual claims checked
directly against this repo's own data and matched exactly: NIFTY daily
close 2026-07-28=23,985.35 -> 2026-07-29=24,250.20 (exactly +264.85,
+1.10%, via upstox_data); VIX 12.01-14.03 in that window (vix_history.csv).
Extended the check directly: VIX during the ACTIVE window (2026-06-29 to
2026-07-29, n=14) has mean=13.19 (range 12.01-14.03); VIX during the
SILENT window (2026-07-30 onward, n=24 through today) has mean=11.59
(range 10.58-12.27) — the entire silent-window range sits below the
active window's mean. This is genuine, verified supporting evidence that
a volatility-regime shift (not a code defect) plausibly explains why the
strategy stopped generating ANY signals — a separate question from the
win-rate-inversion question, which the diagnostic-checklist trace above
still needs to resolve on its own. When running that trace, explicitly
check whether the qualifying-setup threshold (score >= 4.2) or any VIX/
volatility-dependent input is what's suppressing signal generation in
the current, calmer regime.

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

## Additional queue item: ORB as its own parameter family

Flagged by an external AI review, checked and confirmed accurate: the
current `orb` grid (`adx_min`, `volume_min`, `stop_mult=[1.0]`,
`target_mult=[1.5,2.0,2.5]`) treats ADX/volume as the only knobs and
locks the opening-range window itself. Real gap: no range-window
variants (5/15/30/45/60 min), no max-OR-width cap, no breakout-buffer-
as-%-of-range option, no time-of-day cutoff for late breakouts, no
previous-range confirmation. Worth building as a distinct grid, not
another ADX threshold tick — same discipline as everything else (walk-
forward + deflated Sharpe + locked holdout) before any promotion.

## Concrete next step for the pivot_scalping investigation

Two things worth doing before more shadow data accumulates, both cheap
and mechanical rather than more code-reading:
1. **Reconstruct one full signal end-to-end**: pick one pre-2026-07-29
   signal and trace index bar -> CPR/Camarilla level values -> which
   comparison fired -> option symbol selected -> final signed
   BUY/SELL order. If the signed order comes out opposite what the CPR
   scenario intended, that's a labeling/sign bug with an exact name and
   line, not a mystery.
2. **Feed one synthetic bar dated after 2026-07-29** through the same
   path and see exactly where it stops (silently filtered by a guard
   that started failing closed? a data dependency now empty/renamed?
   the score just never reaching the 4.2 threshold under current
   conditions?). This directly tests whether the "stopped firing since
   July 29" behavior is a data/guard failure (unrelated to the direction
   question) rather than assuming it's connected to the inversion.
Both of these are stronger diagnostic moves than more static code
reading of the already-checked bias/cross/formula functions, which came
back clean on inspection.

## RESOLVED (2026-09-13, later the same night): ai_score=0.5 is not a bug

Full trace completed. `signal_log.py:649` reads `signal["confidence"]`,
set in `live_signal_engine.py:2267` from `ai_prob = self._get_ai_probability(signal)`.
That function's fallback chain (`_get_ai_probability`, ~line 2441) tries
`ml_trainer.predict()` first; direct test:
```
ml_trainer.predict({}, symbol='NIFTY')
-> {'win_prob': 0.5, 'available': False, 'reason': 'missing_positive_profit_utility'}
```
The saved model it would otherwise use (`ml_models/cross_symbol_model.pkl`,
2026-07-27) has `promoted: True` in its own metadata — it cleared
*statistical* promotion (AUC, calibration, purged Brier skill). But
`ml_trainer.predict()` enforces a separate, stricter *economic* check
(positive profit utility) that this model does not clear — the same
standard behind every "REJECTED for live gating on cost-adjusted
evidence" verdict already found in `meta_labeler_report.json` and
`regime_meta_labeler_report.json`. Given no model has positive economic
utility, the code correctly falls through the whole chain
(`_get_ai_probability`'s final fallback, `signal_engine.py`'s own
comment: "A score-derived heuristic is not an independent probability...
treating it as AI confidence would amplify the same error twice") and
returns a neutral 0.5 rather than serve false confidence from an
unprofitable model.

**This is the capital-preservation discipline working correctly one
level deeper than anyone had checked, not a defect.** No code change
needed or made. The external AI review that flagged this got the
*observation* right (ai_score really is frozen at 0.5) but the
*diagnosis* wrong (called it a dead/broken component) — worth remembering
alongside the Bollinger correction: verify not just whether an observed
fact is true, but whether the proposed explanation for it is the right
one, especially when "constant value" could mean either "broken" or
"correctly reporting nothing to report."

## Ground rule for all four items

Same standard as everything shipped today: purged k-fold CV + deflated
Sharpe + locked holdout, holdout untouched, two-independent-halves
consistency check, confound-check against regime where relevant, and
outlier-robust reporting (median/trimmed-mean alongside mean) given how
easily a handful of large trades can dominate a small subgroup's average
(seen directly in both the TREND-regime HTF-alignment cell and the earlier
iron condor forward test). Don't loosen these standards to make a
promising-looking finding clear the bar faster.

## External-repo strategy extraction (2026-09-20)

User instruction: check public GitHub NIFTY-options trading repos, extract
their concrete strategy LOGIC (never their claimed results), and backtest
each within this project's own framework (candle_cache.db, real cost model,
option_intraday_pricer.py's EOD-settle-anchored Black-Scholes pricer).

### 1. "Target Premium Breakout" (udhay8005/nifty_bot) -- REJECTED

Rule (from `core/strategy.py` + `config.py`, read in full): 9:25 scan the
option chain for the CE strike and PE strike (independently) whose premium
is closest to Rs180; 9:30-9:35 one-shot entry window, buy whichever leg's
premium first breaks above Rs180 (CE has priority if both fire); SL
entry-20pts, target entry+40pts; breakeven once +20pts favorable; SL trails
to the prior 5-min candle's low from 9:45; hard time exit 10:00 AM
regardless. At most 1 trade/day. Implemented in
`backtest_premium_breakout.py`, with two deliberate strategy-favoring
approximations documented in its header (assumes every breakout sustains;
checks each bar's favorable extreme, not just its close).

**Result, full `candle_cache.db` history (2025-05-19 to 2026-09-17, 334
candidate days, qty=65 = 1 lot):**
- 308 trades taken (23 no-breakout days, 1 no-pricing, 1 no-expiry skip)
- Exit breakdown: 233 SL_INITIAL (75.6%), 34 SL_TRAILING, 12 SL_BREAKEVEN,
  18 TIME_EXIT, only 11 TARGET (3.6%)
- Win rate net of cost: 11.69%
- NET P&L: -Rs294,589 (gross -Rs270,172, cost Rs24,417)
- Sharpe (net, annualized): -16.46, max drawdown Rs294,811

**Verdict: REJECTED, decisively.** Three out of four trades never even
survive to breakeven -- this isn't a marginal miss, it's a structural
mismatch between a 20pt stop and a 40pt target on a long-premium momentum
scalp with a ~30-minute lifecycle. The likely mechanism (consistent with
every other tested strategy in this system): theta decay works against a
long-premium position over even a short holding window, while a tight
initial stop gets clipped by ordinary 5-min intraday noise before any
directional edge has a chance to pay off. Sample checked by hand (8 random
trades) before trusting the aggregate -- confirmed the mechanics are
correct, not an artifact (the apparent "SL exit above entry price" cases
are real trailing-stop locks, not a bug). No further validation
(walk-forward/deflated Sharpe) warranted -- the initial screen result is
unambiguous and this doesn't need the full harness to know it fails.

### 2. Short-straddle repos (buzzsubash/algo_trading_strategies_india,
0920_short_straddle family) -- same strategy CLASS as an
already-rejected candidate, not separately backtested

Partial read of `nifty50_0920_short_straddle.py` (sell ATM CE+PE at 9:20,
25% per-leg premium SL, single scheduled re-entry at 12:30, square-off
15:06) shows this is mechanically the same strategy class as
`rolling_short_straddle` (already tested this session: -Rs2,897,571 over
498 cycles, 58% whipsawed by leg-level % stop-loss -- see the seminar-
strategy results referenced earlier in this file). Different specific
parameters (25% vs 20% leg SL, 9:20 vs 10:00 entry, single vs continuous
re-entry), but the SAME fundamental failure mode this system has already
measured: leg-level percentage stops on short-premium positions get
whipsawed by ordinary intraday chop before theta decay can pay off. Given
that established result, a full separate backtest of this variant was
judged not worth building -- the prior is already strong and the marginal
information from re-testing a parameter variant of an already-rejected
strategy class is low relative to the effort. Flagged here rather than
silently dropped; can be built on request.

`snowjug/Trading-Bot` and `Aditya0049/NIFTY-OPTIONS-TRADING-AI` were
cloned but not yet read in detail -- deprioritized once the pattern above
(two independent repos separately reinventing recipes this project has
already tested and rejected) reduced the expected value of continuing
through the remaining repos versus moving to TradingView's well-known
built-in strategies (user's next-stated priority, 2026-09-20).

## TradingView built-in strategies (2026-09-20)

User instruction: after the external-repo effort above, check what "well-
known TradingView built-in indicators/strategies" this system's 79-strategy
registry (`signal_engine.py:2326`) doesn't already cover, and backtest the
gaps. Registry inventory found the large majority already present under
this project's own names (Supertrend, RSI+divergence, Bollinger %B, VWAP
variants, Ichimoku, Parabolic SAR, Chaikin MF, Williams %R, Awesome
Oscillator, Elliott Wave, Volume Profile, pivot/CPR variants, etc.) --
genuinely missing: MACD (standalone), Stochastic Oscillator, ADX/DMI,
Donchian Channel breakout, Aroon. All five indicator calculations already
existed as tested functions in `indicators.py`; only the entry/exit rule
and wiring needed writing. Implemented as
`backtest_{macd_crossover,stochastic_crossover,adx_dmi_crossover,
donchian_breakout,aroon_crossover}.py`, each a standard textbook rule
(crossover for MACD/Stochastic/ADX-DMI/Aroon, N-period breakout for
Donchian) reusing `single_leg_intraday_option_backtest.py`'s existing
single-leg option-buy harness -- same one `backtest_sma20_atm_option.py`
uses. ATM strike, generic +Rs30,000/-Rs20,000 unrealized exit / 3:10pm
square-off (this project's established default for adapting an
underlying-chart signal into option-buying, not a TradingView-native rule).

**Initial screen, full `candle_cache.db` history (2025-05-19 to
2026-09-18, 334 candidate days, qty=65):**

| Strategy | Trades | Net P&L | Sharpe |
|---|---|---|---|
| macd_crossover | 325 | -Rs41,431 | -0.808 |
| stochastic_crossover | 330 | +Rs140,694 | 1.700 |
| adx_dmi_crossover | 246 | +Rs46,751 | 1.073 |
| donchian_breakout | 330 | +Rs111,872 | 1.551 |
| aroon_crossover | 306 | +Rs104,997 | 1.635 |

Four of five looked positive -- which, given this project's entire track
record (every one of 79 formally-validated strategies, every seminar
strategy, every confluence modifier, and the HTF-alignment candidate has
failed OOS after costs), was immediately treated as suspicious rather than
reported as a finding. Two things stood out before trusting it: (1) every
single trade across all five backtests exited via TIME_EXIT -- the
+Rs30,000/-Rs20,000 unrealized thresholds NEVER fired once, so each
"strategy" reduces to "buy ATM CE or PE at some signal bar, hold to
3:10pm square-off," nothing more; (2) per-side P&L decomposition showed
PE trades dominating profit in all four positive strategies (e.g. Donchian:
CE=-Rs1,589 across 159 trades vs PE=+Rs113,461 across 171 trades) while
NIFTY fell -6.71% over the exact same window (25,026.5 -> 23,346.4).

**Decisive confound check**: backtested two trivial baselines with NO
indicator at all -- "always buy ATM PE at the day's open, hold to
square-off" and the CE mirror. Naive always-PE: net +Rs234,294, Sharpe
2.428 -- BETTER than every one of the four "positive" indicator strategies.
Naive always-CE: net +Rs28,892, Sharpe 0.316 -- consistent with the
downward drift. This proves the four positive results are not adding any
signal value at all: they are diluted, worse-timed versions of just
capturing the sample period's realized index drift. An indicator that
only sometimes fires PE and sometimes CE, with no real timing skill, will
show a positive Sharpe purely by being correlated with the direction the
market happened to move over this specific 16-month sample -- which is
look-ahead information a live strategy would never have, not a real edge.

**Verdict: REJECTED, all five.** MACD failed outright even including the
drift. The other four's apparent edge is 100% explained by directional
drift capture over this specific backtest sample, not genuine
signal -- confirmed by underperforming a same-side, zero-indicator naive
baseline in every case. No walk-forward/locked-holdout validation
warranted; the baseline decomposition already falsifies the "this
indicator has edge" claim before that stage. This is the same discipline
already applied to cross-sectional/pairs-trading candidates
(`cross_sectional_factor_test.py`, `pairs_stat_arb_validation.py`) now
extended to single-leg option-buying backtests: an aggregate positive
number must survive decomposition against the simplest possible
directional-exposure explanation before being trusted at all.

**Stopped here (2026-09-20), by user decision.** Remaining untested
indicators (Keltner Channel, Force Index, Hull MA, TEMA) are the same
directional-momentum shape that just produced 4 drift-artifact false
positives above; StochRSI and OBV exist in `signals.py` only as buried
confluence-scoring features, never as standalone entry triggers, and were
also left untested as such. Flagged here rather than silently dropped, in
case a specific one of these is ever worth revisiting on request.

## Chartink public screener strategies (2026-09-20)

User instruction: test well-known, publicly-shared Chartink screener
strategies. Chartink itself only screens (flags matching stocks daily);
it has no built-in exit, so this backtest picks ONE holding period
(10 trading days, a common retail swing horizon) as the primary test --
this project's own modeling choice, stated plainly, not scraped from an
authoritative source.

Implemented in `chartink_screen_backtest.py`, testing four of Chartink's
most widely shared public scans against the ~192-stock NIFTY200 universe
in `candle_cache.db` (1d interval): SMA(200) breakout, 52-week-high +
1.5x-volume breakout, Supertrend(10,3) bullish flip, RSI(14) oversold
(<30) bounce. Reuses `indicators.py`'s existing, unmodified
`calculate_supertrend`/`calculate_rsi` -- no new indicator math. Applies
the SAME lookahead discipline already established this session (the
52-week high is computed from PRIOR bars only, never the triggering
bar's own close -- the Donchian self-reference lesson from the
TradingView-strategies effort, applied here from the start).

**Drift-confound discipline applied UP FRONT this time** (learned from
the intraday-options false positives the same day): every hit's return is
measured as EXCESS return over the equal-weight universe's return across
the exact same window, never raw return -- so a screen correlated with
market-wide direction over this sample can't masquerade as edge. Real
delivery-equity cost (0.22% round-trip, same estimate as
`cross_sectional_factor_test.py`). Day-split 70/30 train/holdout,
one-sided LCB95, Bonferroni across the 4 screens.

**Data-depth caveat**: common daily history is only ~1 year per stock
(313 bars for RELIANCE), so the 252-day 52-week-high screen only had
~60-70 days of live signal-generation window after its own warmup --
smaller than ideal, but the resulting hit counts (580-1110 across the
192-stock universe) still gave a workable sample.

**Result, all four REJECTED:**

| Screen | Hits | Train excess (net) | Train p | Holdout excess (net) | Holdout LCB95 | Verdict |
|---|---|---|---|---|---|---|
| sma200_breakout | 822 | -0.095%/trade | 0.62 | -1.43% | -2.16% | NOISE |
| high52w_volume | 580 | -0.51%/trade | 0.048 | -0.61% | -1.29% | NOISE |
| supertrend_flip | 1110 | -0.34%/trade | 0.055 | -1.32% | -1.92% | NOISE |
| rsi_oversold_bounce | 1104 | -0.45%/trade | 0.008 | +0.69% | +0.17% | HURTS |

No screen clears train significance with a positive sign AND a positive
holdout LCB95 -- the bar for CANDIDATE. rsi_oversold_bounce is the closest
to "interesting" (holdout LCB95 barely positive) but its training-period
excess return is significantly NEGATIVE, so per this project's verdict
rule (train sign must agree with holdout, not just holdout alone looking
promising) it's correctly labeled HURTS, not a near-miss worth chasing --
exactly the discipline this project's rules exist to enforce against a
result that would otherwise tempt a "just look at the good half" read.

Ninth and tenth rejections today (11 total across the two prior efforts,
all consistent with `validation_harness.py`'s standing finding that no
rule strategy in this system, options or equity, clears out-of-sample
costs). No further Chartink screens planned unless a specific one is
requested -- diminishing returns from testing more instances of the same
already-falsified pattern (technical crossover/breakout screened across a
liquid Indian equity/index universe, no fundamental or flow data behind
any of them).

## Option-SELLING strategy audit: standalone vs pair (2026-09-20)

User instruction: "any other improvements required" surfaced that
option-selling coverage was thin (only 1 of ~17 selling-type entries in
`option_strategy_registry.py`'s 42-strategy catalog had ever been
rigorously backtested); user then asked to "include all option selling
standalone and pair option selling" -- i.e. both undefined-risk naked
selling (straddle/strangle, no protective wings) and defined-risk paired
spreads (credit spreads, iron condor, iron butterfly, each short leg
hedged by a long leg).

**Pre-existing state found**: `condor_backtest_real.py` (built 2026-06-19
as the real-premia replacement for the blocked, synthetic-credit
`backtest_iron_condor.py`) had never actually been run over its full
available history -- only a live 10-cycle forward test existed
(`condor_forward_test.json`, +Rs16,877/10 cycles/90% win rate). Flagged
explicitly that n=10 is nowhere near enough to have seen this structure's
defining tail risk yet.

**PAIR (defined-risk) results, real options_nifty.db premia, 2020-01-01
to 2026-09-18 (1,660 trading days, 3.24M option rows), qty=75, weekly
entries, real brokerage+slippage costs:**

- **Iron Condor (C3)** -- REJECTED. Default params (otm=1.5%, wing=300):
  n=350, win_rate=71.4%, expectancy_R=-0.0355, total_R=-12.42, OOS
  expectancy also negative (-0.0585). 7 of 9 grid combos (otm x wing)
  negative; the 2 marginally positive ones (otm=1.5-2.0%, wing=500) are
  small enough to be noise from a 9-combo search, not a finding. Worst
  weeks include 3 from the 2020-03 COVID crash -- the tail event the
  10-cycle live sample hadn't seen. Built-in verdict function:
  "negative expectancy after costs -- NO edge."
- **Iron Butterfly (C4)** -- REJECTED, more decisively than the condor
  (same code, `otm_pct=0` so the short strikes sit at-the-money instead
  of OTM). All 4 wing widths tested (150/200/300/500) show negative
  expectancy, win rate drops to 40-47% (an ATM short straddle center gets
  breached far more often than an OTM strangle).
  New file: `credit_spread_backtest_real.py` (reuses condor_backtest_
  real.py's already-validated helpers -- `_load`, `_prep`, `_nearest`,
  `_leg_price`, `_metrics`, `_report` -- rather than re-deriving them).
- **Bear Call Spread (B4)** -- REJECTED cleanly, all 9 otm/wing combos
  negative.
- **Bull Put Spread (B3)** -- the one genuinely interesting case today.
  Best-looking config (otm=2.0%, wing=500): n=350, win_rate=91.7%,
  expectancy_R=+0.0185, total_R=+6.47, net +Rs186,415 over 350 trades.
  Survived BOTH in-sample and OOS with the same sign (IN exp_R=0.0146,
  OOS exp_R=0.0275 -- actually improved OOS, unusual), and passed the
  tail-dependence check cleanly (total_R excluding the 3 worst trades =
  9.51, HIGHER than the full 6.47 -- meaning the result isn't propped up
  by a few lucky weeks, unlike every "fragile positive" pattern seen
  elsewhere today; the defined-risk cap also kept the 2020-03 COVID weeks
  to about -1.0R instead of catastrophic, unlike the naked structures
  below). This was, on its face, the most robust-looking positive result
  of the entire day. **Then rejected anyway** once given the same rigor
  as everything else: full-sample t=2.06/p=0.039 looks nominally
  significant, but Bonferroni-corrected across the 18 combos actually
  tested (both spread sides x 9 grid points), p=0.710 -- not significant
  at all. Worse, the IN-SAMPLE split alone never clears significance on
  its own terms (t=1.366, p=0.172, LCB95=-0.003, crosses zero) -- the
  full-sample p-value was doing all the work by pooling train+holdout
  together, exactly the kind of pooling this project's day-split
  discipline exists to prevent. **Verdict: NOISE, not a real edge** --
  the apparent Rs186,415 is not statistically distinguishable from zero.

**STANDALONE (naked, undefined-risk) results**, same real EOD-settle-
anchored Black-Scholes intraday pricer as this session's other intraday
backtests (`option_intraday_pricer.py`), full `candle_cache.db` history
(334 candidate days), qty=65, real costs. New file:
`naked_straddle_strangle_backtest.py`.

- **09:20 Short Straddle (C1)**, single entry/day (no re-entry, distinct
  from the already-tested E4 Rolling Short Straddle's cycling version)
  -- REJECTED. 329 trades, 86.0% hit the leg-level 30% stop-loss before
  square-off, win rate 22.8%, net -Rs115,671, Sharpe -11.14.
- **Short Strangle OTM3 (C2)** -- REJECTED, same mechanism. 332 trades,
  91.3% hit leg SL, win rate 19.0%, net -Rs75,739, Sharpe -10.47.
- Consistent with E4's earlier result (-Rs2,897,571, 58% whipsawed): the
  fundamental problem for every naked/undefined-risk short-premium
  structure tested in this system is the SAME -- a leg-level percentage
  stop-loss gets whipsawed by ordinary intraday chop long before theta
  decay can pay off, regardless of entry time or strike distance.

**Full standalone-vs-pair scorecard: 7 tested, 7 rejected** (C1, C2, C3,
C4, B3, B4, plus E4 from earlier this session). The defined-risk
structures (C3, C4, B4) fail cleanly on raw expectancy; the ONE that
looked genuinely promising (B3) failed only once proper significance
testing was applied, not on a first look -- worth remembering as the
clearest demonstration all day of why this project never reports a raw
number without the day-split + Bonferroni step. The undefined-risk
structures (C1, C2, E4) all fail catastrophically via the same leg-SL
whipsaw mechanism, regardless of specific parameters.

**Consciously not built**: B5 (Ratio Spread 1:2) and E7 (Ratio Spread
with Tail Hedge) are both tagged DANGEROUS in the user's own spec
(asymmetric/uncapped risk on the extra naked leg) -- given 7/7 rejections
across every reasonably-testable structure in this family already, and
capital-preservation-first being this project's explicit standing rule,
building an intentionally higher-risk untested structure wasn't judged
worth doing without being specifically asked. Also untested: C5
(Calendar Spread), C6 (Expiry-Day OTM Decay Harvest), C9 (Short
Straddle->Strangle Conversion), D2 (Post-Event Vol Crush Short
Strangle), E2/E3/E5/E6 -- flagged here rather than silently dropped, but
the honest prior after this comprehensive a rejection pattern is that
they would very likely reproduce either the naked-whipsaw or the
paired-noise result already established, not something structurally new.

## Modifier pruning locked-holdout check (2026-09-20) -- DO NOT ACT YET

CLAUDE.md's own "Real next step #1" (prune confluence modifiers that
measure NOISE/HURTS) turned out to already be data-ready:
`modifier_edge_report.json` (generated 2026-09-20 17:36, 28,123 live
signals, Bonferroni-corrected across 27 modifiers) flags `mtf_pivot_mod`
(70.5% coverage, endorsed -0.058R vs silent +0.066R, t=-9.08, p~=0) and
`sr_level_mod` (80.1% coverage, endorsed -0.029R vs silent +0.022R,
t=-3.34, p=0.0008) as HURTS -- both already passed the analyzer's own
built-in "two time halves" stability guardrail, so this wasn't a
first-glance number.

Per the module's own stated discipline ("dropping a modifier is a human
decision after a locked-holdout pass"), ran a genuinely separate,
day-boundary holdout check (not reusing the halves already baked into the
report): reserved the most recent 25% of days (2026-08-21 onward, 10
days, n=2,229) as an untouched slice, re-ran the identical endorsed-vs-
silent Welch t-test on ONLY that reserved slice.

**Result: neither HURTS effect replicates in the holdout.**
- mtf_pivot_mod: discovery lift=-0.134 (t=-9.42) -> holdout lift=+0.0048
  (t=0.089, p=0.929) -- collapses to zero, doesn't even keep its sign
  reliably.
- sr_level_mod: discovery lift=-0.055 (t=-3.48) -> holdout lift=-0.0047
  (t=-0.075, p=0.940) -- also collapses to noise.

**Verdict: DO NOT prune or flip either modifier in signal_engine.py right
now.** The strong, highly-significant pattern in the pooled/discovery
data does not hold up on the genuinely out-of-sample slice -- exactly the
scenario the locked-holdout requirement exists to catch. This is NOT "the
original finding was definitively wrong" (the holdout is only 10 days /
2,229 signals, underpowered relative to the 25,894-signal discovery set)
-- it's "not robust enough to act on with real capital," which under this
project's capital-preservation-first rule is treated the same as no.

**Next step (DATA-GATED, genuinely, not a formality this time)**: re-run
this exact holdout check again once more trading days accrue past
2026-08-21, with a larger holdout sample. Do not re-litigate this with
the SAME data -- that would be the identical mistake as reusing a result
that already informed a decision. sip_boost (the one HELPS verdict) and
the 10 DEAD + 9 NOISE modifiers were not re-checked this pass (lower
priority than the two live HURTS candidates that would have caused an
active edit to the live confluence scoring).

### sip_boost holdout check (2026-09-20, same pass)

The one HELPS-verdict modifier from the pooled report (endorsed_mean
+0.0796 vs silent -0.0253, lift=+0.1049, t=3.761, p=0.000175 in
discovery). Same day-boundary holdout (2026-08-21 onward, n=2,229):
**zero endorsed signals in the entire holdout window** -- sip_boost
simply didn't fire once in the last 10 trading days. Inconclusive, not
rejected: there's no data to confirm or deny against, unlike the two
HURTS candidates above which had plenty of holdout occurrences that
actively failed to replicate the effect.

**Modifier-pruning effort, final status for this pass**: every modifier
with an actionable (HELPS or HURTS) verdict in the pooled 28,123-signal
report has now been checked against a genuinely separate, day-boundary
locked holdout. Result: mtf_pivot_mod and sr_level_mod's HURTS effects
both collapsed to statistical noise; sip_boost's HELPS effect couldn't
even be tested (no holdout occurrences). **Net result: zero actionable
changes to signal_engine.py's confluence scoring from this pass.** The
DEAD (10) and NOISE (9) verdicts were not holdout-checked -- a smaller
holdout sample can't produce evidence of an effect the larger pooled
sample already failed to find, so there's nothing to gain by testing
them. Re-run this same check again once meaningfully more days accrue
past 2026-08-21, particularly for sip_boost (needs a window that
actually contains some of its trigger condition to be testable at all).

## pivot_scalping diagnostic trace completed (2026-09-20)

Forward monitor (`pivot_scalping_inversion_monitor.py`) re-run: **still
zero new signals** since the 2026-09-12 discovery date, and zero since
2026-07-29 originally -- an 8-week-plus persistent stall, not a temporary
lull. Ran the two-step trace the queue specified:

**1. Why it stopped firing** -- mechanistic explanation, not just
correlational. `pivot_scalping_strategy.py`'s own score has two penalty
terms that fire specifically in range-bound conditions: `no_trade_zone`
(-1.0 to BOTH buy_score and sell_score, lines 278-280) and "in_cpr
without golden-pivot/EMA-cross confirmation" (-0.8 to both, lines
281-283). Both trigger more often in a calmer market (tighter ranges ->
more time spent inside CPR/between Camarilla levels without a genuine
breakout). This directly explains the already-verified VIX regime shift
(active window 2026-06-29 to 07-29 mean=13.19 vs silent window from
07-30 mean=11.59, entire silent range below the active mean) at the
CODE level, not just as a correlation: lower volatility mechanically
produces more of exactly the score-suppressing conditions this strategy
was designed to sit out in.

**2. Sign-inversion check** -- built a reproducible synthetic-bar test
(`pivot_scalping_sign_check.py`, scratch) rather than rely on
historical-signal reconstruction, which turned out to be infeasible:
signal_log only stores side + outcome for this strategy, not the
intermediate CPR levels/reasons/score components that produced each
historical signal, so byte-for-byte reproduction of a specific logged
signal isn't possible with what's actually recorded. Instead: fed an
unambiguous synthetic breakout-above-resistance scenario and an
unambiguous breakdown-below-support scenario through the CURRENT code.
Result: breakout-up correctly returns BUY (score=8.0, cpr_bias=BULLISH),
breakdown correctly returns SELL (score=8.0, cpr_bias=BEARISH).
**No sign-inversion bug** -- confirms the original static-code audit's
finding with a live, direct test instead of just code reading.

**Combined conclusion**: since there's no code bug to fix and the
strategy currently produces zero signals to test against, the original
92.9%-if-flipped win-rate finding remains an unconfirmed, unexplained
empirical pattern from one specific ~1-month window -- if real at all,
it would have to come from a genuine market-regime mismatch (CPR/
Camarilla "reversal" signals getting run through by an underlying trend
rather than holding, during what the earlier VIX check already showed
was the more volatile of the two windows), not a labeling defect. This
is a hypothesis, not a verified mechanism -- untestable further right
now since there's no new data. **Standing verdict unchanged: do NOT
flip the signal live.** Discovery method (scanning 49 strategies for the
most extreme outlier) carries high multiple-testing risk on its own, and
there is nothing to act on regardless while the strategy produces zero
signals. Re-run the forward monitor periodically; nothing else to do on
this thread until either new signals appear or the regime shifts back.

## ORB parameter family: built and tested, 15/15 REJECTED (2026-09-20)

Closed out the "ORB as its own parameter family" item flagged earlier
(the existing `validation_harness.py` "orb" grid only varied adx_min x
volume_min x target_mult, with the range-window itself, max-OR-width,
breakout buffer, time-cutoff, and previous-day confirmation all
hardcoded/absent).

**Code change** (`backtest_orb.py`, existing research/backtest file --
NOT the live `orb_strategy.py` signal-generation path, which was not
touched): added 5 new parameters, ALL defaulting to exactly the prior
hardcoded behavior so the existing grid entry in validation_harness.py
is completely unaffected -- verified by re-running the unmodified default
call and confirming it still reproduces the original fixed-window
behavior bar for bar (`window_end_t` computes to 09:30:00 for the
default `orb_window_minutes=15`, matching the old `ORB_WINDOW_END`
constant exactly):
  - `orb_window_minutes` (default 15) -- variable opening-range window.
  - `max_or_width_pct` (default None = no cap) -- skip days where the
    range is implausibly wide relative to price.
  - `breakout_buffer_pct` (default 0.0) -- require the close to clear
    the range by more than a bare touch.
  - `valid_until` (default 10:30, matching the old constant) --
    parametrized stale-signal cutoff.
  - `require_prev_range_confirm` (default False) -- require the
    breakout to also clear the PREVIOUS day's high/low, not just
    today's narrow opening range.
  Also added a `trades` key to `_compute_metrics`'s return dict (purely
  additive, needed for a genuine day-split significance test -- same
  pattern as the `backtest_supertrend_mtf.py` fix earlier this session).

**Test method**: one dimension at a time (not a full combinatorial
cross -- 5x3x3x3x2=270 combos would have been an undisciplined fishing
expedition), 15 configs total against `candle_cache.db`'s full NIFTY 5m
history (2025-05-19 to 2026-09-18, 334 days), day-split 70/30, Welch
t-test, Bonferroni across the 15 configs actually run
(alpha_corrected=0.00333), one-sided LCB95.

**Result: all 15 configs negative, in-sample AND out-of-sample, most
clearing Bonferroni-corrected significance.** Baseline (current
defaults): 254 trades, total -Rs664,808, IN p=3e-05, OOS p=1e-05. Every
window size (5/30/45/60 min), every width cap, every buffer, every time
cutoff, and the previous-day confirmation all remain negative -- nothing
flips it positive, and the least-bad configs (buffer=0.20%,
prev_range_confirm=True) are only "less negative," never break even.
**REJECTED across the entire newly-built parameter family**, closing
this out comprehensively rather than leaving it as an open question --
ORB's failure (already known from the adx/volume/target grid) isn't
fixable by any of the structural variants this item proposed.

## Item 2 (hour-of-day pattern): both required checks run (2026-09-20)

**Two-halves consistency**: hour9-vs-hour13 MFE>=0.5R-rate gap holds in
both halves (OLDER +0.269, NEWER +0.195, same sign, same order of
magnitude).

**Regime confound check**: the MFE>=0.5R-rate gap survives within EVERY
regime tested (EARLY_TREND +0.243, BREAKOUT +0.093, TREND +0.246, RANGE
+0.237) -- not a regime artifact.

**Critical reframing, found by checking the economically relevant
metric alongside the originally-cited one**: the queue's original
observation used MFE-hit-rate only. Checking net R-multiple by hour
(the metric that actually matters for P&L) shows the OPPOSITE story:
hour 9 has the WORST mean net R of any hour (-0.246), while hour 13 --
the hour with the WORST MFE-hit-rate -- has one of the BEST (-0.175).
**Hour 9 doesn't win more; it wins early and gives it back.** This
"giveback" reading holds in both time halves (OLDER gap -0.076, NEWER
gap -0.065, same sign) and in 3 of 4 regimes (BREAKOUT -0.291, TREND
-0.102, RANGE -0.060), but is essentially flat within EARLY_TREND
specifically (+0.012, the single largest regime bucket by n) -- so the
giveback problem is concentrated in TREND/BREAKOUT/RANGE conditions, not
uniform across all regimes.

**Conclusion: this is not "trade more at the open," it's a live lead for
item 3 (winner-giveback/trailing-stop efficiency), concentrated at
market open specifically.** The original "opposite of the avoid-the-open
assumption" framing was based on an incomplete metric (MFE-hit-rate
alone) and, now corrected against net R, actually supports the opposite
practical conclusion: hour-9 entries are where exit/trailing mechanics
are losing the most ground, not where they're working best. No code
change made -- this is an analysis result, feeding directly into item 3
below.

## Item 3 (winner-giveback ceiling): computed, hard bound found (2026-09-20)

Used only the existing MFE dataset (28,123 training-eligible trades with
both `max_favorable_move` and `tb_r_multiple_net` populated), no new
signal generation, per the item's own instruction. Winners' median MFE
reproduces the cited figure almost exactly (0.847R vs 0.85R cited) --
good sanity check that this is the same data/method. Winners' median
*realized* R is 0.385R (lower than the 0.63R cited on 09-13, consistent
with more data having accrued and/or a mean-vs-median difference in the
earlier note) -- either way, directionally the same large gap between
peak and close.

**Per-threshold ceiling** (idealized, NOT achievable in practice -- zero
slippage, exact-peak lock-in the instant each threshold is first
touched, the most generous possible assumption):

| threshold | % of all trades touching it | mean realized R (of those) | ceiling uplift (aggregate R) |
|---|---|---|---|
| 0.5R | 39.5% | 0.188 | +3,465.8 |
| 0.75R | 25.4% | 0.391 | +2,566.6 |
| 1.0R | 16.5% | 0.595 | +1,882.1 |
| 1.25R | 11.4% | 0.786 | +1,487.3 |
| 1.5R | 8.4% | 0.973 | +1,237.0 |
| 2.0R | 4.5% | 1.159 | +1,070.4 |

**Applied against the actual aggregate (-5,615.1R across all 28,123
trades), NONE of the six idealized ceilings flip the portfolio
positive.** The best case (locking in at 0.5R, the most inclusive
threshold) closes 61.7% of the deficit (-5,615.1 -> -2,149.3) but
remains solidly negative; every other threshold closes less (down to
19.1% at 2.0R, since fewer trades ever reach it).

**This is a hard, quantified ceiling, not just a qualitative caution: an
idealized, unrealistically perfect trailing-stop mechanism -- something
no real implementation could actually achieve -- still cannot make this
system profitable on its own.** Confirms and now numerically bounds the
"Sharpened research thesis" already at the top of this file (entry
quality is the dominant problem, exit/trailing is real but secondary):
at most ~62% of the current deficit is closeable via exit mechanics
alone, under the most generous possible assumption; a real, implementable
trailing-stop change (with actual slippage, imperfect timing, and the
practical cost of testing/deploying it) would close meaningfully less
than even that.

**Recommendation: do not prioritize building new trailing-stop logic as
primary research effort.** The three trail-shape variants proposed
earlier (delay activation to +1R, trail behind structure, scale out at
the realized average) remain reasonable engineering ideas and could
still be tested cheaply if there's appetite, but none of them should be
expected to fix the system's overall economics -- that requires
entry-quality work (items 4-5), not exit-mechanics work. No trailing
logic was written this pass; the ceiling estimate itself is the
deliverable this item asked for, and it argues against spending further
effort here before entry quality improves.

## Item 4 (ML: predict MFE threshold, not win/loss) -- built and run (2026-09-20)

New module `mfe_threshold_labeler.py`, reusing `meta_labeler.py`'s
existing feature list (`_FEATURES`, not duplicated) and cleaning
pipeline, but trained against `(max_favorable_move >= T)` for
T in {0.5R, 0.75R, 1.0R} instead of final win/loss.

**Deliberately stricter split than meta_labeler.py's own methodology**:
that module picks its best P(win) gating threshold by scanning multiple
thresholds directly against its held-out test set -- a mild form of
threshold-shopping against the holdout. This module uses a genuine
3-way, date-ordered split instead: TRAIN (60%) fits the model,
VALIDATION (20%) selects the single cutoff using only its own
avg_net_r, and the HOLDOUT (20%) is touched exactly once with the
already-chosen cutoff, never used for model or cutoff selection. CPCV
is computed on train+validation only, holdout excluded from that too.

**Results (holdout AUC / CPCV mean, then the one locked-holdout
application)**:
- MFE>=0.5R: AUC=0.619, CPCV mean=0.624 (15/15 paths). Validation chose
  cutoff 0.65 -> holdout: n=11 (0.5% coverage), avg_net_r=-0.112 vs
  baseline -0.167 (beats baseline, still net negative).
- MFE>=0.75R: AUC=0.638, CPCV mean=0.651. Cutoff 0.65 -> holdout: n=21
  (0.95% coverage), avg_net_r=-0.066 vs baseline -0.167 (beats baseline
  by the largest margin of the three, still net negative).
- MFE>=1.0R: AUC=0.641, CPCV mean=0.679 -- the STRONGEST discriminative
  power of the three targets. Cutoff 0.65 -> holdout: n=19 (0.86%
  coverage), avg_net_r=-0.194 vs baseline -0.167 -- **does NOT beat
  baseline**, despite having the best AUC/CPCV. A direct demonstration
  of why the locked-holdout discipline matters: predictive power alone
  doesn't guarantee the chosen cutoff generalizes economically.

**Conclusion**: genuine, non-noise predictive signal exists for all
three MFE targets (CPCV mean 0.62-0.68, comparable to or better than the
existing WIN/LOSS model's own 0.611) -- confirms, from a different
angle, today's earlier finding that entry-time features carry real
information about a trade's eventual favorable excursion. But every
gated cutoff selects an extremely thin slice (11-21 trades out of
~2,200 holdout rows, well under 1% coverage) -- too small a sample to
trust any of these avg_net_r numbers as validated economic evidence
either direction, and even the "beats baseline" cases remain solidly
net-negative in absolute terms. **Not promoted, per this item's own
instruction, regardless of result.** No further action -- this closes
the item as "real signal exists, not economically actionable at current
coverage," distinct from meta_labeler's existing WIN/LOSS model, which
is "real signal exists, actively HURTS economics when gated."

## Volatility-surface relative-value: exploratory SVI fit built and tested (2026-09-20)

New module `svi_smile_relative_value.py` -- borrows the METHODOLOGY cited
from public research (Gatheral's raw SVI parametrization, a standard,
decades-old, publicly-documented approach, not proprietary to any of the
six repos flagged earlier), built entirely from scratch using scipy +
this project's own already-tested `option_intraday_pricer.implied_vol()`
against real `options_nifty.db` EOD chains. No external repo code was
read or imported, per the queue's own explicit caution that this idea is
"a fundamentally different risk class" requiring review before any
third-party code is trusted.

**Fit quality**: sanity-checked on 10 recent days, 87-96 OTM-side quote
points per smile, RMSE(w) consistently 0.00006-0.00009 (small in
variance-space) -- the SVI parametrization fits NIFTY's real weekly
smile cleanly. 0-DTE expiry days correctly fail to fit (T~=0, expected,
not a bug).

**The actual diagnostic the queue asked for, run before any trade
construction**: flagged the top-decile |residual_iv| strikes on each of
52 successfully-fit days (410 same-contract day-over-day pairs, expiry
rolls excluded), and checked whether each strike's |residual| shrinks
the next day (convergence) or not.

**Result: 80.7% GREW, only 19.3% shrank -- the OPPOSITE of the
convergence hypothesis, and a strong, non-random split (not a
50/50-ish "no effect" result).**

**Important confound, flagged rather than over-claimed**: within a
short NIFTY weekly expiry, time-to-expiry shrinks day by day, and
IV-extraction noise mechanically AMPLIFIES as T->0 (a fixed rupee
bid-ask spread translates into a larger IV-space error as vega shrinks
near expiry). This plausibly explains some or all of the "residuals
grow" pattern as a mechanical noise-amplification artifact of short
weekly expiries specifically, rather than genuine, exploitable relative
mispricing that shrinks over time. The honest statement is therefore:
**no evidence of repeatable convergence was found, and there's a
plausible, un-ruled-out mechanical explanation for the negative result
itself** (as distinct from a clean "definitely no relative value exists
anywhere in the vol surface" claim, which this test doesn't have the
power to make).

**Verdict: does NOT pass its own first diagnostic gate.** Per the
queue's own framing ("test whether that residual shows repeatable
convergence -- BEFORE ever constructing a trade"), this idea does not
proceed to trade construction. If revisited, the noise-amplification
confound should be addressed first (e.g. restrict to longer-dated
monthly expiries where vega stays larger for longer, or filter out the
most illiquid far-OTM strikes before flagging residuals) rather than
concluding the underlying idea is dead outright -- this is a "the test
as designed didn't support it, with a known weakness in the test
itself" result, not a maximally rigorous rejection like the 20+
strategy backtests earlier today.

## Status: all four remaining queue items from 2026-09-13 now addressed (2026-09-20)

Item 2 (hour-of-day): both required checks run, reframed via net-R as a
giveback-concentration lead for item 3, not a "trade the open" signal.
Item 3 (winner-giveback ceiling): computed, hard quantified bound found
(at most ~62% of the deficit closeable even under an idealized,
unachievable trailing stop) -- recommend NOT prioritizing new trailing
logic. Item 4 (ML MFE-threshold): built and run with a stricter
locked-holdout discipline than the existing WIN/LOSS model; genuine
signal exists (CPCV 0.62-0.68) but not economically actionable at
current coverage (<1% of signals gated, still net-negative). Item 5 in
this file was actually "Option-level P&L translation" (still open, no
next step defined yet, not attempted this pass) -- what the user meant
by the fourth item was the volatility-surface idea, now covered above.
Nothing here is promoted or wired into the live system; every result is
report-only, matching this project's standing rule.

## Zerodha Streak "EMA+Supertrend confirmation" -- REJECTED (2026-09-20, retroactively documented)

Found via web search of Zerodha Streak's publicly documented
strategy-builder examples: "Alert when 5 period EMA crosses 20 period
EMA and Supertrend is on uptrend and vice versa for sell order" -- a
combined-CONFIRMATION rule (both signals must agree), distinct from
testing either indicator alone (both already tested and rejected
elsewhere in this file: EMA-crossover is already in the 79-strategy
registry, Supertrend alone was tested as `supertrend_flip` in the
Chartink batch). Implemented in `backtest_ema_supertrend_confirm.py`
(single-leg intraday option-buying harness, same convention as every
other strategy in this batch).

**Initial screen looked genuinely positive**: 239 trades, net +Rs112,862,
Sharpe 2.871 -- one of the better-looking raw numbers all day.

**Drift-decomposition check** (same discipline applied to every prior
result): side split showed PE dominating (176 trades/+Rs85,983) over CE
(63 trades/+Rs26,879), the same captured-NIFTY-drift signature as every
other apparent positive that day. Built a matched control -- same
signal timing, but every entry forced to PE regardless of the rule's
actual direction call -- to isolate whether the CE/PE CHOICE itself adds
value beyond just being selective about which days to trade: net
+Rs92,634, Sharpe 2.484 (238 matched trades). The actual rule beat this
matched control by +Rs20,228 in raw terms.

**Paired significance test on that Rs20,228 gap** (238 paired days,
combo rule vs forced-PE on identical timing): mean paired difference
=Rs85.80/trade, t=0.520, one-sided LCB95=-Rs185.69. **Not statistically
distinguishable from zero** -- the confidence bound crosses well below
zero, so the apparent directional-selection value is noise, not a real
effect. Also note the rule's absolute performance (Rs112,862) is
strictly worse than a full-period naive always-PE baseline
(Rs234,294/Sharpe 2.428, established earlier the same day) -- so even
setting the significance test aside, this never actually beat the
"do nothing sophisticated" baseline in raw terms.

**Verdict: REJECTED.** Same conclusion as every other tested strategy
that day -- looked interesting on a raw number, evaporated under the
same paired/matched-control discipline applied everywhere else. Twelfth
rejection of that day's external-strategy-sourcing effort (external
repos + TradingView + Chartink + this Zerodha Streak example).

## "If we're negative, can we invert everything?" -- checked properly, answer is no (2026-09-21)

User question, tested empirically rather than reasoned about abstractly
(same discipline as the MACD-inversion check two days earlier).

**Critical methodology correction found along the way**: a naive sign-flip
of `tb_r_multiple_net` is WRONG and systematically overstates the
inverted case, because it implicitly gives the inverted trade a cost
REBATE rather than making it pay costs too. Transaction costs are a drag
regardless of direction (you cross the spread going in and out either
way). The correct inverted value, using the real per-trade cost
C = `tb_r_multiple` (gross) - `tb_r_multiple_net` (both columns exist in
signal_log): `inverted_net_R = -gross_R - C`, NOT `-net_R`. The naive
calculation on the full 28,123-trade aggregate showed a tempting
+Rs5,615 total (mean +0.1997/trade) -- properly corrected, the true
inverted mean is **-0.1655**, still solidly negative (barely better than
the actual -0.1997). **Inverting everything does not flip the system
profitable -- both directions pay the same cost drag, and the
underlying signal carries no reliable directional information either
way.** Confirmed via day-split: properly-corrected inverted mean is
negative in BOTH halves (-0.1598 older, -0.1905 newer).

**Per-strategy breakdown** (51 strategies with n>=100, Bonferroni
alpha=0.00098, day-split-consistency required): **zero strategies**
show a statistically significant, day-split-consistent, properly
cost-corrected POSITIVE result when inverted. Every single one is either
not significant (p far above the corrected threshold, LCB95 crossing
zero) or significantly negative even inverted.

**Correction to the earlier pivot_scalping inversion finding
(2026-09-12/2026-09-20 entries above)**: with this proper cost-corrected
calculation, on the FULL dataset, pivot_scalping's inverted mean is
**-0.1517 (p=0.0, highly significant NEGATIVE)** -- not the 92.9%
win-rate-if-flipped pattern originally reported. That earlier finding was
very likely computed via win-rate on `tb_label` directly (a different,
uncorrected metric) rather than this project's house-standard
cost-adjusted R-multiple, or captured a narrower/different date window.
Recorded here as the more rigorous, full-dataset, properly-corrected
answer -- supersedes the earlier finding rather than sitting alongside
it unresolved.

**Scalping, specifically checked per request**: the core `scalping`
strategy itself is currently OOS-disabled (`_SCALPING_OOS_DISABLED` flag
in signal_engine.py's registry) and has zero signal_log data to check at
all. Of the two scalp-named strategies with real data: `pivot_scalping`
(n=266) is covered above -- significantly negative even inverted.
`expiry_scalp` (n=33, only 6 distinct trading days) shows a marginal,
NOT statistically significant result even before Bonferroni correction
(p=0.070, LCB95=+0.037, barely above zero) and is wildly inconsistent
across its own two halves (28-trade older slice +0.457 vs 5-trade newer
slice +0.047) -- a small-sample artifact, not a finding.

**Verdict: nothing here qualifies to be "fixed"/flipped in the live
system.** No code change made. This closes the "can we just invert it"
question comprehensively rather than leaving it open to be re-asked --
the answer, checked properly across the whole system and every
individual strategy, is no.

## Contrarian-sell (sell opposite option to our own signal) -- REJECTED (2026-09-21)

User idea, distinct from the aggregate/per-strategy inversion check
earlier the same day (that flipped BUY<->SELL and still BOUGHT the
flipped side -- long premium, pays theta): this SELLS the opposite
option type to our own real historical confluence signal (sell PE when
our system said BUY/bullish, sell CE when it said SELL/bearish) --
collecting premium against our own directional call, a genuinely
different risk/reward shape.

New file `backtest_contrarian_sell.py`. Uses REAL historical signal_log
(NIFTY only, 301 training-eligible signals -- signal_log doesn't store
the exact strike/expiry used per signal, both re-derived at signal time:
ATM strike, nearest weekly expiry, same convention as every other
backtest here), real EOD-settle-anchored Black-Scholes pricer, real
costs, 30% leg-SL / 3:10pm square-off (same convention as
`naked_straddle_strangle_backtest.py`).

**Initial screen looked positive**: 275 trades, net +Rs75,967, Sharpe
2.399. Side-split immediately showed the same drift signature as every
other result this session: sold-CE trades (orig signal SELL, n=191)
carried the entire gain (+Rs89,599); sold-PE trades (orig signal BUY,
n=84) were negative (-Rs13,632) -- NIFTY fell -6.71% over this window.

**Matched-timing decomposition** (does the DIRECTION call add value
beyond just our system's signal TIMING?): built a naive "sell CE at a
fixed 9:30 every day" baseline -- NEGATIVE (-Rs42,816/329 trades,
Sharpe -0.686), unlike every prior naive-baseline check this session
(which always beat the "sophisticated" version). This made the
contrarian-sell result look genuinely different from prior false
positives at first: it appeared to beat its own naive baseline. Built
the correct matched control instead -- sell CE at ALL 275 real signal
TIMES regardless of original direction: also positive (+Rs84,802, n=275,
t=2.78), nearly as strong as the sell-side-only subset (+Rs89,599,
n=191, t=3.27) -- suggesting the apparent edge was coming from WHEN our
system fires signals at all, not from which direction it calls.

**Day-split holdout (the check that actually resolves this)**: the
matched-timing pool's ENTIRE positive result is concentrated in the
OLDER half (n=199, +Rs84,881, t=3.11) -- the NEWER half is exactly zero
(n=76, total=-Rs78, mean=-Rs1.0/trade, t=-0.006). The sell-side-only cut
holds up marginally better (older t=3.11, newer t=1.03, still positive
sign but nowhere near significant with n=45). **The pooled result does
not replicate out-of-sample at all** -- same pattern as the mtf_pivot_mod/
sr_level_mod modifier-pruning holdout collapse and the bull-put-spread
Bonferroni failure earlier this session: strong in discovery data,
gone in the genuinely held-out slice.

**Verdict: REJECTED.** Neither "sell against our own signal's direction"
nor "sell at our own signal's timing regardless of direction" shows a
validated, holdout-surviving edge. Small sample throughout (275 total,
76-146 per half) is a real limitation worth remembering if re-tested
once more signal_log data accrues, but the CURRENT evidence does not
support this idea being used live.

## Strategy-hunting search formally closed (2026-09-21, by user decision)

After ~25 independently-sourced, rigorously-tested candidates across
external GitHub repos, TradingView built-ins, Chartink screens, Zerodha
Streak/AngelOne examples, the full option-selling taxonomy
(standalone+paired), signal inversion (two distinct constructions), an
ML MFE-threshold model, and a volatility-surface relative-value idea --
ALL rejected, several only after catching a holdout/significance failure
that a first look missed -- user agreed to stop open-ended external
strategy-sourcing here rather than continue an unbounded "check
everything" search. This is a decision, not a gap: the evidence is
strong and convergent that simple technical/rule-based retail strategies
do not have exploitable edge on NIFTY options after real transaction
costs, regardless of source.

**Not closed, if revisited later on request**: a SPECIFIC named
book/author/product (not "all of them"), or one bounded, explicitly-
scoped search -- both remain available if the user names something
concrete. What is closed is the open-ended "keep searching more sources"
mode this session had been in.

**Suggested alternative directions going forward** (not started,
awaiting direction): execution quality / slippage reduction, position
sizing refinement, cost reduction, or finishing remaining internal-data
items already flagged in this file (sip_boost modifier re-check once
more days accrue past 2026-08-21, the still-untested DEAD/NOISE
modifiers, C5/C6/C9/D2/E2/E3/E5/E6 from the option-selling catalog if
ever wanted, the "Option-level P&L translation" item which was never
started this whole session).
