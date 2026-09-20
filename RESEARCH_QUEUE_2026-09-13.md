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
