# ARCHITECTURE.md

Live NSE algorithmic trading system — canonical engine chain and service layout.

> All file:line references are verified against current source code.

---

## Entry point

```
main_autonomous.py  →  class AutonomousTradingSystem (line 456)
```

Run via systemd:
```
trading-bot.service  →  ExecStart: venv/bin/python3 main_autonomous.py
```

---

## Signal flow

```
AutonomousTradingSystem.run()
  └─ self.live_engine  (LiveSignalEngine, live_signal_engine.py:460)
       └─ per-symbol scan loop
            └─ generate_signal()  (signal_engine.py)
                 └─ STRATEGIES list (signal_engine.py:1757)
                      48 entries: 22 core (always loaded) + conditional
                      confluence scoring → final signal dict
```

`LiveSignalEngine` is the **orchestrator**: fetches data, filters symbols,
calls `generate_signal`, applies confluence thresholds, manages the trade
lifecycle, and records `_rejection_stats` (total/passed/reasons per scan,
`live_signal_engine.py:1349`).

`signal_engine.generate_signal` is the **strategy registry**: holds all
~48 strategy functions and the confluence scorer. Both files are required;
neither is a duplicate.

---

## Confluence thresholds (env-configurable)

| Variable                 | Purpose                              |
|--------------------------|--------------------------------------|
| `MIN_CONFLUENCE_SCORE`   | Minimum score to enter a position    |
| `POST_CONFLUENCE_MIN_SCORE` | Score required after re-check     |
| `SWING_MIN_SCORE`        | Score threshold for swing trades     |
| `AI_MIN_SCORE_THRESHOLD` | Score threshold for AI-augmented sig |

---

## Data sources (priority order)

1. **Angel One `getCandleData`** — PRIMARY for intraday candles  
   `angel.py:get_historical_data` → token via `INDEX_TOKEN_MAP` (line 49) + `get_token`
2. **NSE direct APIs** — option chain, indices, FII-DII data
3. **Bhavcopy** (`bhavcopy_cache.py`) — EOD history
4. **SmartConnect direct** (`data_fetcher.py:_fetch_via_smartconnect`) — fallback intraday
5. **yfinance** (`data_fetcher.py:644`) — last resort; gate with `DISABLE_YFINANCE=true`

---

## Risk layer

| File                        | Role                                      |
|-----------------------------|-------------------------------------------|
| `value_at_risk.py`          | `ValueAtRisk` — historical VaR per symbol |
| `cvar_optimizer.py`         | CVaR portfolio optimisation               |
| `daily_loss_limit.py`       | Intraday loss circuit-breaker             |
| `gap_risk_manager.py`       | Overnight gap protection                  |
| `portfolio_risk.py`         | Aggregate portfolio exposure limits       |
| `kill_switch.py`            | `KillSwitch` — hard halt on breach        |
| `adaptive_position_sizer.py`| Kelly/ATR-based position sizing           |

---

## Capital

Fetched **live** from Angel One `rmsLimit()` at startup via
`main_autonomous.py:_fetch_startup_balance` (line 3126).

- Paper mode → returns `PAPER_CAPITAL` from `.env`
- Live mode → `broker.get_balance()` from Angel; if that fails → **halts startup**
  (returns 0.0 and raises `RuntimeError`; does NOT fall back to `REAL_CAPITAL`)
- `REAL_CAPITAL` in `.env` is no longer used as a silent fallback

---

## Persistence

SQLite database `trades.db` — managed by `TradeDatabase`
(`main_autonomous.py:347`).

Key tables:

| Table                  | Content                               |
|------------------------|---------------------------------------|
| `trades`               | All system-generated trades           |
| `manual_trades`        | Trades flagged by manual-tracker      |
| `manual_trade_updates` | Status updates on manual trades       |
| `strategy_scores`      | Per-strategy signal outcomes          |
| `eod_ml_feedback`      | End-of-day ML feedback records        |

---

## Manual trade protection

```
manual_trade_tracker.py  ←→  manual-tracker.service
```

Runs as a separate systemd service. Detects manual trades placed outside the
bot, logs them to `manual_trades` table, and prevents the bot from interfering
with open manual positions.

---

## Systemd services

| Service file                    | What it runs               |
|---------------------------------|----------------------------|
| `trading-bot.service`           | `main_autonomous.py`       |
| `manual-tracker.service`        | `manual_trade_tracker.py`  |
| `trading-bot-watchdog.service`  | `watchdog.py`              |
| `auto-deploy.service`           | Auto-deploy/update helper  |

---

## Score calibration

`score_calibrator.py` — `ScoreCalibrator` class (line 34).  
Reads/writes `score_calibration.json`. Adjusts per-strategy confluence weights
based on historical outcome data from `strategy_scores` table.

