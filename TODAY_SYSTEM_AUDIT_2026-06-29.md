# System and Data Audit - 2026-06-29

## Executive Result

- Runtime: trading bot, manual tracker, and watchdog are active.
- Tests: 250 passed; 8 SmartAPI dependency deprecation warnings.
- Scaled-live decision: BLOCKED by evidence, not infrastructure.
- Actual trades today: 0; booked P&L today: INR 0.
- Existing closed paper trades: 12; cumulative net P&L: INR -625.47.

## Today Data

- Generated signals: 1,805 across 187 symbols; executed: 0.
- Clean labelled signals: 1,173 from one market day.
- Outcomes: 38 target hits, 115 stop hits, 1,020 timeouts, 630 pending.
- Clean aggregate net R: -147.0995; average net R: -0.1254.
- Option strike signals: 420; 251 labelled from later premium observations.
- Option shadow outcomes: 125 wins, 126 losses; net simulated P&L: INR -12,010.64.
- Today is not profitable on generated-signal evidence. No booked live P&L exists.

## Total Stored Data

- Evidence catalog: 17 databases, 30 tables, 5,364,457 rows, no catalog issues.
- Candles: 1,973,205 rows across 197 symbols.
- 1m: 1,551,647 rows / 192 symbols.
- 5m: 196,766 rows / 194 symbols.
- 15m: 108,892 rows / 193 symbols.
- 1h: 57,247 rows / 191 symbols.
- 1d: 58,653 rows / 196 symbols.
- Historical options: 3,145,541 rows, 2020-01-01 through 2026-06-25.
- Option snapshots: 1,119; market-profile snapshots: 33,697.

## Defects Fixed

1. Blocked a MIDCPNIFTY symbol-resolution error that mixed an ETF-like price near 880 with the index near 14,300.
2. Preserved 2,926 contaminated candles in `quarantined_candles` and removed them from the usable cache.
3. Quarantined eight historical cross-scale signal labels and excluded them from every learner.
4. Added label-time price-scale validation so corrupt outcomes are retired as `tb_label=-2`.
5. Corrected Upstox V2 interval handling: 5m/15m/1h bars are now explicitly resampled from supported base intervals.
6. Wired Angel FULL quote volume, bid/ask, and depth quantities through option-chain storage and strike scoring.
7. Made sparse after-hours option caches refresh from authenticated Angel data.
8. Corrected the option audit to count generated strike signals and verified strike outcomes.
9. Labelled 251 source-attributed option outcomes and generated bounded flow weights.
10. Fixed manual-tracker handling of Angel `AG8003 Token missing`; it now marks the session down and reconnects.
11. Rebuilt and verified the SHA-256 release manifest for 515 executable files.

## Scores Before and After

| Area | Before | After | Evidence |
|---|---:|---:|---|
| Option bot official | 59/F | 88/B | 420 signals, 251 verified outcomes |
| Data integrity | 76/C | 92/A- | corrupt candles/labels quarantined, interval truth fixed |
| Python/code health | 90/A- | 96/A | 250 tests passed, compile checks passed |
| Manual tracker operational | 70/C | 95/A | authenticated reconnect verified after restart |
| Data pipeline official | 95/A | 91.5/A | stricter audit exposes paper mode and evidence gaps |
| Institutional raw capability | 78/B | 78/B | infrastructure unchanged |
| Institutional evidence-adjusted | 59/F | 59/F | only 1/15 clean days; no after-cost edge |
| Autonomous option wiring | 100/A | 100/A | all scheduled loops and tools wired |

## Remaining Blocks

- No strategy has passed the live-eligibility manifest: 0/94.
- Clean training evidence is 1,173/5,000 labels and 1/15 market days.
- No statistically defensible after-cost edge exists yet.
- Fifteen candle groups are stale after market close and should be rechecked next session.
- Live size must remain zero until promotion gates pass; more signals alone do not create edge.
