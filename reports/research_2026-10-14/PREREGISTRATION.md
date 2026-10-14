# Pre-registered tests — 2026-10-14

Written before either test was run. Rules and pass/fail criteria below are
fixed; no parameter is tuned on these results. Out-of-sample (OOS) = the last
30% of each test's trades/days, chronologically.

## Data
- NIFTY daily close: `options_nifty.db` chain-underlying median (2020-01-01 →
  2026-10-12); before that, 1-minute bars in
  `external_data/nse_index_upto_2024/NIFTY_50.csv` resampled to daily (from
  2015-01-09). Overlap check (1,009 days): median |diff| 0.05%, max 0.83%.
- BANKNIFTY daily close: 1-minute bars `NIFTY_BANK.csv` resampled, 2015-01-09 →
  2024-01-25 only (no source for 2024-01 → 2025-05).
- Options: real NIFTY EOD settlement prices, `options_nifty.db`, 2020-01 → 2026-10.

## Test 1 — EWMAC trend following (Carver), NIFTY and BANKNIFTY
- Code: `backtest_carver_ewmac.backtest_carver_ewmac()` unchanged — EWMAC
  (4,16)/(8,32)/(16,64), published forecast scalars, FDM 1.2, cap ±20, 20%
  annual vol target, daily rebalance, real FUT costs per rebalance leg.
- Variants reported: (a) as coded (fractional lots, ₹10L capital);
  (b) ₹5L capital with positions rounded to whole lots (lot size 75), the
  realistic retail version.
- Benchmark: buy-and-hold of the same index over the same days.
- PASS only if ALL hold, for NIFTY variant (a):
  1. full-period net Sharpe ≥ 0.5
  2. OOS net Sharpe ≥ 0.3 and OOS net P&L > 0
  3. variant (b) full-period net P&L > 0
  BANKNIFTY is supporting evidence only (shorter, ends 2024-01).

## Test 4 — Bull put spread only above the 200-day average
- Code: `credit_spread_backtest_real.backtest_credit_spread()` unchanged
  (side=put, 1.5% OTM short strike, 300-pt wing, min 3 DTE, ₹20/leg
  brokerage, 2% slippage per leg on premium, qty 75), then keep only weeks
  whose entry-day NIFTY close is above its 200-day simple average computed
  from closes up to and including that day. A filtered-out week is skipped,
  not re-entered later that week.
- PASS only if ALL hold:
  1. ≥ 60 trades in total
  2. OOS profit factor ≥ 1.2 and OOS expectancy ≥ +0.03R
  3. total R stays positive after removing the 3 BEST trades (guards the
     unexplained +4.28R outlier seen in the unfiltered run)
  4. full-period expectancy beats the unfiltered version (+0.0141R)

A FAIL is recorded as a FAIL. No re-run with different parameters counts as
this test.

