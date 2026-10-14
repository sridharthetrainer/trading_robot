# Signal Reverse Engineering Report

- Generated: `2026-10-14T06:53:53+0530`
- Status: `READY`
- Rows: `11975`
- Labelled rows: `11969`
- Pending rows: `6`
- Labelled pct: `0.9995`
- Overall target/loss/timeout: `771` / `1503` / `9695`
- Overall average return pct: `-0.0`

## Best Context Edges

- `confluence=SINGLE` n `10` target_rate `0.3` avg_return `1.1925`
- `strategy=chart_pattern_bull_flag` n `11` target_rate `0.4545` avg_return `0.6957`
- `strategy=candlestick_three_river_evening_star` n `36` target_rate `0.2222` avg_return `0.6854`
- `strategy=candlestick_bullish_separating_lines` n `28` target_rate `0.2857` avg_return `0.5056`
- `confluence=WEAK` n `93` target_rate `0.129` avg_return `0.4`
- `strategy=candlestick_spinning_top_black` n `56` target_rate `0.1964` avg_return `0.3498`
- `strategy=candlestick_tweezer_top` n `28` target_rate `0.1786` avg_return `0.3142`
- `strategy=oops` n `21` target_rate `0.0952` avg_return `0.262`
- `strategy=candlestick_bullish_marubozu` n `59` target_rate `0.1017` avg_return `0.2461`
- `strategy=uo_divergence` n `17` target_rate `0.1765` avg_return `0.2188`
- `confluence=MEDIUM` n `268` target_rate `0.0821` avg_return `0.1572`
- `strategy=candlestick_dark_cloud_cover` n `13` target_rate `0.0` avg_return `0.1524`
- `strategy=chart_pattern_rectangle` n `15` target_rate `0.1333` avg_return `0.1484`
- `strategy=candlestick_fred_tam_white_inside_out` n `82` target_rate `0.0854` avg_return `0.1385`
- `strategy=anchored_vwap` n `34` target_rate `0.0294` avg_return `0.123`

## Feature Lifts

- `pivot_boss_mod` negative_vs_silent lift `1.0184`
- `pivot_boss_mod` positive_vs_silent lift `1.0036`
- `market_quality_mod` negative_vs_silent lift `0.1905`
- `news_mod` positive_vs_silent lift `0.1572`
- `mtf_pivot_mod` negative_vs_silent lift `0.1057`
- `sip_boost` positive_vs_silent lift `0.0912`
- `candidate_quality_mod` negative_vs_silent lift `0.0504`
- `structure_mod` positive_vs_silent lift `0.0491`
- `sr_level_mod` negative_vs_silent lift `0.0467`
- `ai_score` positive_vs_silent lift `0.042`
- `expiry_mod` positive_vs_silent lift `0.0403`
- `cross_asset_mod` positive_vs_silent lift `0.027`
- `oi_mod` negative_vs_silent lift `0.0256`
- `structure_mod` negative_vs_silent lift `0.0211`
- `expiry_mod` negative_vs_silent lift `0.0083`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `11969`; days `31`; live reversal `BLOCKED`
- `failed_bear_breakout` SHADOW_COLLECTING train/test `540/62` reverse OOS `0.1762%` positive test days `0.5`

## Pending Signal Profile

- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
- `missing_rigorous_validation,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
