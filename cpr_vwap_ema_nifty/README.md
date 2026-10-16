# CPR_VWAP_EMA_Nifty (research-only)

Self-contained strategy package. Not imported by `signal_engine.py`,
`live_signal_engine.py` or `main_autonomous.py` (a test enforces this). Uses
existing code only through read-only calls: `angel.AngelOne` (downloads),
`request_governor.acquire` (shared Angel pacing), `nse_cost_model.NseCostModel`
(costs), `option_intraday_pricer.DayPricer` (modelled option prices), and
read-only sqlite opens of `external_backtest_data.db`, `candle_cache.db`,
`options_nifty.db`, `option_chain_snapshots.db`.

## Files

| File | Purpose |
|---|---|
| `config.json` | All parameters: live flag, window, OOS split, CPR width cut-offs, session times, option sizing/slippage, Angel tokens |
| `rules.json` | Conditions as data (C001 ...) |
| `levels.py` | CPR, Traditional pivots, Camarilla, relations, open location (prior periods only) |
| `data.py` | Own cache `data_cache.db` (spot 1m, futures 5m with volume) + Angel download |
| `engine.py` | Indicator frame (VWAP, EMAs 5m/1m/30m), setup matching, trade simulation |
| `options_layers.py` | Layer B (modelled ATM option) and layer C (real snapshot prices) |
| `rules.py` | Validation, duplicate / conflict check, add condition |
| `backtest.py` | Runner, metrics, report, comparison with previous run |
| `__main__.py` | CLI |
| `logs/` | `fetch.log`, `backtest.log` |
| `results/latest/` | `trades.csv`, `summary.json`, `summary.md`, `equity.png` |
| `results/history/<run>/` | previous runs (archived automatically) |

## Enable / disable

`config.json`: `"enabled_live": false`, `"research_only": true` (defaults).
`is_enabled_live()` is true only when `enabled_live` is true AND
`research_only` is false. Even then nothing trades: the live engine does not
call this package until it is explicitly registered in `signal_engine.py`
(deliberately not done).

## Commands

```
.venv/bin/python -m cpr_vwap_ema_nifty status
.venv/bin/python -m cpr_vwap_ema_nifty fetch            # refresh own data cache
.venv/bin/python -m cpr_vwap_ema_nifty backtest
.venv/bin/python -m cpr_vwap_ema_nifty check-condition new.json
.venv/bin/python -m cpr_vwap_ema_nifty add-condition new.json
```

## Adding a condition

Write a JSON object with the same fields as C001 in `rules.json` (new `id`,
`date`, `setup`, `context`, `vwap_behavior`, `entry`, `exit`,
`observations`, `outcome`, optional `outcome_class`), then run
`add-condition`. It:

1. validates field values (allowed values are listed in `rules.py`),
2. checks duplicates: same timeframe + CPR relations + CPR width + open
   location + MA context + VWAP behaviour **and** same outcome -> skipped and
   reported; same everything but a different outcome -> added with
   `conflict_with` set and reported as a CONFLICT,
3. re-runs the full backtest and writes "Change vs previous run" into
   `results/latest/summary.md` (previous run archived in `results/history/`).

## Data notes

* Levels, EMAs and the open come from NIFTY spot 1m (Angel token 99926000).
* VWAP comes from futures 5m with volume (Angel token 48704). That token
  returns a near-month-like rolled series back to 2020-07 (basis -40..+190
  pts); Angel does not document this. Its contract expires 2026-10-27; the
  history is cached locally. A later `fetch` past that date needs the new
  near-month token in `config.json`.
* Days without usable futures volume are skipped in the `futures` set and
  included with a spot TWAP (labelled) in the `with_missing` set.
* `options_nifty.db` has no underlying price for 2020-2023; layer B uses this
  package's own previous-session spot close there.

## Pre-registered rules and forward paper evaluation

`rules.json` -> `"preregistered"` holds frozen rules (R001 = R1/PDH breakout).
A rule stays `"status": "proposed"` until the user approves its forward bar;
approval = set `"status": "approved"` and `"approved_on"`. Counting starts the
first session AFTER `approved_on`. Any later change to the definition, costs
or bar needs a new rule id with a fresh forward window.

```
.venv/bin/python -m cpr_vwap_ema_nifty forward-report R001
```

Paper only: it re-runs the frozen rule on new sessions, prices the CE from
real option-chain snapshots (layer C; needs the bot's snapshot recorder
running) and the model (layer B), applies the bar and writes
`results/forward/R001_trades.csv`, `R001_status.json`, `R001_status_log.jsonl`.
Until approval it returns `not_approved` and counts nothing.

## Field tests

`config.json` -> `"field_tests"`: one-at-a-time filters (values from the
condition's setup) applied to a base variant; reported under "Field tests" in
`summary.md`. Combinations are deliberately not generated.
