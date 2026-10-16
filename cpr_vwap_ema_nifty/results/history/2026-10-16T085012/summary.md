# CPR_VWAP_EMA_Nifty backtest - 2026-10-16T08:50:12

Window 2020-07-01 to 2026-10-15; out-of-sample from 2024-07-01.
Sessions: 1555; missing futures volume (skipped in 'futures' set, spot-TWAP in 'with_missing' set): 3.
Spot data source per session: {'angel': 1614, 'insufficient': 4}.

Layer A = NIFTY index points, no costs. B = modelled 1-lot ATM option, net of costs and 1pt/side slippage (prior-session IV held constant). C = real option prices from 5-min chain snapshots (2026-06-19 onward only).

## C001
Setup-matched sessions: 1 (2026-10-15)

### Variants (layer A, points)
- **+all_filters|futures**
  - matched: 1 trades | win 100.0% | avg win 233.95 / loss 0.0 pts | PF n/a | exp 233.95 pts | total 233.95 | maxDD 0.0 | hold 190.0m | MFE 235.7 / MAE 0.0 pts
  - non_matched: 361 trades | win 46.8% | avg win 43.35 / loss -31.58 pts | PF 1.21 | exp 3.5 pts | total 1264.02 | maxDD -808.63 | hold 107.3m | MFE 43.6 / MAE 28.4 pts
  - all: 362 trades | win 47.0% | avg win 44.47 / loss -31.58 pts | PF 1.25 | exp 4.14 pts | total 1497.97 | maxDD -808.63 | hold 107.6m | MFE 44.1 / MAE 28.3 pts
- **+all_filters|with_missing**
  - matched: 1 trades | win 100.0% | avg win 233.95 / loss 0.0 pts | PF n/a | exp 233.95 pts | total 233.95 | maxDD 0.0 | hold 190.0m | MFE 235.7 / MAE 0.0 pts
  - non_matched: 362 trades | win 46.7% | avg win 43.35 / loss -31.59 pts | PF 1.2 | exp 3.4 pts | total 1230.42 | maxDD -842.23 | hold 107.3m | MFE 43.5 / MAE 28.4 pts
  - all: 363 trades | win 46.8% | avg win 44.47 / loss -31.59 pts | PF 1.24 | exp 4.03 pts | total 1464.37 | maxDD -842.23 | hold 107.5m | MFE 44.0 / MAE 28.3 pts
- **+ema200_30m|futures**
  - matched: 1 trades | win 100.0% | avg win 267.55 / loss 0.0 pts | PF n/a | exp 267.55 pts | total 267.55 | maxDD 0.0 | hold 280.0m | MFE 309.6 / MAE 2.5 pts
  - non_matched: 912 trades | win 36.8% | avg win 49.83 / loss -29.84 pts | PF 0.97 | exp -0.49 pts | total -446.85 | maxDD -889.4 | hold 90.2m | MFE 42.9 / MAE 29.1 pts
  - all: 913 trades | win 36.9% | avg win 50.47 / loss -29.84 pts | PF 0.99 | exp -0.2 pts | total -179.3 | maxDD -889.4 | hold 90.4m | MFE 43.2 / MAE 29.1 pts
- **+ema200_30m|with_missing**
  - matched: 1 trades | win 100.0% | avg win 267.55 / loss 0.0 pts | PF n/a | exp 267.55 pts | total 267.55 | maxDD 0.0 | hold 280.0m | MFE 309.6 / MAE 2.5 pts
  - non_matched: 914 trades | win 36.8% | avg win 49.83 / loss -29.97 pts | PF 0.97 | exp -0.64 pts | total -582.0 | maxDD -923.0 | hold 90.1m | MFE 42.8 / MAE 29.2 pts
  - all: 915 trades | win 36.8% | avg win 50.47 / loss -29.97 pts | PF 0.98 | exp -0.34 pts | total -314.45 | maxDD -923.0 | hold 90.3m | MFE 43.1 / MAE 29.2 pts
