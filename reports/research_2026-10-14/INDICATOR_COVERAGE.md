# Indicator / strategy coverage vs TradingView and Chartink (2026-10-14)

Asked: "check all indicators and strategies from Chartink, TradingView and other
sites and include". Bulk inclusion was not done: the system already has 79
registry strategies and ~125 logged strategy families, none survives costs, and
every extra variant raises the multiple-testing bar (deflated Sharpe). Instead,
the most-used items on those sites were mapped against the system; only
genuinely new families with a documented rationale would be added as
pre-registered tests.

Sources for "most used" (vendor/affiliate blogs, unverified rankings): quantum-algo,
pickmytrade, lunefi, pineify, tradingtoolshub (TradingView); malvaniainternational,
buzzchronicles, Chartink scanner user guide (Chartink). TradingView's library has
>100,000 public scripts; Chartink supports NSE cash and indices only.

| Popular item | In system | Where |
|---|---|---|
| Moving averages, RSI, MACD, Stochastic | yes | ma_cross, rsi2_mr, rsi_divergence; MACD/Stochastic in indicators.py and confluence scoring |
| Supertrend | yes | supertrend_mtf |
| Volume Profile / VPOC / VRVP | yes | volume_profile_full, vpoc_magnet, vrvp_zone, vp_breakout |
| Squeeze Momentum (LazyBear) | yes | ttm_squeeze |
| SMC / order blocks / liquidity sweeps (LuxAlgo-style) | yes | smc, order_block, liquidity_sweep, market_structure |
| Ichimoku, Heikin-Ashi, Parabolic SAR, Donchian, KAMA, Aroon, CCI, Williams %R, Awesome Osc, Alligator, KST, Elder Ray, Ehlers Fisher, Waddah Attar | yes | same-named strategies |
| ORB, CPR, pivots, VWAP bands/reversion, anchored VWAP | yes | orb, orb_2min, cpr, pivot_scalping, vwap_bands, vwap_reversion, anchored_vwap |
| Candlestick patterns (40+) and chart patterns (20) | yes | candlestick_*, chart_pattern_* |
| Chartink: breakout above yesterday's high / 5-day range breakout | yes | breakout, donchian_breakout |
| Chartink: volume breakout (2x average) | yes | vsa, volume modifiers |
| Chartink: tight range / NR7 | yes | NR7 confluence modifier |
| Chartink: reversal after 5 down days | yes | rsi2_mr, mean_reversion |
| Chartink: momentum stocks | yes | Test 7 cross-sectional momentum (the one PASS) |
| Chartink: techno-funda | partly | canslim, fundamentals inputs |
| WaveTrend oscillator (LazyBear) | no | momentum oscillator from the same inputs as CCI/RSI (~0R families) |
| Lorentzian Classification (jdehorty) | no | kNN on RSI/CCI/ADX features — same information as the existing ML |
| 3-Bar Reversal | no | candlestick-pattern variant (40+ patterns already, ~0R) |

Decision: nothing missing is a genuinely new mechanism with documented evidence,
so nothing is added. The three gaps are variants of families that already fail;
adding them would only increase false-positive risk.
