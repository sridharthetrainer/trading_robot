# CPR_VWAP_EMA_Nifty backtest - 2026-10-16T22:15:41

Window 2020-07-01 to 2026-10-15; out-of-sample from 2024-07-01.
Sessions: 1555; missing futures volume (skipped in 'futures' set, spot-TWAP in 'with_missing' set): 3.
Spot data source per session: {'angel': 1614, 'insufficient': 4}.

**Tests run on this dataset so far: 23** (new this run: 3; ledger: test_ledger.json). Hypotheses and forward paper trades are not counted.
Conditions logged (observations): [{'id': 'C001', 'date': '2026-10-15', 'role': 'observation'}]
Hypotheses (observation only, not tested, not counted): [{'id': 'H001', 'date': '2026-10-16', 'status': 'observation_only'}]
Pre-registered rules: [{'id': 'R001', 'status': 'approved', 'approved_on': '2026-10-16', 'frozen_ok': True}] (forward status: results/forward/<id>_status.json)

Layer A = NIFTY index points, no costs. B = modelled 1-lot ATM option, net of costs and 1pt/side slippage (prior-session IV held constant). C = real option prices from 5-min chain snapshots (2026-06-19 onward only).

## C001
Setup-matched sessions: 1 (2026-10-15)

### Variants (layer A, points)
- **+all_filters|futures**
  - matched: 1 trades | win 100.0% | avg win 233.95 / loss 0.0 pts | PF n/a | exp 233.95 pts | total 233.95 | maxDD 0.0 | hold 190.0m | MFE 235.7 / MAE 0.0 pts
  - non_matched: 361 trades | win 46.8% | avg win 43.35 / loss -31.58 pts | PF 1.21 | exp 3.5 pts | total 1264.02 | maxDD -808.63 | hold 107.3m | MFE 43.6 / MAE 28.4 pts
  - all: 362 trades | win 47.0% | avg win 44.47 / loss -31.58 pts | PF 1.25 | exp 4.14 pts | total 1497.97 | maxDD -808.63 | hold 107.6m | MFE 44.1 / MAE 28.3 pts
  - all_B_rs: 360 trades | win 36.4% | avg win 1985.05 / loss -1032.43 Rs | PF 1.1 | exp 65.6 Rs | total 23616.12 | maxDD -49136.17 | hold 107.6m | MFE 44.2 / MAE 28.2 pts
  - all_C_rs: 3 trades | win 66.7% | avg win 5480.7 / loss -1215.76 Rs | PF 9.02 | exp 3248.55 Rs | total 9745.65 | maxDD -1215.76 | hold 118.3m | MFE 103.9 / MAE 14.2 pts
  - IS_all_points: 264 trades | win 46.6% | avg win 37.81 / loss -30.15 pts | PF 1.09 | exp 1.51 pts | total 399.93 | maxDD -808.63 | hold 107.0m | MFE 38.6 / MAE 26.1 pts
  - OOS_all_points: 98 trades | win 48.0% | avg win 61.9 / loss -35.52 pts | PF 1.61 | exp 11.2 pts | total 1098.04 | maxDD -272.25 | hold 109.1m | MFE 58.9 / MAE 34.2 pts
  - IS_all_B_rs: 262 trades | win 35.5% | avg win 1627.63 / loss -976.36 Rs | PF 0.92 | exp -52.04 Rs | total -13634.92 | maxDD -49136.17 | hold 107.1m | MFE 38.7 / MAE 26.0 pts
  - OOS_all_B_rs: 98 trades | win 38.8% | avg win 2859.78 / loss -1190.34 Rs | PF 1.52 | exp 380.11 Rs | total 37251.04 | maxDD -14251.22 | hold 109.1m | MFE 58.9 / MAE 34.2 pts
  - block bootstrap, mean pts/trade (all days): {'n': 362, 'mean': 4.14, 'ci95': [-1.63, 9.16], 'p_mean_le_0': 0.093}
  - by year (all days): 2020: n=45 exp=-7.9 PF=0.41 B=-22472.91; 2021: n=69 exp=-4.57 PF=0.76 B=-22612.32; 2022: n=57 exp=4.93 PF=1.26 B=5424.03; 2023: n=64 exp=11.95 PF=2.07 B=23985.47; 2024: n=59 exp=-3.08 PF=0.84 B=-12210.41; 2025: n=48 exp=22.84 PF=2.23 B=47503.42; 2026: n=20 exp=10.41 PF=1.57 B=3998.84
- **+all_filters|with_missing**
  - matched: 1 trades | win 100.0% | avg win 233.95 / loss 0.0 pts | PF n/a | exp 233.95 pts | total 233.95 | maxDD 0.0 | hold 190.0m | MFE 235.7 / MAE 0.0 pts
  - non_matched: 362 trades | win 46.7% | avg win 43.35 / loss -31.59 pts | PF 1.2 | exp 3.4 pts | total 1230.42 | maxDD -842.23 | hold 107.3m | MFE 43.5 / MAE 28.4 pts
  - all: 363 trades | win 46.8% | avg win 44.47 / loss -31.59 pts | PF 1.24 | exp 4.03 pts | total 1464.37 | maxDD -842.23 | hold 107.5m | MFE 44.0 / MAE 28.3 pts
- **+ema200_30m|futures**
  - matched: 1 trades | win 100.0% | avg win 267.55 / loss 0.0 pts | PF n/a | exp 267.55 pts | total 267.55 | maxDD 0.0 | hold 280.0m | MFE 309.6 / MAE 2.5 pts
  - non_matched: 912 trades | win 36.8% | avg win 49.83 / loss -29.84 pts | PF 0.97 | exp -0.49 pts | total -446.85 | maxDD -889.4 | hold 90.2m | MFE 42.9 / MAE 29.1 pts
  - all: 913 trades | win 36.9% | avg win 50.47 / loss -29.84 pts | PF 0.99 | exp -0.2 pts | total -179.3 | maxDD -889.4 | hold 90.4m | MFE 43.2 / MAE 29.1 pts
  - all_B_rs: 908 trades | win 29.7% | avg win 2134.73 / loss -1046.56 Rs | PF 0.86 | exp -100.58 Rs | total -91330.36 | maxDD -102986.5 | hold 90.5m | MFE 43.3 / MAE 29.0 pts
  - all_C_rs: 16 trades | win 25.0% | avg win 3021.96 / loss -544.29 Rs | PF 1.85 | exp 347.27 Rs | total 5556.32 | maxDD -4916.82 | hold 87.8m | MFE 42.1 / MAE 27.3 pts
  - IS_all_points: 643 trades | win 37.6% | avg win 45.09 / loss -27.93 pts | PF 0.97 | exp -0.45 pts | total -289.3 | maxDD -889.4 | hold 92.5m | MFE 39.5 / MAE 26.5 pts
  - OOS_all_points: 270 trades | win 35.2% | avg win 64.18 / loss -34.21 pts | PF 1.02 | exp 0.41 pts | total 110.0 | maxDD -738.5 | hold 85.2m | MFE 52.0 / MAE 35.1 pts
  - IS_all_B_rs: 639 trades | win 29.6% | avg win 1926.25 / loss -977.1 Rs | PF 0.83 | exp -118.36 Rs | total -75632.62 | maxDD -86703.26 | hold 92.6m | MFE 39.6 / MAE 26.5 pts
  - OOS_all_B_rs: 269 trades | win 30.1% | avg win 2621.16 / loss -1212.83 Rs | PF 0.93 | exp -58.36 Rs | total -15697.74 | maxDD -33956.23 | hold 85.5m | MFE 52.2 / MAE 35.1 pts
  - block bootstrap, mean pts/trade (all days): {'n': 913, 'mean': -0.2, 'ci95': [-3.39, 2.61], 'p_mean_le_0': 0.5798}
  - by year (all days): 2020: n=106 exp=-4.31 PF=0.66 B=-34483.74; 2021: n=165 exp=0.67 PF=1.04 B=-14968.24; 2022: n=138 exp=-2.63 PF=0.87 B=-28988.14; 2023: n=152 exp=4.01 PF=1.31 B=6205.47; 2024: n=146 exp=-1.75 PF=0.93 B=-11806.13; 2025: n=133 exp=3.35 PF=1.15 B=10077.8; 2026: n=73 exp=-3.67 PF=0.85 B=-17367.38
