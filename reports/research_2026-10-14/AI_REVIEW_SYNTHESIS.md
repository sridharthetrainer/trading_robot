# Six external AI reviews — consolidated (2026-10-14)

Sources: Grok, DeepSeek, Perplexity, Gemini, Kimi, ChatGPT, each given the same
prompt (plan + measured results). Each review's claims were checked against
this repo's code and data where possible. External statistics I could not
verify are marked **unverified**; none of the conclusions below depend on them.

## 1. Where all six agree (and our data agrees)

| Point | Our own evidence |
|---|---|
| ₹30,000/month on ₹5L (6%/month ≈ 101%/yr compounded) is unrealistic | 125 intraday strategies ≈ 0R after costs; no option structure passes |
| A low-cost index fund is the honest baseline to beat | EWMAC Sharpe 0.30 vs buy-and-hold 0.58 (Test 1) |
| The live edge gate blocking negative strategies is correct — keep it | `live_signal_engine.py:3445-3462` |
| Weekly option selling is crash risk for a small premium | Condor PF 0.78, call spread 0.75, put spread 1.19 (≈ long beta); live condor 10/12 wins, −₹3,344 |
| Trend filter on put spreads does not create an edge | Test 4: filtered expectancy ≈ 0R, OOS PF 0.65 |
| 2% slippage per option leg is optimistic for OTM wings; stress at 5–10% | Not yet run; results already fail at 2% |
| EOD settlement ≠ executable price; option backtests are screens, not proof | `options_nifty.db` has close/settle/OI only, no bid/ask |
| Pre- vs post-Nov-2024 option data are different markets | Not yet split by regime |
| Survivorship bias would fake momentum returns | Confirmed: `external_data/nse_2015_2022/5minute.zip` lacks DHFL, JETAIRWAYS, RCOM, SINTEX, VIDEOIND, MINDTREE → unusable; `nse_cache.db` (bhavcopy) includes them → usable |
| RSI(2) pullback: low prior, likely dead after costs | Not tested; deprioritised |
| Drop / tightly limit the "unknown pattern" search | Agreed; not started |

## 2. What to test first — where they differ

| Review | First test |
|---|---|
| Grok, DeepSeek, Perplexity, Kimi | Cross-sectional momentum, point-in-time universe, monthly, long-only |
| Gemini | EWMAC on NIFTY futures (pass: OOS Sharpe ≥ 0.4, max DD ≤ 25%) |
| ChatGPT | NIFTY 10-month moving-average timing vs volatility-matched mix |

Gemini's and ChatGPT's tests are **already run** (Tests 1 and 6):
- EWMAC fails Gemini's own rule: OOS Sharpe 0.21, max DD ≈ 62% of ₹10L. By
  Gemini's stated rule this ends single-index directional technical trading.
- 10-month MA: same return as buy-and-hold (9.69% vs 9.82%) with half the
  drawdown (−14.9% vs −29.1%), but bootstrap lower bound −2.6%/yr → FAIL.
  Plausible risk overlay, not alpha, and not statistically established.

**Remaining consensus test: survivorship-free momentum** (4 of 6 reviews).

## 3. Practical points worth adopting

- **Lot-size leverage (ChatGPT, Kimi):** one NIFTY lot (65) ≈ ₹15L notional ≈
  3× a ₹5L account; vol-targeted futures can't be held fractionally. Our
  whole-lot ₹5L EWMAC variant shows this (max 4 lots, Sharpe 0.24). Any
  futures idea should be compared with an ETF implementation.
- **Option trade risk vs account size (ChatGPT):** a 300-pt spread at lot 65
  risks ≈ ₹14,000 ≈ 2.9% of ₹5L per trade — above a 2% risk limit.
- **Beta control (ChatGPT, Perplexity):** compare any put-selling result with
  an equal-risk long NIFTY position under the same filter before calling it an
  options edge.