- **+pwh_break|futures**
  - matched: 1 trades | win 100.0% | avg win 227.65 / loss 0.0 pts | PF n/a | exp 227.65 pts | total 227.65 | maxDD 0.0 | hold 275.0m | MFE 269.6 / MAE 0.0 pts
  - non_matched: 573 trades | win 38.2% | avg win 41.83 / loss -30.4 pts | PF 0.85 | exp -2.79 pts | total -1599.95 | maxDD -1667.75 | hold 93.5m | MFE 41.1 / MAE 29.6 pts
  - all: 574 trades | win 38.3% | avg win 42.68 / loss -30.4 pts | PF 0.87 | exp -2.39 pts | total -1372.3 | maxDD -1667.75 | hold 93.8m | MFE 41.5 / MAE 29.5 pts
- **+pwh_break|with_missing**
  - matched: 1 trades | win 100.0% | avg win 227.65 / loss 0.0 pts | PF n/a | exp 227.65 pts | total 227.65 | maxDD 0.0 | hold 275.0m | MFE 269.6 / MAE 0.0 pts
  - non_matched: 574 trades | win 38.2% | avg win 41.83 / loss -30.41 pts | PF 0.85 | exp -2.85 pts | total -1633.55 | maxDD -1701.35 | hold 93.5m | MFE 41.0 / MAE 29.6 pts
  - all: 575 trades | win 38.3% | avg win 42.68 / loss -30.41 pts | PF 0.87 | exp -2.45 pts | total -1405.9 | maxDD -1701.35 | hold 93.8m | MFE 41.4 / MAE 29.5 pts
- **+r1_pdh_break|futures**
  - matched: 1 trades | win 100.0% | avg win 253.75 / loss 0.0 pts | PF n/a | exp 253.75 pts | total 253.75 | maxDD 0.0 | hold 285.0m | MFE 295.8 / MAE 16.3 pts
  - non_matched: 614 trades | win 46.3% | avg win 56.19 / loss -34.57 pts | PF 1.4 | exp 7.41 pts | total 4550.0 | maxDD -565.45 | hold 119.7m | MFE 53.9 / MAE 30.5 pts
  - all: 615 trades | win 46.3% | avg win 56.88 / loss -34.57 pts | PF 1.42 | exp 7.81 pts | total 4803.75 | maxDD -565.45 | hold 120.0m | MFE 54.3 / MAE 30.4 pts
- **+r1_pdh_break|with_missing**
  - matched: 1 trades | win 100.0% | avg win 253.75 / loss 0.0 pts | PF n/a | exp 253.75 pts | total 253.75 | maxDD 0.0 | hold 285.0m | MFE 295.8 / MAE 16.3 pts
  - non_matched: 616 trades | win 46.1% | avg win 56.19 / loss -34.52 pts | PF 1.39 | exp 7.3 pts | total 4495.8 | maxDD -565.45 | hold 119.6m | MFE 53.8 / MAE 30.5 pts
  - all: 617 trades | win 46.2% | avg win 56.88 / loss -34.52 pts | PF 1.41 | exp 7.7 pts | total 4749.55 | maxDD -565.45 | hold 119.9m | MFE 54.2 / MAE 30.4 pts
- **+target_r4_mh3|futures**
  - matched: 1 trades | win 100.0% | avg win 295.85 / loss 0.0 pts | PF n/a | exp 295.85 pts | total 295.85 | maxDD 0.0 | hold 220.0m | MFE 297.6 / MAE 44.4 pts
  - non_matched: 1159 trades | win 39.2% | avg win 48.84 / loss -32.84 pts | PF 0.96 | exp -0.84 pts | total -976.07 | maxDD -1714.13 | hold 83.6m | MFE 44.0 / MAE 31.0 pts
  - all: 1160 trades | win 39.2% | avg win 49.39 / loss -32.84 pts | PF 0.97 | exp -0.59 pts | total -680.22 | maxDD -1714.13 | hold 83.7m | MFE 44.2 / MAE 31.0 pts
