# Strategy keep/prune — 2026-07-04 (nightly, NET-of-cost R)

Ranked by tb_r_multiple_net (costs+slippage included). CONFIRMED requires n>=150 AND distinct days>=8 (max days so far: 5).
Rigorous gate is validation_harness (DSR). Reversible prune via pruning.py.

## CONFIRMED KEEP (net-R>=0.1, n>=150, days>=8)
  (none yet)

## CONFIRMED PRUNE (net-R<=-0.03, n>=150, days>=8)
  (none yet)

## ALL (ranked by net-R)
  heikin_ashi                            n=20   days=5  netR=+0.169 (gross +0.352) win=5.0%
  elder_triple_screen                    n=121  days=5  netR=+0.120 (gross +0.303) win=9.9%
  weinstein_stage                        n=31   days=5  netR=+0.067 (gross +0.249) win=9.7%
  td_sequential                          n=26   days=5  netR=+0.022 (gross +0.204) win=7.7%
  breakout                               n=95   days=5  netR=+0.004 (gross +0.187) win=12.6%
  chart_pattern_diamond_bottom           n=15   days=5  netR=-0.030 (gross +0.153) win=20.0%
  candlestick_spinning_top_white         n=24   days=5  netR=-0.036 (gross +0.146) win=4.2%
  failed_bear_breakout                   n=114  days=5  netR=-0.054 (gross +0.129) win=12.3%
  cpr                                    n=64   days=5  netR=-0.066 (gross +0.117) win=9.4%
  chart_pattern_ascending_triangle       n=290  days=5  netR=-0.070 (gross +0.112) win=9.7%
  cci_trend                              n=30   days=4  netR=-0.074 (gross +0.108) win=0.0%
  price_structure                        n=1328 days=5  netR=-0.092 (gross +0.090) win=7.2%
  chart_pattern_head_and_shoulders       n=208  days=5  netR=-0.093 (gross +0.090) win=5.8%
  candlestick_evening_star               n=33   days=5  netR=-0.101 (gross +0.082) win=6.1%
  kama_trend                             n=24   days=5  netR=-0.132 (gross +0.050) win=4.2%
  candlestick_doji                       n=16   days=5  netR=-0.137 (gross +0.045) win=12.5%
  candlestick_bullish_engulfing          n=49   days=5  netR=-0.139 (gross +0.044) win=10.2%
  chart_pattern_inverse_head_shoulders   n=152  days=5  netR=-0.145 (gross +0.038) win=6.6%
  candlestick_on_neck_pattern            n=28   days=5  netR=-0.153 (gross +0.030) win=0.0%
  candlestick_tweezer_bottom             n=42   days=5  netR=-0.178 (gross +0.004) win=7.1%
  candlestick_three_white_soldiers       n=51   days=4  netR=-0.180 (gross +0.003) win=3.9%
  chart_pattern_double_bottom            n=131  days=5  netR=-0.193 (gross -0.010) win=9.2%
  donchian_breakout                      n=59   days=5  netR=-0.199 (gross -0.017) win=3.4%
  elder_ray                              n=30   days=5  netR=-0.201 (gross -0.018) win=3.3%
  vrvp_zone                              n=355  days=5  netR=-0.208 (gross -0.025) win=7.3%
  holy_grail                             n=170  days=5  netR=-0.213 (gross -0.030) win=4.7%
  mean_reversion                         n=81   days=5  netR=-0.216 (gross -0.033) win=2.5%
  candlestick_hammer                     n=23   days=5  netR=-0.219 (gross -0.036) win=0.0%
  candlestick_shooting_star              n=16   days=5  netR=-0.224 (gross -0.041) win=6.2%
  candlestick_bearish_engulfing          n=121  days=5  netR=-0.225 (gross -0.042) win=2.5%
  ma_cross                               n=91   days=5  netR=-0.232 (gross -0.050) win=5.5%
  trend                                  n=529  days=5  netR=-0.235 (gross -0.052) win=4.5%
  chart_pattern_range_expansion          n=42   days=5  netR=-0.246 (gross -0.063) win=4.8%
  rsi2_mr                                n=32   days=5  netR=-0.265 (gross -0.082) win=6.2%
  rsi_divergence                         n=697  days=5  netR=-0.268 (gross -0.085) win=1.7%
  failed_bull_breakout                   n=116  days=5  netR=-0.269 (gross -0.086) win=2.6%
  alligator_ao                           n=408  days=5  netR=-0.281 (gross -0.098) win=3.2%
  chart_pattern_descending_triangle      n=698  days=5  netR=-0.291 (gross -0.108) win=2.6%
  ttm_squeeze                            n=66   days=5  netR=-0.302 (gross -0.119) win=0.0%
  chart_pattern_double_top               n=121  days=5  netR=-0.315 (gross -0.132) win=5.8%
  candlestick_fred_tam_black_inside_out  n=24   days=5  netR=-0.327 (gross -0.142) win=0.0%
  candlestick_three_black_soldiers       n=85   days=5  netR=-0.331 (gross -0.148) win=4.7%
  pivot_scalping                         n=37   days=4  netR=-0.357 (gross -0.173) win=0.0%
  chart_pattern_rising_wedge             n=21   days=4  netR=-0.367 (gross -0.184) win=0.0%
  candlestick_spinning_top_black         n=75   days=5  netR=-0.399 (gross -0.217) win=5.3%
  aroon_trend                            n=44   days=5  netR=-0.433 (gross -0.250) win=2.3%
  vp_breakout                            n=38   days=5  netR=-0.499 (gross -0.317) win=2.6%
  chart_pattern_diamond_top              n=27   days=5  netR=-0.644 (gross -0.464) win=3.7%
  ichimoku                               n=33   days=5  netR=-0.707 (gross -0.524) win=0.0%
  expiry_scalp                           n=21   days=2  netR=-1.090 (gross -0.906) win=0.0%
