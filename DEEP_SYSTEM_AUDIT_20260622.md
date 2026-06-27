# Deep System Audit - 2026-06-22

Generated: 2026-06-22 21:07 IST

## Executive Status

- Services: `trading-bot.service` active, `manual-tracker.service` active.
- Broker: Angel One connected.
- Trading mode: PAPER. Live execution remains blocked by eligibility gates.
- Telegram: treated as pending external setup; code now classifies network/API failure correctly.
- Full smoke test: 118 passed, 0 failed.
- Focused tests: intraday/data/system tests passed.

## Scores

- Python/code health: A-, full syntax/import smoke clean across 429 Python files.
- Option bot: 94/100, grade A.
- Autonomous option wiring: 100/100, grade A.
- Data pipeline: 91.5/100, grade A.
- Institutional readiness: 76/100, grade B.
- Live readiness: blocked, 0/82 strategies live-ready.

## Critical Findings

1. Live trading is not ready.
   - Blocks: `no_live_ready_strategy`, `labelled_days_below_target`.
   - Current labels: 3641/5000.
   - Current labelled days: 3/15.

2. Intraday candle coverage is still the largest system gap.
   - `candle_cache.db` integrity is OK.
   - Stale/bad candle groups after partial repair: 204 bad, 203 stale, 407 total.
   - Today rows were successfully fetched and saved for sampled NIFTY, BANKNIFTY, RELIANCE, and TCS after fixes.

3. Previous recorder reports overstated saved rows.
   - Cause: direct fetch frames could arrive with uppercase OHLCV columns.
   - `candle_cache.save_candles` expects lowercase columns and rejected rows.
   - Fixed: recorder and fetcher normalize OHLCV columns before saving, and recorder reports actual inserted row count.

4. Systemd restart remains imperfect.
   - Direct process reload works and service restarts.
   - `systemctl restart trading-bot.service` still timed out once.
   - SIGTERM handler was improved by removing blocking Telegram shutdown calls, but systemd control behavior still needs follow-up.

5. Option bot is structurally strong, but today option signal activity is weak.
   - Option chain snapshot is fresh: latest OK `2026-06-22T15:34:56+0530`.
   - Option bot audit gap: `today_option_rows=0`, `executed=0`.

6. NSE direct option-chain feed needs a proxy for reliability.
   - Runtime warning: 3 NSE-direct feeds blocked without `NSE_PROXY`.
   - Not fatal due fallback architecture, but not institutional-grade.

7. WebSocket is unavailable.
   - Runtime falls back to REST polling for stop-loss checks.
   - REST fallback works, but fast SL/trailing SL quality is lower than websocket.

## Fixes Completed In This Audit Pass

- `data_fetcher.py`
  - Rejects daily-shaped bars for intraday requests.
  - Keeps intraday requests intraday after market close.
  - Normalizes OHLCV before candle-cache persistence.

- `intraday_candle_recorder.py`
  - Requires same-day candles by default.
  - Retries shorter lookback windows.
  - Builds 5m/15m/30m/1h from valid 1m data.
  - Saves normalized OHLCV.
  - Reports actual inserted rows.

- `data_quality_watchdog.py`
  - Adds intraday freshness scoring.
  - Reports stale candle groups.

- `system_readiness_report.py`
  - Exposes stale candle groups in readiness summary.
  - Adds `intraday_candle_cache_stale` warning.

- `autonomous_learning_cycle.py`
  - Includes stale candle count in learning-cycle data quality result.

- `telegram_commands.py`
  - Separates Telegram auth/token errors from connectivity/API failures.

- `main_autonomous.py`
  - Removes blocking Telegram shutdown alert from SIGTERM handler.

- `test_intraday_candle_recorder.py`
  - Adds regression coverage for stale bars, interval mismatch, resampling, fetcher interval validation, and OHLCV normalization before cache save.

## Verification

- `.venv/bin/python3 test_all_files.py`
  - 118 passed, 0 failed, 3 warnings.

- `.venv/bin/python3 -m pytest -q test_intraday_candle_recorder.py test_data.py test_system.py`
  - 25 passed.

- Database integrity:
  - `candle_cache.db`: OK.
  - `signal_log.db`: OK.
  - `trades.db`: OK.
  - `option_chain_snapshots.db`: OK.
  - `manual_trades.db`: OK.
  - `experiments.db`: OK.

## Next Priorities

1. Run full intraday recorder/backfill for all symbols during or after market hours.
2. Investigate `systemctl restart trading-bot.service` timeout at the unit/systemd level.
3. Add `NSE_PROXY` for option-chain reliability.
4. Restore SmartWebSocketV2 if possible.
5. Keep collecting labelled days until 15/15 target is met.
6. Investigate why today option signal rows are zero despite fresh option snapshots.
