# Signal Reverse Engineering Report

- Generated: `2026-08-20T16:02:30+0530`
- Status: `READY`
- Rows: `35009`
- Labelled rows: `34756`
- Pending rows: `186`
- Labelled pct: `0.9928`
- Overall target/loss/timeout: `4650` / `7727` / `22379`
- Overall average return pct: `-0.0157`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=WEAK` n `63` target_rate `0.1905` avg_return `0.511`
- `strategy=chart_pattern_falling_wedge` n `64` target_rate `0.125` avg_return `0.4011`
- `strategy=awesome_osc` n `15` target_rate `0.2` avg_return `0.3576`
- `strategy=candlestick_three_river_evening_star` n `57` target_rate `0.1754` avg_return `0.3565`
- `strategy=candlestick_bullish_separating_lines` n `36` target_rate `0.1944` avg_return `0.3038`
- `strategy=candlestick_ladder_bottom` n `23` target_rate `0.087` avg_return `0.2574`
- `strategy=vwap_bands` n `47` target_rate `0.383` avg_return `0.2308`
- `strategy=vwap_reversion` n `77` target_rate `0.1948` avg_return `0.2298`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=elder_triple_screen` n `1228` target_rate `0.1164` avg_return `0.1297`

## Feature Lifts

- `pivot_boss_mod` positive_vs_silent lift `0.2221`
- `pivot_boss_mod` negative_vs_silent lift `0.2167`
- `news_mod` positive_vs_silent lift `0.1854`
- `market_quality_mod` negative_vs_silent lift `0.1632`
- `mtf_pivot_mod` negative_vs_silent lift `0.149`
- `structure_mod` negative_vs_silent lift `0.0683`
- `structure_mod` positive_vs_silent lift `0.0679`
- `weinstein_mod` negative_vs_silent lift `0.0661`
- `candidate_quality_mod` negative_vs_silent lift `0.0424`
- `sr_level_mod` negative_vs_silent lift `0.0276`
- `oi_mod` negative_vs_silent lift `0.0272`
- `expiry_mod` negative_vs_silent lift `0.0146`
- `sip_boost` positive_vs_silent lift `0.011`
- `cross_asset_mod` positive_vs_silent lift `0.0027`
- `cross_asset_mod` negative_vs_silent lift `0.0023`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `34753`; days `35`; live reversal `BLOCKED`
- `failed_bull_breakout` SHADOW_VALIDATED train/test `554/42` reverse OOS `1.3277%` positive test days `1.0`
- `weinstein_stage` SHADOW_VALIDATED train/test `274/44` reverse OOS `0.6679%` positive test days `1.0`
- `cci_trend` SHADOW_VALIDATED train/test `390/36` reverse OOS `0.4947%` positive test days `1.0`
- `cpr` SHADOW_VALIDATED train/test `488/35` reverse OOS `0.1719%` positive test days `1.0`
- `elder_ray` SHADOW_VALIDATED train/test `358/36` reverse OOS `0.1538%` positive test days `1.0`
- `price_structure` SHADOW_COLLECTING train/test `4558/107` reverse OOS `0.2293%` positive test days `0.5`

## Pending Signal Profile

- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `92`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `40`
- `validation_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `27`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `17`
- `edge_insufficient_data,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `3`
- `validation_fail,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `3`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
- `validation_fail,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `1`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `1`
