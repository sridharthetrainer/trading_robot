# Signal Reverse Engineering Report

- Generated: `2026-07-05T21:56:19+0530`
- Status: `READY`
- Rows: `16867`
- Labelled rows: `16757`
- Pending rows: `43`
- Labelled pct: `0.9935`
- Overall target/loss/timeout: `3286` / `4943` / `8528`
- Overall average return pct: `-0.0095`

## Best Context Edges

- `strategy=PIVOT_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `regime=UPTREND` n `10` target_rate `1.0` avg_return `2.6354`
- `htf_bias=blank` n `10` target_rate `1.0` avg_return `2.6354`
- `confluence=HIGH` n `10` target_rate `1.0` avg_return `2.6354`
- `cross_asset_bias=BULLISH` n `10` target_rate `1.0` avg_return `2.6354`
- `expiry_regime=0DTE_SCALPING` n `10` target_rate `1.0` avg_return `2.6354`
- `strategy=uo_overbought` n `11` target_rate `0.6364` avg_return `1.1332`
- `strategy=vwap_reversion` n `10` target_rate `0.7` avg_return `1.0117`
- `strategy=vwap_bands` n `28` target_rate `0.5714` avg_return `0.2566`
- `strategy=orb` n `13` target_rate `0.3077` avg_return `0.2551`
- `strategy=elder_triple_screen` n `274` target_rate `0.2591` avg_return `0.2498`
- `strategy=elliott_wave` n `30` target_rate `0.4` avg_return `0.2394`
- `strategy=williams_r` n `13` target_rate `0.0769` avg_return `0.2237`
- `strategy=td_sequential` n `67` target_rate `0.3134` avg_return `0.215`
- `confluence=SHADOW` n `363` target_rate `0.5592` avg_return `0.202`

## Feature Lifts

- `weinstein_mod` positive_vs_silent lift `0.4578`
- `sr_level_mod` negative_vs_silent lift `0.1045`
- `market_quality_mod` negative_vs_silent lift `0.0604`
- `candidate_quality_mod` negative_vs_silent lift `0.0045`
- `cross_asset_mod` negative_vs_silent lift `-0.0033`
- `expiry_mod` negative_vs_silent lift `-0.0112`
- `pivot_boss_mod` positive_vs_silent lift `-0.0223`
- `expiry_mod` positive_vs_silent lift `-0.0228`
- `ai_score` positive_vs_silent lift `-0.0266`
- `mtf_pivot_mod` negative_vs_silent lift `-0.0284`
- `volume_ratio` positive_vs_silent lift `-0.0416`
- `structure_mod` positive_vs_silent lift `-0.0447`
- `cross_asset_mod` positive_vs_silent lift `-0.05`
- `structure_mod` negative_vs_silent lift `-0.0502`
- `pivot_boss_mod` negative_vs_silent lift `-0.0541`

## Reverse Shadow A/B (All Signals)

- Scope `all_generated_labelled_signals`; signals `16754`; days `12`; live reversal `BLOCKED`
- `vp_breakout` SHADOW_COLLECTING train/test `64/36` reverse OOS `0.1854%` positive test days `0.75`
- `chart_pattern_double_top` SHADOW_COLLECTING train/test `159/77` reverse OOS `0.167%` positive test days `1.0`
- `chart_pattern_descending_triangle` SHADOW_COLLECTING train/test `806/560` reverse OOS `0.1382%` positive test days `1.0`
- `failed_bull_breakout` SHADOW_COLLECTING train/test `166/95` reverse OOS `0.1184%` positive test days `1.0`
- `rsi_divergence` SHADOW_COLLECTING train/test `1083/607` reverse OOS `0.109%` positive test days `1.0`
- `ttm_squeeze` SHADOW_COLLECTING train/test `104/50` reverse OOS `0.0909%` positive test days `0.75`
- `trend` SHADOW_COLLECTING train/test `1091/471` reverse OOS `0.0732%` positive test days `0.75`
- `ma_cross` SHADOW_COLLECTING train/test `356/88` reverse OOS `0.0635%` positive test days `0.5`
- `vrvp_zone` SHADOW_COLLECTING train/test `397/287` reverse OOS `0.0157%` positive test days `0.5`
- `mean_reversion` SHADOW_COLLECTING train/test `166/69` reverse OOS `0.0065%` positive test days `0.5`

## Pending Signal Profile

- `ai_unvalidated_rule_fallback,filter_score_below_live_min` count `23`
- `negative_generated_signal_edge,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `19`
- `paper_only_strategy_holy_grail,ai_unvalidated_rule_fallback,filter_score_below_live_min` count `1`
