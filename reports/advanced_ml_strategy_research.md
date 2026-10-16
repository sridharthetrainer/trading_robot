# Advanced ML strategy research — 16 October 2026

Completed result: **rejected; no candidate adopted**. The run used 26,544 usable signals across 123 strategies and completed all 16 configurations without model errors. Development used 34 sessions (25,198 rows); the reserved holdout used nine sessions (1,048 rows), with one intervening session excluded.

The development-selected depth-8 random forest used a probability threshold of 0.20. Its development selections averaged +0.2705R over 87 signals and 18 sessions, with a session-bootstrap lower bound of +0.1132R. On the reserved sessions it selected only one signal, returning -0.2698R. This fails both sample and performance requirements. The 0.20 threshold is an experimental result, not an adopted entry setting. No runner settings were adjusted in response to holdout results.

This research runner tests 16 configurations across logistic regression, histogram gradient boosting, random forests, Extra Trees, direct net-return regression, and a fixed soft-voting ensemble. It searches model regularization, tree depth, and entry thresholds. It does not alter strategy exits or indicator periods: those require candle replay.

The input is the local eligible signal history, including rejected signals with known labels, valid risk levels, finite net R, valid prices, and recorded outcome times. Numeric inputs use the existing entry-feature encoder; strategy, symbol and side are also encoded. No current candle snapshot is attached to historical signals. Outcome fields remain separate from features.

The latest 20% of complete recorded sessions (at least five) are reserved before model selection. One preceding session is excluded, and development rows with outcomes ending at or after the holdout start are purged. Within development, three expanding chronological folds omit a complete session before each test block and purge training outcomes unavailable at that block's start. Imputation, scaling and category encoding fit on each fold's training rows.

Development predictions choose the model and threshold. The objective is the fifth percentile of 1,000 whole-session bootstrap means of selected after-cost R. Each proposal needs at least 50 selected signals across five sessions. The selected model is fitted on development and evaluated once on reserved sessions at its fixed threshold. A positive development and holdout bound is only a research screen; it does not authorize adoption. Many model/threshold trials and prior reuse of the historical data prevent calling the holdout fresh prospective evidence.

Outputs include every model's development metrics and parameters, the selected threshold, holdout statistics, and holdout selections by strategy. No models are written into the runtime model directory. No broker, order, messaging or scheduler actions are invoked.

Limitations: recorded costs/labels and feature timestamps depend on upstream quality; no portfolio allocation or executable option translation is simulated. Bootstrap bounds are not adjusted for the model/threshold search. Sparse strategy groups and sparse market regimes still need more independent sessions. Missing optional XGBoost, LightGBM, CatBoost, Optuna and neural-network packages are not installed by this runner.

Reproduce with installed dependencies:

```bash
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 .venv/bin/python advanced_ml_strategy_research.py
```

Detailed results: `reports/advanced_ml_strategy_research.json`.

Validation: 37 tests passed across the new runner, feature hygiene, model comparison, ML training contract, score-threshold review, and strategy evolution. Checks cover session separation, outcome purging, fold-local preprocessing, unseen categories, missing data, read-only database access, outcome-feature exclusion, and independence of model selection from holdout returns.
