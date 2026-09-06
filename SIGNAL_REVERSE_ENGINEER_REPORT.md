# Signal Reverse Engineering Report

- Generated: `2026-09-05T11:39:12+0530`
- Status: `READY`
- Rows: `35520`
- Labelled rows: `35040`
- Pending rows: `413`
- Labelled pct: `0.9865`
- Overall target/loss/timeout: `4681` / `7768` / `22591`
- Overall average return pct: `-0.0159`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=WEAK` n `64` target_rate `0.1875` avg_return `0.5023`
- `strategy=chart_pattern_falling_wedge` n `67` target_rate `0.1194` avg_return `0.3785`
- `strategy=awesome_osc` n `15` target_rate `0.2` avg_return `0.3576`
- `strategy=candlestick_three_river_evening_star` n `58` target_rate `0.1724` avg_return `0.3513`
- `strategy=candlestick_bullish_separating_lines` n `37` target_rate `0.1892` avg_return `0.3137`
- `strategy=candlestick_ladder_bottom` n `23` target_rate `0.087` avg_return `0.2574`
- `strategy=vwap_reversion` n `81` target_rate `0.1975` avg_return `0.2451`
- `strategy=vwap_bands` n `50` target_rate `0.36` avg_return `0.216`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`
- `strategy=chart_pattern_bull_flag` n `16` target_rate `0.1875` avg_return `0.1244`

## Feature Lifts

- `pivot_boss_mod` positive_vs_silent lift `0.2222`
- `pivot_boss_mod` negative_vs_silent lift `0.2156`
- `news_mod` positive_vs_silent lift `0.1828`
- `market_quality_mod` negative_vs_silent lift `0.1583`
- `mtf_pivot_mod` negative_vs_silent lift `0.149`
- `weinstein_mod` negative_vs_silent lift `0.0683`
- `structure_mod` positive_vs_silent lift `0.0652`
- `structure_mod` negative_vs_silent lift `0.0648`
- `candidate_quality_mod` negative_vs_silent lift `0.0416`
- `sr_level_mod` negative_vs_silent lift `0.0333`
- `oi_mod` negative_vs_silent lift `0.0258`
- `expiry_mod` negative_vs_silent lift `0.0161`
- `sip_boost` positive_vs_silent lift `0.0119`
- `cross_asset_mod` positive_vs_silent lift `0.0025`
- `cross_asset_mod` negative_vs_silent lift `0.0006`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `35037`; days `38`; live reversal `BLOCKED`
- `candlestick_bearish_marubozu` SHADOW_COLLECTING train/test `107/43` reverse OOS `0.0803%` positive test days `0.2857`

## Pending Signal Profile

- `missing_rigorous_validation,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `159`
- `edge_negative_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `116`
- `validation_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `67`
- `edge_insufficient_data,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `52`
- `missing_rigorous_validation,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `5`
- `edge_negative_edge,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `5`
- `strategy_missing_from_live_eligibility_manifest,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `5`
- `edge_insufficient_data,score_below_live_min,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `4`
