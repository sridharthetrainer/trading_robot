# Data Pipeline Audit

- Generated: `2026-06-19T06:21:10+0530`
- Status: `PASS`
- Fetch sample: `0`
- Internet probe: `False`
- Audit score: `89.5/100` grade `B` readiness `PAPER_OR_SHADOW`

## Score

- `source_coverage` `20.0/20` - 11/11 source domains covered
- `broker_runtime` `15/15` - brokers=AngelOne
- `fetcher_universe` `15/15` - ordered=194, full=194, learning=78
- `storage_capture` `14.5/15` - candles=126703, signals=3563, option_snaps=9, historical_options=3122237, confluence=3562
- `learning_readiness` `2.0/10` - live_ready=0/64, selected=1, shadow=0
- `sample_fetch` `15/15` - skipped; run with --fetch-sample N for live pull validation
- `freshness_reachability` `8.0/10` - market-hours freshness plus official-source reachability

## Improvement Priorities

1. Optional: Dhan option-chain API can add Greeks, bid/ask and richer all-strike data.
2. Increase successful intraday option-chain snapshots; this is the biggest gap for strike-flow learning.
3. Build live-ready strategy labels through shadow/live journaling before increasing size.
4. Collect shadow option strike outcomes so autotune can compare selected vs missed strikes.

## Checks

- `PASS` `import:config`
- `PASS` `import:universe_manager`
- `PASS` `import:data_fetcher`
- `PASS` `import:live_signal_engine`
- `PASS` `import:option_chain_fetcher`
- `PASS` `source_config`
  - angel primary `True`, configured backups `none`, disable yfinance `True`
- `PASS` `internet_source_catalog`
  - covered `11/11`, critical gaps `none`, optional gaps `none`
  - `intraday_price_bars` covered: Angel SmartAPI historical candle data (local `3`/3`)
  - `nse_option_chain` covered: NSE option-chain endpoint with Angel/Sensibull fallback (local `3`/3`)
  - `bse_sensex_bfo` covered: BSE public APIs and Angel BSE/BFO historical access (local `3`/3`)
  - `participant_oi` covered: NSE derivative reports (local `3`/3`)
  - `fii_dii_flows` covered: NSE FII/FPI & DII reports (local `3`/3`)
  - `bulk_block_deals` covered: NSE bulk/block archives (local `2`/2`)
  - `nse_full_market_reports` covered: Unified NSE data hub backed by official NSE reports/endpoints (local `2`/2`)
  - `corporate_actions` covered: BSE/NSE corporate announcement feeds (local `2`/2`)
  - `news_sentiment` covered: NewsAPI key with cached/free fallback (local `2`/3`)
  - `option_chain_addon_candidate` covered: DhanHQ option-chain API if credentials are intentionally enabled (local `1`/1`)
  - `alerts_and_backup` covered: Telegram Bot API and rclone Google Drive remote (local `3`/3`)
  - recommendations:
    - Optional: Dhan option-chain API can add Greeks, bid/ask and richer all-strike data.
- `PASS` `fetcher_fallback_wiring`
  - fetcher methods `6`, fallback modules `7`
- `PASS` `runtime_broker_wiring`
  - brokers `AngelOne`, angel attached `True`, connected `True`, paper_trade `False`
- `PASS` `nifty200.csv`
- `PASS` `universe`
  - learning `78` mode `all`, probation `NIFTY,BANKNIFTY,SENSEX`
- `PASS` `data_fetcher_wiring`
  - ordered symbols `194`, full tier `194`, angel attached `False`
- `PASS` `option_fetchers`
- `PASS` `storage`
  - trades `12`, signals `3563`, candles `126703`, candle meta `240`, option snaps `9`, historical options `3122237`, market snaps `2`, confluence `3562`, nse hub `10/10`, journal exists `True`
- `PASS` `learning_files`
  - live-ready `0/64`, autotune selected `1`, shadow `0`
- `PASS` `fetch_sample`
  - ok `0/0`
- `PASS` `market_hours_freshness` - outside_market_hours
  - skipped `outside_market_hours`