- **+target_r4_mh3|with_missing**
  - matched: 1 trades | win 100.0% | avg win 295.85 / loss 0.0 pts | PF n/a | exp 295.85 pts | total 295.85 | maxDD 0.0 | hold 220.0m | MFE 297.6 / MAE 44.4 pts
  - non_matched: 1162 trades | win 39.1% | avg win 48.84 / loss -32.92 pts | PF 0.95 | exp -0.97 pts | total -1131.82 | maxDD -1815.68 | hold 83.5m | MFE 43.9 / MAE 31.1 pts
  - all: 1163 trades | win 39.1% | avg win 49.39 / loss -32.92 pts | PF 0.96 | exp -0.72 pts | total -835.97 | maxDD -1815.68 | hold 83.6m | MFE 44.1 / MAE 31.1 pts
- **base|futures**
  - matched: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - non_matched: 1159 trades | win 37.8% | avg win 52.57 / loss -32.71 pts | PF 0.98 | exp -0.48 pts | total -557.65 | maxDD -1370.75 | hold 90.2m | MFE 46.7 / MAE 31.3 pts
  - all: 1160 trades | win 37.8% | avg win 53.11 / loss -32.71 pts | PF 0.99 | exp -0.23 pts | total -268.1 | maxDD -1370.75 | hold 90.4m | MFE 46.9 / MAE 31.3 pts
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
  - B: 492 trades | win 30.7% | avg win 2536.71 / loss -1314.4 Rs | PF 0.85 | exp -132.46 Rs | total -65168.06 | maxDD -68114.72 | hold 83.2m | MFE 53.3 / MAE 37.6 pts
  - C: 26 trades | win 19.2% | avg win 739.43 / loss -915.5 Rs | PF 0.19 | exp -597.25 Rs | total -15528.45 | maxDD -16992.56 | hold 67.3m | MFE 29.5 / MAE 29.3 pts
- **all**
  - A: 1160 trades | win 37.8% | avg win 53.11 / loss -32.71 pts | PF 0.99 | exp -0.23 pts | total -268.1 | maxDD -1370.75 | hold 90.4m | MFE 46.9 / MAE 31.3 pts
  - B: 493 trades | win 30.8% | avg win 2608.63 / loss -1314.4 Rs | PF 0.88 | exp -104.87 Rs | total -51700.28 | maxDD -68114.72 | hold 83.6m | MFE 53.8 / MAE 37.6 pts
  - C: 27 trades | win 22.2% | avg win 2673.16 / loss -915.5 Rs | PF 0.83 | exp -118.02 Rs | total -3186.66 | maxDD -16992.56 | hold 76.1m | MFE 40.7 / MAE 29.9 pts
- **B vs C on real-price days**: {'trades': 26, 'B_total': -2568.7, 'C_total': -3094.78, 'mean_abs_diff_per_trade': 512.72, 'sign_agreement_pct': 84.6}

### In-sample vs out-of-sample (base)
- IS matched: 0 trades
- IS non-matched: 762 trades | win 38.7% | avg win 48.09 / loss -30.58 pts | PF 0.99 | exp -0.12 pts | total -90.85 | maxDD -1057.7 | hold 94.0m | MFE 42.4 / MAE 28.6 pts
- IS matched B: 0 trades
- IS non-matched B: 96 trades | win 29.2% | avg win 2776.52 / loss -1445.34 Rs | PF 0.79 | exp -213.97 Rs | total -20540.75 | maxDD -34558.05 | hold 82.7m | MFE 46.4 / MAE 42.0 pts
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

