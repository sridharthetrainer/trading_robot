# Quantitative model inventory — 15 October 2026

Implemented a validated JSON specification catalog and a read-only trainer inventory command for the supplied 28 numbered groups. All original definitions, formulas and risk descriptions are preserved in each entry and in the fingerprinted source snapshot. No historical backtests, model training, parameter tuning, broker calls or live promotions were performed by this task.

## Files and use

- `quant_model_catalog.json`: supplied definitions, fixed values/examples, variants, explicit grids, missing definitions, data requirements and related local files.
- `quant_model_catalog.py`: source validation, finite metadata enumeration, versioned configuration fingerprints and an integration plan. Unimplemented backtest requests raise `SpecificationIncomplete`.
- `quant_specs/source_request_2026-10-15.txt`: verbatim source snapshot; original research citations were absent, so attribution remains user-supplied and unverified.
- `autonomous_param_trainer.py --quant-inventory`: prints the inventory and returns before any training, data fetch, parameter write or experiment scheduling.
- `reports/quant_model_inventory_2026-10-15/integration_plan.json`: generated plan with all blockers.

```bash
.venv/bin/python autonomous_param_trainer.py --quant-inventory
.venv/bin/python quant_model_catalog.py --output reports/quant_model_inventory_2026-10-15/integration_plan.json
```

The catalog is an integration template, not 28 newly implemented backtest engines. It is not added to `STRATEGY_BACKTESTS` or the live signal registry. Source files listed as related are overlap leads, not verified equivalents.

## Counts and interpretation

All 28 source headings are present, with 60 named variants. The explicitly supplied rolling-OLS grid has 3 × 3 × 3 = **27** points. Enumerating that grid and the other named variants produces **86 metadata configurations**. These counts are not completed tests, independent hypotheses, or an exhaustive search over every numerical parameter. Values identified as examples, multi-value architecture definitions, and unspecified parameters are not silently expanded into search dimensions.

The headings include 14 signal-strategy groups, four portfolio-allocation families, four predictive-model groups, and one each of an RL agent, risk overlay, microstructure inference model, optimizer, market-making model and execution-algorithm suite. A turbulence override and TWAP/VWAP execution are not independent entry signals.

**Current status: 0 schedulable backtests, 0 live approvals, monthly income probability unknown.** Each group is missing definitions needed for an exact runnable system, and none has a complete options contract/execution adapter. Declaring that a data capability exists cannot make a specification runnable or count as a data-quality audit.

## Per-group integration gaps

| ID | Supplied model group | First unresolved definition |
|---|---|---|
| Q01 | The Perfect Order FX Strategy | ADX initialization convention |
| Q02 | Enhanced Multi-Factor Technical & Sentiment Strategy | Momentum return definition and MA type |
| Q03 | Triple Technical Breakout (ASP Framework: ADX + Supertrend + Fibonacci Pivots) | Supertrend ATR period and multiplier |
| Q04 | Dual & Triple Moving Average Crossover Systems | ATR period and stop multiple |
| Q05 | MACD Momentum with Volume Surge Filter | Signal timeframe |
| Q06 | Dual-Lookback Hybrid Momentum-Mean Reversion Strategy | Signal timeframe and permitted instruments |
| Q07 | RSI Mean Reversion with Stationarity Filter | ADF window, deterministic terms and lag selection |
| Q08 | Cointegrated Pairs & Triples Trading (CADF & Johansen) | Point-in-time asset universe and formation window |
| Q09 | Bollinger Band Spread Mean Reversion | Spread definition and hedge weights |
| Q10 | Dynamic Kalman Filter Pairs Trading | State transition and observation matrices |
| Q11 | Intraday Rolling OLS Regression Pairs Engine | Asset universe and prior training selection |
| Q12 | Cross-Sectional Basket Mean Reversion | Return measurement window |
| Q13 | Opening Gap-Down Intraday Strategy | Gap return denominator and standard-deviation ddof |
| Q14 | Futures Calendar Spread & Roll Return Strategy | Eligible futures and synchronized contract prices |
| Q15 | Follow-the-Winner (FTW) Momentum Portfolio Family | Per-family learning rates, ridge beta and Monte Carlo budget/seed |
| Q16 | Follow-the-Loser (FTL-Reversion) Portfolio Family | Anticor window and exact capital transfer rules |
| Q17 | Nonparametric Pattern-Matching Portfolio Framework | Window lengths, histogram bins and kernel bandwidth |
| Q18 | Meta-Learning & Ensemble Portfolio Frameworks | Precise gradient/Newton updates and regularization |
| Q19 | Supervised Random Forest Classifier (Walk-Forward) | Names and causal availability of all 47 features |
| Q20 | ASP-LSTM Deep Learning Hybrid System | Sequence length, normalization and full ASP parameters |
| Q21 | Multi-Factor Supervised Classification Suite | Full feature definitions and label availability |
| Q22 | Multi-Modal Sentiment-Gated Execution | Publication-time data, revisions and deduplication |
| Q23 | Advantage Actor-Critic (A2C) Deep RL Portfolio Agent | Concrete state normalization, action bounds and lot constraints |
| Q24 | Turbulence Index Circuit Breaker | Historical mean/covariance window and shrinkage |
| Q25 | Gaussian Process Inverse Reinforcement Learning (GPIRL) | 243-state encoding and full MDP transitions |
| Q26 | Genetic Algorithm (GA) Strategy Rule Discovery | Causal indicator-policy genome; avoid timestamp-specific action memorization |
| Q27 | Multivariate Hawkes Process High-Frequency Market Making | Event definitions, kernel/decay and stability constraints |
| Q28 | Institutional Algorithmic Order Execution Suite | Parent-order side/size/limit and participation constraints |

