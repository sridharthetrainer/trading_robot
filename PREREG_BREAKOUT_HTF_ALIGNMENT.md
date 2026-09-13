# Pre-registration: BREAKOUT strategy HTF-alignment gate

Written 2026-09-13, before any build work, so validation criteria are fixed
before results exist to put pressure on them. Read this file before writing
any code for this test — do not reconstruct the criteria from memory.

## The claim being tested (keep it this narrow)

**"Within the `breakout` strategy, conditional on the already-computed
`htf_bias` field, signal-side agreement with `htf_bias` shows a consistent
loss reduction across regimes, in the available sample."**

This is NOT "MTF alignment works" and NOT "this applies to other strategies."
Do not let a passing result inflate the claim back into a general principle
the data doesn't support. `trend` was tested as a control and showed NO
alignment benefit (-0.208 aligned vs -0.189 unaligned, aggregate) — the
effect is specific to `breakout`, not a property of MTF alignment in general.

## Definition (operationalization — do not substitute a different one)

- **Higher timeframe / bias source**: the existing `htf_bias` field, computed
  by `mtf.get_htf_bias(df_htf)` (reads `df_htf.iloc[-1]`, compares close vs
  ema20/ema50 on the HTF frame) and logged per-signal in `signal_log.htf_bias`.
  Values: `BULLISH`, `BEARISH`, `SIDEWAYS`.
- **"Aligned"**: `(side='BUY' AND htf_bias='BULLISH') OR (side='SELL' AND
  htf_bias='BEARISH')`.
- **Scope**: `strategy='breakout'` only.

## Exploratory evidence (2026-09-13, from signal_log.db, training_eligible=1)

- Retention if this became a hard gate: 88.3% (1448/1640 breakout signals
  aligned) — healthy, won't starve the sample.
- Aggregate: aligned avg_R=-0.0807 vs unaligned avg_R=-0.3639.
- Confound check (does it just proxy for regime?) — **passed**, effect holds
  within every regime checked:
  - BREAKOUT regime: aligned n=175 avg_R=-0.0077 vs unaligned n=46 avg_R=-0.1149
  - TREND regime: aligned n=480 avg_R=-0.0636 vs unaligned n=45 avg_R=-0.7938
    (**caution**: n=45 unaligned is small; this magnitude may be a few
    outlier losses, don't trust it beyond direction)
  - EARLY_TREND regime: aligned n=793 avg_R=-0.1072 vs unaligned n=101
    avg_R=-0.2858 (most reliable cell — largest unaligned n)
- Control: `trend` strategy showed no benefit at all (-0.2082 vs -0.1893) —
  confirms this isn't a universal MTF-alignment effect.

## Honest prior

Every other test run today failed: 10/10 formally-validated strategies,
27/27 confluence modifiers, the exit-time-barrier-extension hypothesis.
This candidate is better-supported than any of those (it passed a confound
check none of the others were put through, and is narrowly scoped rather
than asserted generally) — but the honest prior is still that it probably
will NOT clear full walk-forward/deflated-Sharpe validation. Running it is
worth doing regardless: a clean negative result is informative about the
base signal quality, not just about this one gate.

## Build sequencing (must happen in this order)

1. **First**: build a shared causal-slicing helper (e.g.
   `causal_htf_slice(df_htf, as_of_ts)`) in `validation_harness.py` (or a
   module it imports) that truncates any HTF frame to bars closed strictly
   before `as_of_ts`. This is reusable infrastructure for any future
   multi-timeframe backtest work, not specific to this one test.
2. **Prove it's correct with a poisoned-input assertion**, not a code-review
   checklist item: run the backtest twice on the same data — once normally,
   once with every HTF bar at/after each simulated signal's timestamp set to
   NaN (or shuffled). If any signal changes between the two runs, causality
   is violated somewhere in the slicing — fix it before proceeding. This
   assertion should be a permanent, automated part of the harness, not a
   one-time manual check.
3. **Only after step 2 passes**, build the `breakout` HTF-alignment gate
   using the exact definition above, and wire it into `validation_harness.py`
   as a new backtest variant (or a parameter on the existing `backtest_breakout`
   path).
4. Run full walk-forward + deflated Sharpe validation (same harness as the
   other 10 strategies).

## Pass criteria (all required, pre-registered before seeing results)

- Must pass the existing walk-forward + deflated-Sharpe + locked-holdout gate
  (same bar as the other 10 strategies — no special-casing).
- Report **median or trimmed-mean net-R alongside the mean** — the effect
  must not depend on a few outlier trades in either direction (this cuts
  both ways: the TREND-regime cell above is exactly the kind of result that
  needs this check).
- Must show **consistent effect across two independent time-halves** of
  whatever data window the validation uses (same method already applied to
  the BREAKOUT-regime preference fix and the `pivot_scalping` anomaly).
- The poisoned-input assertion (step 2 above) must be green before this
  gate's own results are trusted at all.

## Context / where this came from

Emerged from a three-way review (this session + DeepSeek, cross-checked
against the actual codebase at each step) of a request to hand another AI
a detailed strategy/parameter prompt for refinement. DeepSeek's original
broader claims (missing gap/OI/structural-stop features) were checked
against the code and found to already exist elsewhere in the system, just
not in the 10 formally-validated strategies specifically — narrowed from
there to this single, precise, testable claim. Full exchange is in the
2026-09-12/13 conversation history if more context is ever needed.
