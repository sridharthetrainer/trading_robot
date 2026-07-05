# Institutional and Competitor Audit - 2026-06-28

## Executive verdict

The project has broad research, signal, options, risk, and autonomous operations
capability. Its raw data-pipeline capability is A-grade, but it is not ready for
scaled live trading because verified forward evidence is still absent. Historical,
replay, and synthetic rows remain useful for research but are not counted as live
proof.

| Area | Capability | Evidence-adjusted | Verdict |
|---|---:|---:|---|
| Data pipeline | 92/100 (A) | 79/100 (C) | Strong storage and coverage; live option provenance missing |
| Institutional readiness | 73/100 (B) | 59/100 (F) | Engineering breadth exceeds statistical evidence |
| Option bot | 69/100 raw | 59/100 (F) | Automation is wired; verified snapshots/outcomes are zero |
| Indicator lookahead assurance | 12/12 passed | Passed | Full-history and truncated-prefix values match |
| Stored-data integrity | 17 DBs, 29 tables | Passed | 5.26M+ rows inventoried; no database integrity failures |
| Runtime | 3/3 services active | Passed | Angel and Telegram connected; startup health 11/11 |

## Competitor benchmark

This is a representative benchmark against relevant product categories, not a
claim to have inspected every proprietary implementation.

| Reference | What it does well | Project position | Required response |
|---|---|---|---|
| NautilusTrader | Event-driven research/live parity, durable state and reconciliation | Partial: autonomous event flow and recovery exist; full deterministic replay does not | Hash-chain execution evidence added; deterministic decision replay remains roadmap |
| QuantConnect LEAN | Explicit brokerage, fill, slippage, option, and capacity reality models | Strong fees/risk/spread gates; limited empirical market-impact calibration | Collect broker depth/fills before fitting impact, never invent it from OHLC |
| Freqtrade | Automated lookahead and recursive-indicator analysis | Truncation audit now runs autonomously for shared indicators | Add strategy-level signal truncation tests as strategies are standardized |
| Backtrader | Reusable slippage and analyzer contracts | Equivalent core cost/performance modules exist | Continue converging old backtests onto the shared cost contract |
| AlgoTest | Options backtest, forward test, live deployment, leg risk controls | Broader autonomous research; less operator UI; forward evidence currently inadequate | Keep paper mode until clean forward gates pass |
| Tradetron | Cloud automation, multi-leg options and Greek-triggered controls | Multi-leg, Greeks, trailing and autonomous loops exist | Validate atomic/partial-fill recovery with live broker evidence |
| Sensibull | Mature option chain, OI/IV/Greeks, payoff and scenario UX | Comparable analytics in code/Telegram; weaker interactive visualization | UX is secondary to fixing provenance and forward evidence |
| Streak | Accessible scanners, strategy rules, backtesting and scalping workflow | Larger autonomous universe and learning stack | Preserve simpler strategy contracts and avoid feature proliferation |

## Literature mapping

| Source | Principle | Status |
|---|---|---|
| Lopez de Prado, *Advances in Financial Machine Learning* | Triple barrier, meta-labeling, purged CV, sample overlap | Implemented; per-signal barriers now captured for all generated signals |
| Bailey et al., PBO and Deflated Sharpe | Penalize repeated trials and backtest selection | Implemented through CSCV/PBO, DSR and experiment registry |
| Ernest Chan, *Quantitative Trading* and *Algorithmic Trading* | Backtest realism, regime awareness, automation and risk | Broadly implemented; forward evidence gate remains binding |
| Robert Carver, *Systematic Trading* | Conservative fitting, forecast combination, volatility/risk sizing, costs | Portfolio heat, VaR/CVaR, Kelly/ATR and cost gates exist; live calibration pending |
| Larry Harris, *Trading and Exchanges* | Liquidity, order type, priority and transaction-cost measurement | Spread/liquidity/order-routing gates exist; depth and impact history are insufficient |

