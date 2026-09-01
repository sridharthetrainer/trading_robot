# Data Pipeline Audit

- Generated: `2026-08-21T08:38:58+0530`
- Status: `PASS`
- Fetch sample: `3`
- Internet probe: `True`
- Audit score: `95.5/100` grade `A` readiness `LIVE_READY`
- Institutional readiness: `83.0/100` grade `A` state `BUILDING`

## Score

- `source_coverage` `20.0/20` - 18/18 source domains covered
- `broker_runtime` `15/15` - brokers=AngelOne
- `fetcher_universe` `15/15` - ordered=194, full=194, learning=194
- `storage_capture` `15/15` - candles=5537036, 1m_symbols=197, 1d_symbols=198, signals=35009, option_snaps=9018, historical_options=3209189, confluence=35009, coverage_plan=yes
- `learning_readiness` `6.5/10` - live_ready=0/110, selected=320, shadow=2560, param_promoted=0, param_paper=3, fill_trades=12, derived_daily_symbols=197, bad_candle_groups=833/986, experiments=46
- `sample_fetch` `15.0/15` - 3/3 sample fetches OK
- `freshness_reachability` `9.0/10` - market-hours freshness plus official-source reachability

## Improvement Priorities

1. Optional: Dhan option-chain API can add Greeks, bid/ask and richer all-strike data.
2. Build live-ready strategy labels through shadow/live journaling before increasing size.
3. Parameter trainer is wired; keep collecting data until a parameter set clears DSR and holdout gates.

## Institutional Readiness

- `tick_or_1sec_data` `8/10` - tick modules=True, candles=5537036
- `all_strike_option_history` `14/15` - option_snapshots=9018, historical_options=3209189
- `market_depth_spread` `8/10` - depth modules present; live depth depends on broker entitlement
- `execution_fill_quality` `8/12` - trades=12, fill_report_trades=12, matched_slippage=3, order_id_coverage=100.0%
- `labelled_learning_dataset` `10/18` - labelled=25676/5000, days=28/45
- `participant_fii_flows` `10/10` - participant OI plus FII/DII flow coverage
- `market_profile_history` `8/8` - profile_snapshots=409111
- `sector_news_events` `7/7` - sector breadth, news and corporate event coverage
- `vol_surface_skew` `6/6` - IV/skew modules require all-strike snapshots for full edge
- `broker_latency_health` `4/4` - health monitor modules present; live latency history depends on runtime

## Institutional Blockers

1. Need 45 labelled signal days and about 5000 labels before trusting institutional ML weights.
2. Need at least 100 paired paper/live fill comparisons before execution readiness can be trusted.
3. No after-cost statistically confirmed strategy edge; research remains paper-only.
4. No strategy has passed the live-eligibility manifest.

## Checks

- `PASS` `import:config`
- `PASS` `import:universe_manager`
- `PASS` `import:data_fetcher`
- `PASS` `import:live_signal_engine`
- `PASS` `import:option_chain_fetcher`
- `PASS` `source_config`
  - angel primary `True`, configured backups `none`, disable yfinance `True`
- `PASS` `internet_source_catalog`
  - covered `18/18`, critical gaps `none`, optional gaps `none`
  - `intraday_price_bars` covered: Angel SmartAPI historical candle data (local `3`/3`)
  - `tick_or_1sec_data` covered: Broker websocket tick feed captured to local storage (local `4`/4`)
  - `nse_option_chain` covered: NSE option-chain endpoint with Angel/Sensibull fallback (local `3`/3`)
  - `bse_sensex_bfo` covered: BSE public APIs and Angel BSE/BFO historical access (local `3`/3`)
  - `participant_oi` covered: NSE derivative reports (local `3`/3`)
  - `fii_dii_flows` covered: NSE FII/FPI & DII reports (local `3`/3`)
  - `bulk_block_deals` covered: NSE bulk/block archives (local `2`/2`)
  - `nse_full_market_reports` covered: Unified NSE data hub backed by official NSE reports/endpoints (local `2`/2`)
  - `corporate_actions` covered: BSE/NSE corporate announcement feeds (local `2`/2`)
  - `news_sentiment` covered: NewsAPI key with cached/free fallback (local `3`/3`)
  - `option_chain_addon_candidate` covered: DhanHQ option-chain API if credentials are intentionally enabled (local `1`/1`)
  - `market_depth_orderbook` covered: Broker quote/depth API or websocket depth packets (local `4`/4`)
  - recommendations:
    - Optional: Dhan option-chain API can add Greeks, bid/ask and richer all-strike data.
- `PASS` `fetcher_fallback_wiring`
  - fetcher methods `6`, fallback modules `7`
- `PASS` `runtime_broker_wiring`
  - brokers `AngelOne`, angel attached `True`, connected `True`, paper_trade `False`
- `PASS` `nifty200.csv`
- `PASS` `universe`
  - learning `194` mode `all`, probation `NIFTY,BANKNIFTY,SENSEX`
- `PASS` `data_fetcher_wiring`
  - ordered symbols `194`, full tier `194`, angel attached `False`
- `PASS` `option_fetchers`
- `PASS` `storage`
  - trades `12`, signals `35009`, candles `5537036`, candle meta `987`, option snaps `9018`, profile snaps `409111`, historical options `3209189`, market snaps `435`, confluence `35009`, nse hub `10/10`, journal exists `True`
- `PASS` `learning_files`
  - live-ready `0/110`, autotune selected `320`, shadow `2560`
- `PASS` `labelled_dataset`
  - labelled `25676/35009`, days `28`, executed `3`
- `PASS` `fetch_sample`
  - ok `3/3`
- `PASS` `market_hours_freshness` - outside_market_hours
  - skipped `outside_market_hours`
- `PASS` `internet_source_reachability`
  - reachable `12/12`
