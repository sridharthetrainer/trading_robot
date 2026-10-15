# Ordered trading-system fixes — 15 October 2026

Implemented the five requested areas in sequence. No real orders were sent, no live flags changed and no services restarted. These changes improve software and research controls; they do not establish a profitable strategy or certify the ₹30,000 monthly target.

## 1. Runnable Q01 and Q06 research specifications

`quant_research_adapters.py` implements two frozen, causal **daily underlying-price research adaptations**. `quant_model_catalog.get_research_adapter()` exposes only Q01 and Q06, and the inventory plan lists them separately from the original unverified specifications. The original catalog remains intact; the other 26 groups were not silently implemented or enabled.

- **Q01:** five SMA periods 10/20/50/100/200, repository Wilder ADX(14) ≥20 throughout five completed bars, first qualifying gate only, entry at the following observed daily open. Stop uses the fifth confirmation bar's low/high. Fast-SMA reversal exits at the next open; gap-through stops use the worse opening price. The gate must reset before another entry.
- **Q06:** daily 30/40-bar comparisons, next-open execution. A fixed 2×ATR(14) stop and a 20-session maximum holding period are explicit project risk additions. A timed exit cannot immediately re-enter on the same bar.
- Both use 0.5% planned stop-risk sizing, integer proxy units, a gross-notional cap of capital, 5 bps adverse slippage and 10 bps fee assumptions on each side. Those costs are declared proxy stress assumptions, not certified tax schedules. Fees and slippage are included in net P&L; gap losses can exceed planned stop risk. Window-end liquidation is labeled separately.
- Inputs must be ordered, unique, valid OHLC with explicit completed-bar and daily-interval metadata. No future bars enter signals. These daily proxies may hold overnight; they are not the intraday option workflow.

Frozen choices are saved in `reports/ordered_fixes_2026-10-15/research_specifications.json`. No thresholds were searched against historical outcomes. No new historical alpha evaluation was run. The historical CSV CLI retains the existing research-admission check.

```bash
.venv/bin/python quant_research_adapters.py --model Q01 --plan
.venv/bin/python quant_research_adapters.py --model Q06 --plan
# Historical evaluation, after the existing admission requirements are satisfied:
.venv/bin/python quant_research_adapters.py --model Q01 --csv completed_daily.csv --proposal-id APPROVED_ID --output research_result.json
```

A CSV must contain timestamp/open/high/low/close and only completed daily sessions. The explicit CLI import contract asserts daily completion; the importer cannot independently establish the data supplier's truthfulness. Synthetic tests verify the implementation without pretending to supply independent edge evidence. `option_pnl` and the monthly-target probability remain null.

## 2. Options-data integrity

Fixed the fetch/flatten/record path that dropped Angel's individual call/put symbols and exchange feed timestamps. Indian exchange timestamps supplied without a timezone are normalized to Asia/Kolkata specifically in the Angel adapter. The provider receipt timestamp is retained separately. Missing exchange timestamps remain missing; the chain's aggregate timestamp is never copied into every leg.

`option_research_data.py` adds an explicit separate evidence-store importer for dated contract records and observed quotes. Contract metadata requires exact contract/exchange/underlying/type/strike/expiry, effective-from/to dates, lot size, tick size and provenance. Overlapping conflicting versions are rejected. Quote validation rejects missing metadata, naive/stale/future timestamps, crossed markets, unknown quantities, wrong ticks and contract/exchange mismatches. Exact duplicate imports are idempotent. There is no lot-size fallback or synthetic quote generation.

```bash
.venv/bin/python option_research_data.py --db option_research_evidence.db --contracts dated_contracts.jsonl --quotes observed_quotes.jsonl
```

Each contract line requires `contract,exchange,underlying,expiry,option_type,strike,lot_size,tick_size,effective_from,effective_to,source`. Quote lines require `contract,exchange,source,exchange_timestamp,received_at,bid,ask,bid_quantity,ask_quantity`. Datetimes must contain offsets. Imports validate supplied observations and consistency, not the vendor's authenticity or the completeness of historical coverage.

**Still required:** a verified historical data export/subscription or sufficiently long forward collection. The old archive's missing timestamps and sparse paths cannot be recovered from receipt times. No data subscription was purchased or connection enabled. The running recorder must be restarted through the normal deployment process to collect the newly preserved fields.

## 3. Execution replay and durable recovery

`option_execution_simulator.py` replays fixed BUY LIMIT orders against validated, dated option quotes. It applies confirmation/latency/expiry timing, displayed-quantity limits, partial fills, disconnection/reconnection, terminal cancellations/rejections and uncertain submission outcomes. Repeated identical depth cannot replenish liquidity. Entry fees reuse the dated local cost model without adding another synthetic spread charge. Replay output is explicitly **OBSERVED_QUOTE_REPLAY_MODEL**, not actual exchange fills or certified queue priority.

Also fixed `option_institutional_controls.simulate_limit_fill`: absent or zero depth previously implied a full fill; it now leaves the order unfilled. Nonfinite quotes are invalid.