- **+ema200_30m|with_missing**
  - matched: 1 trades | win 100.0% | avg win 267.55 / loss 0.0 pts | PF n/a | exp 267.55 pts | total 267.55 | maxDD 0.0 | hold 280.0m | MFE 309.6 / MAE 2.5 pts
  - non_matched: 914 trades | win 36.8% | avg win 49.83 / loss -29.97 pts | PF 0.97 | exp -0.64 pts | total -582.0 | maxDD -923.0 | hold 90.1m | MFE 42.8 / MAE 29.2 pts
  - all: 915 trades | win 36.8% | avg win 50.47 / loss -29.97 pts | PF 0.98 | exp -0.34 pts | total -314.45 | maxDD -923.0 | hold 90.3m | MFE 43.1 / MAE 29.2 pts
- **+pwh_break|futures**
  - matched: 1 trades | win 100.0% | avg win 227.65 / loss 0.0 pts | PF n/a | exp 227.65 pts | total 227.65 | maxDD 0.0 | hold 275.0m | MFE 269.6 / MAE 0.0 pts
  - non_matched: 573 trades | win 38.2% | avg win 41.83 / loss -30.4 pts | PF 0.85 | exp -2.79 pts | total -1599.95 | maxDD -1667.75 | hold 93.5m | MFE 41.1 / MAE 29.6 pts
  - all: 574 trades | win 38.3% | avg win 42.68 / loss -30.4 pts | PF 0.87 | exp -2.39 pts | total -1372.3 | maxDD -1667.75 | hold 93.8m | MFE 41.5 / MAE 29.5 pts
  - all_B_rs: 571 trades | win 29.8% | avg win 1801.23 / loss -1038.18 Rs | PF 0.74 | exp -192.82 Rs | total -110100.5 | maxDD -119730.03 | hold 93.9m | MFE 41.6 / MAE 29.5 pts
  - all_C_rs: 7 trades | win 42.9% | avg win 3335.2 / loss -798.88 Rs | PF 3.13 | exp 972.87 Rs | total 6810.09 | maxDD -3186.76 | hold 98.6m | MFE 60.4 / MAE 27.5 pts
  - IS_all_points: 412 trades | win 38.6% | avg win 38.53 / loss -28.92 pts | PF 0.84 | exp -2.89 pts | total -1190.95 | maxDD -1310.0 | hold 93.5m | MFE 37.6 / MAE 26.9 pts
  - OOS_all_points: 162 trades | win 37.7% | avg win 53.5 / loss -34.11 pts | PF 0.95 | exp -1.12 pts | total -181.35 | maxDD -588.65 | hold 94.5m | MFE 51.4 / MAE 36.1 pts
  - IS_all_B_rs: 410 trades | win 29.0% | avg win 1657.33 / loss -977.68 Rs | PF 0.69 | exp -212.88 Rs | total -87282.62 | maxDD -90026.36 | hold 93.5m | MFE 37.6 / MAE 26.9 pts
  - OOS_all_B_rs: 161 trades | win 31.7% | avg win 2137.01 / loss -1198.23 Rs | PF 0.83 | exp -141.73 Rs | total -22817.88 | maxDD -32134.57 | hold 95.1m | MFE 51.7 / MAE 36.2 pts
  - block bootstrap, mean pts/trade (all days): {'n': 574, 'mean': -2.39, 'ci95': [-6.26, 1.07], 'p_mean_le_0': 0.924}
  - by year (all days): 2020: n=73 exp=-7.75 PF=0.46 B=-32827.21; 2021: n=101 exp=-1.2 PF=0.93 B=-15766.62; 2022: n=90 exp=-3.87 PF=0.81 B=-24691.0; 2023: n=96 exp=4.28 PF=1.31 B=6984.03; 2024: n=100 exp=-9.9 PF=0.57 B=-45019.5; 2025: n=76 exp=7.14 PF=1.35 B=17416.34; 2026: n=38 exp=-7.94 PF=0.67 B=-16196.54
- **+pwh_break|with_missing**
  - matched: 1 trades | win 100.0% | avg win 227.65 / loss 0.0 pts | PF n/a | exp 227.65 pts | total 227.65 | maxDD 0.0 | hold 275.0m | MFE 269.6 / MAE 0.0 pts
  - non_matched: 574 trades | win 38.2% | avg win 41.83 / loss -30.41 pts | PF 0.85 | exp -2.85 pts | total -1633.55 | maxDD -1701.35 | hold 93.5m | MFE 41.0 / MAE 29.6 pts
  - all: 575 trades | win 38.3% | avg win 42.68 / loss -30.41 pts | PF 0.87 | exp -2.45 pts | total -1405.9 | maxDD -1701.35 | hold 93.8m | MFE 41.4 / MAE 29.5 pts
- **+r1_pdh_break|futures**
  - matched: 1 trades | win 100.0% | avg win 253.75 / loss 0.0 pts | PF n/a | exp 253.75 pts | total 253.75 | maxDD 0.0 | hold 285.0m | MFE 295.8 / MAE 16.3 pts
  - non_matched: 614 trades | win 46.3% | avg win 56.19 / loss -34.57 pts | PF 1.4 | exp 7.41 pts | total 4550.0 | maxDD -565.45 | hold 119.7m | MFE 53.9 / MAE 30.5 pts
  - all: 615 trades | win 46.3% | avg win 56.88 / loss -34.57 pts | PF 1.42 | exp 7.81 pts | total 4803.75 | maxDD -565.45 | hold 120.0m | MFE 54.3 / MAE 30.4 pts
  - all_B_rs: 612 trades | win 37.6% | avg win 2519.08 / loss -1145.09 Rs | PF 1.32 | exp 231.97 Rs | total 141962.91 | maxDD -30526.42 | hold 120.0m | MFE 54.4 / MAE 30.4 pts
  - all_C_rs: 10 trades | win 30.0% | avg win 4121.47 / loss -1044.86 Rs | PF 1.69 | exp 505.04 Rs | total 5050.36 | maxDD -6723.2 | hold 118.5m | MFE 68.8 / MAE 24.2 pts
  - IS_all_points: 424 trades | win 46.7% | avg win 50.48 / loss -31.37 pts | PF 1.41 | exp 6.85 pts | total 2905.2 | maxDD -462.7 | hold 120.9m | MFE 47.9 / MAE 27.3 pts
  - OOS_all_points: 191 trades | win 45.5% | avg win 71.46 / loss -41.52 pts | PF 1.44 | exp 9.94 pts | total 1898.55 | maxDD -565.45 | hold 118.1m | MFE 68.5 / MAE 37.4 pts
  - IS_all_B_rs: 421 trades | win 36.3% | avg win 2258.5 / loss -1021.24 Rs | PF 1.26 | exp 170.68 Rs | total 71858.15 | maxDD -30526.42 | hold 120.9m | MFE 48.0 / MAE 27.2 pts
  - OOS_all_B_rs: 191 trades | win 40.3% | avg win 3036.85 / loss -1436.25 Rs | PF 1.43 | exp 367.04 Rs | total 70104.76 | maxDD -25603.76 | hold 118.1m | MFE 68.5 / MAE 37.4 pts
  - block bootstrap, mean pts/trade (all days): {'n': 615, 'mean': 7.81, 'ci95': [3.03, 12.43], 'p_mean_le_0': 0.0}
  - by year (all days): 2020: n=67 exp=-5.72 PF=0.58 B=-27479.96; 2021: n=111 exp=3.63 PF=1.21 B=1053.76; 2022: n=98 exp=13.57 PF=1.72 B=42607.01; 2023: n=103 exp=9.18 PF=1.74 B=26773.75; 2024: n=89 exp=8.58 PF=1.38 B=34186.17; 2025: n=89 exp=18.41 PF=1.85 B=72136.46; 2026: n=58 exp=1.82 PF=1.07 B=-7314.28
