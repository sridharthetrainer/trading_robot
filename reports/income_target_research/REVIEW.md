# ₹30,000 monthly target on ₹5,00,000 — research findings

As of 14 October 2026. This review assessed the whole logged strategy universe,
existing validation reports, and a fixed-size option replay. It does not
establish a profitable trading allocation.

## Result

**No strategy currently qualifies for the exploratory shortlist under the
new checks, and none is approved for live deployment.** The income target
requires 6% a month after trading costs, before personal income tax.

The scan covered 125 signal strategy names and
28441 training-eligible rows. Deduplicating the first valid setup per
strategy/symbol/side/day produced 19117 completed outcomes and
6 pending/invalid outcomes across
43 observed sessions. These are underlying signal proxies,
not executable option returns. Data coverage is incomplete across calendar months.

## Three focused research tracks

These form a **research watchlist, with zero live capital allocated**. Their
historical results do not justify funding. An immutable selection record and
future-review requirements are in `research_watchlist_2026-10-14.json`.

| Strategy | Completed proxy outcomes / active days | Mean net R | Latest 10 observed-system-days mean net R | Current issue |
| --- | --- | --- | --- | --- |
| Bullish marubozu | 75 / 31 | +0.0003 | +0.1740 | Overall after-cost result is approximately flat; uncertainty includes losses. |
| Falling wedge | 71 / 29 | +0.1580 | -0.2170 | Recent results reversed negative; no stable live edge. |
| Bollinger OTM reversal | Separate modeled option replay | Not comparable to proxy R | Not comparable | One-lot results miss the target; adverse pricing/cost stress reverses the total negative. |

OOPS was also checked: its overall trade-weighted result is slightly positive
(+0.0177R), but its equal-active-day mean is
-0.4313R. Clustered winning sessions therefore cannot justify
its selection. It remains outside the watchlist.

The prior July frozen cohort (heikin_ashi, td_sequential, weinstein_stage,
bearish_engulfing, cpr) remains unchanged in runtime configuration. Current
reports do not support funding that cohort. The trend and mean-reversion
validation reports have only one development window, no completed holdout,
and failed parameter stability; their attractive development P&L is not a
passed validation. Pair validation reports zero passes, and the option-flow
cohort report has no positive candidates (its source report is older).

## Fixed-size Bollinger test

Used the existing period 14 / standard deviation 1.5 research rule, buying a
10-strike OTM option, with **one 65-unit lot**, planned gross loss trigger
₹2,500 and profit trigger ₹3,750. These exit/sizing choices are exploratory,
not validated by this run. A stop trigger is not a guaranteed maximum loss.
The test spans 29 June through 13 October; June and October are partial months.
Entry premiums plus a round-trip cost reserve were checked against available
model cash starting at ₹5,00,000. No modeled trades were unfunded.

Premiums are Black–Scholes paths anchored to prior-day EOD settlement,
**not recorded executable intraday quotes**. The uniform lot size is a
simplification, not a historical lot-size schedule. This is not a new
untouched holdout and does not change existing prospective experiments.

Stress adds a +0.08 CE / +0.02 PE IV shock and an extra 1% charge on entry
plus exit premium turnover. The extra cost is a stress assumption, not a
broker tariff. Pricing changes also change which candidate days are priceable,
so this is not a paired-trade comparison.

| Month | Base model net | Stressed model net | ₹30,000 target met? |
| --- | ---: | ---: | --- |
| 2026-06 (partial) | ₹-24.25 | ₹-152.93 | No |
| 2026-07 | ₹+5,415.07 | ₹+3,760.57 | No |
| 2026-08 | ₹-894.99 | ₹-2,839.27 | No |
| 2026-09 | ₹-110.11 | ₹-1,764.77 | No |
| 2026-10 (partial) | ₹-405.83 | ₹-4,307.29 | No |

Base total: **₹+3,979.89**, 58 trades. Stressed total: **₹-5,303.69**, 64 trades.
Closed-trade model drawdown: ₹2,811.28 base / ₹10,711.38 stressed; intratrade drawdown is not measured.

## What an income-capable strategy would need

At ₹2,500 planned risk per trade (0.5% of this capital), ₹30,000 requires
**12 net R per month**. That requires an average +0.60 net R across 20 trades,
or +0.30 across 40 trades. At ₹1,250 planned risk, those requirements double.
This is arithmetic, not a suggested trade quota or a return forecast.

Before considering live capital, collect actual executable quotes and
reconciled fills, assess at least three complete future calendar months,
retain day/portfolio exposure controls, and pass the existing rigorous live
eligibility gates. Fewer signals may require longer to accumulate adequate
evidence. Do not count repeated correlated signals as independent trades.
The model-only Bollinger track needs a forward quote observer before any
confirmatory test can begin. New rules or changed parameters require a new
experiment; they cannot reuse an old favorable verdict.

The shortlist checker requires a positive meaningful net-R mean, sufficient
samples and days, a profit factor floor, positive recent performance, and
positive equal-day performance even without the best day. Its bootstrap
intervals are descriptive and are not multiple-testing-adjusted proof.
Nothing in this report authorizes live trading or changes lot sizes, strategy
weights, the existing frozen cohort, or broker settings.

[SEBI's investor guidance](https://investor.sebi.gov.in/investment-thingsbeforeinv.html)
explains that historical performance does not guarantee future returns.

## Reproduce and verify

```
.venv/bin/python income_target_research.py --as-of 2026-10-14 --capital 500000 --monthly-target 30000
.venv/bin/python -m pytest -q test_income_target_research.py
```

The source code reads live databases in read-only mode and never places orders.
11 focused tests passed, including outcome-independent deduplication, zero-return
and stop-label inclusion, future-outcome exclusion, concentrated winning days,
recent deterioration, cash affordability and drawdown from starting capital.
Full machine-readable rankings and model results are saved beside this report.
