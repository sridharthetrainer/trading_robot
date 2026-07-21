# Signal Reverse Engineering Report

- Generated: `2026-07-21T19:55:27+0530`
- Status: `READY`
- Rows: `29083`
- Labelled rows: `27884`
- Pending rows: `1132`
- Labelled pct: `0.9588`
- Overall target/loss/timeout: `4135` / `6741` / `17008`
- Overall average return pct: `-0.0196`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `27` target_rate `0.1852` avg_return `0.8552`
- `strategy=vwap_reversion` n `36` target_rate `0.3056` avg_return `0.5355`
- `strategy=uo_overbought` n `29` target_rate `0.2759` avg_return `0.4223`
- `strategy=candlestick_ladder_bottom` n `12` target_rate `0.0833` avg_return `0.2725`
- `confluence=WEAK` n `34` target_rate `0.1471` avg_return `0.2482`
- `strategy=vwap_bands` n `39` target_rate `0.4615` avg_return `0.2476`
- `strategy=chart_pattern_diamond_bottom` n `64` target_rate `0.25` avg_return `0.2223`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=volume_profile_full` n `31` target_rate `0.1613` avg_return `0.1578`
- `confluence=STRONG` n `240` target_rate `0.1083` avg_return `0.1571`

## Feature Lifts

- `news_mod` negative_vs_silent lift `0.1587`
- `pivot_boss_mod` positive_vs_silent lift `0.1564`
- `mtf_pivot_mod` negative_vs_silent lift `0.1472`
- `pivot_boss_mod` negative_vs_silent lift `0.1459`
- `market_quality_mod` negative_vs_silent lift `0.1412`
- `weinstein_mod` negative_vs_silent lift `0.1028`
- `news_mod` positive_vs_silent lift `0.0911`
- `structure_mod` negative_vs_silent lift `0.089`
- `structure_mod` positive_vs_silent lift `0.0793`
- `candidate_quality_mod` negative_vs_silent lift `0.0571`
- `sr_level_mod` negative_vs_silent lift `0.0345`
- `oi_mod` negative_vs_silent lift `0.0328`
- `expiry_mod` negative_vs_silent lift `0.0225`
- `cross_asset_mod` negative_vs_silent lift `0.0144`
- `cross_asset_mod` positive_vs_silent lift `-0.006`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `27881`; days `22`; live reversal `BLOCKED`
- `vrvp_zone` SHADOW_VALIDATED train/test `837/55` reverse OOS `0.6965%` positive test days `1.0`
- `candlestick_spinning_top_white` SHADOW_VALIDATED train/test `80/43` reverse OOS `0.6165%` positive test days `0.8571`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `116/70` reverse OOS `0.3202%` positive test days `0.7143`
- `rsi_divergence` SHADOW_VALIDATED train/test `1970/255` reverse OOS `0.3174%` positive test days `1.0`
- `supertrend_mtf` SHADOW_VALIDATED train/test `79/92` reverse OOS `0.3076%` positive test days `0.6667`
- `trend` SHADOW_VALIDATED train/test `1885/118` reverse OOS `0.2365%` positive test days `1.0`
- `chart_pattern_descending_triangle` SHADOW_VALIDATED train/test `1745/71` reverse OOS `0.2242%` positive test days `1.0`
- `alligator_ao` SHADOW_VALIDATED train/test `1093/57` reverse OOS `0.2061%` positive test days `1.0`
- `chart_pattern_double_top` SHADOW_VALIDATED train/test `313/57` reverse OOS `0.2009%` positive test days `1.0`
- `candlestick_evening_star` SHADOW_VALIDATED train/test `85/57` reverse OOS `0.1189%` positive test days `0.8571`

## Pending Signal Profile

- `edge_negative_edge,negative_generated_signal_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `698`
- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `243`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `124`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `48`
- `edge_negative_edge,negative_generated_signal_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `9`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
- `edge_insufficient_data,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
- `paper_only_strategy_expiry_scalp,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