- **+r1_pdh_break|with_missing**
  - matched: 1 trades | win 100.0% | avg win 253.75 / loss 0.0 pts | PF n/a | exp 253.75 pts | total 253.75 | maxDD 0.0 | hold 285.0m | MFE 295.8 / MAE 16.3 pts
  - non_matched: 616 trades | win 46.1% | avg win 56.19 / loss -34.52 pts | PF 1.39 | exp 7.3 pts | total 4495.8 | maxDD -565.45 | hold 119.6m | MFE 53.8 / MAE 30.5 pts
  - all: 617 trades | win 46.2% | avg win 56.88 / loss -34.52 pts | PF 1.41 | exp 7.7 pts | total 4749.55 | maxDD -565.45 | hold 119.9m | MFE 54.2 / MAE 30.4 pts
- **+target_r4_mh3|futures**
  - matched: 1 trades | win 100.0% | avg win 295.85 / loss 0.0 pts | PF n/a | exp 295.85 pts | total 295.85 | maxDD 0.0 | hold 220.0m | MFE 297.6 / MAE 44.4 pts
  - non_matched: 1159 trades | win 39.2% | avg win 48.84 / loss -32.84 pts | PF 0.96 | exp -0.84 pts | total -976.07 | maxDD -1714.13 | hold 83.6m | MFE 44.0 / MAE 31.0 pts
  - all: 1160 trades | win 39.2% | avg win 49.39 / loss -32.84 pts | PF 0.97 | exp -0.59 pts | total -680.22 | maxDD -1714.13 | hold 83.7m | MFE 44.2 / MAE 31.0 pts
  - all_B_rs: 1155 trades | win 31.9% | avg win 2065.19 / loss -1120.15 Rs | PF 0.87 | exp -102.49 Rs | total -118379.91 | maxDD -132248.62 | hold 83.8m | MFE 44.3 / MAE 31.0 pts
  - all_C_rs: 27 trades | win 22.2% | avg win 2974.83 / loss -915.5 Rs | PF 0.93 | exp -50.99 Rs | total -1376.64 | maxDD -16609.8 | hold 69.3m | MFE 38.7 / MAE 29.9 pts
  - IS_all_points: 762 trades | win 40.3% | avg win 43.17 / loss -30.54 pts | PF 0.95 | exp -0.84 pts | total -641.03 | maxDD -1257.59 | hold 87.5m | MFE 39.7 / MAE 28.2 pts
  - OOS_all_points: 398 trades | win 37.2% | avg win 62.28 / loss -37.03 pts | PF 1.0 | exp -0.1 pts | total -39.19 | maxDD -797.21 | hold 76.4m | MFE 52.9 / MAE 36.3 pts
  - IS_all_B_rs: 758 trades | win 31.7% | avg win 1841.56 / loss -1029.27 Rs | PF 0.83 | exp -120.3 Rs | total -91187.14 | maxDD -94891.89 | hold 87.6m | MFE 39.8 / MAE 28.2 pts
  - OOS_all_B_rs: 397 trades | win 32.5% | avg win 2481.26 / loss -1295.8 Rs | PF 0.92 | exp -68.5 Rs | total -27192.77 | maxDD -44424.83 | hold 76.6m | MFE 53.0 / MAE 36.4 pts
  - block bootstrap, mean pts/trade (all days): {'n': 1160, 'mean': -0.59, 'ci95': [-3.49, 2.05], 'p_mean_le_0': 0.706}
  - by year (all days): 2020: n=111 exp=-2.34 PF=0.81 B=-27684.73; 2021: n=190 exp=-1.61 PF=0.91 B=-32532.65; 2022: n=176 exp=0.97 PF=1.05 B=-2261.02; 2023: n=188 exp=4.11 PF=1.31 B=8783.73; 2024: n=186 exp=-7.94 PF=0.71 B=-58287.52; 2025: n=179 exp=4.38 PF=1.21 B=20008.18; 2026: n=130 exp=-2.82 PF=0.89 B=-26405.9
- **+target_r4_mh3|with_missing**
  - matched: 1 trades | win 100.0% | avg win 295.85 / loss 0.0 pts | PF n/a | exp 295.85 pts | total 295.85 | maxDD 0.0 | hold 220.0m | MFE 297.6 / MAE 44.4 pts
  - non_matched: 1162 trades | win 39.1% | avg win 48.84 / loss -32.92 pts | PF 0.95 | exp -0.97 pts | total -1131.82 | maxDD -1815.68 | hold 83.5m | MFE 43.9 / MAE 31.1 pts
  - all: 1163 trades | win 39.1% | avg win 49.39 / loss -32.92 pts | PF 0.96 | exp -0.72 pts | total -835.97 | maxDD -1815.68 | hold 83.6m | MFE 44.1 / MAE 31.1 pts
- **base|futures**
  - matched: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - non_matched: 1159 trades | win 37.8% | avg win 52.57 / loss -32.71 pts | PF 0.98 | exp -0.48 pts | total -557.65 | maxDD -1370.75 | hold 90.2m | MFE 46.7 / MAE 31.3 pts
  - all: 1160 trades | win 37.8% | avg win 53.11 / loss -32.71 pts | PF 0.99 | exp -0.23 pts | total -268.1 | maxDD -1370.75 | hold 90.4m | MFE 46.9 / MAE 31.3 pts
  - all_B_rs: 1155 trades | win 30.9% | avg win 2252.48 / loss -1125.6 Rs | PF 0.9 | exp -81.46 Rs | total -94088.29 | maxDD -107556.07 | hold 90.5m | MFE 47.0 / MAE 31.3 pts
  - all_C_rs: 27 trades | win 22.2% | avg win 2673.16 / loss -915.5 Rs | PF 0.83 | exp -118.02 Rs | total -3186.66 | maxDD -16992.56 | hold 76.1m | MFE 40.7 / MAE 29.9 pts
  - IS_all_points: 762 trades | win 38.7% | avg win 48.09 / loss -30.58 pts | PF 0.99 | exp -0.12 pts | total -90.85 | maxDD -1057.7 | hold 94.0m | MFE 42.4 / MAE 28.6 pts
  - OOS_all_points: 398 trades | win 36.2% | avg win 63.39 / loss -36.64 pts | PF 0.98 | exp -0.45 pts | total -177.25 | maxDD -1026.9 | hold 83.6m | MFE 55.5 / MAE 36.5 pts
  - IS_all_B_rs: 758 trades | win 30.7% | avg win 2083.12 / loss -1044.37 Rs | PF 0.89 | exp -83.02 Rs | total -62928.76 | maxDD -73999.4 | hold 94.1m | MFE 42.5 / MAE 28.6 pts
  - OOS_all_B_rs: 397 trades | win 31.2% | avg win 2570.72 / loss -1281.79 Rs | PF 0.91 | exp -78.49 Rs | total -31159.53 | maxDD -53571.35 | hold 83.8m | MFE 55.7 / MAE 36.6 pts
  - block bootstrap, mean pts/trade (all days): {'n': 1160, 'mean': -0.23, 'ci95': [-3.27, 2.58], 'p_mean_le_0': 0.609}
  - by year (all days): 2020: n=111 exp=-2.59 PF=0.8 B=-28565.21; 2021: n=190 exp=-1.11 PF=0.94 B=-26888.77; 2022: n=176 exp=1.64 PF=1.08 B=4997.73; 2023: n=188 exp=3.96 PF=1.29 B=8442.17; 2024: n=186 exp=-6.08 PF=0.77 B=-44592.72; 2025: n=179 exp=5.32 PF=1.25 B=29265.95; 2026: n=130 exp=-4.8 PF=0.81 B=-36747.44
