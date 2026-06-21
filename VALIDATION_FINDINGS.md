# VALIDATION_FINDINGS.md — Edge-Search Results (as of 2026-06-14)

**TL;DR:** Across five independent investigations, **no validated autonomous price-based
edge has been found** in this system on NSE data. Every backtestable strategy fails a
rigorous out-of-sample test once costs and multiple-testing are accounted for. This file
records what was tested and how, so these dead ends are not re-explored. Keep the system
in **PAPER** mode (CLAUDE.md rule 5).

This is not a defect of the engineering — the risk layer, ML pipeline, and validation
harness are well-built. It reflects that liquid NSE index/equity markets are efficient
and durable edges are rare. The missing piece is *predictive information*, which more
code/algorithms/parameters cannot manufacture.

---

## The bar (what "validated" means)

A strategy is only trustworthy if it clears `validation_harness.py`, which requires **all**:

1. **Deflated Sharpe ≥ 0.95** — survives the multiple-testing correction (Bailey & López
   de Prado) for the number of parameter combinations tried. This is the gate that kills
   most "great backtests": a high Sharpe found by trying many combos is usually luck.
2. **≥ 30 trades / window** (intraday) — enough sample for the Sharpe to mean anything.
3. **Parameter stability CV < 0.5** — the same params keep winning across walk-forward windows.
4. **Locked holdout positive** — final 20% of data, never touched in development, P&L and
   Sharpe both > 0.

Costs are modeled inside each `backtest_*` function (recalibrated 2026-06-14 to real NSE
NIFTY futures rates: slippage 0.01%, STT 0.0125% — note these are a FUTURES proxy in index
points; the live bot trades OPTIONS, where theta + spreads make results worse).

---

## 1. Intraday price-indicator strategies — 0 / 10 PASS

Rigorous harness, NIFTY 5-min, 420 days (~21,000 bars), corrected costs:

| Strategy | Dev Sharpe | Verdict | Note |
|---|---|---|---|
| breakout | +0.40 | FAIL | DSR 0.00 — best-of-360 trials = luck |
| trend | −1.01 | FAIL | |
| mean_reversion | −0.48 | FAIL | |
| ma_cross | −1.50 | FAIL | |
| scalping | −65.84 | FAIL | cost-suicidal (258 trades/win) |
| ema_5min | −19.10 | FAIL | |
| cpr | −6.32 | FAIL | |
| supertrend_mtf | −3.02 | FAIL | only 25 trades/win |
| vwap_reversion | 0 (no trades) | FAIL | grid never triggers |
| orb | — | INSUFFICIENT_DATA | once/day signal, too few windows |

**Parameters are not the problem.** The grid search already tested the textbook region
(RSI 14, ADX 25, Bollinger 2σ, Donchian 20) and its best stable picks still give DSR ≈ 0.

## 2. Daily timeframe — 0 / 5 PASS (but informative)

Same harness, 1,764 daily NIFTY bars (~7y), trend/momentum families. The longer timeframe
**flipped every strategy from negative to mildly positive** (intraday noise + costs were a
real drag), but none validated:

| Strategy | Dev Sharpe | Verdict |
|---|---|---|
| trend | +0.24 | FAIL (DSR 0) |
| breakout | +0.41 | FAIL (unstable, CV 0.70) |
| ma_cross | +0.22 | FAIL |
| mean_reversion | +0.07 | FAIL (≈0 trades) |
| supertrend_mtf | +0.65 (corrected) | FAIL |

⚠️ supertrend initially appeared to PASS (Sharpe +5.65, DSR 1.00). **This was a bug**, not
an edge: `backtest_supertrend_mtf.py` hardcoded the 5-minute annualization factor
`√(252×75)`, over-annualizing daily Sharpe by √75 ≈ 8.66×. Fixed (interval-aware). Corrected
Sharpe ≈ 0.65 → FAIL. *Lesson: implausibly high Sharpe + few trades = suspect the units.*

## 3. Existing ML layer — well-built but INERT

`ml_trainer.py` / `post_market_ml.py` / `ml_feature_builder.py` are leakage-free
(TimeSeriesSplit, scaler-in-pipeline, depth/feature caps) and fail safe (ML probability
applied only if CV AUC ≥ 0.62; learned filters clamped to [0.80, 1.50]; danger zones need
≥15 samples). **But the model scores CV AUC = 0.568** on 2,351 labelled outcomes — below
its own 0.62 gate, so **it is not used in live trading.** It cannot help: it is trained on
the outcomes of base signals that have no edge.

## 4. Feature predictiveness — nothing predicts

Univariate ROC-AUC of each logged feature vs. trade outcome (2,351 samples, multiple-testing
bar ≈ 0.538):
- No feature carries tradeable signal. The only 3 crossing the bar are **temporal artifacts**
  (`hour_of_day`, `__log_time` — the win-rate drifts over time = non-stationary data).
- **The system's own confluence `score` has AUC ≈ 0.50 vs. outcomes** — the trade-selection
  layer is uncorrelated with whether its trades win. This is the root cause under all the
  strategy failures, and it kills meta-labeling (no feature for a meta-model to filter on).

## 5. Cross-sectional equity momentum — no alpha

59 NSE large-caps, monthly rebalance, 54 months (2022–2026), ~0.4% round-trip cost, locked
holdout. Market-neutral long-short (pure momentum alpha) is **negative/zero** at every
lookback (3m −16.5%, 6m −10.2%, 12m +1.2%). Long-only top quintile looks positive but is
**bull-market beta** that turned negative in the holdout (−5.8%). *Caveat: 4.5y is short and
2022–26 was momentum-unfriendly in India — not proof momentum never works, but no exploitable
edge in available data.*

---

## Standing rule

Any future signal idea — new data source, new model, new market — **must clear
`validation_harness.py` (locked holdout + deflated Sharpe) before it touches capital.** Do
not promote a strategy on an in-sample or weak-harness (`walk_forward_backtest.py`) result;
those are data-snooping artifacts. Edge, if it exists, will come from *new predictive
information* (order flow, options-surface dynamics, events, cross-asset) — not from more
parameters, more algorithms, or more compute on the same price-derived features.

## Reproduce

```bash
# rigorous, single strategy (intraday 5m)
WF_TOTAL_DAYS=420 python validation_harness.py --strategy breakout --days 420
# weak harness (DATA-SNOOPING — do not trust for promotion)
WF_TOTAL_DAYS=420 python walk_forward_backtest.py
```
Results saved to `validation_results.json` (rigorous) and `walk_forward_results.json` (weak).
