# CPR_VWAP_EMA_Nifty backtest - 2026-10-16T23:30:07

Window 2020-07-01 to 2026-10-16; out-of-sample from 2024-07-01.
Sessions: 1556; missing futures volume (skipped in 'futures' set, spot-TWAP in 'with_missing' set): 3.
Spot data source per session: {'angel': 1615, 'insufficient': 4}.

**Tests run on this dataset so far: 40** (new this run: 10; ledger: test_ledger.json). Hypotheses and forward paper trades are not counted.
Conditions logged (observations): [{'id': 'C001', 'date': '2026-10-15', 'role': 'observation'}, {'id': 'C002', 'date': '2026-10-08', 'role': 'observation'}, {'id': 'C003', 'date': '2026-10-16', 'role': 'observation'}, {'id': 'C004', 'date': '2026-10-14', 'role': 'observation'}, {'id': 'C005', 'date': '2026-10-13', 'role': 'observation'}, {'id': 'C006', 'date': '2026-10-01', 'role': 'observation'}, {'id': 'C007', 'date': '2026-09-22', 'role': 'observation'}]
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
  - non_matched: 913 trades | win 36.8% | avg win 49.83 / loss -29.81 pts | PF 0.97 | exp -0.5 pts | total -457.1 | maxDD -889.4 | hold 90.1m | MFE 42.9 / MAE 29.1 pts
  - all: 914 trades | win 36.9% | avg win 50.47 / loss -29.81 pts | PF 0.99 | exp -0.21 pts | total -189.55 | maxDD -889.4 | hold 90.3m | MFE 43.1 / MAE 29.1 pts
  - all_B_rs: 909 trades | win 29.7% | avg win 2134.73 / loss -1045.83 Rs | PF 0.86 | exp -101.11 Rs | total -91907.87 | maxDD -102986.5 | hold 90.4m | MFE 43.2 / MAE 29.0 pts
  - all_C_rs: 17 trades | win 29.4% | avg win 2521.72 / loss -544.29 Rs | PF 1.93 | exp 357.48 Rs | total 6077.1 | maxDD -4916.82 | hold 82.9m | MFE 39.6 / MAE 27.7 pts
  - IS_all_points: 643 trades | win 37.6% | avg win 45.09 / loss -27.93 pts | PF 0.97 | exp -0.45 pts | total -289.3 | maxDD -889.4 | hold 92.5m | MFE 39.5 / MAE 26.5 pts
  - OOS_all_points: 271 trades | win 35.1% | avg win 64.18 / loss -34.08 pts | PF 1.02 | exp 0.37 pts | total 99.75 | maxDD -738.5 | hold 84.9m | MFE 51.8 / MAE 35.1 pts
  - IS_all_B_rs: 639 trades | win 29.6% | avg win 1926.25 / loss -977.1 Rs | PF 0.83 | exp -118.36 Rs | total -75632.62 | maxDD -86703.26 | hold 92.6m | MFE 39.6 / MAE 26.5 pts
  - OOS_all_B_rs: 270 trades | win 30.0% | avg win 2621.16 / loss -1209.47 Rs | PF 0.93 | exp -60.28 Rs | total -16275.25 | maxDD -33956.23 | hold 85.2m | MFE 52.0 / MAE 35.1 pts
  - block bootstrap, mean pts/trade (all days): {'n': 914, 'mean': -0.21, 'ci95': [-3.33, 2.69], 'p_mean_le_0': 0.576}
  - by year (all days): 2020: n=106 exp=-4.31 PF=0.66 B=-34483.74; 2021: n=165 exp=0.67 PF=1.04 B=-14968.24; 2022: n=138 exp=-2.63 PF=0.87 B=-28988.14; 2023: n=152 exp=4.01 PF=1.31 B=6205.47; 2024: n=146 exp=-1.75 PF=0.93 B=-11806.13; 2025: n=133 exp=3.35 PF=1.15 B=10077.8; 2026: n=74 exp=-3.76 PF=0.84 B=-17944.89
- **+ema200_30m|with_missing**
  - matched: 1 trades | win 100.0% | avg win 267.55 / loss 0.0 pts | PF n/a | exp 267.55 pts | total 267.55 | maxDD 0.0 | hold 280.0m | MFE 309.6 / MAE 2.5 pts
  - non_matched: 915 trades | win 36.7% | avg win 49.83 / loss -29.94 pts | PF 0.97 | exp -0.65 pts | total -592.25 | maxDD -923.0 | hold 90.0m | MFE 42.8 / MAE 29.2 pts
  - all: 916 trades | win 36.8% | avg win 50.47 / loss -29.94 pts | PF 0.98 | exp -0.35 pts | total -324.7 | maxDD -923.0 | hold 90.2m | MFE 43.1 / MAE 29.2 pts
- **+pwh_break|futures**
  - matched: 1 trades | win 100.0% | avg win 227.65 / loss 0.0 pts | PF n/a | exp 227.65 pts | total 227.65 | maxDD 0.0 | hold 275.0m | MFE 269.6 / MAE 0.0 pts
  - non_matched: 574 trades | win 38.2% | avg win 41.83 / loss -30.34 pts | PF 0.85 | exp -2.81 pts | total -1610.2 | maxDD -1667.75 | hold 93.3m | MFE 41.0 / MAE 29.6 pts
  - all: 575 trades | win 38.3% | avg win 42.68 / loss -30.34 pts | PF 0.87 | exp -2.4 pts | total -1382.55 | maxDD -1667.75 | hold 93.7m | MFE 41.4 / MAE 29.5 pts
  - all_B_rs: 572 trades | win 29.7% | avg win 1801.23 / loss -1037.03 Rs | PF 0.73 | exp -193.49 Rs | total -110678.01 | maxDD -119730.03 | hold 93.8m | MFE 41.5 / MAE 29.5 pts
  - all_C_rs: 8 trades | win 50.0% | avg win 2631.6 / loss -798.88 Rs | PF 3.29 | exp 916.36 Rs | total 7330.87 | maxDD -3186.76 | hold 86.9m | MFE 52.9 / MAE 28.3 pts
  - IS_all_points: 412 trades | win 38.6% | avg win 38.53 / loss -28.92 pts | PF 0.84 | exp -2.89 pts | total -1190.95 | maxDD -1310.0 | hold 93.5m | MFE 37.6 / MAE 26.9 pts
  - OOS_all_points: 163 trades | win 37.4% | avg win 53.5 / loss -33.88 pts | PF 0.94 | exp -1.18 pts | total -191.6 | maxDD -588.65 | hold 94.0m | MFE 51.1 / MAE 36.1 pts
  - IS_all_B_rs: 410 trades | win 29.0% | avg win 1657.33 / loss -977.68 Rs | PF 0.69 | exp -212.88 Rs | total -87282.62 | maxDD -90026.36 | hold 93.5m | MFE 37.6 / MAE 26.9 pts
  - OOS_all_B_rs: 162 trades | win 31.5% | avg win 2137.01 / loss -1192.64 Rs | PF 0.82 | exp -144.42 Rs | total -23395.39 | maxDD -32134.57 | hold 94.5m | MFE 51.4 / MAE 36.2 pts
  - block bootstrap, mean pts/trade (all days): {'n': 575, 'mean': -2.4, 'ci95': [-6.22, 0.99], 'p_mean_le_0': 0.9206}
  - by year (all days): 2020: n=73 exp=-7.75 PF=0.46 B=-32827.21; 2021: n=101 exp=-1.2 PF=0.93 B=-15766.62; 2022: n=90 exp=-3.87 PF=0.81 B=-24691.0; 2023: n=96 exp=4.28 PF=1.31 B=6984.03; 2024: n=100 exp=-9.9 PF=0.57 B=-45019.5; 2025: n=76 exp=7.14 PF=1.35 B=17416.34; 2026: n=39 exp=-7.99 PF=0.66 B=-16774.05