**Guard (pending):** weight updates are blocked until a strategy has ≥ 30
recorded outcomes (see `score_calibrator.py` improvement, priority #6).

---

## Diagnostics

| Script               | Purpose                                    |
|----------------------|--------------------------------------------|
| `diag.py`            | General system diagnostics                 |
| `diag_scan.py`       | Scan diagnostics (symbols, signals)        |
| `validate_env.py`    | Check `.env` completeness                  |
| `check_connections.py` | Broker + data source connectivity test   |

---

## Validation pipeline

| Script                    | Purpose                                                 |
|---------------------------|---------------------------------------------------------|
| `walk_forward_backtest.py`| Rolling OOS walk-forward; saves `walk_forward_results.json` + equity curve |
| `validation_harness.py`   | Deflated Sharpe + holdout lock + stability + min-trade  |
| `backtest_*_grid.py`      | Grid search → `best_params_*.json`                      |

---

## Known gaps (as of 2026-06-04)

1. **No validated out-of-sample backtest** — edge is unvalidated until
   `walk_forward_backtest.py` + `validation_harness.py` are run and pass.
2. `yfinance` is broken (Yahoo API dead) — gate with `DISABLE_YFINANCE=true`.

---

## Addendum — 2026-06-12 session changes (verified live)

### Learning loop (now functional end-to-end)
```
LiveSignalEngine logs EVERY candidate → signal_log.db (50+ features/row)
  └─ executed flag → SignalLogger.mark_executed (was never implemented before)
16:45 idle_engine: triple-barrier labeller → tb_label +1/0/-1
        (-2 = junk row retired; -99 = pending). Labeller now has an Angel
        client (was data-less), uses the row's own signal_date (was hardcoded
        to yesterday), fills outcome_price/outcome_time.
16:05 daily: EOD ML analysis → eod_ml_feedback (was Saturday-only, never ran)
20:30 idle_engine: signal_calibrator — QUALITY GUARD: refuses models with
        val Brier > 0.30 or < 3 distinct label days
20:45 idle_engine: eod_weight_engine — HARD_MIN_SAMPLES=30 (neutral below)
21:15 idle_engine: edge_report → edge_report.json + Telegram summary
```

### Critical data-layer fix
`angel.get_token` / `_get_token_no_lock`: NSE cash map is now consulted BEFORE
the NFO master. Previously every NSE stock resolved to a derivative token
(e.g. TATAPOWER→143493) → getCandleData returned SUCCESS with 0 candles →
`get_historical_data` was None for ALL stocks; the system silently lived on
DataFetcher fallbacks (which force DAILY bars after market hours).

### Option chain restored
`option_chain_fetcher.fetch()` chain is now resilience → Sensibull → direct
NSE → **Angel SmartAPI fallback** (`angel_option_chain.py`: spot fetch
re-enabled via broker.get_ltp; OI extracted via getMarketData FULL +
`opnInterest` key) → disk cache. Verified live: 21 strikes, real OI, PCR.

### Registry / validation state
- `run_scalping_strategy` gated OUT by default (DISABLE_SCALPING_STRATEGY):
  walk-forward 2026-06-12 = 0/7 windows profitable, avg −₹377k/window.
- ALL 5 backtestable strategies FAIL OOS (walk_forward_results.json,
  validation_results.json). System stays PAPER.
- `strategy="fallback"` signals = signal_engine's generic-score path (the
  legacy StrategyScanner is config-off), not an error.
- Stub services autonomous-bot / system-bot / trades-bot / trade_guardian
  DISABLED (print-and-exit placeholders that crash-looped ~6.4k times each).

### New standalone tools
`advisory_engine/` (advisory text → validated paper signal), `nifty_scalper_bot.py`
(signal-only Telegram bot), `edge_report.py` (measured-edge analytics).

---

## Addendum — 2026-06-14 session (edge status: MEASURED, not just unvalidated)

The "Known gaps" item #1 above is now resolved in the negative. The harnesses were run
at corrected NSE costs; **see `VALIDATION_FINDINGS.md` for the full record.** Summary:

- **0 validated edges** across 10 intraday + 5 daily price-indicator families, the
  existing ML layer, univariate feature predictiveness, and cross-sectional equity
  momentum. The edge is no longer "unvalidated" — it is measured as **absent** on
  available NSE data. System stays PAPER.
- The confluence `score` was measured to have **AUC ≈ 0.50 vs. trade outcomes** — the
  selection layer is not yet predictive (root cause under the strategy failures).
- The existing ML model scores **CV AUC ≈ 0.568**, below its own 0.62 usage gate → it is
  **not used live** (fails safe), and cannot help while base signals have no edge.

### Fixes this session
- `backtest_supertrend_mtf.py` Sharpe annualization made **interval-aware** (was hardcoded
  `√(252×75)`, over-annualizing DAILY Sharpe ~8.7×; a daily "PASS" was this bug, not an edge).
- `learned_filters.py` stale comment aligned to the real `cv_auc ≥ 0.62` gate.
- Cost recalibration, SEBI algo-order audit tag, `DISABLE_YFINANCE` gate, `vol_surface`
  full-surface expansion (committed earlier this session).

### Standing rule
Any future signal idea must clear `validation_harness.py` (locked holdout + deflated
Sharpe) before live use. `walk_forward_backtest.py` is the WEAK harness (full-sample params
OOS = data-snooping) — do not promote on its results.