`OrderReviews` now persists LIVE broker acknowledgements as ACKNOWLEDGED and blocks further confirmations until reconciliation. `/orderstatus` attempts a read-only order-book reconciliation. Clearing the durable lock requires one exact order tag, contract, exchange, side, product, requested quantity and limit, a terminal broker state, explicit valid filled quantity and a positive average price when filled. Complete-with-zero-fill, open orders, conflicting matches and missing fills remain blocked. Cancellation after a partial fill retains the filled position quantity. Reconciliation does not mean the position was closed or protected.

Each new review binds a fingerprint of the connected broker account. Changing the account invalidates review rechecks and submission. No Telegram force-unlock or automatic retry was added. Old drafts lacking this binding require a new review; unresolved old intents still need reconciliation.

## 4. Portfolio risk controls

`option_portfolio_gate.py` provides an exact-quantity bought-option gate with these initial manual-entry limits:

| Control | Limit |
|---|---:|
| Stated capital context | ₹500,000 |
| Full premium per entry | ₹25,000 |
| Aggregate full premium | ₹50,000 |
| Daily realized-loss lock | ₹5,000 |
| Open positions | 2 maximum |
| Correlated positions | 1 maximum |
| New-entry cutoff | 15:10 IST |
| Overnight entry approval | Disabled |

Missing correlation groups are conservatively treated as correlated. Exposure snapshots must be timezone-aware and fresh. Unknown or short-option exposure, invalid numbers and overnight positions are blocked. The gate never resizes a reviewed order.

The Telegram workflow uses this gate and remains stricter for LIVE: it requires both a flat native bot portfolio and a flat connected Angel book, no outstanding broker orders, sufficient verified cash, existing readiness admission and native live flags. The loss check now adds the manual tracker and the bot's daily realized P&L: the tracker explicitly excludes ALGO-tagged bot fills, so these books are disjoint. Missing native P&L evidence blocks entry. External accounts not connected to these snapshots are not covered by this assessment.

The existing `PortfolioRiskManager` also rejects nonfinite/missing risk inputs instead of allowing NaN comparisons to pass. Failure of an enabled VaR model blocks admission; unknown position groups count toward correlation limits. Existing protective exits were not changed. No new broker-side stop is attached by the confirmation workflow; users still need to manage protection and existing exits.

## 5. Probability and performance reporting

`strategy_probability_report.py` computes net closed-trade expectancy, win rate, average win/loss, daily/monthly net P&L and drawdown. Monthly-target probability stays null unless the caller supplies observed net option outcomes, reconciled trade/daily ledgers, flat sample boundaries, verified complete consecutive calendar months and independent forward provenance. Minimum sample requirements are 12 complete months, 200 sessions and 100 closed trades. Invalid, duplicate or incomplete sessions are rejected. More bootstrap draws never count as more market observations.

For qualified input, it reports historical monthly target frequency and an approximate uncertainty/sensitivity envelope using circular three-month bootstrap blocks plus an effective-block Wilson sensitivity bound. This is conditional on the observed regime and the caller's provenance/coverage attestations. It is not a guaranteed confidence bound under arbitrary dependence, a forecast, or live admission. The Wilson formula follows [NIST's discussion](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm); dependence-aware resampling is motivated by [Künsch's work on stationary observations](https://people.math.ethz.ch/~hkuensch/papers/). The combined sensitivity envelope is a documented project choice.

```bash
.venv/bin/python strategy_probability_report.py --input qualified_daily_and_trade_evidence.json --output option_probability_report.json
```

The JSON has `daily_rows` and `metadata`. Daily rows contain `date,net_pnl,closed_trades,complete_session` and optionally `capital`. Metadata must identify the evidence kind and include independent-forward/complete-month/flat-boundary attestations and `trade_rows` with unique `trade_id,net_pnl`. Do not assert those fields merely to obtain a probability.

Added Telegram `/probability` and a scalping-menu button. It displays unknown when no qualified report exists and rejects malformed/unqualified saved estimates. Direction output now calls its heuristic a **confluence score /100** and explicitly says win probability is uncalibrated.

A read-only journal inspection found **12 CLOSED PAPER trades and no LIVE trades**. It does not qualify as an independent daily option ledger. `reports/ordered_fixes_2026-10-15/probability_status.json` therefore retains a null monthly probability and explicit blockers. No empty daily series was substituted as evidence of zero return.

## Validation and remaining work

See `reports/ordered_fixes_2026-10-15/focused_tests.txt` and `regression_tests.txt`. Tests cover causality, five-bar gating, next-open fills, gap handling, fee accounting, dated metadata, quote validation, timestamp persistence through the real recorder path, replay partials/disconnects/duplicates, account changes, exact terminal reconciliation, combined-book loss locks, portfolio limits and qualified/unknown probability behavior. Tests ran in the isolated network-blocked checkout.

The software changes are implemented. Real options-history completeness and independent future performance remain evidence requirements; they cannot be manufactured by code. Services still need a controlled restart to activate Telegram and recorder changes. The existing live-readiness gates remain enforced.

Final validation: **154 focused tests passed** and **1,275 full regression tests passed** (12 warnings) on the final implementation. Python compilation and whitespace checks passed for the changed implementation files. Broker order tags are stable 20-character alphanumeric hashes so Angel's punctuation sanitization cannot prevent exact reconciliation. Quote replay also prevents fresh timestamps alone from replenishing previously consumed identical depth.