- **+pwh_break|with_missing**
  - matched: 1 trades | win 100.0% | avg win 227.65 / loss 0.0 pts | PF n/a | exp 227.65 pts | total 227.65 | maxDD 0.0 | hold 275.0m | MFE 269.6 / MAE 0.0 pts
  - non_matched: 575 trades | win 38.1% | avg win 41.83 / loss -30.35 pts | PF 0.85 | exp -2.86 pts | total -1643.8 | maxDD -1701.35 | hold 93.3m | MFE 41.0 / MAE 29.6 pts
  - all: 576 trades | win 38.2% | avg win 42.68 / loss -30.35 pts | PF 0.87 | exp -2.46 pts | total -1416.15 | maxDD -1701.35 | hold 93.7m | MFE 41.4 / MAE 29.6 pts
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
  - non_matched: 1160 trades | win 39.1% | avg win 48.84 / loss -32.81 pts | PF 0.96 | exp -0.85 pts | total -986.32 | maxDD -1714.13 | hold 83.5m | MFE 44.0 / MAE 31.0 pts
  - all: 1161 trades | win 39.2% | avg win 49.39 / loss -32.81 pts | PF 0.97 | exp -0.59 pts | total -690.47 | maxDD -1714.13 | hold 83.6m | MFE 44.2 / MAE 31.0 pts
  - all_B_rs: 1156 trades | win 31.9% | avg win 2065.19 / loss -1119.46 Rs | PF 0.86 | exp -102.9 Rs | total -118957.42 | maxDD -132248.62 | hold 83.7m | MFE 44.3 / MAE 31.0 pts
  - all_C_rs: 28 trades | win 25.0% | avg win 2624.25 / loss -915.5 Rs | PF 0.96 | exp -30.57 Rs | total -855.86 | maxDD -16609.8 | hold 67.0m | MFE 37.4 / MAE 30.0 pts
  - IS_all_points: 762 trades | win 40.3% | avg win 43.17 / loss -30.54 pts | PF 0.95 | exp -0.84 pts | total -641.03 | maxDD -1257.59 | hold 87.5m | MFE 39.7 / MAE 28.2 pts
  - OOS_all_points: 399 trades | win 37.1% | avg win 62.28 / loss -36.92 pts | PF 0.99 | exp -0.12 pts | total -49.44 | maxDD -797.21 | hold 76.2m | MFE 52.7 / MAE 36.3 pts
  - IS_all_B_rs: 758 trades | win 31.7% | avg win 1841.56 / loss -1029.27 Rs | PF 0.83 | exp -120.3 Rs | total -91187.14 | maxDD -94891.89 | hold 87.6m | MFE 39.8 / MAE 28.2 pts
  - OOS_all_B_rs: 398 trades | win 32.4% | avg win 2481.26 / loss -1293.13 Rs | PF 0.92 | exp -69.77 Rs | total -27770.28 | maxDD -44424.83 | hold 76.4m | MFE 52.9 / MAE 36.4 pts
  - block bootstrap, mean pts/trade (all days): {'n': 1161, 'mean': -0.59, 'ci95': [-3.46, 2.05], 'p_mean_le_0': 0.6924}
  - by year (all days): 2020: n=111 exp=-2.34 PF=0.81 B=-27684.73; 2021: n=190 exp=-1.61 PF=0.91 B=-32532.65; 2022: n=176 exp=0.97 PF=1.05 B=-2261.02; 2023: n=188 exp=4.11 PF=1.31 B=8783.73; 2024: n=186 exp=-7.94 PF=0.71 B=-58287.52; 2025: n=179 exp=4.38 PF=1.21 B=20008.18; 2026: n=131 exp=-2.87 PF=0.88 B=-26983.41
- **+target_r4_mh3|with_missing**
  - matched: 1 trades | win 100.0% | avg win 295.85 / loss 0.0 pts | PF n/a | exp 295.85 pts | total 295.85 | maxDD 0.0 | hold 220.0m | MFE 297.6 / MAE 44.4 pts
  - non_matched: 1163 trades | win 39.0% | avg win 48.84 / loss -32.89 pts | PF 0.95 | exp -0.98 pts | total -1142.07 | maxDD -1815.68 | hold 83.4m | MFE 43.9 / MAE 31.1 pts
  - all: 1164 trades | win 39.1% | avg win 49.39 / loss -32.89 pts | PF 0.96 | exp -0.73 pts | total -846.22 | maxDD -1815.68 | hold 83.6m | MFE 44.1 / MAE 31.1 pts
- **base|futures**
  - matched: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - non_matched: 1160 trades | win 37.8% | avg win 52.57 / loss -32.68 pts | PF 0.98 | exp -0.49 pts | total -567.9 | maxDD -1370.75 | hold 90.2m | MFE 46.6 / MAE 31.3 pts
  - all: 1161 trades | win 37.8% | avg win 53.11 / loss -32.68 pts | PF 0.99 | exp -0.24 pts | total -278.35 | maxDD -1370.75 | hold 90.4m | MFE 46.9 / MAE 31.3 pts
  - all_B_rs: 1156 trades | win 30.9% | avg win 2252.48 / loss -1124.91 Rs | PF 0.89 | exp -81.89 Rs | total -94665.8 | maxDD -107556.07 | hold 90.5m | MFE 47.0 / MAE 31.3 pts
  - all_C_rs: 28 trades | win 25.0% | avg win 2365.67 / loss -915.5 Rs | PF 0.86 | exp -95.21 Rs | total -2665.88 | maxDD -16992.56 | hold 73.6m | MFE 39.3 / MAE 30.0 pts
  - IS_all_points: 762 trades | win 38.7% | avg win 48.09 / loss -30.58 pts | PF 0.99 | exp -0.12 pts | total -90.85 | maxDD -1057.7 | hold 94.0m | MFE 42.4 / MAE 28.6 pts
  - OOS_all_points: 399 trades | win 36.1% | avg win 63.39 / loss -36.53 pts | PF 0.98 | exp -0.47 pts | total -187.5 | maxDD -1026.9 | hold 83.4m | MFE 55.4 / MAE 36.5 pts
  - IS_all_B_rs: 758 trades | win 30.7% | avg win 2083.12 / loss -1044.37 Rs | PF 0.89 | exp -83.02 Rs | total -62928.76 | maxDD -73999.4 | hold 94.1m | MFE 42.5 / MAE 28.6 pts
  - OOS_all_B_rs: 398 trades | win 31.2% | avg win 2570.72 / loss -1279.22 Rs | PF 0.91 | exp -79.74 Rs | total -31737.04 | maxDD -53571.35 | hold 83.6m | MFE 55.5 / MAE 36.6 pts
  - block bootstrap, mean pts/trade (all days): {'n': 1161, 'mean': -0.24, 'ci95': [-3.24, 2.51], 'p_mean_le_0': 0.5996}
  - by year (all days): 2020: n=111 exp=-2.59 PF=0.8 B=-28565.21; 2021: n=190 exp=-1.11 PF=0.94 B=-26888.77; 2022: n=176 exp=1.64 PF=1.08 B=4997.73; 2023: n=188 exp=3.96 PF=1.29 B=8442.17; 2024: n=186 exp=-6.08 PF=0.77 B=-44592.72; 2025: n=179 exp=5.32 PF=1.25 B=29265.95; 2026: n=131 exp=-4.84 PF=0.81 B=-37324.95
- **base|with_missing**
  - matched: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - non_matched: 1163 trades | win 37.7% | avg win 52.57 / loss -32.76 pts | PF 0.97 | exp -0.62 pts | total -723.65 | maxDD -1472.3 | hold 90.1m | MFE 46.5 / MAE 31.4 pts
  - all: 1164 trades | win 37.7% | avg win 53.11 / loss -32.76 pts | PF 0.98 | exp -0.37 pts | total -434.1 | maxDD -1472.3 | hold 90.3m | MFE 46.8 / MAE 31.4 pts

### Option layers (base, futures VWAP)
- **matched**
  - A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - C: 1 trades | win 100.0% | avg win 12341.79 / loss 0.0 Rs | PF n/a | exp 12341.79 Rs | total 12341.79 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **non_matched**
  - A: 1160 trades | win 37.8% | avg win 52.57 / loss -32.68 pts | PF 0.98 | exp -0.49 pts | total -567.9 | maxDD -1370.75 | hold 90.2m | MFE 46.6 / MAE 31.3 pts
  - B: 1155 trades | win 30.8% | avg win 2220.98 / loss -1124.91 Rs | PF 0.88 | exp -93.62 Rs | total -108133.58 | maxDD -108133.58 | hold 90.3m | MFE 46.7 / MAE 31.3 pts
  - C: 27 trades | win 22.2% | avg win 702.99 / loss -915.5 Rs | PF 0.22 | exp -555.84 Rs | total -15007.67 | maxDD -16992.56 | hold 65.0m | MFE 28.5 / MAE 29.5 pts
