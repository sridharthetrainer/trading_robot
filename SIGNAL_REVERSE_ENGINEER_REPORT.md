# Signal Reverse Engineering Report

- Generated: `2026-07-20T19:56:08+0530`
- Status: `READY`
- Rows: `27809`
- Labelled rows: `27073`
- Pending rows: `669`
- Labelled pct: `0.9735`
- Overall target/loss/timeout: `4097` / `6670` / `16306`
- Overall average return pct: `-0.021`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `25` target_rate `0.16` avg_return `0.9025`
- `strategy=vwap_reversion` n `35` target_rate `0.3143` avg_return `0.5664`
- `strategy=uo_overbought` n `29` target_rate `0.2759` avg_return `0.4223`
- `strategy=candlestick_ladder_bottom` n `12` target_rate `0.0833` avg_return `0.2725`
- `strategy=vwap_bands` n `39` target_rate `0.4615` avg_return `0.2476`
- `confluence=WEAK` n `30` target_rate `0.1667` avg_return `0.2219`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=orb` n `19` target_rate `0.3158` avg_return `0.1986`
- `strategy=chart_pattern_cup_handle` n `11` target_rate `0.0909` avg_return `0.1972`
- `strategy=candlestick_bearish_harami` n `41` target_rate `0.0732` avg_return `0.1896`

## Feature Lifts

- `mtf_pivot_mod` negative_vs_silent lift `0.17`
- `pivot_boss_mod` positive_vs_silent lift `0.1563`
- `news_mod` negative_vs_silent lift `0.1471`
- `market_quality_mod` negative_vs_silent lift `0.1426`
- `pivot_boss_mod` negative_vs_silent lift `0.1422`
- `structure_mod` negative_vs_silent lift `0.0931`
- `news_mod` positive_vs_silent lift `0.087`
- `weinstein_mod` negative_vs_silent lift `0.0841`
- `structure_mod` positive_vs_silent lift `0.0823`
- `candidate_quality_mod` negative_vs_silent lift `0.0491`
- `sr_level_mod` negative_vs_silent lift `0.0373`
- `oi_mod` negative_vs_silent lift `0.03`
- `expiry_mod` negative_vs_silent lift `0.0202`
- `cross_asset_mod` negative_vs_silent lift `0.018`
- `cross_asset_mod` positive_vs_silent lift `-0.0025`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `27070`; days `21`; live reversal `BLOCKED`
- `vrvp_zone` SHADOW_VALIDATED train/test `790/102` reverse OOS `0.8438%` positive test days `1.0`
- `candlestick_spinning_top_white` SHADOW_VALIDATED train/test `80/35` reverse OOS `0.6975%` positive test days `0.8333`
- `supertrend_mtf` SHADOW_VALIDATED train/test `78/73` reverse OOS `0.4365%` positive test days `0.6667`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `107/68` reverse OOS `0.2913%` positive test days `0.6667`
- `chart_pattern_double_top` SHADOW_VALIDATED train/test `276/94` reverse OOS `0.2525%` positive test days `1.0`
- `alligator_ao` SHADOW_VALIDATED train/test `1027/123` reverse OOS `0.1501%` positive test days `1.0`
- `mean_reversion` SHADOW_VALIDATED train/test `256/154` reverse OOS `0.1488%` positive test days `0.8571`
- `candlestick_evening_star` SHADOW_VALIDATED train/test `82/49` reverse OOS `0.0812%` positive test days `0.8571`
- `failed_bull_breakout` SHADOW_VALIDATED train/test `297/157` reverse OOS `0.0304%` positive test days `0.7143`
- `rsi2_mr` SHADOW_COLLECTING train/test `113/69` reverse OOS `0.393%` positive test days `0.5714`

## Pending Signal Profile

- `edge_negative_edge,negative_generated_signal_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `385`
- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `146`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `96`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `26`
- `edge_negative_edge,negative_generated_signal_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `10`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `3`
- `edge_insufficient_data,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `1`
- `paper_only_strategy_expiry_scalp,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `1`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `1`