The complete missing-definition and data lists are in the JSON and generated plan. Preserve them when writing adapters; do not substitute unstated parameter defaults and call the result an exact source reproduction.

## Corrections required before historical evaluation

- Timing: closing-price, complete-volume and daily-rank signals become available after those observations. Same-day opening fills are unavailable to them. At-open gap rankings need an auction/next-observable execution convention.
- Statistical models: the ADF window, lags and refit timing are part of the specification. Cointegration does not automatically impose a dollar-neutral capital allocation; exposure needs a separate constraint. See the [statsmodels cointegration API](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html) for the tested hypothesis and explicit regression/lag choices.
- Portfolio families: Anticor, PAMR, CWMR, OLMAR and RMR require distinct update rules and hyperparameters, not a shared signal alias. The [OLPS toolbox publication](https://jmlr.org/papers/v17/15-317.html) provides a primary-source implementation reference, not validation of Indian option returns.
- RL reward: if cash/wealth already reflects execution costs, subtracting the same fees again double-counts them. Specify accounting, scaling and action constraints. The [FinRL environment definition](https://finrl-contest.readthedocs.io/en/latest/intro/env.html) expresses reward as wealth change and models trading frictions in the environment.
- Training: define the claimed 47 features, labels and availability timestamps. Fit normalization, feature selection and calibration inside training windows; purge overlapping forward labels. Architecture names alone do not produce probabilities.
- Existing overlap: `rl_agent.py` is tabular Q-learning for scoring; it is not A2C. `lstm_model.py` has two 64-unit layers and a different prediction target; it is not the proposed three-layer 50-unit ASP-LSTM. The news module does not establish point-in-time FinBERT transcript scoring.
- HFT: GPIRL and Hawkes/HJB models need order-level event history, queue/fill assumptions and latency calibration. Current candle and sparse option-chain histories do not meet those contracts.
- Search: all candidate/genome evaluations count toward the trial budget. GA optimization is not a separate source of independent evidence. The monthly income target is not an objective to fit historical parameters against.

## Backtest adapter contract

A future adapter must bind exact asset universe, timeframe, completed-bar semantics, data provenance, parameters, position sizing, costs, risk rules, entry/exit timing and missing-data treatment. For options it must additionally bind strike/expiry selection, dated lot/tick sizes, settlement/margin and executable quote paths. It must return observed and modeled evidence separately, reject missing required data explicitly and register every trial. Existing research admission, validation and independent forward-evidence requirements remain in force.

The next specification candidates with relatively simple causal signal definitions are Q01 (perfect-order alignment) and Q06 (30/40 lookback). This is an implementation-effort assessment, not a profit ranking. Q01 still needs sizing and stop/exit timing; Q06 still needs timeframe, close-execution convention and a risk policy. Q11 has a finite declared grid but needs synchronized multi-asset execution/financing evidence. No missing choices were answered on the user’s behalf.

User order confirmation remains required. This inventory does not change existing automatic protection, enable live trading or authorize broker submission.

## Verification

Focused tests verify all 28 source headings, source fingerprints, policy and adapter rejection, paths, missing definitions, finite parameters, unique grid points, stable versioned identities, metadata isolation and the trainer’s early return without training or file writes. See `reports/quant_model_inventory_2026-10-15/focused_tests.txt` and `regression_tests.txt`.

Results: **53 focused checks passed** and **1,229 full regression checks passed** (10 warnings) in the isolated checkout with network calls blocked. Python compilation and whitespace checks for the changed trainer code passed. No running service was restarted.

Subsequent implementation: Q01 and Q06 now have separately labeled underlying-proxy research adaptations. See [the ordered fixes report](ORDERED_FIXES_2026-10-15.md). The original supplied definitions remain unverified and do not become exact option-strategy implementations through this addition.