- **all**
  - A: 1161 trades | win 37.8% | avg win 53.11 / loss -32.68 pts | PF 0.99 | exp -0.24 pts | total -278.35 | maxDD -1370.75 | hold 90.4m | MFE 46.9 / MAE 31.3 pts
  - B: 1156 trades | win 30.9% | avg win 2252.48 / loss -1124.91 Rs | PF 0.89 | exp -81.89 Rs | total -94665.8 | maxDD -107556.07 | hold 90.5m | MFE 47.0 / MAE 31.3 pts
  - C: 28 trades | win 25.0% | avg win 2365.67 / loss -915.5 Rs | PF 0.86 | exp -95.21 Rs | total -2665.88 | maxDD -16992.56 | hold 73.6m | MFE 39.3 / MAE 30.0 pts
- **B vs C on real-price days**: {'trades': 27, 'B_total': -3146.21, 'C_total': -2574.0, 'mean_abs_diff_per_trade': 534.4, 'sign_agreement_pct': 81.5}

### In-sample vs out-of-sample (base)
- IS matched: 0 trades
- IS non-matched: 762 trades | win 38.7% | avg win 48.09 / loss -30.58 pts | PF 0.99 | exp -0.12 pts | total -90.85 | maxDD -1057.7 | hold 94.0m | MFE 42.4 / MAE 28.6 pts
- IS matched B: 0 trades
- IS non-matched B: 758 trades | win 30.7% | avg win 2083.12 / loss -1044.37 Rs | PF 0.89 | exp -83.02 Rs | total -62928.76 | maxDD -73999.4 | hold 94.1m | MFE 42.5 / MAE 28.6 pts
- OOS matched: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- OOS non-matched: 398 trades | win 35.9% | avg win 61.81 / loss -36.53 pts | PF 0.95 | exp -1.2 pts | total -477.05 | maxDD -1026.9 | hold 82.9m | MFE 54.7 / MAE 36.5 pts
- OOS matched B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- OOS non-matched B: 397 trades | win 31.0% | avg win 2482.12 / loss -1279.22 Rs | PF 0.87 | exp -113.87 Rs | total -45204.82 | maxDD -54148.86 | hold 83.1m | MFE 54.8 / MAE 36.5 pts

### by day type (base, points)
- range: matched 0 trades || non-matched 765 trades | win 35.8% | avg win 37.92 / loss -31.87 pts | PF 0.66 | exp -6.87 pts | total -5258.35 | maxDD -5366.8 | hold 81.0m | MFE 41.0 / MAE 31.3 pts
- trending: matched 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts || non-matched 395 trades | win 41.5% | avg win 77.05 / loss -34.4 pts | PF 1.59 | exp 11.87 pts | total 4690.45 | maxDD -736.35 | hold 108.0m | MFE 57.6 / MAE 31.3 pts

### by cpr width (base, points)
- narrow: matched 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts || non-matched 551 trades | win 35.6% | avg win 52.24 / loss -29.5 pts | PF 0.98 | exp -0.43 pts | total -235.3 | maxDD -2078.55 | hold 87.5m | MFE 43.0 / MAE 28.5 pts
- normal: matched 0 trades || non-matched 457 trades | win 41.6% | avg win 52.81 / loss -32.03 pts | PF 1.17 | exp 3.24 pts | total 1481.7 | maxDD -1227.2 | hold 97.0m | MFE 49.7 / MAE 30.2 pts
- wide: matched 0 trades || non-matched 152 trades | win 34.2% | avg win 52.96 / loss -45.68 pts | PF 0.6 | exp -11.94 pts | total -1814.3 | maxDD -1822.6 | hold 79.1m | MFE 50.6 / MAE 45.0 pts

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
- 2026: matched 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts || non-matched 130 trades | win 33.1% | avg win 54.91 / loss -37.76 pts | PF 0.72 | exp -7.1 pts | total -923.5 | maxDD -1026.9 | hold 78.8m | MFE 49.7 / MAE 37.5 pts

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

C001 exact-match sessions: 1 of 1556

| Field (value) | Sessions matching this field alone | Sessions matching all OTHER fields |
|---|---|---|
| cpr_width (narrow) | 712 | 1 |
| cpr_vs_prior_day (overlapping_higher) | 93 | 3 |
| cpr_vs_weekly (above) | 756 | 1 |
| cpr_vs_monthly (below) | 413 | 2 |
| open_vs_cpr (above) | 860 | 2 |
| open_vs_daily_h3 (below) | 1009 | 1 |
| open_vs_weekly_h3 (below) | 1043 | 1 |

## Priority focus: base rule by position at the signal bar (descriptive, not tuned)

- **vwap** (one-sided by construction: being above this level is an entry condition of the base rule)
  - above: 1161 tr | win 37.8% | PF 0.99 | exp -0.24 pts | B -94,666 | IS PF 0.99 (n=762, B -62,929) | OOS PF 0.98 (n=399, B -31,737)
- **ema200_1m** (one-sided by construction: being above this level is an entry condition of the base rule)
  - above: 1161 tr | win 37.8% | PF 0.99 | exp -0.24 pts | B -94,666 | IS PF 0.99 (n=762, B -62,929) | OOS PF 0.98 (n=399, B -31,737)
- **ema200_5m** (one-sided by construction: being above this level is an entry condition of the base rule)
  - above: 1161 tr | win 37.8% | PF 0.99 | exp -0.24 pts | B -94,666 | IS PF 0.99 (n=762, B -62,929) | OOS PF 0.98 (n=399, B -31,737)
- **cpr**
  - above: 924 tr | win 38.5% | PF 0.99 | exp -0.22 pts | B -74,040 | IS PF 0.92 (n=619, B -84,225) | OOS PF 1.11 (n=305, B +10,185)
  - inside: 119 tr | win 35.3% | PF 0.83 | exp -3.17 pts | B -22,157 | IS PF 1.42 (n=70, B +13,059) | OOS PF 0.37 (n=49, B -35,215)
  - below: 118 tr | win 34.7% | PF 1.17 | exp 2.54 pts | B +1,531 | IS PF 1.56 (n=73, B +8,238) | OOS PF 0.82 (n=45, B -6,706)
- **pdh_r1_zone**
  - above: 314 tr | win 48.4% | PF 1.3 | exp 6.16 pts | B +47,607 | IS PF 1.36 (n=234, B +39,494) | OOS PF 1.18 (n=80, B +8,113)
  - inside: 182 tr | win 36.8% | PF 0.76 | exp -5.82 pts | B -53,627 | IS PF 0.43 (n=122, B -79,490) | OOS PF 1.65 (n=60, B +25,862)
  - below: 665 tr | win 33.1% | PF 0.91 | exp -1.73 pts | B -88,646 | IS PF 1.03 (n=406, B -22,933) | OOS PF 0.77 (n=259, B -65,713)
- **pdl_s1_zone**
  - above: 1132 tr | win 37.9% | PF 0.99 | exp -0.25 pts | B -94,320 | IS PF 0.99 (n=742, B -66,358) | OOS PF 0.99 (n=390, B -27,962)
  - inside: 11 tr | win 36.4% | PF 1.51 | exp 9.25 pts | B +5,228 | IS PF 1.06 (n=7, B +2,655) | OOS PF 2.54 (n=4, B +2,573)
  - below: 18 tr | win 33.3% | PF 0.71 | exp -5.28 pts | B -5,573 | IS PF 1.32 (n=13, B +774) | OOS PF 0.12 (n=5, B -6,348)

## Pre-declared tests (exploratory; Bonferroni threshold for 40 tests: p < 0.00125)

### H-A golden pivot zone — bullish: daily Camarilla L3 inside daily CPR; bearish: H3 inside; none otherwise. Day direction = session close vs session open (and vs prior close).
- bullish: 485 sessions | close>open 48.2% | avg C-O -12.23 pts | close>prior close 56.9%
  - base rule: 449 tr | win 35.6% | PF 0.75 | exp -5.54 pts | B -127,906 | IS PF 0.71 (n=312, B -96,766) | OOS PF 0.83 (n=137, B -31,140); bootstrap {'n': 449, 'mean': -5.54, 'ci95': [-10.37, -0.39], 'p_mean_le_0': 0.9826}
