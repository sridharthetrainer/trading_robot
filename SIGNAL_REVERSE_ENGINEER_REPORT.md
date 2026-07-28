# Signal Reverse Engineering Report

- Generated: `2026-07-28T22:30:53+0530`
- Status: `READY`
- Rows: `32320`
- Labelled rows: `31911`
- Pending rows: `342`
- Labelled pct: `0.9873`
- Overall target/loss/timeout: `4467` / `7347` / `20097`
- Overall average return pct: `-0.0169`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `34` target_rate `0.2059` avg_return `0.8013`
- `confluence=WEAK` n `49` target_rate `0.2245` avg_return `0.5518`
- `strategy=vwap_reversion` n `50` target_rate `0.28` avg_return `0.4558`
- `strategy=awesome_osc` n `12` target_rate `0.25` avg_return `0.4356`
- `strategy=candlestick_three_river_evening_star` n `44` target_rate `0.1818` avg_return `0.3886`
- `strategy=uo_overbought` n `42` target_rate `0.2381` avg_return `0.3847`
- `strategy=candlestick_ladder_bottom` n `17` target_rate `0.0588` avg_return `0.2863`
- `strategy=chart_pattern_diamond_bottom` n `69` target_rate `0.2464` avg_return `0.2542`
- `strategy=vwap_bands` n `41` target_rate `0.439` avg_return `0.2432`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`

## Feature Lifts

- `pivot_boss_mod` positive_vs_silent lift `0.2205`
- `pivot_boss_mod` negative_vs_silent lift `0.2146`
- `news_mod` positive_vs_silent lift `0.2077`
- `market_quality_mod` negative_vs_silent lift `0.1872`
- `mtf_pivot_mod` negative_vs_silent lift `0.1323`
- `weinstein_mod` negative_vs_silent lift `0.0862`
- `structure_mod` positive_vs_silent lift `0.083`
- `structure_mod` negative_vs_silent lift `0.079`
- `sr_level_mod` negative_vs_silent lift `0.0468`
- `oi_mod` negative_vs_silent lift `0.0286`
- `sip_boost` positive_vs_silent lift `0.0167`
- `candidate_quality_mod` negative_vs_silent lift `0.0117`
- `expiry_mod` negative_vs_silent lift `0.0114`
- `cross_asset_mod` negative_vs_silent lift `0.0053`
- `cross_asset_mod` positive_vs_silent lift `-0.0026`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `31908`; days `27`; live reversal `BLOCKED`
- `failed_bull_breakout` SHADOW_VALIDATED train/test `397/194` reverse OOS `0.3843%` positive test days `0.625`
- `rsi2_mr` SHADOW_VALIDATED train/test `164/55` reverse OOS `0.2235%` positive test days `0.625`
- `candlestick_spinning_top_white` SHADOW_VALIDATED train/test `100/43` reverse OOS `0.2056%` positive test days `0.8571`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `151/81` reverse OOS `0.1305%` positive test days `0.625`
- `cci_trend` SHADOW_VALIDATED train/test `150/271` reverse OOS `0.0563%` positive test days `0.625`
- `elder_ray` SHADOW_VALIDATED train/test `189/199` reverse OOS `0.0337%` positive test days `0.625`
- `candlestick_evening_star` SHADOW_COLLECTING train/test `109/51` reverse OOS `0.3749%` positive test days `0.5`
- `candlestick_morning_star` SHADOW_COLLECTING train/test `79/33` reverse OOS `0.3039%` positive test days `0.5`
- `chart_pattern_rising_wedge` SHADOW_COLLECTING train/test `93/54` reverse OOS `0.0736%` positive test days `0.25`

## Pending Signal Profile

- `edge_negative_edge,negative_generated_signal_edge,ai_prob_below_live_min,filter_score_below_live_min` count `108`
- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `67`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `56`
- `validation_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `31`
- `edge_negative_edge,ai_prob_below_live_min,filter_score_below_live_min` count `30`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `23`
- `edge_insufficient_data,ai_prob_below_live_min,filter_score_below_live_min` count `8`
- `missing_rigorous_validation,ai_prob_below_live_min,filter_score_below_live_min` count `6`
- `validation_insufficient_data,ai_prob_below_live_min,filter_score_below_live_min` count `5`
- `edge_negative_edge,negative_generated_signal_edge,score_below_live_min,ai_prob_below_live_min,filter_score_below_live_min` count `3`
