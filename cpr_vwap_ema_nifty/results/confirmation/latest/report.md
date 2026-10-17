# Confirmation-candle test (2026-10-17T08:51:09)

Tests 41-42 (ledger total 42, Bonferroni p < 0.00119). Applied every day. R001 frozen and R002 proposed are unchanged; no R003 is created. Everything is EXPLORATORY unless the day-level p clears the threshold.
Rule: after the signal candle closes, the NEXT candle must also close on the right side of futures VWAP and the 1m 200 EMA; entry at the open of the candle after that; otherwise no trade. Exits unchanged.

## R001_long (exit REF20; in-sample ends 2024-06-30) — filter removed 69 of 615 signal days (11.2%)

| Version | Trades | Win% | Avg W / L | PF | Exp | Max DD | Hold | B | C (coverage) | IS PF / B / p | OOS PF / B / p | Day p (CI) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| unconfirmed | 615 | 46.3 | 56.88 / -34.57 | 1.42 | 7.81 | -565.45 | 120.0m | +141,963 | +5,050 (10/615) | 1.41 / +71,858 / 0.0086 | 1.44 / +70,105 / 0.0414 | 0.0012 ([2.5, 13.33]) |
| confirmed | 546 | 44.3 | 55.04 / -31.39 | 1.4 | 6.92 | -659.9 | 115.9m | +100,238 | +4,179 (9/546) | 1.41 / +49,373 / 0.0148 | 1.37 / +50,865 / 0.073 | 0.0044 ([1.77, 12.36]) |

By year (trades, PF, B): unconfirmed 2020: 67, 0.58, -27,480; 2021: 111, 1.21, +1,054; 2022: 98, 1.72, +42,607; 2023: 103, 1.74, +26,774; 2024: 89, 1.38, +34,186; 2025: 89, 1.85, +72,136; 2026: 58, 1.07, -7,314

By year (trades, PF, B): confirmed 2020: 61, 0.46, -28,800; 2021: 101, 1.11, -5,809; 2022: 79, 1.47, +22,196; 2023: 93, 2.16, +26,830; 2024: 77, 1.54, +32,717; 2025: 83, 1.78, +55,825; 2026: 52, 1.13, -2,722

## R002_short (exit E2; in-sample ends 2023-12-31) — filter removed 92 of 645 signal days (14.3%)

| Version | Trades | Win% | Avg W / L | PF | Exp | Max DD | Hold | B | C (coverage) | IS PF / B / p | OOS PF / B / p | Day p (CI) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| unconfirmed | 645 | 40.3 | 101.58 / -49.19 | 1.39 | 11.59 | -563.5 | 152.1m | +401,611 | +11,833 (16/645) | 1.27 / +141,009 / 0.0686 | 1.51 / +260,602 / 0.0052 | 0.0014 ([3.92, 19.56]) |
| confirmed | 553 | 40.5 | 97.16 / -46.92 | 1.41 | 11.44 | -665.65 | 150.9m | +320,772 | -6,234 (15/553) | 1.3 / +112,841 / 0.0574 | 1.51 / +207,931 / 0.009 | 0.0028 ([3.54, 19.79]) |

By year (trades, PF, B): unconfirmed 2020: 44, 1.23, +20,716; 2021: 99, 1.35, +49,057; 2022: 108, 1.12, +35,469; 2023: 94, 1.46, +35,767; 2024: 100, 1.45, +96,113; 2025: 107, 1.14, +25,250; 2026: 93, 1.99, +139,239

By year (trades, PF, B): confirmed 2020: 34, 1.43, +18,348; 2021: 85, 1.25, +25,724; 2022: 89, 1.18, +33,760; 2023: 87, 1.52, +35,009; 2024: 89, 1.54, +88,357; 2025: 89, 1.15, +18,597; 2026: 80, 1.85, +100,977

