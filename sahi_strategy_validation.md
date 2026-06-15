# SAHI Strategy Validation

## Data Used

- Requested window: 2026-04-01 to 2026-06-10
- Limitation: nse_cache.db daily rows currently end at 2026-06-08 for most symbols, so June 9-10 are unavailable locally.
- Empirical backtest scope: daily equity long/short approximation only.
- Local OHLCV volume is zero/missing, so the empirical run is a price-action proxy; live rules still require valid volume confirmation.
- Intraday VWAP/ORH/ORL, option delta, option OI, and spread execution rules are implemented but need matching intraday/options data for full validation.

## Core vs Enhanced Backtest

| Mode | Symbols | Trades | P&L | Win Rate | Profit Factor | Best | Worst |
|---|---:|---:|---:|---:|---:|---|---|
| Core | 181 | 624 | -357940.15 | 39.90% | 0.3608 | EMAMILTD | AFFLE |
| Enhanced | 181 | 624 | -357905.29 | 39.90% | 0.3608 | EMAMILTD | AFFLE |

## Enhancement Decisions

| ID | Decision | Suggestion | Justification |
|---|---|---|---|
| S1 | IMPROVISE | Multiple stop loss tightenings | Use stepwise tightening only after the 75% target milestone; never loosen the stop and cap locked profit at 90% to avoid noise exits. |
| S2 | IMPROVISE | Early exit on OI build-up at same strike | Same-strike OI growth is adverse for long options, but immediate exit should be reserved for >=10% buildup with price/RSI deterioration or used as a hard tighten trigger. |
| S3 | INCLUDE | Gap day reduce targets and size | Consistent with the core regime filter; gap days get 50% size and equity-short targets compressed to 0.5-1%. |
| S4 | INCLUDE | Absolute minimum stop for low-premium options | Prevents meaningless paise-level stops; option stops are max(percent stop, Rs 1). |
| S5 | IMPROVISE | Monthly sector rollover filter | Use as a position-size/bias filter, not a standalone entry trigger; missing or stale rollover data remains neutral. |
| S6 | IMPROVISE | Worst possible price backtest assumption | Use conservative limit-fill assumptions plus normal cost model. Stop fills should include slippage in live-grade tests, not exact fills only. |
| S7 | INCLUDE | Time-based OI re-check every 30 min | Matches options microstructure risk; adverse OI changes above 10% trigger exit/tighten checks. |
| S8 | IMPROVISE | Bad Call logging without action | Keep the label for journaling, but also store rule context so repeated bad calls can feed blacklisting or parameter review. |

## Final Recommendation

Do not promote the SAHI core strategy to live trading as-is. The available daily price-action proxy is negative, and the local cache is missing the volume, intraday, and option-OI data needed to validate the original discretionary edge.

Keep the coded enhancements for controlled testing. Permanently add S3, S4, and S7 as risk controls once matching data is available. Add S1, S2, S5, S6, and S8 only in the improvised forms shown above, with full intraday/options validation before enabling them as hard live exits.
