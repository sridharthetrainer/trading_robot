# ₹30,000 monthly from ₹500,000 — readiness assessment

**Verdict: unsupported by current evidence; no live strategy approved.** Refreshed from local signal outcomes and current manifests on 14 October 2026. Software correctness does not establish profitability or dependable withdrawals.

₹30,000 is **6% of ₹500,000 each month**, after trading costs and before personal income tax. Twelve withdrawals require ₹360,000, or 72% of initial capital annually while preserving that capital. Compounding 6% monthly without withdrawals would instead require approximately 101.2% annually; these are different scenarios, neither a forecast.

## Current strategy evidence

| Population | Finding |
| --- | --- |
| Current live manifest | 133 strategies; **0 live ready** |
| Rigorous validation results | 31 families: **18 FAIL, 13 INSUFFICIENT_DATA, 0 PASS** |
| Strategies with clean signal outcomes | 125; **0 exploratory paper shortlist candidates** under the current gates |
| Research tuning adapters | 31 available; no compatible backtest missing an adapter |
| Option catalogue | 42 entries with callable research logic, including 39 research hypotheses; no inert placeholders |

These populations overlap and must not be added together. Research coverage and successful imports are not trade approval. Live-manifest block reasons: 51 insufficient edge evidence, 50 negative edge, 17 failed validation, 8 missing rigorous validation, and 7 insufficient validation data.

The historical one-lot Bollinger model returned **₹3,979.89 total** across its June–October sample and **−₹5,303.69** under stress assumptions. Neither model met ₹30,000 in any observed month. June and October are partial months. Prices are EOD-anchored Black–Scholes estimates, not historical executable bid/ask fills; lot size is uniformly modeled as 65. These results are exploratory and cannot justify scaling leverage or capital allocation.

Separate momentum research reported historical annualized returns of 14.7% for the unscaled variant and 10.36% for the volatility-scaled variant, with maximum drawdowns of approximately 38.7% and 27.9%, respectively. Those files marked their own research comparisons PASS; they do not pass the runtime live-eligibility gate or demonstrate 6% monthly withdrawals. They remain forward-paper research.

## System status

The preceding system audit passed 1,120 test cases across regression and supplemental runs. Active learning data passed freshness and sampled-spacing checks for 194 symbols and 970 groups. Login, request-pacing, dependency and candle-audit defects were fixed in the working tree. Existing services were not restarted, and no order or external message was sent by this work.

No risk limit, loss limit, trade frequency, eligibility threshold or parameter promotion was changed to chase the income target.

Focused income-research, live-eligibility, option-registry, research-admission and coverage checks: **43 passed**. The refreshed assessment's counts and blocked status were also checked.

## Conditions still required

1. Obtain sufficient complete-session development and independent evaluation data. The current walk-forward contract estimates roughly 188 complete sessions, with a default 400-calendar-day history request; gaps and trade counts can require more.
2. Require after-cost results to survive independent holdout, multiple-testing controls, parameter stability and adverse execution assumptions. Increasing the number of strategies does not meet these requirements.
3. Preserve the frozen prospective experiment and collect executable quotes, real fill/margin evidence, and lifecycle reconciliation. The existing readiness report requires at least 100 paired paper/live fill comparisons; this assessment does not authorize placing live orders to collect them.
4. Assess the funded portfolio as a whole, including overlapping positions, correlated losses, available margin, intratrade drawdown and monthly withdrawals. A profitable strategy alone does not establish dependable monthly income.
5. Verify release integrity and deploy reviewed changes before treating running processes as updated. Retain the live block until the actual evidence gates pass.

Machine-readable assessment: [reports/income_target_assessment_2026-10-14.json](reports/income_target_assessment_2026-10-14.json). Detailed system checks: [SYSTEM_AUDIT_2026-10-14.md](SYSTEM_AUDIT_2026-10-14.md).
