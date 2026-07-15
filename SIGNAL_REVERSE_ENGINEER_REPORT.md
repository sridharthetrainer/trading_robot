# Signal Reverse Engineering Report

- Generated: `2026-07-15T19:44:40+0530`
- Status: `READY`
- Rows: `25613`
- Labelled rows: `25135`
- Pending rows: `411`
- Labelled pct: `0.9813`
- Overall target/loss/timeout: `3985` / `6388` / `14762`
- Overall average return pct: `-0.0199`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `cross_asset_bias=BULLISH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `22` target_rate `0.1364` avg_return `0.9531`
- `strategy=vwap_reversion` n `30` target_rate `0.3667` avg_return `0.741`
- `strategy=uo_overbought` n `25` target_rate `0.32` avg_return `0.4614`
- `strategy=orb` n `18` target_rate `0.3333` avg_return `0.282`
- `strategy=candlestick_ladder_bottom` n `12` target_rate `0.0833` avg_return `0.2725`
- `strategy=vwap_bands` n `38` target_rate `0.4737` avg_return `0.2384`
- `strategy=candlestick_bearish_harami` n `35` target_rate `0.0857` avg_return `0.2298`
- `strategy=chart_pattern_diamond_bottom` n `54` target_rate `0.2593` avg_return `0.2278`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`

## Feature Lifts

- `news_mod` positive_vs_silent lift `0.1881`
- `oi_mod` negative_vs_silent lift `0.1287`
- `weinstein_mod` negative_vs_silent lift `0.128`
- `mtf_pivot_mod` negative_vs_silent lift `0.1193`
- `structure_mod` negative_vs_silent lift `0.1048`
- `structure_mod` positive_vs_silent lift `0.0949`
- `candidate_quality_mod` negative_vs_silent lift `0.0833`
- `sr_level_mod` negative_vs_silent lift `0.0778`
- `pivot_boss_mod` positive_vs_silent lift `0.0729`
- `pivot_boss_mod` negative_vs_silent lift `0.0658`
- `market_quality_mod` negative_vs_silent lift `0.0653`
- `news_mod` negative_vs_silent lift `0.0514`
- `cross_asset_mod` negative_vs_silent lift `0.0226`
- `expiry_mod` negative_vs_silent lift `0.0199`
- `cross_asset_mod` positive_vs_silent lift `-0.0092`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `25132`; days `19`; live reversal `BLOCKED`
- `supertrend_mtf` SHADOW_COLLECTING train/test `77/28` reverse OOS `1.0208%` positive test days `0.8`
- `candlestick_spinning_top_white` SHADOW_COLLECTING train/test `78/22` reverse OOS `0.7865%` positive test days `0.5`
- `vrvp_zone` SHADOW_COLLECTING train/test `735/157` reverse OOS `0.6514%` positive test days `1.0`
- `rsi2_mr` SHADOW_COLLECTING train/test `109/58` reverse OOS `0.3843%` positive test days `0.8333`
- `cpr` SHADOW_COLLECTING train/test `232/114` reverse OOS `0.3527%` positive test days `0.5`
- `cci_trend` SHADOW_COLLECTING train/test `95/69` reverse OOS `0.3387%` positive test days `0.3333`
- `chart_pattern_range_expansion` SHADOW_COLLECTING train/test `97/61` reverse OOS `0.2733%` positive test days `0.5`
- `candlestick_evening_star` SHADOW_COLLECTING train/test `76/41` reverse OOS `0.2113%` positive test days `1.0`
- `chart_pattern_inverse_head_shoulders` SHADOW_COLLECTING train/test `407/87` reverse OOS `0.189%` positive test days `0.6667`
- `chart_pattern_double_top` SHADOW_COLLECTING train/test `253/117` reverse OOS `0.1671%` positive test days `0.6667`

## Pending Signal Profile

- `edge_negative_edge,negative_generated_signal_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `210`
- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `107`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `71`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `13`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `10`
