# System pain-point audit — 14 October 2026

## Scope and evidence

Audited autonomous parameter training, persistent experiment memory, parameter consumption, pipeline status propagation, candle-cache admission/date boundaries, and deployment syntax coverage. Regression checks also exercised options, fees/slippage, risk gates, prospective evidence, ML validation, recorded P&L, position reconciliation, data repair, and Telegram controls. Existing unrelated workspace changes were preserved. No bot restart, live order, broker login, deployment, or Telegram message was initiated.

642 top-level Python files passed AST syntax checks. The combined selected offline regression suite passed 498 tests. A further focused run validates the final calendar-session and benign-skip changes. Critical deployment compilation passed with the newly added modules included. These checks establish tested behavior, not a claim that every possible fault in the system has been eliminated.

## Fixed findings

- Parameter bridge could classify explicitly failed/insufficient-data or paper-only results as validated when their development metrics passed loose thresholds. Such results now remain outside the validated cache.
- NaN/infinite or malformed Sharpe, win rate and trade counts could bypass admission comparisons or throw unexpectedly. They now fail validation. Impossible win rates are rejected.
- Zero holdout Sharpe was replaced by development Sharpe, and zero parameter variability was replaced by one. Both zero values are preserved.
- Learning-cycle steps reported success despite an explicit nested `ok=False`. Both learning and daily pipeline wrappers now propagate reported failures; legitimate off-session option-report skips remain benign.
- Weight summaries mixed positive and negative changes into both promoted and demoted lists, treated zero as one, and could crash on malformed weights. Direction and zeros are preserved; malformed/nonfinite values are excluded.
- An explicit zero training budget was replaced by the default budget. Zero now means no training; invalid capital, days, budgets and intervals are rejected.
- Training-calendar lookup failure previously permitted heavy optimization. It now blocks unforced runs.
- No-write training still logged missing-data and error experiments. Those writes are now conditional.
- Parameter data-loader exceptions could abort the entire strategy batch. They are reported per job so remaining jobs can continue.
- Concurrent scheduled/manual trainers could optimize the same evidence simultaneously. A process-held nonblocking lock now admits only one writing trainer; process exit releases the lock.
- Scheduler rotation only started after the first completed experiment, allowing permanently unavailable jobs to monopolize the queue. Rotation now starts immediately and duplicate jobs are removed.
- Knowledge admission mishandled empty frames, duplicate columns and nonnumeric OHLCV. These cases now return explicit rejection reasons. Session dates use IST; future bars are rejected and new-session counts exclude weekends/known exchange holidays.
- Cache OHLC validation admitted infinity/NaN in some positions. Finite prices and nonnegative finite volume are required. Writes normalize timestamps to IST ISO form and reject missing timestamps.
- Cache queries used a `T` timestamp cutoff against legacy timestamps containing a space, dropping valid first-day opening bars. Date-only cutoff includes both formats.
- Deployment critical-file checks omitted the new knowledge/tuning/visual components and parameter/economic/option policy modules. They are now included.
- The staged-candidate regression tests depended on importing a real backtester after changing working directory. Explicit test doubles remove this test-environment dependency.

## Limits and remaining work

Historical strategy evidence still does not establish the requested monthly return. These fixes do not create a live trading edge. Rolling historical candidates remain staged pending independent forward validation; active parameters, frozen cohorts, risk sizing and trading mode were not changed by this audit.

The memory governs the parameter trainer, not every independent ML/option-weight learner. There is still no general automatic forward-validation approval or automated candidate rollback lifecycle. Process locking applies to this trainer; other tools that write parameter files are outside that lock. All 162 repository test files were not executed because some are broker/network/live-order scripts. Production restart, real Telegram delivery, market-session latency and broker execution require separate operational validation.