## Fixes completed in this audit

1. Every generated signal can carry deterministic signal-time stop, target, and
   reward/risk fields, including rejected and shadow candidates.
2. The triple-barrier label path uses each signal's actual levels where available;
   old generic labels remain preserved but are excluded from clean promotion.
3. ML live scoring rejects legacy training contracts and prefers the clean
   all-generated-signal model.
4. Option multi-strike signals receive after-cost EOD outcomes. Only verified
   live-source outcomes may alter live strike weights.
5. Execution count is no longer a strategy-learning requirement. All eligible
   generated signals train the system; executions validate infrastructure only.
6. A read-only evidence catalog inventories all project SQLite data without
   deleting, rewriting, or silently promoting legacy rows.
7. The shared indicator truncation audit covers SMA, EMA, RSI, ATR, ADX,
   Supertrend, VWAP, Bollinger Bands, MACD, volume ratio, OBV, and MFI.
8. Execution compliance events are append-only and SHA-256 hash chained across
   process restarts, with a verifier exposed to readiness and nightly learning.
9. ML artifacts now carry a training-contract identifier, selected features,
   a training-data fingerprint, and artifact SHA-256 hashes.
10. Readiness now fails closed on indicator lookahead, broken audit chains,
    release-integrity changes, or insufficient clean evidence.

## Deep model audit addendum

The second pass found five false-edge paths that ordinary syntax and import tests
cannot detect:

1. `tb_r_multiple` was retained in the feature matrix and could be selected as a
   predictor even though it is only known after the trade outcome. All outcome,
   exit, P&L, labelling, and barrier-result fields are now forbidden predictors.
2. Same-day FII/DII and sector EOD values were joined to intraday signals. Market
   context is now joined from the latest strictly earlier session.
3. Feature selection used the full target before cross-validation. Selection now
   runs inside each purged fold through the estimator pipeline.
4. Live prediction reconstructed columns in feature-importance order rather than
   training order. Each artifact now carries and enforces an ordered feature list.
5. Legacy `learned_filters`, EOD weights, and condition-matrix replay rows could
   still modify scores. They remain stored as research evidence but are neutral
   unless promoted under the current clean contract and locked forward validation.

The primary model is now a bounded champion-challenger tournament across four
model families: regularized logistic regression, gradient boosting, histogram
gradient boosting, and random forest. Comparison uses purged out-of-fold AUC,
Brier skill against a base-rate forecast, and log loss. Feature selection is
fold-local and the winning classifier receives purged sigmoid calibration.

A model artifact is operational only when all conditions pass: at least 5,000
clean samples, 15 distinct sessions, purged AUC at least 0.55, positive Brier
skill, successful probability calibration, exact training contract, ordered
features, and artifact/data fingerprints. Adding more algorithms without these
controls would increase selection bias rather than expected profit.

Validation after this addendum: 217 pytest tests passed, 467 Python files passed
syntax checks, 118 operational checks passed, and 6/6 offline chaos scenarios
degraded safely. The SmartAPI dependency emits TLS deprecation warnings but no
test failures.

## Alternative price-representation upgrade

The chart-representation request was implemented as causal market features,
not as duplicated visual strategies. Line, line-with-markers, step-line, area,
Excel-area, baseline and columns contain the same close series; their distinct
information is represented once through normalized return, line slope, turning
state, step direction and baseline distance.

New signal-time features cover hollow-candle state/run, volume-candle strength,
three-line-break direction/run/event, Kagi direction/reversal/distance,
Point-and-Figure direction/box count/reversal, range direction/run/event,
Ichimoku cloud/Tenkan-Kijun state, and an explicitly marked OHLCV close-location
volume-delta proxy. True footprint availability remains zero until aggressor-side
tick or bid/ask data is captured.

