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
