# Signal Reverse Engineering Report

- Generated: `2026-07-27T19:55:49+0530`
- Status: `READY`
- Rows: `32033`
- Labelled rows: `31467`
- Pending rows: `499`
- Labelled pct: `0.9823`
- Overall target/loss/timeout: `4425` / `7280` / `19762`
- Overall average return pct: `-0.0172`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `31` target_rate `0.1613` avg_return `0.7444`
- `strategy=vwap_reversion` n `46` target_rate `0.3043` avg_return `0.5477`
- `confluence=WEAK` n `44` target_rate `0.2045` avg_return `0.4513`
- `strategy=awesome_osc` n `12` target_rate `0.25` avg_return `0.4356`
- `strategy=candlestick_three_river_evening_star` n `44` target_rate `0.1818` avg_return `0.3886`
- `strategy=uo_overbought` n `41` target_rate `0.2195` avg_return `0.34`
- `strategy=vwap_bands` n `41` target_rate `0.439` avg_return `0.2432`
- `strategy=candlestick_ladder_bottom` n `16` target_rate `0.0625` avg_return `0.2416`
- `strategy=chart_pattern_diamond_bottom` n `67` target_rate `0.2388` avg_return `0.228`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`

## Feature Lifts

- `news_mod` positive_vs_silent lift `0.2242`
- `pivot_boss_mod` positive_vs_silent lift `0.212`
- `pivot_boss_mod` negative_vs_silent lift `0.2051`
- `market_quality_mod` negative_vs_silent lift `0.1882`
- `mtf_pivot_mod` negative_vs_silent lift `0.1366`
- `weinstein_mod` negative_vs_silent lift `0.0825`
- `structure_mod` positive_vs_silent lift `0.0742`
- `structure_mod` negative_vs_silent lift `0.0674`
- `sr_level_mod` negative_vs_silent lift `0.0493`
- `oi_mod` negative_vs_silent lift `0.0286`
- `expiry_mod` negative_vs_silent lift `0.0166`
- `candidate_quality_mod` negative_vs_silent lift `0.0104`
- `cross_asset_mod` negative_vs_silent lift `0.0048`
- `sip_boost` positive_vs_silent lift `-0.0062`
- `cross_asset_mod` positive_vs_silent lift `-0.008`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `31464`; days `26`; live reversal `BLOCKED`
- `failed_bull_breakout` SHADOW_VALIDATED train/test `397/183` reverse OOS `0.3194%` positive test days `0.625`
- `candlestick_spinning_top_white` SHADOW_VALIDATED train/test `100/38` reverse OOS `0.2714%` positive test days `0.8571`
- `rsi2_mr` SHADOW_VALIDATED train/test `164/51` reverse OOS `0.2557%` positive test days `0.75`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `151/81` reverse OOS `0.1305%` positive test days `0.625`
- `elder_ray` SHADOW_VALIDATED train/test `189/192` reverse OOS `0.0443%` positive test days `0.75`
- `candlestick_evening_star` SHADOW_COLLECTING train/test `109/46` reverse OOS `0.4075%` positive test days `0.5`
- `candlestick_morning_star` SHADOW_COLLECTING train/test `79/30` reverse OOS `0.2741%` positive test days `0.4286`
- `chart_pattern_rising_wedge` SHADOW_COLLECTING train/test `93/49` reverse OOS `0.1012%` positive test days `0.375`
- `cci_trend` SHADOW_COLLECTING train/test `150/262` reverse OOS `0.0481%` positive test days `0.5`

## Pending Signal Profile

- `edge_negative_edge,negative_generated_signal_edge,ai_prob_below_live_min,filter_score_below_live_min` count `339`
- `edge_negative_edge,ai_prob_below_live_min,filter_score_below_live_min` count `75`
- `edge_insufficient_data,ai_prob_below_live_min,filter_score_below_live_min` count `32`
- `missing_rigorous_validation,ai_prob_below_live_min,filter_score_below_live_min` count `20`
- `validation_insufficient_data,ai_prob_below_live_min,filter_score_below_live_min` count `17`
- `edge_negative_edge,negative_generated_signal_edge,score_below_live_min,ai_prob_below_live_min,filter_score_below_live_min` count `8`
- `edge_insufficient_data,score_below_live_min,ai_prob_below_live_min,filter_score_below_live_min` count `3`
- `strategy_missing_from_live_eligibility_manifest,ai_prob_below_live_min,filter_score_below_live_min` count `2`
- `edge_negative_edge,score_below_live_min,ai_prob_below_live_min,filter_score_below_live_min` count `2`
- `paper_only_strategy_expiry_scalp,ai_prob_below_live_min,filter_score_below_live_min` count `1`