Six new candidate strategies are wired: `hollow_candle_state`,
`three_line_break`, `kagi_reversal`, `point_and_figure`, `range_bar_momentum`,
and `ohlcv_footprint_proxy`. They use real source-OHLC closes for entry and label
prices. Synthetic chart levels are never used as fills. All price-transform
strategies share one de-correlated confluence factor, preventing six views of the
same price path from being counted as six independent confirmations.

Heikin-Ashi, Ichimoku, TPO, session volume profile, advanced volume profile and
VSA were already wired and were retained. Ichimoku's Chikou value contract was
made causal: visualization may shift its display coordinate, but future closes
cannot enter historical strategy or ML rows.

All 9,145 stored signal rows were enriched point-in-time from candles at or
before their original signal timestamp. A consistent pre-migration SQLite backup
was created. The enrichment did not alter labels, outcomes, prices, eligibility
or provenance. The learning contract is now
`all_generated_signals_v4_causal_representations`; every older model remains
stored but is rejected for live inference.

Post-upgrade verification: 228 pytest tests passed. The truncation audit covers
14 indicator/representation families and 125 alternative-representation prefix
comparisons with zero drift.

## Low-cost live-data hardening

- Added authenticated Upstox and Dhan option-chain adapters with explicit
  provider order and normalized bid/ask, quantities, OI, volume, IV and Greeks.
- Fixed provenance so stale resilience cache cannot be recorded as a verified
  NSE live snapshot.
- Fixed SmartAPI WebSocket paise-to-rupee normalization and connection-state
  reporting; REST protection remains active until the socket is truly open.
- Wired quantity-weighted tick flow across the NSE signal universe into the
  signal log and v4 ML feature matrix for forward learning. This remains a
  tick-rule proxy, not an exchange aggressor-side footprint feed.
- Added setup and cost guidance in `LOW_COST_DATA_SOURCES.md`.

## Binding gaps

1. `0/5000` clean generated-signal outcomes across `0/15` required sessions.
2. `0` verified live option-chain snapshots and `0` verified generated option
   outcomes. Existing option data is preserved as historical/research evidence.
3. No strategy clears the current live-eligibility manifest.
4. Full deterministic decision replay is not yet available across every engine.
5. Broker depth, partial-fill, latency, and market-impact history are too small
   to calibrate an institutional execution model.
6. `historical_options.db` is an empty placeholder; the populated historical
   option store is `options_nifty.db`.

## Promotion policy

Do not lower the gates to improve the score. Remain in paper mode until all of
the following are true:

- At least 5,000 clean, setup-specific generated-signal outcomes over 15 market sessions.
- Positive after-cost shadow portfolio evidence on untouched forward data.
- At least 20 source-attributed live option snapshots and 100 verified generated
  option-signal outcomes.
- A strategy passes holdout, purged CV, DSR/PBO, stability, and cost gates.
- Release integrity, broker connectivity, audit chain, and indicator truncation
  checks all pass on the promoted build.

## Primary references

- NautilusTrader live concepts: https://nautilustrader.io/docs/latest/concepts/live/
- NautilusTrader event sourcing: https://nautilustrader.io/docs/nightly/concepts/event_sourcing/
- QuantConnect reality modelling: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts
- Freqtrade lookahead analysis: https://docs.freqtrade.io/en/latest/lookahead-analysis/
- Freqtrade recursive analysis: https://docs.freqtrade.io/en/stable/recursive-analysis/
- AlgoTest forward testing: https://docs.algotest.in/Signals-AI/Signals-Deployment/Forward-Test/
- Sensibull feature list: https://sensibull.com/index.html
- Streak product: https://www.streak.tech/
- Lopez de Prado backtesting paper: https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2606462
- Bailey and Lopez de Prado, Deflated Sharpe: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- Robert Carver, *Systematic Trading*: https://www.harriman-house.com/systematic-trading
- Ernest Chan, *Quantitative Trading*: https://onlinelibrary.wiley.com/doi/book/10.1002/9781119203377
- Larry Harris, *Trading and Exchanges*: https://academic.oup.com/book/52292