- **base|with_missing**
  - matched: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - non_matched: 1162 trades | win 37.7% | avg win 52.57 / loss -32.79 pts | PF 0.97 | exp -0.61 pts | total -713.4 | maxDD -1472.3 | hold 90.2m | MFE 46.6 / MAE 31.4 pts
  - all: 1163 trades | win 37.7% | avg win 53.11 / loss -32.79 pts | PF 0.98 | exp -0.36 pts | total -423.85 | maxDD -1472.3 | hold 90.3m | MFE 46.8 / MAE 31.4 pts

### Option layers (base, futures VWAP)
- **matched**
  - A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - C: 1 trades | win 100.0% | avg win 12341.79 / loss 0.0 Rs | PF n/a | exp 12341.79 Rs | total 12341.79 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **non_matched**
  - A: 1159 trades | win 37.8% | avg win 52.57 / loss -32.71 pts | PF 0.98 | exp -0.48 pts | total -557.65 | maxDD -1370.75 | hold 90.2m | MFE 46.7 / MAE 31.3 pts
  - B: 1154 trades | win 30.8% | avg win 2220.98 / loss -1125.6 Rs | PF 0.88 | exp -93.2 Rs | total -107556.07 | maxDD -107556.07 | hold 90.4m | MFE 46.8 / MAE 31.3 pts
  - C: 26 trades | win 19.2% | avg win 739.43 / loss -915.5 Rs | PF 0.19 | exp -597.25 Rs | total -15528.45 | maxDD -16992.56 | hold 67.3m | MFE 29.5 / MAE 29.3 pts
- **all**
  - A: 1160 trades | win 37.8% | avg win 53.11 / loss -32.71 pts | PF 0.99 | exp -0.23 pts | total -268.1 | maxDD -1370.75 | hold 90.4m | MFE 46.9 / MAE 31.3 pts
  - B: 1155 trades | win 30.9% | avg win 2252.48 / loss -1125.6 Rs | PF 0.9 | exp -81.46 Rs | total -94088.29 | maxDD -107556.07 | hold 90.5m | MFE 47.0 / MAE 31.3 pts
  - C: 27 trades | win 22.2% | avg win 2673.16 / loss -915.5 Rs | PF 0.83 | exp -118.02 Rs | total -3186.66 | maxDD -16992.56 | hold 76.1m | MFE 40.7 / MAE 29.9 pts
- **B vs C on real-price days**: {'trades': 26, 'B_total': -2568.7, 'C_total': -3094.78, 'mean_abs_diff_per_trade': 512.72, 'sign_agreement_pct': 84.6}

### In-sample vs out-of-sample (base)
- IS matched: 0 trades
- IS non-matched: 762 trades | win 38.7% | avg win 48.09 / loss -30.58 pts | PF 0.99 | exp -0.12 pts | total -90.85 | maxDD -1057.7 | hold 94.0m | MFE 42.4 / MAE 28.6 pts
- IS matched B: 0 trades
- IS non-matched B: 758 trades | win 30.7% | avg win 2083.12 / loss -1044.37 Rs | PF 0.89 | exp -83.02 Rs | total -62928.76 | maxDD -73999.4 | hold 94.1m | MFE 42.5 / MAE 28.6 pts
- OOS matched: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- OOS non-matched: 397 trades | win 36.0% | avg win 61.81 / loss -36.64 pts | PF 0.95 | exp -1.18 pts | total -466.8 | maxDD -1026.9 | hold 83.1m | MFE 54.8 / MAE 36.5 pts
- OOS matched B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- OOS non-matched B: 396 trades | win 31.1% | avg win 2482.12 / loss -1281.79 Rs | PF 0.87 | exp -112.7 Rs | total -44627.31 | maxDD -53571.35 | hold 83.3m | MFE 55.0 / MAE 36.6 pts

### by day type (base, points)
- range: matched 0 trades || non-matched 764 trades | win 35.9% | avg win 37.92 / loss -31.92 pts | PF 0.66 | exp -6.87 pts | total -5248.1 | maxDD -5356.55 | hold 81.1m | MFE 41.0 / MAE 31.3 pts
- trending: matched 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts || non-matched 395 trades | win 41.5% | avg win 77.05 / loss -34.4 pts | PF 1.59 | exp 11.87 pts | total 4690.45 | maxDD -736.35 | hold 108.0m | MFE 57.6 / MAE 31.3 pts

### by cpr width (base, points)
- narrow: matched 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts || non-matched 551 trades | win 35.6% | avg win 52.24 / loss -29.5 pts | PF 0.98 | exp -0.43 pts | total -235.3 | maxDD -2078.55 | hold 87.5m | MFE 43.0 / MAE 28.5 pts
- normal: matched 0 trades || non-matched 457 trades | win 41.6% | avg win 52.81 / loss -32.03 pts | PF 1.17 | exp 3.24 pts | total 1481.7 | maxDD -1227.2 | hold 97.0m | MFE 49.7 / MAE 30.2 pts
- wide: matched 0 trades || non-matched 151 trades | win 34.4% | avg win 52.96 / loss -46.04 pts | PF 0.6 | exp -11.95 pts | total -1804.05 | maxDD -1812.35 | hold 79.6m | MFE 51.0 / MAE 45.0 pts

