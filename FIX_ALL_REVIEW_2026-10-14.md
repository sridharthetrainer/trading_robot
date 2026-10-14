# Implementation and audit fixes — 14 October 2026

## What is now covered

All 42 option catalog IDs now have callable research logic: the existing C1/C3/D1 entry evaluators plus 39 new `RESEARCH_IMPLEMENTED` hypotheses in `option_catalog_research.py`. No inert placeholders remain. This label describes entry hypotheses and quote-based proposal calculators, not complete execution/adjustment implementations of the original specification. Original activation restrictions remain: disabled, dangerous and skipped entries are blocked by the runtime catalog.

The new logic covers opening-range/momentum/reversion entries, directional debit and credit spreads, ratio and tail-hedged structures, straddles/strangles, butterflies, expiry hypotheses, event/compression hypotheses, and cross-expiry structure quotes. Indicators use completed bars only. Inputs require timestamped, finite, geometrically valid candles and fresh, liquid bid/ask quotes with known unexpired contracts. Delta selection, signed execution sides, leg ratios, expiry payoff bounds and estimated fees are explicit. Missing momentum history, IV evidence, second-expiry data or futures contract quotes blocks the corresponding hypothesis.

Cross-expiry C5/D7 return structure quotes without claiming a bounded expiry payoff. Dynamic IDs calculate hold/roll/hedge/flatten proposals from an explicit OPEN simulation state. A PROPOSED entry is never treated as a filled position. Calculators validate symbol, expiry, session, entry prices, costs, adjustment caps, nonincreasing margin estimates and defined-risk preservation. Futures P&L is included in portfolio marks when a futures position exists. Fractional hedge requirements are reported as unsupported execution. Applying proposals, reconciling real fills and managing an actual broker portfolio remain outside this research engine.

## Backtest adapters

Added explicit adapters for all 20 usable backtests in the prior gap list. The deliberately disabled `backtest_iron_condor.py` stays quarantined because it fabricated option credits/losses; its recommended real-premia replacement remains `condor_backtest_real.py`. Nothing bypasses that block. There are now 31 candidate families in the default bounded tuning rotation. Explicit operator strategy overrides remain respected.

Adapters provide only their supplied validation window through a context-local candle loader; they never replace module globals or fall back to the full candle database while a window is active. NIFTY-only models reject BANKNIFTY. The ADX straddle requires actual 1-minute inputs; 5-minute candles are not upsampled. Open-stamped bars are shifted to their close clock for close-derived signals, and incomplete resampling buckets are discarded.

Modeled option runs remain `MODELED_OPTION_RESEARCH`, with executable fills and funding unverified. Uniform historical NIFTY lot size 65 remains a modeling limitation. Carver daily mark-to-market observations are not manufactured into completed trades; they do not satisfy the standard trade-count gate. Legacy default EMA/RSI is explicitly described as such, not the full signal-engine confluence system. Candidate parameters stay outside active runtime parameter paths.

## Runtime and evidence fixes

- SQLite audit counts use read-only connections, execution deadlines and an interruptible counting query. Timed-out counts are unknown, not zero.
- Grouped storage and labelled-data queries have deadlines. Read-only audits no longer create the option journal.
- Evidence-catalog dry runs read schema metadata without counting tables or checking every database page. Full scans bound table counts/integrity queries and identify incomplete counts.
- Candle-quality audits use read-only connections and a query budget. Selected-symbol filtering happens in SQL; spacing checks use the latest 2,000 bars and declare that scope. Incomplete audits cannot report ready data.
- Learning-cycle dry runs skip the writing candle watchdog. The legacy integration test fell from approximately 138 seconds to 19 seconds on this workspace.
- The legacy default backtest can precompute causal indicators once per window. Tests compare results with original prefix calls and poison future bars. A 500-bar signal benchmark produced identical signals: approximately 1.94 seconds versus 0.005 seconds (362× on this input); this is not an end-to-end trading latency claim.
- Actual attempted grid trials are counted even when they fail. Backtest exceptions are recorded instead of masquerading solely as insufficient data. No-write training also disables the harness experiment log.
- Clean older-history backfills can unlock a prior insufficient-data experiment only when its holdout was never evaluated and at least five additional sessions arrive. A seen holdout or a failed strategy still requires new future sessions; unchanged/revised old data remains blocked.
- Default research capital is ₹500,000, default history request 400 calendar days, with at most 24 grid combinations per job and the existing three-job daily budget. No risk budget was raised to meet the income target.

## Actual evidence

The first local-data pass across the 20 newly adapted families produced 18 FAIL and two INSUFFICIENT_DATA verdicts; no parameters were promoted. The initial ADX straddle attempt used the wrong timeframe, was diagnosed and is excluded from current evidence summaries. Its corrected 1-minute run also produced INSUFFICIENT_DATA, with no backtest errors. Further unchanged-data requests waited rather than retesting. Older native-family results remain insufficient and the unified income research report has zero qualifying paper candidates.

These changes do not establish ₹30,000 monthly income from ₹500,000. The remaining blockers are evidence and execution infrastructure: independent future executable quotes/fills, funding/margin verification, lifecycle reconciliation and a validated multi-leg execution mechanism. No service was restarted, no live order placed and no Telegram message sent by this work. Existing unrelated workspace changes were preserved.

Current machine-readable coverage: `reports/system_strategy_research/summary.json`. The earlier `REVIEW.md` is a historical snapshot of the previous implementation scope; this file supersedes its list of missing research adapters and catalog placeholders.

## Validation

635 tests passed in the broad offline regression run. The final catalogue/schema migration checks passed separately. All 648 top-level Python files parsed and critical deployment compilation passed. A fresh learning-cycle dry-run integration test passed in about 19 seconds. Broker/network/live-order test scripts were excluded. Runtime deployment and real-money readiness were not asserted.

The catalogue leg migration now excludes shadow catalogue/combination/short rows from fabricated generic buy-side entry/stop/target backfills. Actual leg sides and 1:2 ratios are preserved; Telegram leg diagrams display ratios.
