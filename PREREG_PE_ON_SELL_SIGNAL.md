# Pre-registration: PE-buying-when-system-says-SELL forward test

Written 2026-09-21, before any new data exists to test against, so
validation criteria are fixed before results can put pressure on them.
Read this file before running the eventual test -- do not reconstruct
the criteria from memory or adjust them after seeing new results.

## The claim being tested (keep it this narrow)

**When our confluence engine's real historical signal for NIFTY calls
SELL (bearish), buying a PE (matching the signal's own direction, real
option premium via option_intraday_pricer.py, same-day intraday hold,
30% unrealized-loss stop / 3:10pm square-off) shows a positive,
statistically-significant expectancy that a genuinely NEW, prospective
sample of days must independently confirm.**

This is NOT "our system has edge" generally -- it is this one narrow,
specific construction (NIFTY only, SELL-side signals only, same-day
option-buying execution), on data that has not yet been generated as of
this writing.

## Where this came from (context, not evidence for the new test)

Built 2026-09-21 as part of "Option-level P&L translation"
(RESEARCH_QUEUE_2026-09-13.md item 5). On the 301 real historical NIFTY
signals available at the time, the SELL-side (PE-buying) subset showed:
- Full-sample: n=197, mean +Rs955.9/trade (calendar-time pricer) or
  +Rs835.0/trade (trading-time-weighted pricer, built later the same
  day after confirming a real intraday/overnight variance asymmetry).
- A permutation test (does this subset beat random subsets of the same
  signal-firing population) gave z=2.203 (calendar-time) / z=2.946
  (trading-time-weighted) pooled -- but per-half z-scores were
  OLDER=1.592/2.538, NEWER=1.472/1.313 -- inconsistent, with only the
  trading-time-weighted OLDER half independently clearing the
  conventional z>1.96 bar. **Neither pricer convention produces BOTH
  halves independently significant** -- this is why the finding was
  never promoted, only flagged for a genuine forward test.

## Why a fresh pre-registration, not more digging into the same data

Every look taken at this same 301-signal dataset (side split, matched-
timing control, full-pool permutation, per-half permutation, pricer-
convention correction) is a separate test against the SAME underlying
data. Continuing to slice this dataset differently and hoping for a
cleaner result is exactly the kind of look-elsewhere effect this
project's entire methodology exists to prevent. The only honest next
step is NEW data this analysis has never touched.

## Locked protocol for the forward test

1. **Data cutoff**: only NIFTY signal_log entries with
   `signal_date > 2026-09-21` count as the forward/test sample. Every
   signal used in the discovery analysis above (through 2026-09-21) is
   permanently excluded from this test, never re-included even if the
   forward sample turns out small.
2. **Minimum sample before running any test**: at least 60 NEW
   SELL-side NIFTY signals with a priceable outcome (matching this
   project's stated statistical-power conventions elsewhere -- e.g.
   MIN_DAYS=10 distinct trading days used by meta_labeler.py, scaled up
   here given this is a live/replicated hypothesis, not first discovery).
   Do not run the test early "just to check" -- an early peek at a
   partial forward sample is the same mistake as testing the discovery
   data again.
3. **Exact construction to replicate**: NIFTY-only, side='SELL' signals
   from signal_log, buy ATM PE at signal time (nearest_strike, nearest
   weekly expiry, matching every convention already established), real
   option_intraday_pricer.py pricing, 30% unrealized-loss stop / 3:10pm
   square-off, real nse_cost_model.py costs (OPT_BUY/BUY). Use BOTH the
   calendar-time pricer (option_intraday_pricer.DayPricer) and the
   trading-time-weighted pricer (trading_time_pricer.TradingTimeDayPricer)
   and report both -- do not pick whichever looks better after the fact.
4. **Day-split within the forward sample itself**: split the forward
   sample chronologically at its own midpoint into two independent
   halves. Compute mean net P&L and a one-sided LCB95
   (mean - 1.645*SE) for each half separately, for each pricer
   convention.
5. **Pass criteria (ALL required, exactly as stated, no loosening)**:
   - BOTH forward-sample halves must independently show LCB95 > 0
     (positive one-sided 95% lower confidence bound), for at least
     ONE of the two pricer conventions.
   - The forward sample's overall sign must match the discovery
     sample's sign (positive).
   - Applying Bonferroni across the 2 pricer conventions tested
     (alpha_corrected = 0.025 per convention) to each half's own
     p-value.
6. **If it passes**: this becomes a CANDIDATE, not a live strategy --
   it then requires the SAME full walk-forward + deflated-Sharpe +
   locked-holdout validation gate as every other strategy in this
   system before any live-gating consideration (validation_harness.py's
   standard, no special-casing).
7. **If it fails**: record as REJECTED, do not re-test again on a
   THIRD data slice looking for a different cut that might pass --
   two independent looks (this discovery + one forward test) is the
   agreed budget for this specific hypothesis.

## Standing reminder

The honest prior, given six independent AI analyses and ~30 other
rejected candidates this project has tested, is that this ALSO fails
the forward test. Running it is still worth doing -- a clean negative
result on fresh data closes this out properly; a positive result would
be the first genuinely validated finding this entire research program
has produced, and would deserve to be treated with EXTRA scrutiny
(not less) given how many chances it has already had to fail.