### Field tests on +r1_pdh_break (pre-declared, one field at a time, 5 tests, no combinations)
- **reference_no_field_filter** 
  - A_points: 615 trades | win 46.3% | avg win 56.88 / loss -34.57 pts | PF 1.42 | exp 7.81 pts | total 4803.75 | maxDD -565.45 | hold 120.0m | MFE 54.3 / MAE 30.4 pts
  - B_modelled_rs: 612 trades | win 37.6% | avg win 2519.08 / loss -1145.09 Rs | PF 1.32 | exp 231.97 Rs | total 141962.91 | maxDD -30526.42 | hold 120.0m | MFE 54.4 / MAE 30.4 pts
  - C_real_rs: 10 trades | win 30.0% | avg win 4121.47 / loss -1044.86 Rs | PF 1.69 | exp 505.04 Rs | total 5050.36 | maxDD -6723.2 | hold 118.5m | MFE 68.8 / MAE 24.2 pts
  - IS_A_points: 424 trades | win 46.7% | avg win 50.48 / loss -31.37 pts | PF 1.41 | exp 6.85 pts | total 2905.2 | maxDD -462.7 | hold 120.9m | MFE 47.9 / MAE 27.3 pts
  - OOS_A_points: 191 trades | win 45.5% | avg win 71.46 / loss -41.52 pts | PF 1.44 | exp 9.94 pts | total 1898.55 | maxDD -565.45 | hold 118.1m | MFE 68.5 / MAE 37.4 pts
  - IS_B_rs: 421 trades | win 36.3% | avg win 2258.5 / loss -1021.24 Rs | PF 1.26 | exp 170.68 Rs | total 71858.15 | maxDD -30526.42 | hold 120.9m | MFE 48.0 / MAE 27.2 pts
  - OOS_B_rs: 191 trades | win 40.3% | avg win 3036.85 / loss -1436.25 Rs | PF 1.43 | exp 367.04 Rs | total 70104.76 | maxDD -25603.76 | hold 118.1m | MFE 68.5 / MAE 37.4 pts
  - bootstrap pts/trade: {'n': 615, 'mean': 7.81, 'ci95': [3.03, 12.43], 'p_mean_le_0': 0.0}
  - by year: 2020: n=67 exp=-5.72 PF=0.58 B=-27479.96; 2021: n=111 exp=3.63 PF=1.21 B=1053.76; 2022: n=98 exp=13.57 PF=1.72 B=42607.01; 2023: n=103 exp=9.18 PF=1.74 B=26773.75; 2024: n=89 exp=8.58 PF=1.38 B=34186.17; 2025: n=89 exp=18.41 PF=1.85 B=72136.46; 2026: n=58 exp=1.82 PF=1.07 B=-7314.28
- **cpr_width** {'cpr_width': 'narrow'}
  - A_points: 307 trades | win 46.6% | avg win 52.78 / loss -33.06 pts | PF 1.39 | exp 6.92 pts | total 2124.8 | maxDD -714.1 | hold 119.4m | MFE 51.7 / MAE 28.5 pts
  - B_modelled_rs: 306 trades | win 38.2% | avg win 2286.68 / loss -1094.15 Rs | PF 1.29 | exp 198.52 Rs | total 60748.5 | maxDD -21460.63 | hold 119.5m | MFE 51.8 / MAE 28.4 pts
  - C_real_rs: 6 trades | win 33.3% | avg win 5281.2 / loss -954.79 Rs | PF 2.77 | exp 1123.88 Rs | total 6743.25 | maxDD -3465.57 | hold 115.8m | MFE 78.1 / MAE 21.7 pts
  - IS_A_points: 198 trades | win 46.5% | avg win 45.85 / loss -29.75 pts | PF 1.34 | exp 5.38 pts | total 1064.6 | maxDD -329.45 | hold 121.2m | MFE 43.7 / MAE 26.0 pts
  - OOS_A_points: 109 trades | win 46.8% | avg win 65.28 / loss -39.12 pts | PF 1.47 | exp 9.73 pts | total 1060.2 | maxDD -418.0 | hold 116.1m | MFE 66.1 / MAE 33.0 pts
  - IS_B_rs: 197 trades | win 36.5% | avg win 1985.96 / loss -968.93 Rs | PF 1.18 | exp 111.03 Rs | total 21873.19 | maxDD -17233.12 | hold 121.4m | MFE 43.9 / MAE 25.8 pts
  - OOS_B_rs: 109 trades | win 41.3% | avg win 2767.85 / loss -1338.71 Rs | PF 1.45 | exp 356.65 Rs | total 38875.31 | maxDD -17410.11 | hold 116.1m | MFE 66.1 / MAE 33.0 pts
  - bootstrap pts/trade: {'n': 307, 'mean': 6.92, 'ci95': [-0.42, 13.99], 'p_mean_le_0': 0.0336}
  - by year: 2020: n=25 exp=-9.59 PF=0.4 B=-12810.04; 2021: n=52 exp=14.44 PF=2.27 B=19066.58; 2022: n=37 exp=11.78 PF=1.67 B=14701.97; 2023: n=56 exp=3.4 PF=1.26 B=1351.83; 2024: n=57 exp=-3.87 PF=0.86 B=-3502.52; 2025: n=49 exp=17.62 PF=1.94 B=34595.11; 2026: n=31 exp=11.11 PF=1.59 B=7345.57
- **cpr_vs_prior_day** {'cpr_vs_prior_day': 'overlapping_higher'}
  - A_points: 35 trades | win 34.3% | avg win 69.1 / loss -35.05 pts | PF 1.03 | exp 0.66 pts | total 23.1 | maxDD -475.35 | hold 98.0m | MFE 50.9 / MAE 34.2 pts
  - B_modelled_rs: 35 trades | win 28.6% | avg win 3029.46 / loss -1258.33 Rs | PF 0.96 | exp -33.25 Rs | total -1163.58 | maxDD -20462.31 | hold 98.0m | MFE 50.9 / MAE 34.2 pts
  - C_real_rs: 2 trades | win 100.0% | avg win 6005.41 / loss 0.0 Rs | PF n/a | exp 6005.41 Rs | total 12010.82 | maxDD 0.0 | hold 235.0m | MFE 201.2 / MAE 20.4 pts
  - IS_A_points: 22 trades | win 36.4% | avg win 46.32 / loss -28.94 pts | PF 0.91 | exp -1.57 pts | total -34.6 | maxDD -163.05 | hold 91.6m | MFE 39.5 / MAE 27.1 pts
  - OOS_A_points: 13 trades | win 30.8% | avg win 114.65 / loss -44.54 pts | PF 1.14 | exp 4.44 pts | total 57.7 | maxDD -321.85 | hold 108.8m | MFE 70.4 / MAE 46.3 pts
  - IS_B_rs: 22 trades | win 27.3% | avg win 2097.75 / loss -983.69 Rs | PF 0.8 | exp -143.3 Rs | total -3152.6 | maxDD -7344.31 | hold 91.6m | MFE 39.5 / MAE 27.1 pts
  - OOS_B_rs: 13 trades | win 30.8% | avg win 4427.03 / loss -1746.57 Rs | PF 1.13 | exp 153.0 Rs | total 1989.02 | maxDD -13118.0 | hold 108.8m | MFE 70.4 / MAE 46.3 pts
  - bootstrap pts/trade: {'n': 35, 'mean': 0.66, 'ci95': [-24.48, 15.09], 'p_mean_le_0': 0.7692}
  - by year: 2020: n=3 exp=-4.35 PF=0.71 B=-248.08; 2021: n=6 exp=-16.78 PF=0.21 B=-4056.6; 2022: n=7 exp=20.94 PF=2.17 B=4681.64; 2023: n=4 exp=-2.74 PF=0.78 B=-1148.45; 2024: n=6 exp=-33.91 PF=0.0 B=-7960.09; 2025: n=3 exp=-11.88 PF=0.69 B=-1500.33; 2026: n=6 exp=40.05 PF=2.73 B=9068.33