## Test 6 — NIFTY 10-month moving-average timing (added 2026-10-14, before running)
Suggested by ChatGPT's review. Rules fixed here:
- Data: the same stitched NIFTY daily close series as Test 1 (2015-01 → 2026-10),
  month-end closes. It is a PRICE index, so add a 1.3%/yr dividend yield
  (≈ NIFTY's typical yield) to every month held in NIFTY, for both the
  strategy and the benchmarks.
- Signal: at each completed month-end, if close > its 10-month simple average,
  hold NIFTY next month; otherwise hold cash earning 6.5%/yr. No other periods.
- Cost: 0.15% per switch (ETF brokerage + STT + spread, conservative).
- Benchmarks: (a) NIFTY buy-and-hold; (b) static mix of NIFTY and cash with
  the same volatility as the strategy over the full sample.
- Holdout: the last 30% of months.
- PASS only if ALL hold (ChatGPT's proposed rule):
  1. holdout annualized return ≥ static mix (b) + 1.0 percentage point
  2. full-sample max drawdown ≤ static mix (b)'s, and ≥ 20% smaller than (a)'s
  3. 12-month block-bootstrap 90% lower bound of annualized excess return vs (b) > 0
  4. excess return vs (b) still ≥ 0 with costs doubled
- Known weakness, stated in advance: ~130 months with few switches is a small
  sample; a pass would justify forward paper tracking only.

## Test 7 — Cross-sectional 12-1 momentum, survivorship-free (2026-10-14, before running)
Recommended by 4 of 6 external reviews. Data: adjusted_returns.db built by
build_adjusted_returns.py (bhavcopy incl. delisted stocks, split/bonus/
demerger adjusted, gap returns discarded). Price returns only, for every leg.
- Rebalance: each month-end t, trades at the NEXT trading day's close.
- Universe at t: EQ stocks, excluding ETFs (symbol contains BEES, ETF, IETF,
  LIQUID, NIFTY, SENSEX, GOLD, SILVER, NV20, BHARAT22, CPSE), price ≥ ₹20,
  complete daily returns for the past 252 trading days, ranked top 200 by
  median daily traded value over the past 126 trading days (point-in-time).
- Signal: compounded return from t-252 to t-21 trading days (12-1 month).
- Portfolio: top 30 by signal, equal weight, long-only, held one month.
- Costs: 0.30% per side on turnover (STT 0.1% + stamp/exchange/GST + ~0.2%
  impact). A holding whose returns stop mid-month (delisting / left EQ
  series) is exited at its last close minus 20%.
- Benchmarks: (a) equal-weight whole universe (same costs); (b) NIFTY 50
  price index (Test 1 series). Nifty200 Momentum 30 history not available
  here — noted, not tested.
- Holdout: last 30% of monthly returns.
- PASS only if ALL hold:
  1. holdout annualized return ≥ (a) + 2.0 pp AND ≥ (b) + 2.0 pp
  2. full-period monthly Sharpe > (b)'s
  3. 12-month block-bootstrap 90% lower bound of annualized excess vs (a) > 0
  4. full-period excess vs (a) ≥ 0 with costs doubled (0.60%/side)
  5. max drawdown no worse than 1.5 × (b)'s
  6. stability: 6-1 and 9-1 month lookbacks (same rules otherwise) also beat
     (a) over the full period — checked, never used to pick a variant

### Test 7 result (recorded after running)
PASS on all six pre-registered checks (results_test7.json). Deviation found
after running: the ETF filter missed TATSILV (Tata Silver ETF, held 7
months). Labelled robustness re-run excluding it: full 15.02% vs EW 9.92%,
holdout 10.70% vs 7.78% — the pass does not depend on it. The pre-registered
result above stays the result of record.
Not modelled: dividends (favours EW/NIFTY slightly), short-term capital gains
tax on monthly turnover, Nifty200 Momentum 30 comparison.

## Test 8 — NIFTY call time (calendar) spreads, Slivka et al. 2020 (before running)
Source: Slivka, Liang & Xue (2020), Indian Journal of Finance 14(8-9), suggested by
Grok. The paper's out-of-sample test is one year (2019), where it lost to
hold-to-expiry, and its Rule 4 picks the profit threshold from the WHOLE year's
volatility trend (look-ahead). This test keeps Rules 1-3, fixes X instead of Rule 4.
- Data: options_nifty.db real EOD close prices, CE only, 2020-01 -> 2026-10; spot as
  in condor_backtest_real._load(). Monthly expiry = last listed expiry of each month.
- Entry (paper's rule): every trading day d, front = nearest monthly expiry after d;
  require >= 5 trading days to front expiry; back = the following monthly expiry;
  K = strike nearest spot with both calls priced; sell front call, buy back call
  (debit D0 = C2 - C1 > 0). Enter only if the standstill return (model profit at
  front expiry with NIFTY unchanged, annualised on D0) > 6.5% (MIBOR proxy).
  Model: Black-Scholes, r = 6.5%, back-month IV implied from its own close.
- Exits, first one hit, checked at later day-ends:
  Rule 1: annualised return-if-unwound > 6.5% AND profit >= X% of the model max
          profit at front expiry, with X FIXED at 50% (the paper's "rising" value;
          primary). Also reported: X = 25% and 75%, and no Rule 1.
  Rule 2: NIFTY outside the model breakeven range at front expiry -> unwind.
  Rule 3: mandatory unwind 1 trading day before front expiry.
  A day with a missing leg price is skipped for checks; a trade that cannot be
  priced at its forced exit is dropped and counted.
- Costs per leg execution: slippage % of that leg's premium (2% primary, 5% stress)
  + nse_cost_model fees (brokerage, STT at the trade date's historical rate on
  sells, exchange/SEBI/GST/stamp), qty 65.
- Baseline: same entries, Rule 3 only (hold to day before expiry).
- Holdout: last 30% of trades by entry date.
- PASS only if ALL hold (X = 50%, 2% slippage):
  1. >= 100 trades
  2. full-period profit factor >= 1.2 and holdout profit factor >= 1.2
  3. holdout total net P&L > 0
  4. total net P&L still > 0 at 5% slippage
  5. beats the Rule-3-only baseline on full-period total net P&L
  6. 12-month block-bootstrap (monthly P&L by exit month) 90% lower bound of mean > 0

### Test 8 result (recorded after running)
FAIL. 1,214 spreads 2020-01 -> 2026-10 (none dropped). X=50%, 2% slippage:
net -₹26.7L total, PF 0.28, win rate 26%; holdout PF 0.20; 5% slippage PF 0.05.
Diagnosis: before ANY costs the rules give PF 0.97 (hold-to-day-before-expiry
PF 0.81), so there is no gross edge in 2020-2026; costs (~₹2,150 per spread on
a median ₹13,900 debit) make it strongly negative. X=25%/75% and no-Rule-1
variants also fail. Paper's 2015-2019 result does not carry forward.

## Test 9 — Grok #2: put credit spread, NIFTY > 200-DMA AND India VIX in top tercile (before running)
- Data: options_nifty.db real EOD PE closes 2020-01 -> 2026-10; NIFTY = Test 1
  stitched series; India VIX daily closes fetched from NSE (fetch_india_vix.py).
- Entry: first trading day of each ISO week, nearest expiry with >= 3 trading days
  left; short put = largest strike <= spot x (1 - 1.5%), long put 300 points lower;
  credit > 0. Filter (same day's close): NIFTY > its 200-day SMA AND VIX >= the
  67th percentile of its trailing 252 trading days.
- Exit: PRIMARY hold to expiry (settle at intrinsic from NIFTY's expiry close).
  Also reported: close when the spread can be bought back for <= 50% of the
  credit at a day-end (else hold to expiry).
- Costs: 2% slippage on each leg's premium at entry (and on 50% exits) + nse_cost_
  model fees on each leg at entry/exit (historical STT on sells), qty 65, no
  exercise-STT at expiry (understates costs slightly).
- PASS (Grok's stated bar) only if ALL hold, primary version:
  1. deflated Sharpe (validation_harness.deflated_sharpe_ratio, per-trade Sharpe
     annualised by sqrt(52), n_trials = 8 option-selling variants) > 0.5
  2. full-period profit factor > 1.2
  3. max drawdown of cumulative ₹ P&L (1 lot) < ₹1,25,000 (25% of ₹5L)
  4. net P&L positive in each regime: 2020-21, 2022-23, 2024-26
  5. still net positive with costs doubled (4% slippage, 2x fees)
  6. holdout (last 30% of trades) net positive

### Test 9 result (recorded after running)
FAIL on check 1 only: deflated Sharpe 0.0013 (needs > 0.5). Passed PF 1.277,
max DD ₹22,090, positive in all three regimes, positive with costs doubled
(PF 1.15), holdout +₹10,099. Only 53 trades in ~7 years; total ₹18,004 on one
lot (~₹220/month). 50%-take-profit variant PF 0.91. Unfiltered with full
costs PF 1.01, holdout -₹45,779. Note for later review: validation_harness.
deflated_sharpe_ratio compares an annualised SR with a unit-variance SR*;
units should be checked, but 53 trades / ₹220 a month is not usable either way.

## Test 10 — DeepSeek: VRP-conditioned NIFTY weekly iron condor (before running)
DeepSeek's frozen rule, with only the changes the data forces:
 (a) "Thursday close / Friday open" -> the close of the first trading day after
     each weekly expiry (expiry moved to Tuesday in Sep-2025; only EOD prices exist);
 (b) limit-at-mid fills cannot be modelled -> EOD close + slippage, as DeepSeek's
     own cost line specifies.
- Signal at that close: RV5 = std of the last 5 daily NIFTY log returns x sqrt(252)
  (vol points); VRP = India VIX close - RV5. Enter only if VRP > 3.
- Iron condor on the nearest expiry: short call = smallest strike >= spot x 1.015,
  long call = smallest strike >= short + 300; short put = largest strike <=
  spot x 0.985, long put = largest strike <= short - 300; credit > 0.
- Hold to expiry, settle at intrinsic from NIFTY's expiry close. No adjustments.
- Costs: 2% slippage on every leg's premium + nse_cost_model fees per leg
  (historical STT on sold legs), qty 65.
- PASS (DeepSeek's stated bar) only if ALL hold:
  1. full-sample profit factor > 1.15
  2. >= 200 trades
  3. positive expectancy in the locked holdout (last 20% of trades by date)
  4. profit factor still > 1.0 at 4% slippage per leg
- Also reported, not part of the verdict: same condor without the VRP filter.

### Test 10 result (recorded after running)
FAIL (checks 1 and 4). 205 trades: PF 0.933, net -₹31,411 (1 lot), win rate 76%,
max DD ₹1,09,393; holdout (from 2025-04-25) +₹624/trade; 4% slippage PF 0.882.
The VRP filter made it worse than the same condor unfiltered (357 trades, PF 1.106,
+₹75,765) -- which is itself below DeepSeek's 1.15 bar and not the registered test.
First run was killed (out of memory loading all option rows); re-run loads only
entry-day rows, logic unchanged.

## Test 11 — Perplexity: hedged put spread (sell 3% OTM, buy 6% OTM), post-Nov-2024 only (before running)
Perplexity's frozen rule, with only the changes the data forces:
 (a) "10:15 AM entry" -> that day's close (only EOD prices exist);
 (b) sizing "risk <= ₹5,000 per trade" is impossible with the 65-unit lot (one lot
     of a ~3%-wide spread risks ~₹40-50k) -> tested per unit in R; the sizing
     infeasibility is reported as a finding, not ignored.
- Period: expiries from 2024-11-20 (SEBI changes effective) to 2026-10-13.
- Entry: for each expiry date, the first trading day of that expiry's ISO week
  (skip if that is the expiry day itself). Short put = largest strike <= spot x 0.97,
  long put = largest strike <= spot x 0.94, same expiry; credit > 0.
- Hold to expiry; settle at intrinsic from NIFTY's expiry close. No management.
- Costs: 4% slippage on each leg's premium (7% stress) + nse_cost_model fees per leg
  (STT on the sold leg), qty 65. Skip a trade if its entry costs exceed 25% of the
  net credit (Perplexity's exclusion rule).
- Holdout: last 30% of trades.
- PASS (Perplexity's bar) only if ALL hold:
  1. >= 100 trades (Perplexity's minimum)
  2. holdout expectancy > +0.05R
  3. holdout profit factor > 1.15
  4. total net positive at 7% slippage
  5. max drawdown (1 lot, cumulative ₹) < ₹1,00,000 (20% of ₹5L)
  6. total net R still positive after removing the 3 best trades
  7. beta control: intercept of trade R regressed on NIFTY's entry->expiry return
     must be > 0 (else it is long beta / tail selling, not an edge)

### Test 11 result (recorded after running)
FAIL (checks 1 and 2). Only 32 trades: Perplexity's own 25%-cost exclusion removed
61 of 93 expiry weeks (median credit 8.4 points). 32/32 winners, +₹21,952 on one
lot, but expectancy +0.016R (needs > 0.05R) -- ~₹690 earned per ~₹46,000 risked.
Since Sep-2025 the entry (first day of expiry week) is usually Monday for a Tuesday
expiry, a 1-day hold; one >3% drop would cost ~65 trades of profit. Sizing: median
one-lot max loss ₹46,214 vs Perplexity's ₹5,000 cap -- not executable at ₹5L.

## Test 12 — ChatGPT #1: VRP-conditioned monthly NIFTY iron fly (before running)
ChatGPT's frozen spec; interpretations and forced changes stated here:
- Signal (known at the PREVIOUS close): VRP = (VIX/100)^2 - 252*v, v = EWMA of
  squared daily NIFTY log returns, v_t = 0.94 v_{t-1} + 0.06 r_t^2 (not tuned).
  Eligible if VRP > 0 AND VRP >= 75th percentile of the preceding 252 daily VRP
  values (excluding the current one).
- Cycle: after each monthly expiry, scan sessions in order; enter at the FIRST
  session (EOD close, modelled entry) where the signal is eligible, the target
  expiry = nearest monthly expiry with 21-35 calendar days left exists, and all
  four strikes are priced. At most one position per monthly cycle; if no session
  qualifies before the target drops below 21 days, no trade that cycle.
- Iron fly, 65 qty: sell ATM call + ATM put (strike nearest spot), buy call
  K+150 and put K-150 (must be listed; else that session is ineligible).
- Exit: close all legs at the close of the 5th trading session after entry.
- Costs: baseline 2% slippage of each leg's premium per execution + nse_cost_model
  fees (STT on sold legs, historical rate); stress = 4% slippage + 2x fees.
- Comparison: the same fly, unconditional (first session of each cycle with a
  21-35-day expiry and all strikes priced).
- Periods: development 2020-2022; out-of-sample (OOS) = trades from 2023-01;
  post-reform (from 2024-11-20) reported separately.
- Gates (ChatGPT's checklist):
  G1 OOS mean net P&L > 0; G2 OOS mean net > 0 under stress costs;
  G3 OOS mean net beats the unconditional fly; G4 block-bootstrap (block 3) 95%
  lower bound of OOS mean net > 0; G5 max contractual loss (₹9,750 - credit) <=
  ₹10,000 (broker margin cannot be reconstructed -- reported, not tested);
  G6 post-reform results reported; G7 >= 100 OOS trades; G8 >= 50 forward
  executable paper observations (not evaluable from history).
- Verdict: any of G1-G4 fails -> FAIL. G1-G4 pass but G7/G8 unmet ->
  INCONCLUSIVE (forward paper candidate only). PASS is impossible from history.

### Test 12 result (recorded after running)
FAIL (G1-G4 all fail). Conditioned: 47 trades (28 OOS, 14 post-reform), mean -₹3,498,
win rate 0%; stress mean -₹7,009; unconditional mean -₹3,309; OOS bootstrap 95% lower
bound -₹4,089. G5 passed (max contractual loss ₹1,563; median credit 139 of 150 pts).
Diagnostics (not the verdict): before ANY costs mean +₹12/trade (unconditional +₹64),
so there is no gross edge and the VRP filter adds nothing; at 0.5% slippage it still
loses ~₹1,107/trade. The 0% win rate is cost-driven: 4 near-ATM legs x 2 executions.

### Test 7 after-tax robustness (post-hoc, prompted by Gemini's review; not a new test)
On ₹5L, Sep-2017 -> Oct-2026, assumed dividend yields 0.8% (momentum) / 1.3% (NIFTY):
momentum pre-tax 15.62%/yr (₹18.68L); momentum with 20% STCG on each calendar year's
net gain (losses carried forward) 12.67%/yr (₹14.77L); NIFTY fund with 12.5% LTCG on
gains above ₹1.25L at the end 9.96%/yr (₹11.84L). After-tax edge ≈ 2.7 pp/yr (vs ~5.6
pp pre-tax). Simplified: yearly tax settlement, no cess, dividend tax ignored for both.

### ChatGPT #2 — replicate John (2026) delta-hedged VRP preprint: NOT TESTABLE
ResearchGate returned 403 and a web search found no copy or citation; its rules
(strikes, hedging, threshold) cannot be obtained, so it is recorded as unverified,
like "Pillai (2026)" from Grok's review.

### Gemini — no options test proposed
Recommendation adopted: harden momentum (after-tax note above; forward tracker running).

## Test 13 — Kimi: long iron butterfly around scheduled events (before running)
Kimi's rule. Note recorded in advance: Kimi motivates it as harvesting the post-event
IV crush, but a LONG fly bought before the event loses from IV crush; the specified
(long) trade is the test, the mirror SHORT fly is reported as a labelled diagnostic.
- Events (dates from official/news sources; deviations from Kimi's list stated):
  FOMC scheduled decisions 2020-2026 (federalreserve.gov; 2020 emergency cuts excluded);
  US Employment Situation 2020-2026 (bls.gov yearly schedules); Union Budget (Feb 1
  each year, plus 2024-07-23 full budget); RBI MPC decisions ONLY where confirmed by
  RBI releases or news (19 dates); India CPI EXCLUDED (no official release dates found).
- Timing: events announced after NSE close (FOMC, NFP) -> entry at that day's close;
  daytime events (RBI 10:00, Budget 11:00) -> entry at the previous trading day's close.
  Exit at the close of the first trading day after the event day (Kimi's T+1); if the
  exit day is the expiry day, settle at intrinsic.
- Fly: buy ATM call + ATM put (strike nearest spot), sell call = smallest strike >=
  spot x 1.01 and put = largest strike <= spot x 0.99, on the nearest expiry on/after
  the exit day. Skip if the net debit > 76.9 points (Kimi: ₹5,000 / 65 cap -> 1 lot).
- Costs: 2% slippage on each leg's premium per execution + nse_cost_model fees.
- PASS (Kimi's bar) only if ALL hold: >= 30 trades; expectancy after costs > 0;
  profit factor > 1.15; deflated Sharpe (harness function, per-trade Sharpe x
  sqrt(trades per year), n_trials = 11 option tests so far) >= 0.95; stability:
  wings at 0.75% and 1.25% give expectancy of the same sign.

### Test 13 result (recorded after running)
FAIL (trades, deflated Sharpe, stability). 152 events planned, but Kimi's own debit
cap (<= 76.9 pts, from its ₹5,000/65-lot sizing) left only 3 trades at 1% wings
(+₹5,170, all FOMC). 0.75% wings: 10 trades, expectancy -₹45.5 (NFP -₹1,972);
1.25%: 2 trades. Deflated Sharpe 0.00. Diagnostic short fly on the same 3 events
lost (-₹6,630). As sized by Kimi the strategy is mostly not tradeable on ₹5L.

## Test 14 — 52-week high (George & Hwang 2004), survivorship-free (before running)
Suggested by the user ("52 week high low"); published: George & Hwang (2004),
"The 52-Week High and Momentum Investing", Journal of Finance. An earlier test
(cross_sectional_factor_test.py, Sept 2026) on survivorship-biased 2022-2026 data
with 40 rebalance points found it negative; this re-tests on the clean 2016-2026 data.
- Identical to Test 7 (universe, rebalance timing, costs, delisting haircut,
  benchmarks, holdout, broader ETF filter as in the tracker) EXCEPT the signal:
  nearness to 52-week high = adjusted price at t / max adjusted price over the
  past 252 trading days (adjusted price = compounded adjusted daily returns).
- Portfolio: top 30 by nearness, equal weight, monthly.
- Diagnostic (not part of the verdict): the 30 stocks NEAREST their 52-week LOW,
  and the correlation of monthly returns with Test 7 momentum.
- PASS = Test 7's six checks, with stability = 126- and 189-day high windows
  (instead of 6-1 / 9-1 momentum).

### Test 14 result (recorded after running)
FAIL (5 of 6 checks). 109 months: 10.07%/yr vs equal-weight 9.92% vs NIFTY 9.33%;
holdout 5.08% vs EW 7.78% (needed EW + 2 pp); bootstrap lower bound -4.46%/yr;
costs doubled -3.88%/yr vs EW; 126-/189-day windows 5.87% / 8.20% (below EW).
Turnover 56%/month (2x momentum). Correlation with Test 7 momentum 0.887 -- largely
a noisier, costlier version of it. Diagnostic near-52-week-LOW portfolio 4.96%/yr.

## Test 15 — Fibonacci 61.8% retracement bounce, with placebo levels (before running)
Suggested by the user ("Fibonacci"). No verified published evidence that Fibonacci
levels predict reversals; the system has calculate_fibonacci_levels() but no strategy
uses it. The test asks whether the 61.8% level beats arbitrary levels.
- Data/universe: adjusted_returns.db, point-in-time top-200 by 126-day median traded
  value, recomputed at each month-end (Test 7 rules, broader ETF filter), daily signals.
- Swing at day t (adjusted price path over the past 60 trading days, no missing
  returns): L = 60-day low, H = 60-day high, L dated BEFORE H, and H/L - 1 >= 15%.
- Signal: level = H - f x (H - L) with f = 0.618; trigger when today's close is within
  +/-1% of the level AND today's close > yesterday's close (bounce). Entry at the NEXT
  day's close; exit 10 trading days later; one open signal per stock at a time.
- Placebos: identical rule with f = 0.55 and f = 0.70.
- Measure: trade return minus the equal-weight universe return over the same days,
  minus 0.6% round-trip cost. Holdout: last 30% of signals by date.
- PASS only if ALL hold (f = 0.618): >= 200 signals; mean net excess > 0 with t > 2
  (t from date-clustered means); holdout mean net excess > 0; mean net excess beats
  BOTH placebos (Fibonacci-specific).

### Test 15 result (recorded after running)
FAIL. 61.8% bounce: 1,965 signals, mean -1.13% vs universe after 0.6% costs (about
-0.5% before costs), date-clustered t = -5.9, holdout -1.48%. Placebos: 55% level
-0.82% (t -6.3), 70% level -1.04% (t -6.3). The Fibonacci level is no better than
arbitrary levels -- worse than 55%. Retracement-bounce buying underperforms here.

## Test 16 — Low-volatility factor, survivorship-free (before running)
Prompted by the user's "refer all mathematical models"; published: Blitz & van Vliet
(2007), Baker, Bradley & Wurgler (2011); NSE runs NIFTY Low Volatility indices.
Earlier test (cross_sectional_factor_test.py) used survivorship-biased 2022-2026 data.
- Identical to Test 7 (universe, timing, costs, haircut, benchmarks, holdout, broader
  ETF filter) EXCEPT the signal: lowest standard deviation of daily adjusted returns
  over the past 252 trading days; top 30 = the 30 LOWEST-volatility stocks.
- PASS = Test 7's six checks unchanged, stability = 126- and 63-day volatility
  windows. Stated in advance: low-vol mainly targets lower risk, so it can fail the
  return-based checks while improving drawdown; Sharpe and drawdown are reported.

### Test 16 result (recorded after running)
FAIL (return-based checks). 9.09%/yr vs EW 9.92% vs NIFTY 9.33%; Sharpe 0.681 (best of
the three); max DD -20.9% vs EW -49.3% / NIFTY -32.2%; holdout 0.99% vs EW 7.78%;
bootstrap lower bound -9.73%; costs doubled -1.26% vs EW; 126/63-day windows below EW.
As stated in advance: lower risk, no extra return.

## Test 17 — Volatility-scaled momentum (Barroso & Santa-Clara 2015) (before running)
Published: Barroso & Santa-Clara (2015), "Momentum has its moments", JFE. Upgrade
candidate for the Test 7 strategy (same picks, broader ETF filter as the tracker).
- Daily strategy return = mean daily adjusted return of the month's 30 holdings.
- At each rebalance, sigma = std of the strategy's daily returns over the previous
  126 trading days x sqrt(252); weight w = min(1, 0.12 / sigma) (paper's 12% target;
  NO leverage -- a cash account cannot lever cheaply, stated deviation); first months
  without 126 days of history use w = 1. Rest in cash at 6.5%/yr.
- Month return = w x momentum return + (1 - w) x cash - 0.30% x |w - w_prev|.
- PASS only if ALL hold vs UNSCALED Test 7 momentum (same filter):
  1. full-period Sharpe higher; 2. max drawdown smaller; 3. holdout Sharpe >= unscaled
  holdout Sharpe; 4. full-period return >= equal-weight universe; 5. Sharpe still higher
  with costs doubled.

### Test 17 result (recorded after running)
PASS on all five checks vs unscaled momentum: Sharpe 0.767 vs 0.708; max DD -27.9% vs
-38.7%; holdout Sharpe 0.753 vs 0.558; return 10.36% vs equal-weight 9.92%; costs
doubled Sharpe 0.680 vs 0.631. Trade-off: absolute return 10.36% vs 15.02% unscaled
(partly in cash). A risk overlay -- qualifies for forward paper tracking alongside
the unscaled version, not for capital.

## Test 18 — Pairs trading, distance method (Gatev, Goetzmann & Rouwenhorst 2006) (before running)
Published: GGR (2006), Review of Financial Studies; profits documented to decline
over time. Feasibility note recorded in advance: multi-day shorts need stock futures
(F&O names, ₹5-10L lots) or SLB -- largely impractical on ₹5L; tested for edge only.
- Data: adjusted_returns.db (survivorship-free). Non-overlapping cycles: 252-day
  formation then 126-day trading, starting at the first day with 252 days of data,
  stepping 126 days.
- Universe per cycle: point-in-time top-200 liquid at formation end (Test 7 rules,
  broader ETF filter), complete returns over formation.
- Pairs: normalised prices (cumprod of returns) over formation; the 20 pairs with
  the smallest sum of squared differences; sigma = std of their formation spread.
- Trading: renormalise at trading start; open when |spread| > 2 sigma (long the
  lower, short the higher, ₹1 each side); close when the spread crosses zero or at
  period end; may reopen. A leg whose data stops closes the pair at its last price.
- Return per cycle = sum of trade returns / 20 (₹1 committed per pair), minus 1.2%
  per round trip (4 legs x 0.30%); short-side financing/SLB fees not modelled.
- PASS only if ALL hold: >= 10 cycles; mean cycle return > 0 with t > 2; holdout
  (last 30% of cycles) mean > 0; mean still > 0 with costs doubled.

### Test 18 result (recorded after running)
FAIL. 16 cycles (2018-2026), 491 pair trades: mean -2.24% per 6-month cycle after
1.2% round-trip costs (t -2.21); holdout -1.64%; costs doubled -4.08%. Costs are
~1.8% per cycle, so the gross result is about -0.4% -- no edge before costs either,
consistent with GGR-type profits having faded. Also impractical on ₹5L (shorting).
