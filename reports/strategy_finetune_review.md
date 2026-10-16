# Strategy fine-tuning review — 16 October 2026

Assessed 128 strategy names from eligible recorded signals and configured genomes using the local signal database. This covers score-threshold research; it does not validate every strategy's entry logic or optimize indicator periods, stops, targets, or option structures.

| Result | Strategies |
| --- | ---: |
| Proposed threshold failed validation | 49 |
| Insufficient evidence | 56 |
| No training improvement | 23 |
| Candidates meeting all checks | 0 |

The shared evolution evaluator previously substituted a proposed target/stop ratio for actual winning returns and treated timeout labels as losses. This could make larger targets appear better without replaying any trade. It now uses finite recorded net R (including zero and timeout returns), estimates Sharpe from daily average returns, and restricts mutation to score thresholds. It checks evidence days separately for each strategy, requires adequate baseline and candidate holdout observations, and requires positive training and holdout results before promoting a score threshold. Changes to exits or confirmation bars require candle replay.

The review selects the best threshold on the first 70% of each strategy's recorded days and evaluates that one proposal on the remaining days. Thresholds range from 3 to 8 in 0.5 increments. Names without configured genomes use a score baseline of 5 for research only. Minimum observations are 20 training and 10 holdout, with at least three holdout days. These are screening floors, not proof of profitability. The existing candle-replay trainer and forward-validation requirements remain necessary.

No runtime parameter files or genomes were changed, no services were restarted, and no orders or messages were sent. No strategy was disabled based on this review.

Limits: recorded net R depends on upstream label and cost accuracy. Signal-level evidence is not a portfolio backtest and does not model concurrent capital allocation. Date splitting does not purge positions whose outcomes cross the boundary. Reusing this history or testing many strategies creates selection bias; fresh forward evidence is required before adoption.

Validation: 30 tests passed across strategy evolution, review, autonomous parameter training, and research backtest adapters. `git diff --check` passed.

Reproduce the read-only assessment:

```bash
.venv/bin/python strategy_finetune_review.py
```

Detailed per-strategy metrics: `reports/strategy_finetune_review.json`.