- **cpr_vs_weekly** {'cpr_vs_weekly': 'above'}
  - A_points: 342 trades | win 45.6% | avg win 48.27 / loss -30.4 pts | PF 1.33 | exp 5.48 pts | total 1875.65 | maxDD -626.35 | hold 120.9m | MFE 48.2 / MAE 28.1 pts
  - B_modelled_rs: 341 trades | win 36.1% | avg win 2212.48 / loss -1020.65 Rs | PF 1.22 | exp 145.56 Rs | total 49634.76 | maxDD -42021.32 | hold 121.1m | MFE 48.2 / MAE 28.0 pts
  - C_real_rs: 2 trades | win 100.0% | avg win 5281.2 / loss 0.0 Rs | PF n/a | exp 5281.2 Rs | total 10562.4 | maxDD 0.0 | hold 237.5m | MFE 178.2 / MAE 20.7 pts
  - IS_A_points: 257 trades | win 45.5% | avg win 43.83 / loss -29.34 pts | PF 1.25 | exp 3.97 pts | total 1020.25 | maxDD -626.35 | hold 119.7m | MFE 43.4 / MAE 26.3 pts
  - OOS_A_points: 85 trades | win 45.9% | avg win 61.58 / loss -33.61 pts | PF 1.55 | exp 10.06 pts | total 855.4 | maxDD -346.6 | hold 124.8m | MFE 62.4 / MAE 33.3 pts
  - IS_B_rs: 256 trades | win 34.8% | avg win 2008.61 / loss -963.53 Rs | PF 1.11 | exp 69.76 Rs | total 17857.39 | maxDD -42021.32 | hold 119.9m | MFE 43.5 / MAE 26.2 pts
  - OOS_B_rs: 85 trades | win 40.0% | avg win 2746.15 / loss -1207.68 Rs | PF 1.52 | exp 373.85 Rs | total 31777.37 | maxDD -17020.06 | hold 124.8m | MFE 62.4 / MAE 33.3 pts
  - bootstrap pts/trade: {'n': 342, 'mean': 5.48, 'ci95': [-0.59, 10.95], 'p_mean_le_0': 0.0426}
  - by year: 2020: n=48 exp=-9.37 PF=0.39 B=-25724.13; 2021: n=69 exp=-0.15 PF=0.99 B=-8803.57; 2022: n=57 exp=2.28 PF=1.12 B=-2951.09; 2023: n=59 exp=16.65 PF=2.66 B=34535.62; 2024: n=52 exp=1.02 PF=1.05 B=4558.5; 2025: n=39 exp=21.88 PF=2.27 B=39274.62; 2026: n=18 exp=17.6 PF=2.13 B=8744.81
- **cpr_vs_monthly** {'cpr_vs_monthly': 'below'}
  - A_points: 138 trades | win 41.3% | avg win 73.94 / loss -44.99 pts | PF 1.16 | exp 4.14 pts | total 571.0 | maxDD -1067.95 | hold 118.4m | MFE 62.9 / MAE 41.0 pts
  - B_modelled_rs: 138 trades | win 34.8% | avg win 3047.45 / loss -1495.35 Rs | PF 1.09 | exp 84.76 Rs | total 11696.6 | maxDD -45369.39 | hold 118.4m | MFE 62.9 / MAE 41.0 pts
  - C_real_rs: 5 trades | win 20.0% | avg win 10208.82 / loss -1242.38 Rs | PF 2.05 | exp 1047.86 Rs | total 5239.31 | maxDD -4969.51 | hold 93.0m | MFE 77.6 / MAE 24.7 pts
  - IS_A_points: 79 trades | win 46.8% | avg win 69.67 / loss -38.13 pts | PF 1.61 | exp 12.36 pts | total 976.4 | maxDD -396.35 | hold 128.4m | MFE 59.9 / MAE 33.3 pts
  - OOS_A_points: 59 trades | win 33.9% | avg win 81.85 / loss -52.37 pts | PF 0.8 | exp -6.87 pts | total -405.4 | maxDD -679.85 | hold 105.0m | MFE 67.0 / MAE 51.3 pts
  - IS_B_rs: 79 trades | win 40.5% | avg win 2755.69 / loss -1277.43 Rs | PF 1.47 | exp 356.24 Rs | total 28143.13 | maxDD -17747.28 | hold 128.4m | MFE 59.9 / MAE 33.3 pts
  - OOS_B_rs: 59 trades | win 27.1% | avg win 3630.97 / loss -1733.54 Rs | PF 0.78 | exp -278.75 Rs | total -16446.53 | maxDD -30815.07 | hold 105.0m | MFE 67.0 / MAE 51.3 pts
  - bootstrap pts/trade: {'n': 138, 'mean': 4.14, 'ci95': [-9.29, 14.26], 'p_mean_le_0': 0.3504}
  - by year: 2020: n=4 exp=18.9 PF=2.19 B=2536.5; 2021: n=15 exp=42.69 PF=6.53 B=20859.25; 2022: n=29 exp=18.19 PF=1.68 B=17889.16; 2023: n=26 exp=-4.32 PF=0.75 B=-7601.58; 2024: n=18 exp=-22.4 PF=0.3 B=-15849.31; 2025: n=24 exp=4.99 PF=1.16 B=6624.54; 2026: n=22 exp=-12.58 PF=0.69 B=-12761.96
- **open_location** {'open_vs_cpr': 'above', 'open_vs_daily_h3': 'below', 'open_vs_weekly_h3': 'below'}
  - A_points: 81 trades | win 44.4% | avg win 88.66 / loss -30.95 pts | PF 2.29 | exp 22.21 pts | total 1798.95 | maxDD -276.7 | hold 121.7m | MFE 69.8 / MAE 31.4 pts
  - B_modelled_rs: 81 trades | win 34.6% | avg win 4476.1 / loss -1084.12 Rs | PF 2.18 | exp 837.93 Rs | total 67872.32 | maxDD -13938.31 | hold 121.7m | MFE 69.8 / MAE 31.4 pts
  - C_real_rs: 1 trades | win 100.0% | avg win 10208.82 / loss 0.0 Rs | PF n/a | exp 10208.82 Rs | total 10208.82 | maxDD 0.0 | hold 285.0m | MFE 295.8 / MAE 16.3 pts
  - IS_A_points: 46 trades | win 39.1% | avg win 73.86 / loss -26.58 pts | PF 1.79 | exp 12.72 pts | total 585.3 | maxDD -276.7 | hold 114.9m | MFE 51.7 / MAE 26.0 pts
  - OOS_A_points: 35 trades | win 51.4% | avg win 103.46 / loss -38.16 pts | PF 2.87 | exp 34.68 pts | total 1213.65 | maxDD -201.05 | hold 130.7m | MFE 93.5 / MAE 38.5 pts
  - IS_B_rs: 46 trades | win 28.3% | avg win 4028.13 / loss -923.88 Rs | PF 1.72 | exp 475.6 Rs | total 21877.73 | maxDD -13938.31 | hold 114.9m | MFE 51.7 / MAE 26.0 pts
  - OOS_B_rs: 35 trades | win 42.9% | avg win 4864.33 / loss -1348.52 Rs | PF 2.71 | exp 1314.13 Rs | total 45994.59 | maxDD -7977.81 | hold 130.7m | MFE 93.5 / MAE 38.5 pts
  - bootstrap pts/trade: {'n': 81, 'mean': 22.21, 'ci95': [5.52, 37.38], 'p_mean_le_0': 0.0034}
  - by year: 2020: n=5 exp=-17.78 PF=0.0 B=-4081.23; 2021: n=15 exp=-9.52 PF=0.52 B=-8983.04; 2022: n=7 exp=24.38 PF=2.31 B=6126.86; 2023: n=12 exp=-2.08 PF=0.87 B=-1844.59; 2024: n=17 exp=52.45 PF=5.07 B=38877.68; 2025: n=16 exp=48.55 PF=5.36 B=30483.75; 2026: n=9 exp=24.07 PF=1.74 B=7292.89

