# Signal Reverse Engineering Report

- Generated: `2026-07-23T19:58:13+0530`
- Status: `READY`
- Rows: `30962`
- Labelled rows: `29998`
- Pending rows: `897`
- Labelled pct: `0.9689`
- Overall target/loss/timeout: `4256` / `7009` / `18733`
- Overall average return pct: `-0.0169`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `29` target_rate `0.1724` avg_return `0.8129`
- `strategy=vwap_reversion` n `42` target_rate `0.2857` avg_return `0.5175`
- `strategy=uo_overbought` n `32` target_rate `0.25` avg_return `0.4279`
- `strategy=candlestick_three_river_evening_star` n `43` target_rate `0.186` avg_return `0.3989`
- `strategy=candlestick_ladder_bottom` n `12` target_rate `0.0833` avg_return `0.2725`
- `confluence=WEAK` n `38` target_rate `0.1316` avg_return `0.2504`
- `strategy=vwap_bands` n `40` target_rate `0.45` avg_return `0.2435`
- `strategy=chart_pattern_diamond_bottom` n `66` target_rate `0.2424` avg_return `0.2103`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=volume_profile_full` n `36` target_rate `0.1389` avg_return `0.1491`

## Feature Lifts

- `news_mod` positive_vs_silent lift `0.2382`
- `pivot_boss_mod` positive_vs_silent lift `0.2119`
- `pivot_boss_mod` negative_vs_silent lift `0.2063`
- `market_quality_mod` negative_vs_silent lift `0.1908`
- `news_mod` negative_vs_silent lift `0.1454`
- `mtf_pivot_mod` negative_vs_silent lift `0.1424`
- `structure_mod` negative_vs_silent lift `0.0848`
- `structure_mod` positive_vs_silent lift `0.0766`
- `weinstein_mod` negative_vs_silent lift `0.074`
- `sr_level_mod` negative_vs_silent lift `0.0546`
- `candidate_quality_mod` negative_vs_silent lift `0.0516`
- `oi_mod` negative_vs_silent lift `0.0343`
- `expiry_mod` negative_vs_silent lift `0.0264`
- `cross_asset_mod` negative_vs_silent lift `0.0095`
- `cross_asset_mod` positive_vs_silent lift `-0.0094`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `29995`; days `24`; live reversal `BLOCKED`
- `rsi2_mr` SHADOW_VALIDATED train/test `131/66` reverse OOS `0.3285%` positive test days `0.625`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `128/83` reverse OOS `0.3076%` positive test days `0.75`
- `supertrend_mtf` SHADOW_VALIDATED train/test `79/144` reverse OOS `0.1434%` positive test days `0.625`
- `mean_reversion` SHADOW_VALIDATED train/test `277/185` reverse OOS `0.1367%` positive test days `0.625`
- `cci_trend` SHADOW_VALIDATED train/test `111/230` reverse OOS `0.1068%` positive test days `0.625`
- `chart_pattern_rising_wedge` SHADOW_COLLECTING train/test `69/64` reverse OOS `0.1171%` positive test days `0.4286`
- `cpr` SHADOW_COLLECTING train/test `252/224` reverse OOS `0.0962%` positive test days `0.5`
- `aroon_trend` SHADOW_COLLECTING train/test `163/144` reverse OOS `0.0561%` positive test days `0.5`

## Pending Signal Profile

- `edge_negative_edge,negative_generated_signal_edge,ai_prob_below_live_min,filter_score_below_live_min` count `312`
- `edge_negative_edge,negative_generated_signal_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `288`
- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `106`
- `edge_negative_edge,ai_prob_below_live_min,filter_score_below_live_min` count `88`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `35`
- `edge_insufficient_data,ai_prob_below_live_min,filter_score_below_live_min` count `27`
- `missing_rigorous_validation,ai_prob_below_live_min,filter_score_below_live_min` count `16`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `8`
- `edge_negative_edge,negative_generated_signal_edge,score_below_live_min,ai_prob_below_live_min,filter_score_below_live_min` count `5`
- `edge_negative_edge,negative_generated_signal_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `3`
