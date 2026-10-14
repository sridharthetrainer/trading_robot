# Strategy coverage and tuning review — 14 October 2026

Capital: ₹500,000. Research target: ₹30,000 net per month before personal income tax (6% monthly). This remains an unproven target.

The report covers 125 logged signal strategies, 112 live-manifest entries, 42 backtest source modules, 65 conditionally declared signal functions, and all 42 option catalog entries. These counts overlap and must not be added together.

All eleven validation-compatible families are now in the default autonomous rotation: trend, mean reversion, breakout, moving-average cross, scalping, EMA 5-minute, CPR, ORB, VWAP reversion, multi-timeframe Supertrend, and Fibonacci. An explicit PARAM_TRAIN_STRATEGIES override remains respected. The existing default budget is three strategy–symbol jobs daily, rotated across the entire queue. Each job samples at most 24 deterministically spaced grid combinations (PARAM_TRAIN_MAX_GRID_COMBINATIONS); this is a bounded exploratory search, not exhaustive optimization. Full grids are still visible in the coverage report.

## Completed tuning

The research pass covered all 22 family–symbol jobs across NIFTY and BANKNIFTY. Every result was INSUFFICIENT_DATA; none was approved or promoted. Seven BANKNIFTY jobs completed before the grid budget change at ₹100,000 backtest capital. Their unchanged data was not retested. The other fifteen completed with the bounded grid and ₹500,000 backtest capital. The first unrestricted NIFTY trend run was interrupted before completion to avoid an excessive computation budget. Thus the report does not claim 22 fresh ₹500,000 validations.

| Symbol | Family | Verdict |
|---|---|---|
| BANKNIFTY | breakout | INSUFFICIENT_DATA |
| BANKNIFTY | cpr | INSUFFICIENT_DATA |
| BANKNIFTY | ema_5min | INSUFFICIENT_DATA |
| BANKNIFTY | fibonacci | INSUFFICIENT_DATA |
| BANKNIFTY | ma_cross | INSUFFICIENT_DATA |
| BANKNIFTY | mean_reversion | INSUFFICIENT_DATA |
| BANKNIFTY | orb | INSUFFICIENT_DATA |
| BANKNIFTY | scalping | INSUFFICIENT_DATA |
| BANKNIFTY | supertrend_mtf | INSUFFICIENT_DATA |
| BANKNIFTY | trend | INSUFFICIENT_DATA |
| BANKNIFTY | vwap_reversion | INSUFFICIENT_DATA |
| NIFTY | breakout | INSUFFICIENT_DATA |
| NIFTY | cpr | INSUFFICIENT_DATA |
| NIFTY | ema_5min | INSUFFICIENT_DATA |
| NIFTY | fibonacci | INSUFFICIENT_DATA |
| NIFTY | ma_cross | INSUFFICIENT_DATA |
| NIFTY | mean_reversion | INSUFFICIENT_DATA |
| NIFTY | orb | INSUFFICIENT_DATA |
| NIFTY | scalping | INSUFFICIENT_DATA |
| NIFTY | supertrend_mtf | INSUFFICIENT_DATA |
| NIFTY | trend | INSUFFICIENT_DATA |
| NIFTY | vwap_reversion | INSUFFICIENT_DATA |

An option-feature weight candidate was generated separately from active weights. Its 30 feature groups use the existing capped evidence stream: 1,120 journal selections, 8,960 shadow observations and 8,000 generated live-source strike observations. Those correlated observations are not independent executable trades or an income forecast. Zero net P&L is preserved; missing/nonfinite live net P&L is excluded; wins use the P&L sign instead of target-hit labels. Active weights were not replaced by this research run.

## Automatic coverage

Both the daily pipeline and nightly learning cycle now rebuild the unified coverage/income research report. Dry runs inspect code and catalog coverage without querying outcome evidence or writing this report. Syntax deployment checks include the new controller. The latest experiments and live eligibility blockers are included alongside separate ML, pairs, option-flow and combination research tracks. Existing prospective model freezes, live gates and risk budgets remain intact. No services were restarted and no Telegram message or order was sent.

## Explicit implementation gaps

The option catalog has 3 actual evaluators and 39 placeholders. This change does not implement the 39 missing strategies or an atomic multi-leg executor. They remain research gaps, not deployable options strategies.

21 backtest modules have entry points but lack compatible bounded-validation adapters. Source presence is not validation. They are inventoried for further implementation rather than automatically imported or executed with guessed arguments:

- backtest.py
- backtest_adx_dmi_crossover.py
- backtest_adx_long_straddle.py
- backtest_aroon_crossover.py
- backtest_bollinger_otm_momentum.py
- backtest_bollinger_otm_reversal.py
- backtest_carver_ewmac.py
- backtest_contrarian_sell.py
- backtest_delta_hedged_iron_butterfly.py
- backtest_di_momentum_call.py
- backtest_donchian_breakout.py
- backtest_ema_supertrend_confirm.py
- backtest_iron_condor.py
- backtest_macd_crossover.py
- backtest_mean_reversion.py
- backtest_premium_breakout.py
- backtest_real_option_translation.py
- backtest_rolling_short_straddle.py
- backtest_sahi_strategy.py
- backtest_sma20_atm_option.py
- backtest_stochastic_crossover.py

Current paper shortlist: 0. Income target supported: no. A fixed monthly payout cannot be established by adding strategy names or increasing leverage. Further progress requires cleaner/deeper walk-forward data, implemented adapters and independent future executable evidence.

Validation: 74 focused regressions passed, including deterministic grid-budget/trial accounting, all-family selection, honest placeholder coverage, no-write planning, option net-zero handling, after-cost outcome classification, Fibonacci and validation-harness compatibility. Critical syntax checks passed. New learning-cycle coverage wiring passed an isolated integration check. The unrestricted legacy dry-run cycle test was interrupted after 170 seconds in an unbounded SQLite table count; full-cycle runtime remains an operational validation gap.

The trainer now requests 400 calendar days by default, replacing 210. Current harness settings require approximately 188 complete trading sessions (60 train sessions plus three 30-session test windows, with 20% held out). The completed research run used the previous 210-day lookback. A larger request cannot create absent history; material gaps still block validation.