### Setup sensitivity - EXPLORATORY, not rules (base, futures VWAP)
Each row loosens the setup; many looks at one dataset, so a single good row is weak evidence.
- **drop cpr_width** (1 sessions): A 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **drop cpr_vs_prior_day** (3 sessions): A 3 trades | win 66.7% | avg win 152.93 / loss -9.25 pts | PF 33.06 | exp 98.87 pts | total 296.6 | maxDD -9.25 | hold 121.7m | MFE 130.1 / MAE 20.7 pts
  - B: 2 trades | win 50.0% | avg win 13467.78 / loss -484.96 Rs | PF 27.77 | exp 6491.41 Rs | total 12982.82 | maxDD -484.96 | hold 155.0m | MFE 168.8 / MAE 28.9 pts
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
  - B: 1 trades | win 100.0% | avg win 13467.78 / loss 0.0 Rs | PF n/a | exp 13467.78 Rs | total 13467.78 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
  - OOS A: 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts
- **CPR width only** (712 sessions): A 552 trades | win 35.7% | avg win 53.44 / loss -29.5 pts | PF 1.01 | exp 0.1 pts | total 54.25 | maxDD -2078.55 | hold 87.9m | MFE 43.5 / MAE 28.5 pts
  - B: 250 trades | win 30.0% | avg win 2554.61 / loss -1257.76 Rs | PF 0.87 | exp -114.05 Rs | total -28512.62 | maxDD -68519.65 | hold 80.9m | MFE 49.5 / MAE 34.7 pts
  - OOS A: 205 trades | win 35.1% | avg win 63.78 / loss -35.75 pts | PF 0.97 | exp -0.8 pts | total -163.2 | maxDD -1295.35 | hold 81.8m | MFE 51.4 / MAE 34.5 pts

### Walk-forward by year (base, points)
- 2020: matched 0 trades || non-matched 111 trades | win 39.6% | avg win 25.99 / loss -21.36 pts | PF 0.8 | exp -2.59 pts | total -287.95 | maxDD -377.2 | hold 87.7m | MFE 33.2 / MAE 20.1 pts
- 2021: matched 0 trades || non-matched 190 trades | win 40.5% | avg win 44.78 / loss -32.38 pts | PF 0.94 | exp -1.11 pts | total -211.25 | maxDD -690.2 | hold 97.5m | MFE 42.6 / MAE 29.5 pts
- 2022: matched 0 trades || non-matched 176 trades | win 35.8% | avg win 65.77 / loss -34.11 pts | PF 1.08 | exp 1.64 pts | total 289.15 | maxDD -416.55 | hold 94.5m | MFE 49.1 / MAE 33.3 pts
- 2023: matched 0 trades || non-matched 188 trades | win 42.0% | avg win 41.48 / loss -23.23 pts | PF 1.29 | exp 3.96 pts | total 745.4 | maxDD -338.5 | hold 99.8m | MFE 39.5 / MAE 21.6 pts
- 2024: matched 0 trades || non-matched 186 trades | win 31.2% | avg win 66.88 / loss -39.14 pts | PF 0.77 | exp -6.08 pts | total -1131.2 | maxDD -1370.75 | hold 79.0m | MFE 48.5 / MAE 40.2 pts
- 2025: matched 0 trades || non-matched 179 trades | win 41.3% | avg win 64.52 / loss -36.41 pts | PF 1.25 | exp 5.32 pts | total 951.45 | maxDD -531.6 | hold 89.4m | MFE 60.1 / MAE 34.8 pts
- 2026: matched 1 trades | win 100.0% | avg win 289.55 / loss 0.0 pts | PF n/a | exp 289.55 pts | total 289.55 | maxDD 0.0 | hold 305.0m | MFE 331.6 / MAE 44.4 pts || non-matched 129 trades | win 33.3% | avg win 54.91 / loss -38.08 pts | PF 0.72 | exp -7.08 pts | total -913.25 | maxDD -1026.9 | hold 79.4m | MFE 50.1 / MAE 37.5 pts

