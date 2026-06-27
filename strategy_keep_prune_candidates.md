# Strategy keep/prune — 2026-06-27 (nightly, R-weighted)

PRE-COST, asymmetric barriers bias avg-R +. CONFIRMED = n>=150.
Rigorous gate is validation_harness (DSR). Reversible prune via pruning.py.

## CONFIRMED KEEP (avg-R>=0.1, n>=150)
  (none yet)

## CONFIRMED PRUNE (avg-R<=-0.03, n>=150)
  cpr                                    n=151  avgR=-0.078 win=38.4%

## ALL (ranked by avg-R)
  candlestick_bearish_marubozu           n=32   avgR=+0.337 win=25.0%
  chart_pattern_double_bottom            n=131  avgR=+0.175 win=47.3%
  candlestick_three_white_soldiers       n=59   avgR=+0.165 win=40.7%
  candlestick_evening_star               n=30   avgR=+0.164 win=30.0%
  candlestick_shooting_star              n=21   avgR=+0.162 win=28.6%
  elder_triple_screen                    n=120  avgR=+0.130 win=40.8%
  cci_trend                              n=56   avgR=+0.125 win=32.1%
  rsi2_mr                                n=60   avgR=+0.095 win=26.7%
  chart_pattern_rising_wedge             n=32   avgR=+0.091 win=34.4%
  alligator_ao                           n=348  avgR=+0.081 win=37.9%
  candlestick_hammer                     n=41   avgR=+0.081 win=22.0%
  failed_bear_breakout                   n=98   avgR=+0.067 win=32.7%
  chart_pattern_double_top               n=87   avgR=+0.060 win=31.0%
  donchian_breakout                      n=87   avgR=+0.058 win=24.1%
  candlestick_three_black_soldiers       n=102  avgR=+0.056 win=28.4%
  chart_pattern_descending_triangle      n=582  avgR=+0.052 win=22.3%
  vwap_bands                             n=25   avgR=+0.052 win=64.0%
  ttm_squeeze                            n=72   avgR=+0.049 win=13.9%
  rsi_divergence                         n=877  avgR=+0.047 win=26.5%
  candlestick_doji                       n=20   avgR=+0.044 win=40.0%
  aroon_trend                            n=73   avgR=+0.043 win=32.9%
  ema_cloud_sd                           n=30   avgR=+0.038 win=43.3%
  price_structure                        n=933  avgR=+0.035 win=34.3%
  candlestick_bullish_engulfing          n=65   avgR=+0.020 win=35.4%
  ichimoku                               n=52   avgR=+0.018 win=51.9%
  candlestick_spinning_top_black         n=71   avgR=+0.017 win=33.8%
  ma_cross                               n=329  avgR=+0.016 win=35.9%
  trend                                  n=890  avgR=+0.015 win=27.5%
  candlestick_on_neck_pattern            n=33   avgR=+0.014 win=15.2%
  breakout                               n=197  avgR=+0.010 win=38.6%
  heikin_ashi                            n=79   avgR=+0.007 win=35.4%
  supertrend_mtf                         n=66   avgR=+0.005 win=45.5%
  weinstein_stage                        n=45   avgR=+0.003 win=40.0%
  anchored_vwap                          n=50   avgR=+0.000 win=46.0%
  candlestick_bullish_marubozu           n=21   avgR=+0.000 win=23.8%
  candlestick_spinning_top_white         n=40   avgR=+0.000 win=25.0%
  candlestick_tweezer_bottom             n=44   avgR=+0.000 win=22.7%
  canslim                                n=23   avgR=+0.000 win=39.1%
  chaikin_mf                             n=31   avgR=+0.000 win=51.6%
  elliott_wave                           n=25   avgR=+0.000 win=48.0%
  ema_ribbon                             n=35   avgR=+0.000 win=48.6%
  order_flow                             n=58   avgR=+0.000 win=50.0%
  parabolic_sar                          n=56   avgR=+0.000 win=50.0%
  smc                                    n=31   avgR=+0.000 win=38.7%
  vrvp_zone                              n=270  avgR=+0.000 win=30.0%
  chart_pattern_range_expansion          n=36   avgR=-0.007 win=22.2%
  chart_pattern_head_and_shoulders       n=206  avgR=-0.014 win=25.2%
  candlestick_fred_tam_white_inside_out  n=26   avgR=-0.016 win=26.9%
  candlestick_tweezer_top                n=36   avgR=-0.016 win=38.9%
  chart_pattern_ascending_triangle       n=477  avgR=-0.018 win=33.3%
  vp_breakout                            n=52   avgR=-0.019 win=28.8%
  kama_trend                             n=26   avgR=-0.023 win=42.3%
  candlestick_morning_star               n=31   avgR=-0.027 win=16.1%
  chart_pattern_inverse_head_shoulders   n=196  avgR=-0.028 win=31.1%
  waddah_attar                           n=43   avgR=-0.028 win=39.5%
  failed_bull_breakout                   n=124  avgR=-0.034 win=21.8%
  mean_reversion                         n=136  avgR=-0.044 win=19.9%
  td_sequential                          n=37   avgR=-0.048 win=45.9%
  candlestick_bearish_engulfing          n=79   avgR=-0.062 win=24.1%
  elder_ray                              n=42   avgR=-0.064 win=31.0%
  cpr                                    n=151  avgR=-0.078 win=38.4%
  candlestick_fred_tam_black_inside_out  n=19   avgR=-0.114 win=10.5%
