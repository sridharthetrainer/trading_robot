# R002 random-entry control (2026-10-17T02:01:07)

DESCRIPTIVE ONLY: not a hypothesis test, NOT added to the test count (stays 40). R002 stays proposed; R001 untouched.
Seed 20261017, 1000 draws, 645 R002 days, random entry candle 09:20-14:00, one short per day, ATM put, same costs.

## Distribution vs R002's real result

| Exit | metric | R002 real | percentile | median | 5% | 95% |
|---|---|---|---|---|---|---|
| E2 | pf | 1.39 | 0.0 | 1.88 | 1.65 | 2.15 |
| E2 | B_net_rs | 401611.15 | 97.8 | 305744.03 | 229148.7 | 380318.77 |
| REF20 | pf | 1.18 | 0.2 | 1.6 | 1.33 | 1.88 |
| REF20 | B_net_rs | 143302.38 | 82.7 | 102114.41 | 33823.27 | 174342.4 |
| REF50 | pf | 1.32 | 0.0 | 1.77 | 1.56 | 2.02 |
| REF50 | B_net_rs | 381375.49 | 93.9 | 310599.87 | 235482.5 | 385950.88 |
| E3 | pf | 1.13 | 0.0 | 1.49 | 1.33 | 1.66 |
| E3 | B_net_rs | 296240.64 | 22.6 | 332901.81 | 256094.78 | 405804.52 |

## Lookahead in the control as specified

The 645 days were selected because a breakdown signal fired; a random candle BEFORE that signal 'knows' the day breaks down. Same table, split:

| Exit | R002 real PF / B per trade | random BEFORE real entry PF / B per trade | random AFTER real entry PF / B per trade |
|---|---|---|---|
| E2 | 1.39 / 623 | 4.56 / 1319 | 1.53 / 314 |
| REF20 | 1.18 / 222 | 3.51 / 895 | 1.27 / 22 |
| REF50 | 1.32 / 591 | 4.51 / 1290 | 1.44 / 312 |
| E3 | 1.13 / 459 | 4.52 / 1305 | 1.17 / 259 |

## R002 by year / half-year (E2 exit, first signal per day)

| Period | trades | PF | exp pts | B | win % |
|---|---|---|---|---|---|
| 2020 | 44 | 1.23 | 5.61 | +20,716 | 34 |
| 2021 | 99 | 1.35 | 9.77 | +49,057 | 39 |
| 2022 | 108 | 1.12 | 3.93 | +35,469 | 39 |
| 2023 | 94 | 1.46 | 8.87 | +35,767 | 43 |
| 2024 | 100 | 1.45 | 16.56 | +96,113 | 40 |
| 2025 | 107 | 1.14 | 4.31 | +25,250 | 34 |
| 2026 | 93 | 1.99 | 31.04 | +139,239 | 52 |
| 2020-H2 | 44 | 1.23 | 5.61 | +20,716 | 34 |
| 2021-H1 | 45 | 1.71 | 16.23 | +25,991 | 47 |
| 2021-H2 | 54 | 1.14 | 4.39 | +23,066 | 33 |
| 2022-H1 | 58 | 1.2 | 7.08 | +33,923 | 40 |
| 2022-H2 | 50 | 1.01 | 0.29 | +1,546 | 38 |
| 2023-H1 | 48 | 1.58 | 10.01 | +16,285 | 48 |
| 2023-H2 | 46 | 1.36 | 7.68 | +19,482 | 37 |
| 2024-H1 | 47 | 1.51 | 17.13 | +47,101 | 36 |
| 2024-H2 | 53 | 1.4 | 16.05 | +49,012 | 43 |
| 2025-H1 | 50 | 1.12 | 4.69 | +19,331 | 30 |
| 2025-H2 | 57 | 1.17 | 3.97 | +5,919 | 37 |
| 2026-H1 | 56 | 1.98 | 36.92 | +105,554 | 54 |
| 2026-H2 | 37 | 2.02 | 22.14 | +33,685 | 49 |

All: 645 trades, PF 1.39, B +401,611. Excluding 2026: 552 trades, PF 1.29, B +262,372; IS <=2023 PF 1.27, 2024-2025 PF 1.31; day-bootstrap 95% CI excl. 2026 [0.35, 16.83] pts/trade, p 0.02.

## Layer C (real prices) and the constant-IV model for puts

Real-price trades: 16 of 645 (snapshots exist only from 2026-06-19). C total +11,833 vs modelled B on the same trades +18,289. Real minus modelled per trade: -403 overall, -670 on winners (n=9), -61 on losers (n=7).
Model: IV solved from the previous session's closing settle of the SAME put strike/expiry, held constant through the day; only time decay and spot move the price. On these 16 trades the model OVERSTATED put profits (mostly on winners). Theory (IV tends to rise when the index falls) would push the other way, so the overstatement likely comes from yesterday's IV being higher than the next day's traded IV, plus last-price vs model differences. Sample is small; treat modelled put P&L as optimistic.