### Setup sensitivity - EXPLORATORY, not rules (base, futures VWAP)
Each row loosens the setup; many looks at one dataset, so a single good row is weak evidence.
- **drop cpr_width** (1 sessions): A 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **drop cpr_vs_prior_day** (3 sessions): A 3 trades | win 66.7% | avg win 152.93 / loss -9.25 pts | PF 33.06 | exp 98.87 pts | total 296.6 | maxDD -9.25 | hold 121.7m | MFE 130.1 / MAE 20.7 pts
  - B: 3 trades | win 66.7% | avg win 6863.27 / loss -484.96 Rs | PF 28.3 | exp 4413.86 Rs | total 13241.58 | maxDD -484.96 | hold 121.7m | MFE 130.1 / MAE 20.7 pts
  - OOS A: 2 trades | win 50.0% | avg win 289.55 / loss -9.25 pts | PF 31.3 | exp 140.15 pts | total 280.3 | maxDD -9.25 | hold 155.0m | MFE 168.8 / MAE 28.9 pts
- **drop cpr_vs_weekly** (1 sessions): A 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **drop cpr_vs_monthly** (2 sessions): A 2 trades | win 50.0% | avg win 289.55 / loss -22.5 pts | PF 12.87 | exp 133.53 pts | total 267.05 | maxDD -22.5 | hold 185.0m | MFE 174.3 / MAE 36.2 pts
  - B: 2 trades | win 50.0% | avg win 13467.78 / loss -1028.27 Rs | PF 13.1 | exp 6219.76 Rs | total 12439.51 | maxDD -1028.27 | hold 185.0m | MFE 174.3 / MAE 36.2 pts
  - OOS A: 2 trades | win 50.0% | avg win 289.55 / loss -22.5 pts | PF 12.87 | exp 133.53 pts | total 267.05 | maxDD -22.5 | hold 185.0m | MFE 174.3 / MAE 36.2 pts
- **drop open_vs_cpr** (2 sessions): A 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **drop open_vs_daily_h3** (1 sessions): A 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **drop open_vs_weekly_h3** (1 sessions): A 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **CPR fields only** (3 sessions): A 2 trades | win 100.0% | avg win 170.38 / loss 0.0 pts | PF n/a | exp 170.38 pts | total 340.75 | maxDD 0.0 | hold 230.0m | MFE 214.4 / MAE 22.2 pts
  - B: 2 trades | win 100.0% | avg win 7516.31 / loss 0.0 Rs | PF n/a | exp 7516.31 Rs | total 15032.61 | maxDD 0.0 | hold 230.0m | MFE 214.4 / MAE 22.2 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **CPR width only** (712 sessions): A 552 trades | win 35.7% | avg win 53.44 / loss -29.5 pts | PF 1.01 | exp 0.1 pts | total 54.25 | maxDD -2078.55 | hold 87.9m | MFE 43.5 / MAE 28.5 pts
  - B: 551 trades | win 29.6% | avg win 2255.64 / loss -1047.48 Rs | PF 0.9 | exp -70.33 Rs | total -38751.55 | maxDD -90893.98 | hold 87.9m | MFE 43.5 / MAE 28.5 pts
  - OOS A: 205 trades | win 35.1% | avg win 63.78 / loss -35.75 pts | PF 0.97 | exp -0.8 pts | total -163.2 | maxDD -1295.35 | hold 81.8m | MFE 51.4 / MAE 34.5 pts

### Walk-forward by year (base, points)
- 2020: matched 0 trades || non-matched 111 trades | win 39.6% | avg win 25.99 / loss -21.36 pts | PF 0.8 | exp -2.59 pts | total -287.95 | maxDD -377.2 | hold 87.7m | MFE 33.2 / MAE 20.1 pts
- 2021: matched 0 trades || non-matched 190 trades | win 40.5% | avg win 44.78 / loss -32.38 pts | PF 0.94 | exp -1.11 pts | total -211.25 | maxDD -690.2 | hold 97.5m | MFE 42.6 / MAE 29.5 pts
- 2022: matched 0 trades || non-matched 176 trades | win 35.8% | avg win 65.77 / loss -34.11 pts | PF 1.08 | exp 1.64 pts | total 289.15 | maxDD -416.55 | hold 94.5m | MFE 49.1 / MAE 33.3 pts
- 2023: matched 0 trades || non-matched 188 trades | win 42.0% | avg win 41.48 / loss -23.23 pts | PF 1.29 | exp 3.96 pts | total 745.4 | maxDD -338.5 | hold 99.8m | MFE 39.5 / MAE 21.6 pts
- 2024: matched 0 trades || non-matched 186 trades | win 31.2% | avg win 66.88 / loss -39.14 pts | PF 0.77 | exp -6.08 pts | total -1131.2 | maxDD -1370.75 | hold 79.0m | MFE 48.5 / MAE 40.2 pts
- 2025: matched 0 trades || non-matched 179 trades | win 41.3% | avg win 64.52 / loss -36.41 pts | PF 1.25 | exp 5.32 pts | total 951.45 | maxDD -531.6 | hold 89.4m | MFE 60.1 / MAE 34.8 pts
- 2026: matched 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts || non-matched 129 trades | win 33.3% | avg win 54.91 / loss -38.08 pts | PF 0.72 | exp -7.08 pts | total -913.25 | maxDD -1026.9 | hold 79.4m | MFE 50.1 / MAE 37.5 pts

## C001 re-verification (priority focus run)

Duplicate/conflict check vs existing rules: {'status': 'duplicate', 'of': 'C001'}

| Field | Stored | Data (2026-10-15) | Match |
|---|---|---|---|
| cpr_width | narrow | narrow | yes |
| cpr_vs_prior_day | overlapping_higher | overlapping_higher | yes |
| cpr_vs_weekly | above | above | yes |
| cpr_vs_monthly | below | below | yes |
| open_vs_cpr | above | above | yes |
| open_vs_daily_h3 | below | below | yes |
| open_vs_weekly_h3 | below | below | yes |
| golden_pivot_zone | (not a stored C001 field) | none |  |

Context at signal bar: {'signal_bar': '09:15', 'above_vwap': True, 'above_ema20_5m': True, 'above_ema50_5m': True, 'above_ema200_5m': True, 'above_ema200_1m': True, '30m_200ema_at_open_bar': 'below', '30m_200ema_first_above_bar_close': '09:45', '30m_close_minus_ema_at_first_above': 2.94}

Reproduction: {'entry_time': '2026-10-15 09:20', 'entry_px': np.float64(22724.85), 'exit_time': '2026-10-15 14:25', 'exit_px': np.float64(23014.4), 'exit_reason': 'close_vs_ema', 'points': np.float64(289.55), 'strike': np.float64(22700.0), 'B_entry': np.float64(136.71), 'B_exit': np.float64(347.36), 'B_net': np.float64(13467.78), 'C_entry': np.float64(139.35), 'C_exit': np.float64(332.65), 'C_net': np.float64(12341.79)}

C001 exact-match sessions: 1 of 1555

| Field (value) | Sessions matching this field alone | Sessions matching all OTHER fields |
|---|---|---|
| cpr_width (narrow) | 712 | 1 |
| cpr_vs_prior_day (overlapping_higher) | 93 | 3 |
| cpr_vs_weekly (above) | 755 | 1 |
| cpr_vs_monthly (below) | 413 | 2 |
| open_vs_cpr (above) | 859 | 2 |
| open_vs_daily_h3 (below) | 1008 | 1 |
| open_vs_weekly_h3 (below) | 1043 | 1 |

## Priority focus: base rule by position at the signal bar (descriptive, not tuned)

