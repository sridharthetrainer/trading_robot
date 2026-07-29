# Signal Reverse Engineering Report

- Generated: `2026-07-30T03:13:47+0530`
- Status: `READY`
- Rows: `32656`
- Labelled rows: `32282`
- Pending rows: `307`
- Labelled pct: `0.9885`
- Overall target/loss/timeout: `4492` / `7398` / `20392`
- Overall average return pct: `-0.0174`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `42` target_rate `0.1667` avg_return `0.6243`
- `confluence=WEAK` n `50` target_rate `0.22` avg_return `0.561`
- `strategy=awesome_osc` n `12` target_rate `0.25` avg_return `0.4356`
- `strategy=candlestick_three_river_evening_star` n `45` target_rate `0.1778` avg_return `0.3781`
- `strategy=vwap_reversion` n `53` target_rate `0.2642` avg_return `0.3745`
- `strategy=uo_overbought` n `46` target_rate `0.2174` avg_return `0.3636`
- `strategy=candlestick_ladder_bottom` n `17` target_rate `0.0588` avg_return `0.2863`
- `strategy=chart_pattern_diamond_bottom` n `76` target_rate `0.25` avg_return `0.2322`
- `strategy=vwap_bands` n `43` target_rate `0.4186` avg_return `0.2139`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`

## Feature Lifts

- `pivot_boss_mod` positive_vs_silent lift `0.2193`
- `news_mod` positive_vs_silent lift `0.2166`
- `pivot_boss_mod` negative_vs_silent lift `0.2153`
- `market_quality_mod` negative_vs_silent lift `0.1824`
- `mtf_pivot_mod` negative_vs_silent lift `0.1226`
- `weinstein_mod` negative_vs_silent lift `0.0854`
- `structure_mod` positive_vs_silent lift `0.0778`
- `structure_mod` negative_vs_silent lift `0.0745`
- `sr_level_mod` negative_vs_silent lift `0.0479`
- `oi_mod` negative_vs_silent lift `0.0253`
- `sip_boost` positive_vs_silent lift `0.0142`
- `candidate_quality_mod` negative_vs_silent lift `0.0104`
- `expiry_mod` negative_vs_silent lift `0.0094`
- `cross_asset_mod` negative_vs_silent lift `0.0051`
- `cross_asset_mod` positive_vs_silent lift `-0.0022`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `32279`; days `28`; live reversal `BLOCKED`
- `failed_bull_breakout` SHADOW_VALIDATED train/test `408/188` reverse OOS `0.4326%` positive test days `0.7143`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `167/65` reverse OOS `0.2162%` positive test days `0.7143`
- `candlestick_spinning_top_white` SHADOW_VALIDATED train/test `104/41` reverse OOS `0.1026%` positive test days `0.8333`
- `cci_trend` SHADOW_VALIDATED train/test `166/260` reverse OOS `0.0639%` positive test days `0.7143`
- `candlestick_evening_star` SHADOW_COLLECTING train/test `123/38` reverse OOS `0.4725%` positive test days `0.4286`
- `candlestick_morning_star` SHADOW_COLLECTING train/test `87/28` reverse OOS `0.3653%` positive test days `0.5`
- `chart_pattern_rising_wedge` SHADOW_COLLECTING train/test `108/43` reverse OOS `0.1596%` positive test days `0.2857`
- `rsi2_mr` SHADOW_COLLECTING train/test `174/45` reverse OOS `0.113%` positive test days `0.5714`
- `cpr` SHADOW_COLLECTING train/test `360/163` reverse OOS `0.0112%` positive test days `0.7143`

## Pending Signal Profile

- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `117`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `86`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `46`
- `validation_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `42`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `6`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `5`
- `edge_insufficient_data,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `5`
