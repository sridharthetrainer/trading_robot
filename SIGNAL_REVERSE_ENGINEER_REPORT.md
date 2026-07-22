# Signal Reverse Engineering Report

- Generated: `2026-07-22T19:56:11+0530`
- Status: `READY`
- Rows: `30259`
- Labelled rows: `28882`
- Pending rows: `1310`
- Labelled pct: `0.9545`
- Overall target/loss/timeout: `4201` / `6885` / `17796`
- Overall average return pct: `-0.0184`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=chart_pattern_falling_wedge` n `28` target_rate `0.1786` avg_return `0.8422`
- `strategy=vwap_reversion` n `39` target_rate `0.2821` avg_return `0.5029`
- `strategy=uo_overbought` n `32` target_rate `0.25` avg_return `0.4279`
- `strategy=candlestick_ladder_bottom` n `12` target_rate `0.0833` avg_return `0.2725`
- `confluence=WEAK` n `36` target_rate `0.1389` avg_return `0.2512`
- `strategy=vwap_bands` n `39` target_rate `0.4615` avg_return `0.2476`
- `strategy=chart_pattern_diamond_bottom` n `64` target_rate `0.25` avg_return `0.2223`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=volume_profile_full` n `31` target_rate `0.1613` avg_return `0.1578`
- `confluence=STRONG` n `260` target_rate `0.1038` avg_return `0.1437`

## Feature Lifts

- `news_mod` negative_vs_silent lift `0.1766`
- `pivot_boss_mod` positive_vs_silent lift `0.1556`
- `mtf_pivot_mod` negative_vs_silent lift `0.1526`
- `pivot_boss_mod` negative_vs_silent lift `0.1505`
- `market_quality_mod` negative_vs_silent lift `0.1399`
- `news_mod` positive_vs_silent lift `0.1385`
- `weinstein_mod` negative_vs_silent lift `0.0946`
- `structure_mod` negative_vs_silent lift `0.0854`
- `structure_mod` positive_vs_silent lift `0.0784`
- `candidate_quality_mod` negative_vs_silent lift `0.0383`
- `oi_mod` negative_vs_silent lift `0.0335`
- `sr_level_mod` negative_vs_silent lift `0.0299`
- `expiry_mod` negative_vs_silent lift `0.0216`
- `cross_asset_mod` negative_vs_silent lift `0.012`
- `cross_asset_mod` positive_vs_silent lift `-0.0068`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `28879`; days `23`; live reversal `BLOCKED`
- `mean_reversion` SHADOW_VALIDATED train/test `277/167` reverse OOS `0.174%` positive test days `0.7143`
- `cci_trend` SHADOW_VALIDATED train/test `111/187` reverse OOS `0.1638%` positive test days `0.8571`
- `chart_pattern_range_expansion` SHADOW_VALIDATED train/test `128/71` reverse OOS `0.1241%` positive test days `0.7143`
- `rsi2_mr` SHADOW_COLLECTING train/test `131/62` reverse OOS `0.3362%` positive test days `0.5714`
- `supertrend_mtf` SHADOW_COLLECTING train/test `79/124` reverse OOS `0.1747%` positive test days `0.5714`
- `chart_pattern_rising_wedge` SHADOW_COLLECTING train/test `69/60` reverse OOS `0.1622%` positive test days `0.5714`
- `cpr` SHADOW_COLLECTING train/test `252/184` reverse OOS `0.1331%` positive test days `0.2857`
- `aroon_trend` SHADOW_COLLECTING train/test `163/123` reverse OOS `0.0702%` positive test days `0.5714`
- `candlestick_evening_star` SHADOW_COLLECTING train/test `103/41` reverse OOS `0.0164%` positive test days `0.6667`

## Pending Signal Profile

- `edge_negative_edge,negative_generated_signal_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `829`
- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `286`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `139`
- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `32`
- `edge_negative_edge,negative_generated_signal_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `8`
- `edge_insufficient_data,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `6`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
- `paper_only_strategy_expiry_scalp,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `2`