- bearish: 345 sessions | close>open 53.3% | avg C-O 2.19 pts | close>prior close 52.5%
  - base rule: 164 tr | win 40.9% | PF 1.15 | exp 2.79 pts | B +315 | IS PF 1.63 (n=96, B +27,645) | OOS PF 0.67 (n=68, B -27,330); bootstrap {'n': 164, 'mean': 2.79, 'ci95': [-4.92, 11.41], 'p_mean_le_0': 0.2552}
- none: 726 sessions | close>open 48.2% | avg C-O -11.25 pts | close>prior close 51.9%
  - base rule: 548 tr | win 38.7% | PF 1.17 | exp 3.2 pts | B +32,925 | IS PF 1.14 (n=354, B +6,192) | OOS PF 1.2 (n=194, B +26,733); bootstrap {'n': 548, 'mean': 3.2, 'ci95': [-2.06, 7.7], 'p_mean_le_0': 0.1316}

### H-B Camarilla sideways zones — signal-bar close inside daily Camarilla [H3,H4] or [L4,L3] = inside; otherwise outside.
- inside: 365 tr | win 36.7% | PF 0.92 | exp -1.7 pts | B -42,814 | IS PF 0.92 (n=236, B -27,154) | OOS PF 0.93 (n=129, B -15,660) (H3-H4: 321, L3-L4: 44)
- outside: 796 tr | win 38.3% | PF 1.02 | exp 0.43 pts | B -51,851 | IS PF 1.03 (n=526, B -35,774) | OOS PF 1.0 (n=270, B -16,077)
- inside minus outside (pts/trade): {'diff_mean': -2.12, 'ci95': [-9.41, 5.27], 'p_diff_ge_0': 0.284} -> inside worse than outside

### H-C late close vs 1m 200 EMA -> next open (run once) — Run once (user definition 2026-10-16): signal = session's final 1m close above the 1m 200 EMA;
    hit = next session's open strictly above that close (gap up, no threshold). Mirror: final close
    below the EMA, hit = gap down. Baselines: share of all pairs that gap up / gap down.
- all: 1551 session pairs | baseline gap up 63.2% / gap down 36.6%
  - final close ABOVE EMA: {'sessions': 822, 'gap_up': 535, 'hit_rate_pct': 65.1, 'p_vs_baseline': 0.14552}
  - final close BELOW EMA: {'sessions': 729, 'gap_down': 283, 'hit_rate_pct': 38.8, 'p_vs_baseline': 0.11654}
- IS: 984 session pairs | baseline gap up 68.1% / gap down 31.9%
  - final close ABOVE EMA: {'sessions': 538, 'gap_up': 378, 'hit_rate_pct': 70.3, 'p_vs_baseline': 0.15048}
  - final close BELOW EMA: {'sessions': 446, 'gap_down': 154, 'hit_rate_pct': 34.5, 'p_vs_baseline': 0.1284}
- OOS: 567 session pairs | baseline gap up 54.9% / gap down 44.8%
  - final close ABOVE EMA: {'sessions': 284, 'gap_up': 157, 'hit_rate_pct': 55.3, 'p_vs_baseline': 0.46629}
  - final close BELOW EMA: {'sessions': 283, 'gap_down': 129, 'hit_rate_pct': 45.6, 'p_vs_baseline': 0.41761}
- pairs with next open exactly equal to the close (neither): 2

### H-D short mirror of R001 (ATM put) — run once
- A: 514 tr | win 42.2% | PF 1.2 | exp 5.21 pts | B +127,337 | IS PF 1.22 (n=311, B +87,431) | OOS PF 1.18 (n=203, B +39,906)
- B modelled: {'trades': 514, 'total': 127337.21, 'pf': 1.28}
- C real: {'trades': 11, 'total': 2821.57, 'pf': 1.41}
- bootstrap points: {'n': 514, 'mean': 5.21, 'ci95': [-1.54, 11.74], 'p_mean_le_0': 0.0708}
- bootstrap B: {'n': 514, 'mean': 247.74, 'ci95': [-32.59, 526.28], 'p_mean_le_0': 0.043}
- by year: 2020: n=33 PF=1.47 exp=12.27 B=+26,513; 2021: n=75 PF=1.06 exp=1.58 B=+5,612; 2022: n=91 PF=0.81 exp=-5.25 B=-17,427; 2023: n=76 PF=1.5 exp=8.16 B=+21,805; 2024: n=79 PF=1.59 exp=18.37 B=+76,721; 2025: n=79 PF=0.96 exp=-0.81 B=-15,645; 2026: n=81 PF=1.28 exp=7.74 B=+29,759


Descriptive looks (priority-focus splits, not counted as tests): 32. Everything in this report is EXPLORATORY; only R001's forward paper result is confirmatory.
## All conditions C001-C007 (versions A/B/C) — Bonferroni threshold for 40 tests: p < 0.00125
Version B for C002-C007 = pre-declared tests 25-30. Versions A and C are descriptive (not counted). Every result is EXPLORATORY unless a version-B bootstrap p (mean <= 0) is below the threshold.

Unfiltered base rule (C001 B): 1161 tr | win 37.8% | avg W 53.11 / L -32.68 | PF 0.99 | exp -0.24 | maxDD -1370.75 | hold 90.4m | B -94,666 (PF 0.89) | C -2,666 (28/1161 trades priced from real snapshots) | IS PF 0.99 / OOS PF 0.98 | {'n': 1161, 'mean': -0.24, 'ci95': [-3.24, 2.51], 'p_mean_le_0': 0.5996}

### C001 (2026-10-15, LONG) — entry `vwap_context_long`, exit `close_vs_ema`
- Reproduction on 2026-10-15: signal 09:15, entry 09:20 @ 22724.85, exit 14:25 @ 23014.4 (close_beyond_ema20), +289.55 pts, B 13467.78, C 12341.79
- Primary positions at own signal candle (version B filter): {'vwap': 'above', 'ema200_1m': 'above', 'ema200_5m': 'above', 'cpr': 'above', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}
- **A exact** (1 sessions — SAMPLE TOO SMALL, no significance): 1 tr | win 100.0% | avg W 289.55 / L 0.0 | PF None | exp 289.55 | maxDD 0.0 | hold 305.0m | B +13,468 (PF None) | C +12,342 (1/1 trades priced from real snapshots) | IS PF None / OOS PF None | not computed: fewer than 10 trades (sample too small)
  - dates: 2026-10-15
- **B primary** (C001 version B = the unfiltered base rule (already counted earlier; not recounted)): 1161 tr | win 37.8% | avg W 53.11 / L -32.68 | PF 0.99 | exp -0.24 | maxDD -1370.75 | hold 90.4m | B -94,666 (PF 0.89) | C -2,666 (28/1161 trades priced from real snapshots) | IS PF 0.99 / OOS PF 0.98 | {'n': 1161, 'mean': -0.24, 'ci95': [-3.24, 2.51], 'p_mean_le_0': 0.5996}
  - by year: 2020: n=111 PF=0.8 B=-28,565; 2021: n=190 PF=0.94 B=-26,889; 2022: n=176 PF=1.08 B=+4,998; 2023: n=188 PF=1.29 B=+8,442; 2024: n=186 PF=0.77 B=-44,593; 2025: n=179 PF=1.25 B=+29,266; 2026: n=131 PF=0.81 B=-37,325
