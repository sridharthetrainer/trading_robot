# Signal Reverse Engineering Report

- Generated: `2026-08-06T19:44:01+0530`
- Status: `READY`
- Rows: `33262`
- Labelled rows: `33077`
- Pending rows: `118`
- Labelled pct: `0.9944`
- Overall target/loss/timeout: `4547` / `7546` / `20984`
- Overall average return pct: `-0.0188`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=WEAK` n `54` target_rate `0.2037` avg_return `0.5256`
- `strategy=chart_pattern_falling_wedge` n `53` target_rate `0.1321` avg_return `0.4361`
- `strategy=awesome_osc` n `13` target_rate `0.2308` avg_return `0.4126`
- `strategy=candlestick_ladder_bottom` n `21` target_rate `0.0952` avg_return `0.3254`
- `strategy=vwap_reversion` n `63` target_rate `0.2222` avg_return `0.3093`
- `strategy=candlestick_three_river_evening_star` n `50` target_rate `0.16` avg_return `0.2901`
- `strategy=uo_overbought` n `54` target_rate `0.1852` avg_return `0.2556`
- `strategy=vwap_bands` n `46` target_rate `0.3913` avg_return `0.2221`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=chart_pattern_diamond_bottom` n `83` target_rate `0.2289` avg_return `0.1765`

## Feature Lifts

- `pivot_boss_mod` positive_vs_silent lift `0.2189`
- `pivot_boss_mod` negative_vs_silent lift `0.212`
- `news_mod` positive_vs_silent lift `0.1867`
- `market_quality_mod` negative_vs_silent lift `0.1689`
- `mtf_pivot_mod` negative_vs_silent lift `0.1497`
- `weinstein_mod` negative_vs_silent lift `0.0815`
- `structure_mod` positive_vs_silent lift `0.0731`
- `structure_mod` negative_vs_silent lift `0.0724`
- `candidate_quality_mod` negative_vs_silent lift `0.028`
- `sr_level_mod` negative_vs_silent lift `0.019`
- `oi_mod` negative_vs_silent lift `0.0186`
- `sip_boost` positive_vs_silent lift `0.0156`
- `expiry_mod` negative_vs_silent lift `0.0123`
- `cross_asset_mod` positive_vs_silent lift `0.0013`
- `cross_asset_mod` negative_vs_silent lift `-0.0015`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `33074`; days `30`; live reversal `BLOCKED`
- `failed_bull_breakout` SHADOW_VALIDATED train/test `474/122` reverse OOS `0.5132%` positive test days `0.6`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `177/55` reverse OOS `0.1948%` positive test days `0.6`
- `rsi2_mr` SHADOW_VALIDATED train/test `190/29` reverse OOS `0.1899%` positive test days `0.8`
- `cci_trend` SHADOW_VALIDATED train/test `257/169` reverse OOS `0.0857%` positive test days `0.6`
- `candlestick_spinning_top_white` SHADOW_VALIDATED train/test `125/20` reverse OOS `0.076%` positive test days `0.75`
- `aroon_trend` SHADOW_VALIDATED train/test `273/68` reverse OOS `0.057%` positive test days `0.8`
- `candlestick_evening_star` SHADOW_COLLECTING train/test `140/21` reverse OOS `1.0581%` positive test days `0.4`
- `candlestick_morning_star` SHADOW_COLLECTING train/test `97/22` reverse OOS `0.2927%` positive test days `0.5`
- `cpr` SHADOW_COLLECTING train/test `400/123` reverse OOS `0.0247%` positive test days `0.8`

## Pending Signal Profile

- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `70`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `18`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `16`
- `validation_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `9`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `1`
