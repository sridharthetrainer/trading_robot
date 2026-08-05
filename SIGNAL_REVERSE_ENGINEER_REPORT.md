# Signal Reverse Engineering Report

- Generated: `2026-08-05T22:34:20+0530`
- Status: `READY`
- Rows: `33262`
- Labelled rows: `32563`
- Pending rows: `632`
- Labelled pct: `0.979`
- Overall target/loss/timeout: `4512` / `7446` / `20605`
- Overall average return pct: `-0.0175`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=WEAK` n `51` target_rate `0.2157` avg_return `0.5294`
- `strategy=chart_pattern_falling_wedge` n `48` target_rate `0.1458` avg_return `0.518`
- `strategy=awesome_osc` n `13` target_rate `0.2308` avg_return `0.4126`
- `strategy=vwap_reversion` n `57` target_rate `0.2456` avg_return `0.343`
- `strategy=uo_overbought` n `47` target_rate `0.2128` avg_return `0.3357`
- `strategy=candlestick_three_river_evening_star` n `47` target_rate `0.1702` avg_return `0.3238`
- `strategy=candlestick_ladder_bottom` n `17` target_rate `0.0588` avg_return `0.2863`
- `strategy=vwap_bands` n `44` target_rate `0.4091` avg_return `0.2186`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=chart_pattern_diamond_bottom` n `78` target_rate `0.2436` avg_return `0.1926`

## Feature Lifts

- `pivot_boss_mod` positive_vs_silent lift `0.2194`
- `news_mod` positive_vs_silent lift `0.2148`
- `pivot_boss_mod` negative_vs_silent lift `0.2146`
- `market_quality_mod` negative_vs_silent lift `0.1713`
- `mtf_pivot_mod` negative_vs_silent lift `0.117`
- `weinstein_mod` negative_vs_silent lift `0.094`
- `structure_mod` positive_vs_silent lift `0.0769`
- `structure_mod` negative_vs_silent lift `0.073`
- `sr_level_mod` negative_vs_silent lift `0.0443`
- `oi_mod` negative_vs_silent lift `0.0241`
- `sip_boost` positive_vs_silent lift `0.0143`
- `expiry_mod` negative_vs_silent lift `0.0099`
- `candidate_quality_mod` negative_vs_silent lift `0.0077`
- `cross_asset_mod` negative_vs_silent lift `0.0018`
- `cross_asset_mod` positive_vs_silent lift `0.0011`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `32560`; days `29`; live reversal `BLOCKED`
- `failed_bull_breakout` SHADOW_VALIDATED train/test `437/159` reverse OOS `0.4961%` positive test days `0.6667`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `175/57` reverse OOS `0.1957%` positive test days `0.6667`
- `rsi2_mr` SHADOW_VALIDATED train/test `180/39` reverse OOS `0.1406%` positive test days `0.6667`
- `candlestick_spinning_top_white` SHADOW_VALIDATED train/test `111/34` reverse OOS `0.087%` positive test days `0.8`
- `cci_trend` SHADOW_VALIDATED train/test `207/219` reverse OOS `0.0707%` positive test days `0.6667`
- `cpr` SHADOW_VALIDATED train/test `388/135` reverse OOS `0.0452%` positive test days `0.8333`
- `candlestick_evening_star` SHADOW_COLLECTING train/test `129/32` reverse OOS `0.7829%` positive test days `0.5`
- `candlestick_morning_star` SHADOW_COLLECTING train/test `90/28` reverse OOS `0.3071%` positive test days `0.4286`

## Pending Signal Profile

- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `313`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `104`
- `validation_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `96`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `78`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `14`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `14`
- `validation_fail,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
- `missing_rigorous_validation,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
- `validation_fail,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
- `edge_insufficient_data,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
