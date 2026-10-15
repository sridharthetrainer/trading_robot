# Trading system audit — 14 October 2026

Audited repository code, dependency coverage, automated tests, local candle quality, recent runtime errors and trading-service status. Changes are in the working tree; running services were not restarted. Existing runtime files and unrelated edits were preserved. No live order or external message was sent by this audit.

## Fixes

- Angel login now returns immediately when required credentials are absent, before consuming a shared login slot or retrying authentication.
- Request pacing recovers from malformed, nonfinite and future-dated state. Clock rollback waits at most one configured interval. Negative/nonfinite intervals are rejected.
- Candle audits reject a scope containing missing requested symbols. The CLI returns a failure status when quality checks fail.
- The candle audit's default SQLite budget is now 60 seconds, configurable through `DATA_WATCHDOG_QUERY_BUDGET_SEC` or `--query-budget`. The full production cache needed approximately 41 seconds, so the old 15-second budget consistently produced incomplete results. Timeouts still fail closed; no readiness threshold was relaxed.
- Dependency checking resolves static local script search paths without executing repository code. It no longer recommends installing six local research modules from PyPI. Imports protected only by unrelated exception handlers or `finally` remain required dependencies.
- Pytest blocks unmocked requests HTTP traffic; Telegram calls retain harmless fake acknowledgements to prevent delivery or spooling. Recorder tests mock live direction lookups. The lifecycle test explicitly isolates transitions from focused-entry admission. Scoped-audit tests correctly retain unknown out-of-scope counts rather than requiring a full scan.

## Validation

- Broad isolated regression: **1,119 passed, 1 skipped, 6 warnings**, approximately 49 seconds. The skip was the momentum integration test because its historical database was initially omitted from the isolated copy.
- The skipped momentum integration test subsequently **passed** against an isolated copy of the 346 MB adjusted-return database. All 1,120 collected cases therefore passed across the broad run and this supplemental run.
- Final dependency/HTTP guard checks: **7 passed**.
- Final watchdog checks after the budget change: **11 passed**.
- **701 Python files** parsed successfully across the repository root, pattern engine and scripts.
- Repository-wide static scan: no undefined names or references before assignment. Existing cosmetic lint findings remain.
- Dependency coverage check passed; `pip check` found no broken installed requirements. Changed Python files pass whitespace checks.
- System trading bot, manual tracker and trading watchdog services were running. Recent bot errors included rejection of a fallback MIDCPNIFTY quote below the index floor; that rejection preserves the data-quality guard.

Test logs and complete candle findings are in [reports/system_audit_2026-10-14](reports/system_audit_2026-10-14/).

## Data findings and remaining blockers

The **active learning universe passed**: 194 requested symbols, 970 symbol/interval groups, 8,906,802 bars, no missing symbols, bad groups or stale groups. Full findings: `learning_candle_quality.json`.

The wider historical cache contains 8,951,974 bars across 991 groups. Its 21 stale groups belong to HEROMOTOCO, ICICIPRULI, OIL, SHREECEM and old TATAMOTORS daily history. These groups are outside the audited active learning scope. They were retained as historical evidence rather than deleted, renamed or fabricated into current data. Full findings: `candle_quality.json`. The quality checks audit sampled timestamp spacing and freshness, not every OHLC value or every database page.

The existing readiness report still blocks real-money readiness: no strategy passed live eligibility, no demonstrated after-cost statistical edge, no executed option-fill evidence, insufficient paired paper/live fills and labelled days, pending prospective execution evidence, and unverified release integrity. Its candle-watchdog failure was based on an incomplete prior scan; this audit provides separate completed results without overwriting that runtime report. Storage-catalog "interrupted" results are incomplete integrity checks, not proof of database corruption.

These evidence requirements cannot be cleared by changing pass flags. Broker execution, profitability and release integrity were not certified, and changes require a normal deployment/restart before existing Python service processes use them.