- **vwap** (one-sided by construction: being above this level is an entry condition of the base rule)
  - above: 1160 tr | win 37.8% | PF 0.99 | exp -0.23 pts | B -94,088 | IS PF 0.99 (n=762, B -62,929) | OOS PF 0.98 (n=398, B -31,160)
- **ema200_1m** (one-sided by construction: being above this level is an entry condition of the base rule)
  - above: 1160 tr | win 37.8% | PF 0.99 | exp -0.23 pts | B -94,088 | IS PF 0.99 (n=762, B -62,929) | OOS PF 0.98 (n=398, B -31,160)
- **ema200_5m** (one-sided by construction: being above this level is an entry condition of the base rule)
  - above: 1160 tr | win 37.8% | PF 0.99 | exp -0.23 pts | B -94,088 | IS PF 0.99 (n=762, B -62,929) | OOS PF 0.98 (n=398, B -31,160)
- **cpr**
  - above: 923 tr | win 38.6% | PF 0.99 | exp -0.21 pts | B -73,463 | IS PF 0.92 (n=619, B -84,225) | OOS PF 1.11 (n=304, B +10,762)
  - inside: 119 tr | win 35.3% | PF 0.83 | exp -3.17 pts | B -22,157 | IS PF 1.42 (n=70, B +13,059) | OOS PF 0.37 (n=49, B -35,215)
  - below: 118 tr | win 34.7% | PF 1.17 | exp 2.54 pts | B +1,531 | IS PF 1.56 (n=73, B +8,238) | OOS PF 0.82 (n=45, B -6,706)
- **pdh_r1_zone**
  - above: 314 tr | win 48.4% | PF 1.3 | exp 6.16 pts | B +47,607 | IS PF 1.36 (n=234, B +39,494) | OOS PF 1.18 (n=80, B +8,113)
  - inside: 182 tr | win 36.8% | PF 0.76 | exp -5.82 pts | B -53,627 | IS PF 0.43 (n=122, B -79,490) | OOS PF 1.65 (n=60, B +25,862)
  - below: 664 tr | win 33.1% | PF 0.91 | exp -1.72 pts | B -88,068 | IS PF 1.03 (n=406, B -22,933) | OOS PF 0.77 (n=258, B -65,135)
- **pdl_s1_zone**
  - above: 1131 tr | win 37.9% | PF 0.99 | exp -0.24 pts | B -93,743 | IS PF 0.99 (n=742, B -66,358) | OOS PF 0.99 (n=389, B -27,385)
  - inside: 11 tr | win 36.4% | PF 1.51 | exp 9.25 pts | B +5,228 | IS PF 1.06 (n=7, B +2,655) | OOS PF 2.54 (n=4, B +2,573)
  - below: 18 tr | win 33.3% | PF 0.71 | exp -5.28 pts | B -5,573 | IS PF 1.32 (n=13, B +774) | OOS PF 0.12 (n=5, B -6,348)

## Pre-declared tests (exploratory; Bonferroni threshold for 23 tests: p < 0.00217)

### H-A golden pivot zone — bullish: daily Camarilla L3 inside daily CPR; bearish: H3 inside; none otherwise. Day direction = session close vs session open (and vs prior close).
- bullish: 484 sessions | close>open 48.1% | avg C-O -12.42 pts | close>prior close 56.8%
  - base rule: 448 tr | win 35.7% | PF 0.75 | exp -5.53 pts | B -127,328 | IS PF 0.71 (n=312, B -96,766) | OOS PF 0.83 (n=136, B -30,562); bootstrap {'n': 448, 'mean': -5.53, 'ci95': [-10.17, -0.4], 'p_mean_le_0': 0.9828}
- bearish: 345 sessions | close>open 53.3% | avg C-O 2.19 pts | close>prior close 52.5%
  - base rule: 164 tr | win 40.9% | PF 1.15 | exp 2.79 pts | B +315 | IS PF 1.63 (n=96, B +27,645) | OOS PF 0.67 (n=68, B -27,330); bootstrap {'n': 164, 'mean': 2.79, 'ci95': [-4.92, 11.41], 'p_mean_le_0': 0.2552}
- none: 726 sessions | close>open 48.2% | avg C-O -11.25 pts | close>prior close 51.9%
  - base rule: 548 tr | win 38.7% | PF 1.17 | exp 3.2 pts | B +32,925 | IS PF 1.14 (n=354, B +6,192) | OOS PF 1.2 (n=194, B +26,733); bootstrap {'n': 548, 'mean': 3.2, 'ci95': [-2.06, 7.7], 'p_mean_le_0': 0.1316}

### H-B Camarilla sideways zones — signal-bar close inside daily Camarilla [H3,H4] or [L4,L3] = inside; otherwise outside.
- inside: 365 tr | win 36.7% | PF 0.92 | exp -1.7 pts | B -42,814 | IS PF 0.92 (n=236, B -27,154) | OOS PF 0.93 (n=129, B -15,660) (H3-H4: 321, L3-L4: 44)
- outside: 795 tr | win 38.4% | PF 1.02 | exp 0.44 pts | B -51,274 | IS PF 1.03 (n=526, B -35,774) | OOS PF 1.01 (n=269, B -15,500)
- inside minus outside (pts/trade): {'diff_mean': -2.14, 'ci95': [-9.51, 5.39], 'p_diff_ge_0': 0.2916} -> inside worse than outside

### H-C — awaiting_definition: Not run: definition ambiguous (which closes in the last 30 minutes; what counts as a bullish next-day open). Runs once the user defines it; counted then.

### H-D short mirror of R001 (ATM put) — run once
- A: 514 tr | win 42.2% | PF 1.2 | exp 5.21 pts | B +127,337 | IS PF 1.22 (n=311, B +87,431) | OOS PF 1.18 (n=203, B +39,906)
- B modelled: {'trades': 514, 'total': 127337.21, 'pf': 1.28}
- C real: {'trades': 11, 'total': 2821.57, 'pf': 1.41}
- bootstrap points: {'n': 514, 'mean': 5.21, 'ci95': [-1.54, 11.74], 'p_mean_le_0': 0.0708}
- bootstrap B: {'n': 514, 'mean': 247.74, 'ci95': [-32.59, 526.28], 'p_mean_le_0': 0.043}
- by year: 2020: n=33 PF=1.47 exp=12.27 B=+26,513; 2021: n=75 PF=1.06 exp=1.58 B=+5,612; 2022: n=91 PF=0.81 exp=-5.25 B=-17,427; 2023: n=76 PF=1.5 exp=8.16 B=+21,805; 2024: n=79 PF=1.59 exp=18.37 B=+76,721; 2025: n=79 PF=0.96 exp=-0.81 B=-15,645; 2026: n=81 PF=1.28 exp=7.74 B=+29,759


Descriptive looks (priority-focus splits, not counted as tests): 6. Everything in this report is EXPLORATORY; only R001's forward paper result is confirmatory.

## R001 forward status (paper)
- IN_PROGRESS: 0/150 trades (evaluated 2026-10-16T16:00:09)
- Snapshot coverage: 0/0 sessions full, 0 partial, 0 with no snapshots; trade price coverage n/a (need >= 80%). OK

## Change vs previous run
Previous run: 2026-10-16T11:19:09
- NEW this run: golden_pivot_zone field, C001 re-verification, priority-focus splits, pre-declared tests H-A, H-B, H-D (H-C awaiting definition)
- tests on dataset: 20 -> 23
- C001 base|futures matched: trades 1 -> 1, expectancy 289.55 -> 289.55, PF None -> None
- C001 base|futures non_matched: trades 1159 -> 1159, expectancy -0.48 -> -0.48, PF 0.98 -> 0.98
