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
