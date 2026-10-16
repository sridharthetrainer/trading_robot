# Signal Reverse Engineering Report

- Generated: `2026-10-15T20:27:08+0530`
- Status: `READY`
- Rows: `11104`
- Labelled rows: `11104`
- Pending rows: `0`
- Labelled pct: `1.0`
- Overall target/loss/timeout: `731` / `1363` / `9010`
- Overall average return pct: `0.0062`

## Best Context Edges

- `confluence=SINGLE` n `10` target_rate `0.3` avg_return `1.2015`
- `strategy=candlestick_three_river_evening_star` n `36` target_rate `0.2222` avg_return `0.6854`
- `strategy=chart_pattern_bull_flag` n `12` target_rate `0.4167` avg_return `0.6504`
- `strategy=candlestick_bullish_separating_lines` n `27` target_rate `0.2963` avg_return `0.5367`
- `strategy=candlestick_spinning_top_black` n `45` target_rate `0.2444` avg_return `0.4002`
- `confluence=WEAK` n `96` target_rate `0.1146` avg_return `0.371`
- `strategy=candlestick_bullish_marubozu` n `65` target_rate `0.1231` avg_return `0.2714`
- `strategy=uo_divergence` n `20` target_rate `0.15` avg_return `0.262`
- `strategy=oops` n `21` target_rate `0.0952` avg_return `0.262`
- `strategy=candlestick_tweezer_top` n `24` target_rate `0.1667` avg_return `0.2439`
- `strategy=chart_pattern_rectangle` n `13` target_rate `0.1538` avg_return `0.2301`
- `strategy=mean_reversion` n `88` target_rate `0.0795` avg_return `0.1569`
- `strategy=candlestick_dark_cloud_cover` n `13` target_rate `0.0` avg_return `0.1524`
- `strategy=supertrend_mtf` n `132` target_rate `0.0985` avg_return `0.1439`
- `strategy=chart_pattern_rising_wedge` n `29` target_rate `0.1034` avg_return `0.14`

## Feature Lifts

- `pivot_boss_mod` negative_vs_silent lift `0.5011`
- `pivot_boss_mod` positive_vs_silent lift `0.478`
- `news_mod` positive_vs_silent lift `0.2001`
- `market_quality_mod` negative_vs_silent lift `0.1838`
- `mtf_pivot_mod` negative_vs_silent lift `0.097`
- `sip_boost` positive_vs_silent lift `0.085`
- `ai_score` positive_vs_silent lift `0.05`
- `candidate_quality_mod` negative_vs_silent lift `0.0487`
- `cross_asset_mod` positive_vs_silent lift `0.0429`
- `expiry_mod` positive_vs_silent lift `0.0326`
- `sr_level_mod` negative_vs_silent lift `0.0307`
- `oi_mod` negative_vs_silent lift `0.0019`
- `expiry_mod` negative_vs_silent lift `0.0006`
- `cross_asset_mod` negative_vs_silent lift `-0.0161`
- `structure_mod` positive_vs_silent lift `-0.0236`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `11104`; days `31`; live reversal `BLOCKED`
- no chronologically stable reverse candidates yet

## Pending Signal Profile

- none