- **C fixed exit 20 EMA**: 1161 tr | win 37.8% | avg W 53.11 / L -32.68 | PF 0.99 | exp -0.24 | maxDD -1370.75 | hold 90.4m | B -94,666 (PF 0.89) | C -2,666 (28/1161 trades priced from real snapshots) | IS PF 0.99 / OOS PF 0.98 | {'n': 1161, 'mean': -0.24, 'ci95': [-3.24, 2.51], 'p_mean_le_0': 0.5996}
- **C fixed exit 50 EMA**: 1161 tr | win 34.5% | avg W 76.74 / L -38.61 | PF 1.05 | exp 1.23 | maxDD -1907.7 | hold 153.4m | B +33,859 (PF 1.03) | C +4,989 (29/1161 trades priced from real snapshots) | IS PF 1.09 / OOS PF 0.98 | {'n': 1161, 'mean': 1.23, 'ci95': [-3.03, 4.9], 'p_mean_le_0': 0.308}  (fixed exits replace the condition's own exit (close_vs_ema))

### C002 (2026-10-08, SHORT) — entry `weekly_pivot_break_short`, exit `close_vs_ema`
- Reproduction on 2026-10-08: signal 09:15, entry 09:20 @ 22530.8, exit 15:25 @ 22231.8 (force_exit), +299.00 pts, B 12336.61, C None
- Primary positions at own signal candle (version B filter): {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'below', 'cpr': 'below', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'inside'}
- **A exact** (2 sessions — SAMPLE TOO SMALL, no significance): 2 tr | win 50.0% | avg W 299.0 / L -58.65 | PF 5.1 | exp 120.17 | maxDD -58.65 | hold 232.5m | B +10,349 (PF 6.21) | C +0 (0/2 trades priced from real snapshots) | IS PF 0.0 / OOS PF None | not computed: fewer than 10 trades (sample too small)
  - dates: 2022-02-22, 2026-10-08
- **B primary** (pre-declared test: own entry + own exit + primary positions {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'below', 'cpr': 'below', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'inside'}): 99 tr | win 40.4% | avg W 76.67 / L -49.35 | PF 1.05 | exp 1.56 | maxDD -1001.5 | hold 100.3m | B +16,870 (PF 1.18) | C -2,356 (3/99 trades priced from real snapshots) | IS PF 0.95 / OOS PF 1.22 | {'n': 99, 'mean': 1.56, 'ci95': [-13.99, 14.29], 'p_mean_le_0': 0.4912}
  - by year: 2020: n=5 PF=3.29 B=+13,215; 2021: n=17 PF=1.25 B=+10,269; 2022: n=19 PF=0.73 B=-5,997; 2023: n=11 PF=0.6 B=-3,614; 2024: n=13 PF=0.37 B=-13,139; 2025: n=15 PF=1.07 B=+601; 2026: n=19 PF=2.06 B=+15,535
- **C fixed exit 20 EMA**: 99 tr | win 40.4% | avg W 76.67 / L -49.35 | PF 1.05 | exp 1.56 | maxDD -1001.5 | hold 100.3m | B +16,870 (PF 1.18) | C -2,356 (3/99 trades priced from real snapshots) | IS PF 0.95 / OOS PF 1.22 | {'n': 99, 'mean': 1.56, 'ci95': [-13.99, 14.29], 'p_mean_le_0': 0.4912}
- **C fixed exit 50 EMA**: 99 tr | win 39.4% | avg W 99.5 / L -68.33 | PF 0.95 | exp -2.21 | maxDD -1527.65 | hold 173.2m | B +27,356 (PF 1.22) | C -1,706 (2/99 trades priced from real snapshots) | IS PF 1.03 / OOS PF 0.81 | {'n': 99, 'mean': -2.21, 'ci95': [-23.35, 16.6], 'p_mean_le_0': 0.649}  (fixed exits replace the condition's own exit (close_vs_ema))

### C003 (2026-10-16, LONG) — entry `cpr_support_reclaim_long`, exit `close_vs_ema`
- Reproduction on 2026-10-16: signal 09:40, entry 09:45 @ 22974.75, exit 14:25 @ 23084.25 (close_beyond_ema50), +109.50 pts, B 4670.24, C 4465.19
- Primary positions at own signal candle (version B filter): {'vwap': 'above', 'ema200_1m': 'above', 'ema200_5m': 'above', 'cpr': 'above', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}
- **A exact** (5 sessions — SAMPLE TOO SMALL, no significance): 5 tr | win 40.0% | avg W 76.15 / L -34.45 | PF 1.47 | exp 9.79 | maxDD -103.35 | hold 147.0m | B +1,521 (PF 1.37) | C +4,465 (1/5 trades priced from real snapshots) | IS PF 0.0 / OOS PF 1.95 | not computed: fewer than 10 trades (sample too small)
  - dates: 2021-11-09, 2025-01-03, 2025-03-07, 2025-03-19, 2026-10-16
- **B primary** (pre-declared test: own entry + own exit + primary positions {'vwap': 'above', 'ema200_1m': 'above', 'ema200_5m': 'above', 'cpr': 'above', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}): 329 tr | win 26.4% | avg W 69.65 / L -32.11 | PF 0.78 | exp -5.2 | maxDD -2387.55 | hold 98.6m | B -70,901 (PF 0.76) | C +9,504 (10/329 trades priced from real snapshots) | IS PF 0.92 / OOS PF 0.62 | {'n': 329, 'mean': -5.2, 'ci95': [-12.84, 1.57], 'p_mean_le_0': 0.9428}
  - by year: 2020: n=30 PF=0.97 B=-5,290; 2021: n=56 PF=1.0 B=-4,869; 2022: n=36 PF=0.63 B=-15,399; 2023: n=55 PF=1.02 B=+612; 2024: n=60 PF=0.49 B=-27,080; 2025: n=59 PF=0.91 B=-7,769; 2026: n=33 PF=0.74 B=-11,105
- **C fixed exit 20 EMA**: 329 tr | win 28.6% | avg W 52.98 / L -26.68 | PF 0.79 | exp -3.92 | maxDD -1912.1 | hold 60.1m | B -69,494 (PF 0.71) | C +7,652 (10/329 trades priced from real snapshots) | IS PF 0.86 / OOS PF 0.72 | {'n': 329, 'mean': -3.92, 'ci95': [-9.66, 1.42], 'p_mean_le_0': 0.9354}
- **C fixed exit 50 EMA**: 329 tr | win 26.4% | avg W 69.65 / L -32.11 | PF 0.78 | exp -5.2 | maxDD -2387.55 | hold 98.6m | B -70,901 (PF 0.76) | C +9,504 (10/329 trades priced from real snapshots) | IS PF 0.92 / OOS PF 0.62 | {'n': 329, 'mean': -5.2, 'ci95': [-12.84, 1.57], 'p_mean_le_0': 0.9428}  (fixed exits replace the condition's own exit (close_vs_ema))

### C004 (2026-10-14, SHORT) — entry `strong_vwap_then_strong_emas_short`, exit `green_after_reds`
- Reproduction on 2026-10-14: signal 13:00, entry 13:05 @ 22611.95, exit 14:25 @ 22620.4 (green_after_reds), -8.45 pts, B -478.14, C -920.01
- Primary positions at own signal candle (version B filter): {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'above', 'cpr': 'inside', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}
- **A exact** (1 sessions — SAMPLE TOO SMALL, no significance): 1 tr | win 0.0% | avg W 0.0 / L -8.45 | PF 0.0 | exp -8.45 | maxDD -8.45 | hold 80.0m | B -478 (PF 0.0) | C -920 (1/1 trades priced from real snapshots) | IS PF None / OOS PF 0.0 | not computed: fewer than 10 trades (sample too small)
  - dates: 2026-10-14
- **B primary** (pre-declared test: own entry + own exit + primary positions {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'above', 'cpr': 'inside', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}): 133 tr | win 53.4% | avg W 38.85 / L -43.57 | PF 1.02 | exp 0.43 | maxDD -548.9 | hold 85.4m | B +1,413 (PF 1.02) | C +10,353 (6/133 trades priced from real snapshots) | IS PF 0.81 / OOS PF 1.3 | {'n': 133, 'mean': 0.43, 'ci95': [-10.39, 10.12], 'p_mean_le_0': 0.4862}
  - by year: 2020: n=13 PF=0.93 B=-2,704; 2021: n=24 PF=0.44 B=-11,808; 2022: n=16 PF=1.93 B=+8,055; 2023: n=20 PF=1.14 B=-619; 2024: n=21 PF=0.79 B=-1,419; 2025: n=26 PF=2.06 B=+13,641; 2026: n=13 PF=0.56 B=-3,733
- **C fixed exit 20 EMA**: 133 tr | win 36.8% | avg W 53.99 / L -28.3 | PF 1.11 | exp 2.02 | maxDD -519.45 | hold 68.2m | B +5,470 (PF 1.06) | C +9,401 (5/133 trades priced from real snapshots) | IS PF 0.72 / OOS PF 1.7 | {'n': 133, 'mean': 2.02, 'ci95': [-5.94, 11.29], 'p_mean_le_0': 0.3006}
- **C fixed exit 50 EMA**: 133 tr | win 24.1% | avg W 106.45 / L -19.29 | PF 1.75 | exp 10.96 | maxDD -359.95 | hold 72.9m | B +65,242 (PF 1.81) | C +13,946 (5/133 trades priced from real snapshots) | IS PF 1.2 / OOS PF 2.33 | {'n': 133, 'mean': 10.96, 'ci95': [0.75, 23.5], 'p_mean_le_0': 0.0172}  (fixed exits replace the condition's own exit (green_after_reds))

### C005 (2026-10-13, LONG) — entry `reclaim_all_from_0920_long`, exit `red_at_pdh_r1_zone`
- Reproduction on 2026-10-13: signal 09:20, entry 09:25 @ 22456.35, exit 10:05 @ 22562.35 (red_at_pdh_r1_zone), +106.00 pts, B 4921.52, C 4173.5
- Primary positions at own signal candle (version B filter): {'vwap': 'above', 'ema200_1m': 'above', 'ema200_5m': 'above', 'cpr': 'inside', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}
- **A exact** (1 sessions — SAMPLE TOO SMALL, no significance): 1 tr | win 100.0% | avg W 106.0 / L 0.0 | PF None | exp 106.0 | maxDD 0.0 | hold 40.0m | B +4,922 (PF None) | C +4,174 (1/1 trades priced from real snapshots) | IS PF None / OOS PF None | not computed: fewer than 10 trades (sample too small)
  - dates: 2026-10-13
- **B primary** (pre-declared test: own entry + own exit + primary positions {'vwap': 'above', 'ema200_1m': 'above', 'ema200_5m': 'above', 'cpr': 'inside', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}): 119 tr | win 56.3% | avg W 57.32 / L -82.31 | PF 0.9 | exp -3.7 | maxDD -1241.1 | hold 207.7m | B -7,235 (PF 0.95) | C +2,074 (3/119 trades priced from real snapshots) | IS PF 1.19 / OOS PF 0.65 | {'n': 119, 'mean': -3.7, 'ci95': [-22.73, 11.47], 'p_mean_le_0': 0.7008}
  - by year: 2020: n=9 PF=1.45 B=+2,015; 2021: n=15 PF=1.77 B=+8,102; 2022: n=14 PF=0.63 B=-4,744; 2023: n=18 PF=0.62 B=-7,859; 2024: n=23 PF=2.56 B=+24,605; 2025: n=26 PF=0.39 B=-26,328; 2026: n=14 PF=0.86 B=-3,026
- **C fixed exit 20 EMA**: 119 tr | win 35.3% | avg W 45.38 / L -29.65 | PF 0.83 | exp -3.17 | maxDD -858.85 | hold 61.0m | B -22,157 (PF 0.75) | C -1,511 (3/119 trades priced from real snapshots) | IS PF 1.42 / OOS PF 0.37 | {'n': 119, 'mean': -3.17, 'ci95': [-12.63, 6.78], 'p_mean_le_0': 0.768}
- **C fixed exit 50 EMA**: 119 tr | win 28.6% | avg W 75.61 / L -24.75 | PF 1.22 | exp 3.92 | maxDD -801.0 | hold 86.0m | B +18,038 (PF 1.2) | C +9,985 (3/119 trades priced from real snapshots) | IS PF 2.14 / OOS PF 0.57 | {'n': 119, 'mean': 3.92, 'ci95': [-8.29, 15.71], 'p_mean_le_0': 0.3636}  (fixed exits replace the condition's own exit (red_at_pdh_r1_zone))

### C006 (2026-10-01, SHORT) — entry `vwap_pdl_ema20_resistance_short`, exit `two_strong_bullish`
- Reproduction on 2026-10-01: signal 09:20, entry 09:25 @ 22512.65, exit 10:05 @ 22599.5 (two_strong_bullish), -86.85 pts, B -2483.9, C None
- Primary positions at own signal candle (version B filter): {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'below', 'cpr': 'below', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'below'}
- **A exact** (1 sessions — SAMPLE TOO SMALL, no significance): 1 tr | win 0.0% | avg W 0.0 / L -86.85 | PF 0.0 | exp -86.85 | maxDD -86.85 | hold 40.0m | B -2,484 (PF 0.0) | C +0 (0/1 trades priced from real snapshots) | IS PF None / OOS PF 0.0 | not computed: fewer than 10 trades (sample too small)
  - dates: 2026-10-01
- **B primary** (pre-declared test: own entry + own exit + primary positions {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'below', 'cpr': 'below', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'below'}): 361 tr | win 43.2% | avg W 92.81 / L -57.63 | PF 1.23 | exp 7.38 | maxDD -1150.9 | hold 138.3m | B +185,552 (PF 1.5) | C +3,418 (10/361 trades priced from real snapshots) | IS PF 1.31 / OOS PF 1.13 | {'n': 361, 'mean': 7.38, 'ci95': [-2.72, 18.08], 'p_mean_le_0': 0.082}
  - by year: 2020: n=28 PF=2.35 B=+45,767; 2021: n=42 PF=1.61 B=+23,340; 2022: n=72 PF=0.86 B=-6,112; 2023: n=49 PF=1.43 B=+24,595; 2024: n=58 PF=1.03 B=+43,096; 2025: n=51 PF=0.63 B=-25,550; 2026: n=61 PF=1.91 B=+80,416
- **C fixed exit 20 EMA**: 361 tr | win 44.3% | avg W 74.45 / L -47.25 | PF 1.25 | exp 6.69 | maxDD -970.75 | hold 114.4m | B +115,079 (PF 1.36) | C +2,840 (10/361 trades priced from real snapshots) | IS PF 1.26 / OOS PF 1.24 | {'n': 361, 'mean': 6.69, 'ci95': [-1.48, 15.91], 'p_mean_le_0': 0.06}
- **C fixed exit 50 EMA**: 361 tr | win 44.6% | avg W 107.35 / L -63.53 | PF 1.36 | exp 12.68 | maxDD -830.5 | hold 196.2m | B +259,350 (PF 1.64) | C +9,493 (10/361 trades priced from real snapshots) | IS PF 1.24 / OOS PF 1.51 | {'n': 361, 'mean': 12.68, 'ci95': [2.44, 23.22], 'p_mean_le_0': 0.0072}  (fixed exits replace the condition's own exit (two_strong_bullish))

### C007 (2026-09-22, SHORT) — entry `rejections_then_strong_breakdown_short`, exit `staged_zones`
- Reproduction on 2026-09-22: signal 10:35, entry 10:40 @ 23399.25, exit 12:55 @ 23354.58 (staged:stage1:ema200_5m,stage2:weekly_cpr,stage3:pdl_s1_zone), +44.67 pts, B 1628.05, C None
- Primary positions at own signal candle (version B filter): {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'above', 'cpr': 'inside', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}
- **A exact** (2 sessions — SAMPLE TOO SMALL, no significance): 1 tr | win 100.0% | avg W 44.67 / L 0.0 | PF None | exp 44.67 | maxDD 0.0 | hold 135.0m | B +1,628 (PF None) | C +0 (0/1 trades priced from real snapshots) | IS PF None / OOS PF None | not computed: fewer than 10 trades (sample too small)
  - dates: 2020-09-30, 2026-09-22
- **B primary** (pre-declared test: own entry + own exit + primary positions {'vwap': 'below', 'ema200_1m': 'below', 'ema200_5m': 'above', 'cpr': 'inside', 'pdh_r1_zone': 'below', 'pdl_s1_zone': 'above'}): 41 tr | win 53.7% | avg W 48.83 / L -60.56 | PF 0.93 | exp -1.86 | maxDD -484.56 | hold 252.3m | B -124 (PF 1.0) | C -1,705 (1/41 trades priced from real snapshots) | IS PF 1.04 / OOS PF 0.63 | {'n': 41, 'mean': -1.86, 'ci95': [-24.58, 12.64], 'p_mean_le_0': 0.7198}
  - by year: 2020: n=3 PF=6.23 B=+4,473; 2021: n=8 PF=0.47 B=-4,875; 2022: n=9 PF=0.98 B=+1,560; 2023: n=8 PF=0.77 B=+876; 2024: n=6 PF=2.37 B=+2,486; 2025: n=3 PF=0.03 B=-6,001; 2026: n=4 PF=2.99 B=+1,355
- **C fixed exit 20 EMA**: 41 tr | win 22.0% | avg W 64.19 / L -27.01 | PF 0.67 | exp -6.99 | maxDD -369.7 | hold 56.1m | B -8,508 (PF 0.73) | C -720 (1/41 trades priced from real snapshots) | IS PF 0.85 / OOS PF 0.18 | {'n': 41, 'mean': -6.99, 'ci95': [-20.41, 7.77], 'p_mean_le_0': 0.8124}
- **C fixed exit 50 EMA**: 41 tr | win 14.6% | avg W 94.23 / L -15.75 | PF 1.03 | exp 0.34 | maxDD -171.25 | hold 52.8m | B -2,703 (PF 0.89) | C -571 (1/41 trades priced from real snapshots) | IS PF 1.26 / OOS PF 0.48 | {'n': 41, 'mean': 0.34, 'ci95': [-11.86, 17.82], 'p_mean_le_0': 0.4656}  (fixed exits replace the condition's own exit (staged_zones))


## Alternative exits E1-E5 on unchanged version-B entries (tests 31-40; threshold p < 0.00125)
Entries per source: {'C001': 1161, 'H-D': 514, 'C006': 361, 'C003': 329, 'C004': 133, 'C005': 119, 'C002': 99, 'C007': 41}. Split: IS 2020-07..2023-12, OOS 2024-01..2026-10-16. Screen = PF>1.15 both periods, layer B > 0 both, OOS bootstrap CI above zero (screen, not proof). REF20/REF50 and per-condition rows are descriptive. Everything is EXPLORATORY.

| Exit | Side | Trades | Win% | PF | Exp pts | B total | IS PF / OOS PF | IS B / OOS B | OOS CI | full p | Screen |
|---|---|---|---|---|---|---|---|---|---|---|---|
| E1 | long | 1609 | 31.0 | 1.06 | 1.03 | -64,689 | 1.14 / 0.99 | -15,687 / -49,002 | [-5.89, 5.46] | 0.283 | fail |
| E2 | long | 1609 | 32.8 | 1.0 | -0.09 | -40,251 | 1.06 / 0.94 | +966 / -41,217 | [-8.47, 4.65] | 0.5332 | fail |
| E3 | long | 1609 | 37.1 | 0.95 | -1.5 | +43,604 | 0.96 / 0.94 | +5,485 / +38,119 | [-11.26, 5.8] | 0.7822 | fail |
| E4 | long | 1609 | 39.2 | 1.09 | 2.22 | +66,626 | 1.1 / 1.08 | +24,909 / +41,717 | [-5.48, 8.51] | 0.18 | fail |
| E5 | long | 1609 | 51.3 | 0.99 | -0.1 | -231,323 | 1.02 / 0.97 | -118,599 / -112,724 | [-3.63, 2.85] | 0.5466 | fail |
| REF20 | long | 1609 | 35.7 | 0.94 | -1.21 | -186,316 | 1.03 / 0.86 | -67,365 / -118,951 | [-8.7, 1.51] | 0.8138 | fail |
| REF50 | long | 1609 | 32.4 | 1.0 | 0.12 | -19,003 | 1.09 / 0.93 | +34,706 / -53,710 | [-9.12, 3.98] | 0.511 | fail |
| E1 | short | 1148 | 38.3 | 1.24 | 5.56 | +320,271 | 1.17 / 1.32 | +129,794 / +190,477 | [-1.7, 16.74] | 0.03 | fail |
| E2 | short | 1148 | 41.4 | 1.37 | 11.12 | +704,477 | 1.22 / 1.51 | +249,960 / +454,517 | [4.53, 29.16] | 0.0004 | PASS |
| E3 | short | 1148 | 44.0 | 1.19 | 6.83 | +624,813 | 1.09 / 1.28 | +200,141 / +424,672 | [-3.58, 26.25] | 0.0574 | fail |
| E4 | short | 1148 | 41.6 | 1.23 | 6.5 | +388,240 | 1.07 / 1.38 | +97,945 / +290,295 | [-0.1, 23.34] | 0.0244 | fail |
| E5 | short | 1148 | 44.9 | 1.2 | 4.23 | +211,168 | 1.13 / 1.27 | +80,954 / +130,214 | [-2.18, 14.12] | 0.051 | fail |
| REF20 | short | 1148 | 41.4 | 1.18 | 4.56 | +256,248 | 1.08 / 1.29 | +65,675 / +190,574 | [-3.21, 19.52] | 0.0696 | fail |
| REF50 | short | 1148 | 40.9 | 1.33 | 10.57 | +707,679 | 1.16 / 1.49 | +222,223 / +485,456 | [4.59, 30.22] | 0.001 | PASS |

Per pooled group detail (avg win/loss, max DD, hold, layer C coverage, by year):
- E1|long: avg W 58.52 / L -24.81 | maxDD -2720.6 | hold 82.7m | C -5,402 (42/1609 trades priced from real snapshots) | years 2020: n=150 PF=1.04 B=-21,354; 2021: n=261 PF=1.23 B=+15,646; 2022: n=226 PF=0.98 B=-19,851; 2023: n=261 PF=1.27 B=+9,874; 2024: n=269 PF=1.0 B=-4,412; 2025: n=264 PF=1.03 B=-10,052; 2026: n=178 PF=0.92 B=-34,538
- E2|long: avg W 70.8 / L -34.71 | maxDD -3051.75 | hold 124.0m | C +14,440 (41/1609 trades priced from real snapshots) | years 2020: n=150 PF=0.87 B=-34,937; 2021: n=261 PF=1.14 B=+25,697; 2022: n=226 PF=0.97 B=-17,250; 2023: n=261 PF=1.17 B=+27,456; 2024: n=269 PF=0.97 B=+17,730; 2025: n=264 PF=1.1 B=+7,625; 2026: n=178 PF=0.7 B=-66,572
- E3|long: avg W 81.43 / L -50.42 | maxDD -3715.85 | hold 209.9m | C +27,403 (38/1609 trades priced from real snapshots) | years 2020: n=150 PF=0.96 B=-14,598; 2021: n=261 PF=0.82 B=-32,008; 2022: n=226 PF=0.91 B=-13,905; 2023: n=261 PF=1.24 B=+65,995; 2024: n=269 PF=0.87 B=-9,890; 2025: n=264 PF=1.04 B=+48,576; 2026: n=178 PF=0.91 B=-567
- E4|long: avg W 70.48 / L -41.82 | maxDD -1703.65 | hold 153.2m | C +35,746 (39/1609 trades priced from real snapshots) | years 2020: n=150 PF=1.03 B=-19,136; 2021: n=261 PF=1.09 B=+4,247; 2022: n=226 PF=1.03 B=+4,594; 2023: n=261 PF=1.23 B=+35,203; 2024: n=269 PF=1.06 B=+11,497; 2025: n=264 PF=1.1 B=+12,033; 2026: n=178 PF=1.06 B=+18,188
- E5|long: avg W 23.42 / L -24.91 | maxDD -2214.15 | hold 34.7m | C -20,582 (43/1609 trades priced from real snapshots) | years 2020: n=150 PF=0.91 B=-27,743; 2021: n=261 PF=0.99 B=-33,427; 2022: n=226 PF=0.96 B=-33,161; 2023: n=261 PF=1.2 B=-24,269; 2024: n=269 PF=0.78 B=-65,254; 2025: n=264 PF=1.23 B=-2,684; 2026: n=178 PF=0.88 B=-44,786
- REF20|long: avg W 52.52 / L -31.09 | maxDD -2630.95 | hold 82.0m | C +3,475 (41/1609 trades priced from real snapshots) | years 2020: n=150 PF=0.77 B=-39,397; 2021: n=261 PF=1.03 B=-22,962; 2022: n=226 PF=1.02 B=-6,503; 2023: n=261 PF=1.21 B=+1,496; 2024: n=269 PF=0.74 B=-68,554; 2025: n=264 PF=1.03 B=-4,799; 2026: n=178 PF=0.82 B=-45,599
- REF50|long: avg W 75.49 / L -36.08 | maxDD -3212.65 | hold 137.2m | C +24,477 (42/1609 trades priced from real snapshots) | years 2020: n=150 PF=0.84 B=-36,762; 2021: n=261 PF=1.1 B=+20,219; 2022: n=226 PF=1.09 B=+13,937; 2023: n=261 PF=1.22 B=+37,313; 2024: n=269 PF=0.89 B=-5,488; 2025: n=264 PF=1.11 B=+16,655; 2026: n=178 PF=0.75 B=-64,877
- E1|short: avg W 74.86 / L -37.51 | maxDD -928.7 | hold 102.6m | C +15,587 (30/1148 trades priced from real snapshots) | years 2020: n=82 PF=1.45 B=+58,279; 2021: n=166 PF=1.17 B=+23,871; 2022: n=207 PF=0.96 B=+9,562; 2023: n=164 PF=1.38 B=+38,082; 2024: n=177 PF=1.49 B=+101,450; 2025: n=174 PF=0.89 B=-23,571; 2026: n=178 PF=1.59 B=+112,598
- E2|short: avg W 100.46 / L -51.93 | maxDD -1305.95 | hold 158.1m | C +28,501 (30/1148 trades priced from real snapshots) | years 2020: n=82 PF=1.59 B=+76,629; 2021: n=166 PF=1.31 B=+79,865; 2022: n=207 PF=0.99 B=+39,919; 2023: n=164 PF=1.38 B=+53,547; 2024: n=177 PF=1.4 B=+152,248; 2025: n=174 PF=1.13 B=+35,850; 2026: n=178 PF=2.01 B=+266,420
- E3|short: avg W 98.22 / L -64.95 | maxDD -2321.85 | hold 194.0m | C +9,540 (32/1148 trades priced from real snapshots) | years 2020: n=82 PF=2.02 B=+85,996; 2021: n=166 PF=1.35 B=+99,056; 2022: n=207 PF=0.79 B=-11,133; 2023: n=164 PF=1.04 B=+26,222; 2024: n=177 PF=1.39 B=+207,323; 2025: n=174 PF=0.94 B=-110; 2026: n=178 PF=1.43 B=+217,459
- E4|short: avg W 84.61 / L -49.02 | maxDD -1175.0 | hold 130.8m | C +6,382 (30/1148 trades priced from real snapshots) | years 2020: n=82 PF=1.14 B=+28,488; 2021: n=166 PF=1.27 B=+58,094; 2022: n=207 PF=0.8 B=-40,133; 2023: n=164 PF=1.37 B=+51,496; 2024: n=177 PF=1.66 B=+141,082; 2025: n=174 PF=0.93 B=-14,310; 2026: n=178 PF=1.56 B=+163,523
- E5|short: avg W 56.8 / L -38.68 | maxDD -868.58 | hold 85.2m | C +16,531 (32/1148 trades priced from real snapshots) | years 2020: n=82 PF=1.38 B=+47,972; 2021: n=166 PF=1.08 B=-165; 2022: n=207 PF=0.91 B=-8,130; 2023: n=164 PF=1.5 B=+41,277; 2024: n=177 PF=1.45 B=+82,133; 2025: n=174 PF=0.8 B=-45,804; 2026: n=178 PF=1.6 B=+93,885
- REF20|short: avg W 71.87 / L -42.95 | maxDD -1001.5 | hold 103.4m | C +11,987 (30/1148 trades priced from real snapshots) | years 2020: n=82 PF=1.67 B=+67,892; 2021: n=166 PF=0.98 B=+1,525; 2022: n=207 PF=0.82 B=-44,829; 2023: n=164 PF=1.4 B=+41,086; 2024: n=177 PF=1.4 B=+129,270; 2025: n=174 PF=1.01 B=-19,469; 2026: n=178 PF=1.38 B=+80,773
- REF50|short: avg W 104.72 / L -54.46 | maxDD -1577.9 | hold 170.8m | C +30,365 (29/1148 trades priced from real snapshots) | years 2020: n=82 PF=1.54 B=+74,178; 2021: n=166 PF=1.26 B=+74,247; 2022: n=207 PF=0.96 B=+37,012; 2023: n=164 PF=1.24 B=+36,786; 2024: n=177 PF=1.39 B=+173,225; 2025: n=174 PF=1.23 B=+57,775; 2026: n=178 PF=1.85 B=+254,456

Per-condition (DESCRIPTIVE, small samples, not for choosing an exit):
- C001|E1: n=1161 PF=1.17 B=+14,341
- C001|E2: n=1161 PF=1.04 B=+8,964
- C001|E3: n=1161 PF=0.98 B=+49,315
- C001|E4: n=1161 PF=1.13 B=+80,245
- C001|E5: n=1161 PF=1.12 B=-106,975
- C001|REF20: n=1161 PF=0.99 B=-94,666
- C001|REF50: n=1161 PF=1.05 B=+33,859
- C003|E1: n=329 PF=0.67 B=-93,262
- C003|E2: n=329 PF=0.79 B=-60,433
- C003|E3: n=329 PF=0.81 B=-46,407
- C003|E4: n=329 PF=0.91 B=-43,903
- C003|E5: n=329 PF=0.67 B=-97,911
- C003|REF20: n=329 PF=0.79 B=-69,494
- C003|REF50: n=329 PF=0.78 B=-70,901
- C005|E1: n=119 PF=1.24 B=+14,232
- C005|E2: n=119 PF=1.13 B=+11,217
- C005|E3: n=119 PF=1.17 B=+40,696
- C005|E4: n=119 PF=1.22 B=+30,284
- C005|E5: n=119 PF=0.85 B=-26,437
- C005|REF20: n=119 PF=0.83 B=-22,157
- C005|REF50: n=119 PF=1.22 B=+18,038
- C002|E1: n=99 PF=1.09 B=+14,696
- C002|E2: n=99 PF=1.1 B=+35,890
- C002|E3: n=99 PF=0.83 B=+27,418
- C002|E4: n=99 PF=1.1 B=+20,927
- C002|E5: n=99 PF=1.1 B=+1,228
- C002|REF20: n=99 PF=1.05 B=+16,870
- C002|REF50: n=99 PF=0.95 B=+27,356
- C004|E1: n=133 PF=1.38 B=+49,695
- C004|E2: n=133 PF=1.47 B=+51,084
- C004|E3: n=133 PF=0.76 B=-30,186
- C004|E4: n=133 PF=1.49 B=+68,143
- C004|E5: n=133 PF=0.73 B=-34,431
- C004|REF20: n=133 PF=1.11 B=+5,470
- C004|REF50: n=133 PF=1.75 B=+65,242
- C006|E1: n=361 PF=1.23 B=+92,389
- C006|E2: n=361 PF=1.39 B=+256,915
- C006|E3: n=361 PF=1.29 B=+271,500
- C006|E4: n=361 PF=1.17 B=+100,525
- C006|E5: n=361 PF=1.26 B=+96,669
- C006|REF20: n=361 PF=1.25 B=+115,079
- C006|REF50: n=361 PF=1.36 B=+259,350
- C007|E1: n=41 PF=0.91 B=+3,398
- C007|E2: n=41 PF=0.87 B=-3,911
- C007|E3: n=41 PF=1.45 B=+40
- C007|E4: n=41 PF=0.75 B=-5,468
- C007|E5: n=41 PF=0.62 B=-12,976
- C007|REF20: n=41 PF=0.67 B=-8,508
- C007|REF50: n=41 PF=1.03 B=-2,703
- H-D|E1: n=514 PF=1.27 B=+160,093
- H-D|E2: n=514 PF=1.41 B=+364,500
- H-D|E3: n=514 PF=1.22 B=+356,041
- H-D|E4: n=514 PF=1.28 B=+204,113
- H-D|E5: n=514 PF=1.29 B=+160,678
- H-D|REF20: n=514 PF=1.2 B=+127,337
- H-D|REF50: n=514 PF=1.36 B=+358,433


## R001 forward status (paper)
- IN_PROGRESS: 0/150 trades (evaluated 2026-10-16T16:00:09)
- Snapshot coverage: 0/0 sessions full, 0 partial, 0 with no snapshots; trade price coverage n/a (need >= 80%). OK

## Change vs previous run
Previous run: 2026-10-16T22:50:51
- tests on dataset: 30 -> 40
- C001 base|futures matched: trades 1 -> 1, expectancy 289.55 -> 289.55, PF None -> None
- C001 base|futures non_matched: trades 1160 -> 1160, expectancy -0.49 -> -0.49, PF 0.98 -> 0.98
