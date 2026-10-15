# Parameter and strategy combination research — 14 October 2026

**The ₹30,000 monthly target from ₹500,000 is still unsupported. No live parameters or risk limits were changed.**

## What was checked

- Enumerated all **2,364 parameter combinations in the current finite grids for 31 tuning families**. Enumeration is not backtesting. Arbitrary numeric values, new indicator definitions and all 42 option-catalogue hypotheses are not an exhaustively backtested search space.
- Assessed all **46 compatible NIFTY/BANKNIFTY tuning jobs** against the current 400-day cache and experiment memory: **11** became eligible through older development-history backfills, **4** had new evidence, and **31** remained blocked pending new sessions.
- Including the symbol dimension produces **4,678 finite symbol/parameter cases**; these have not all been evaluated.
- Evaluated **100,000 supported strategy co-occurrence subsets** from 30 development days and 14 historical evaluation days: all **2,097 pairs**, **16,627 triples** and **73,420 four-strategy groups** meeting the 40-training-row minimum, plus **7,856 five-strategy groups**. **Zero exploratory candidates** passed the daily aggregation and multiple-testing checks.
- The all-size search is **incomplete**. The supported singleton universe contains 101 strategies, with approximately **2.535 × 10³⁰ theoretical subsets** before combination-support pruning. Unsupported cohorts cannot be statistically evaluated; permutations of the same unordered agreement set duplicate the same test. Ordered execution policies would be a separate research question.

## Search reliability fixes

The existing pair/triple miner now chooses its candidate universe using development data only. Previously, evaluation-period frequency could influence selection. Significance uses Student's t distribution and unrounded p-values; rounding tiny p-values to zero before Bonferroni correction could create misleading passes. Database access is read-only and explicitly closed, and nonfinite outcomes are excluded.

The new `combination_research.py` searches all subset sizes with training-support pruning. It covers smaller sets before larger sets, rather than consuming the budget in one deep alphabetical branch. Reports expose complete subset sizes, the interrupted size and trial/time budgets. Incomplete searches conservatively correct against the entire eligible-universe subset count. Daily average net R reduces repeated intraday observations being treated as independent evidence.

This remains repeated historical exploration: its evaluation period is not independent future evidence, and a co-occurrence net-R cohort is not a funded portfolio with real margin and executable fills. A candidate, if found, would still require prospective validation and would not activate trading.

## Fresh bounded fine-tuning

Three newly eligible NIFTY families were evaluated against an isolated candle-cache and experiment-memory copy. Each used 267 sessions and five walk-forward windows. There were **300 development parameter trials** in total: all 12 Supertrend tuples in each window (60 trials), and 24 sampled tuples per window for MA crossover and Fibonacci (120 trials each). MA crossover's full grid has 40 tuples; Fibonacci's has 36. Repeated window trials are not distinct parameter tuples.

| Family | Verdict | Average development-window P&L | Deflated Sharpe | Stability CV |
| --- | --- | --- | --- | --- |
| Supertrend MTF | FAIL | −₹7,423.18 | 0 | 0.2405 |
| MA crossover | FAIL | ₹14,876.44 | 0 | 0.8526 |
| Fibonacci | FAIL | −₹73,530.35 | 0 | 0 |

These are underlying-proxy research results, not monthly income or actual execution results. The final holdout remained unopened for all three because development checks failed. No backtest errors were recorded and no family was promoted. Failed results and fingerprints were appended to the actual experiment memory so unchanged evidence cannot be repeatedly retested; candidate parameter files were retained only under this report directory. Active parameter files and validation manifests were preserved.

## Artifacts and validation

Artifacts are in [reports/combination_research_2026-10-14](reports/combination_research_2026-10-14/):

- `parameter_inventory.json`: every current finite parameter tuple, explicitly marked unevaluated by inventory.
- `training_evidence.json`: eligibility and data fingerprints for every compatible tuning job, captured before the new bounded run.
- `summary.json`: search coverage, budgets and candidate count.
- `combinations.json`: all 100,000 evaluated cohorts and daily statistics.
- `bounded_finetuning_result.json` and `bounded_validation_results.json`: complete results of the three fresh tuning jobs.

Focused combination, income-research and coverage checks: **34 passed**. Static checks found no issues in the changed research modules; dependency coverage passed.

The existing default training budget remains three jobs per run and at most 24 parameter combinations per job. Exhaustive enumeration of the finite grids does not remove the evidence freeze or imply those parameters have been evaluated. Further data-dependent optimization must preserve the independent evaluation and experiment-history gates.