- **VRP look-ahead (ChatGPT):** the signal must use a forecast of realised
  vol, never the realised vol that follows; compare variances, matched horizons.
- **Momentum crashes (all):** expect deep drawdowns; report them explicitly.
- **DeepSeek's shortcut:** the Nifty 200 Momentum 30 index fund delivers the
  factor without building or running a stock strategy. Our momentum test
  should be judged against it, not only against Nifty 200.
- **Milestone, not income target (all):** replace ₹30,000/month with: one
  pre-registered strategy that beats its passive benchmark after full costs,
  out of sample, with ≥100 independent trades/periods and tolerable drawdown;
  then forward paper tracking before any capital.

## 4. Claims checked against this repo

| Claim | Status |
|---|---|
| NIFTY lot 75 → 65 (Kimi, ChatGPT) | ✅ `MasterContract_NFO.csv` shows 65 |
| Option STT 0.15% from 2026-04-01 (ChatGPT) | ✅ `nse_cost_model.py:110-118` (0.0625% → 0.10% Oct-2024 → 0.15% Apr-2026) |
| Gemini: option STT "raised to 0.1%" | ⚠️ outdated — current rate is 0.15% |
| NSE expiry moved to Tuesday (ChatGPT) | ✅ `expiry_regime.py` |
| Our option backtests use a constant 75 qty (Kimi, ChatGPT) | ✅ true (`condor_backtest_real._LOT = 75`); lots were 25 pre-Nov-2024, so per-unit brokerage was understated then |
| Option backtests include full statutory costs | ❌ they include slippage + brokerage only — no STT/exchange fees. Real results are worse; verdicts unchanged |
| "72%/yr" (DeepSeek, Gemini) | ⚠️ that is 6%×12 simple; compounded it is ≈ 101%/yr (Perplexity, ChatGPT) |
| Kimi: 2005–2014 in-sample for momentum | ⚠️ not possible here — survivorship-free stock data starts 2016-07 |
| 87.7% of individual F&O traders lost money in FY2025-26 (ChatGPT, DeepSeek) | **unverified** (two reviews agree) |
| Nifty200 Momentum 30: 18.30% vs 13.46%, "98.8% odds" (Kimi, DeepSeek) | **unverified**; partly back-calculated index history (ChatGPT's caution) |
| Smallcap survivorship bias 4.94 pp/yr (DeepSeek) | **unverified** |
| SPIVA India: 76.3% of large-cap funds underperform over 10 yrs (ChatGPT) | **unverified** |

## 5. Actions

Done today:
- Tests 1, 4, 6 pre-registered and run (all FAIL) — `PREREGISTRATION.md`, `results*.json`.
- Bhavcopy volume bug fixed (`7d1e1db3`); NSE `prev_close` captured for
  split/bonus adjustment (`e31171f3`); full re-download in progress.

Next, in order:
1. Finish the bhavcopy repair; build split-adjusted prices from
   `prev_close(t)/close(t-1)`; spot-check against known splits.
2. Point-in-time universe: each month, top 200 stocks by trailing 6-month
   median traded value, using only data available then.
3. Pre-register and run momentum: 12-1 month return, monthly rebalance,
   top 20–30 equal-weight, long-only, full delivery costs + 0.3–0.5%
   round-trip impact, cost-doubling stress, locked final 30% holdout.
   Benchmarks: equal-weight universe, NIFTY buy-and-hold, and the Momentum 30
   index if its history can be sourced.
4. If momentum fails: stop low-frequency strategy search; index fund is the
   capital baseline; keep the system in PAPER as research only.
5. Separate, lower priority: re-run option backtests with full statutory
   costs, historical lot sizes, 5%/10% slippage, and pre/post-Nov-2024 split —
   to document the negative result properly, not to rescue it.
6. Live system (unrelated to research): Angel-fallback option chains lack an
   exchange timestamp (now rejected by `option_quality.py`); option-chain
   fetch failures; FINNIFTY scalp selected with premium 0.0.
