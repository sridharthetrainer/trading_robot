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

## Storage rule (rules.json)

`rules.json` stores ONLY rule fields plus dates: `date` is the trade date the
item describes (so that session can be revisited and backtested later);
`added` / `proposed_on` / `approved_on` record when it was logged. No prices,
candles, indicator values, levels-crossed lists, trade logs or backtest
results. Everything else is pulled from the date at run time; outputs go only
to `results/` and `results/history/`. `rules.validate()` enforces this with a
field whitelist and rejects numbers other than `exit.period`.

Sections: `conditions` (observations, e.g. C001), `preregistered` (frozen
forward rules, e.g. R001, fingerprinted), `hypotheses` (observation only, not
tested, not counted, e.g. H001).

## Test count

`test_ledger.json` lists every distinct test looked at on the backtest dataset
(variant, sensitivity row, field test) with the date first run. Every
backtest report and forward report prints the total. Hypotheses and forward
paper trades do not add to it.

## Adding a condition

Write a JSON object with the same fields as C001 in `rules.json` (new `id`,
`date` = the trade date, `instrument`, `timeframe`, `bias`, `setup`,
`context`, `vwap_behavior`, `entry`, `exit`, `outcome_class` = win / loss /
flat / unknown, optional `optional_filters`), then run `add-condition`. It:

1. validates field values (allowed values are listed in `rules.py`),
2. checks duplicates: same timeframe + CPR relations + CPR width + open
   location + MA context + VWAP behaviour **and** same outcome class ->
   skipped and reported; same everything but a different outcome class ->
   added with `conflict_with` set and reported as a CONFLICT,
3. re-runs the full backtest and writes "Change vs previous run" into
   `results/latest/summary.md` (previous run archived in `results/history/`).

## Data notes

* Levels, EMAs and the open come from NIFTY spot 1m (Angel token 99926000).
* VWAP comes from futures 5m with volume (Angel token 48704). That token
  returns a near-month-like rolled series back to 2020-07 (basis -40..+190
  pts); Angel does not document this. Its contract expires 2026-10-27; the
  history is cached locally. After that date `forward-report` fetches each
  day from the near-month contract listed in `MasterContract_NFO.csv`
  (`data.fetch_futures_near_month`); the bulk `fetch` command still uses the
  configured token.
* Days without usable futures volume are skipped in the `futures` set and
  included with a spot TWAP (labelled) in the `with_missing` set.
* `options_nifty.db` has no underlying price for 2020-2023; layer B uses this
  package's own previous-session spot close there.

## Pre-registered rules and forward paper evaluation

`rules.json` -> `"preregistered"` holds frozen rules (R001 = R1/PDH breakout).
A rule stays `"status": "proposed"` until the user approves its forward bar;
approval = set `"status": "approved"` and `"approved_on"`, then `frozen` and a
`fingerprint` of definition + engine_mapping + forward_bar. Counting starts the
first session AFTER `approved_on`. If the frozen parts change, `forward-report`
returns `frozen_rule_modified` and counts nothing; a changed rule needs a new
id with a fresh forward window. R001 was approved 2026-10-16 (counting from
2026-10-19). The bar's simulation rationale is in
`results/preregistration/R001_bar_rationale.json`.

The forward report includes a snapshot-coverage line (sessions with full /
partial / no option-chain snapshots and the share of trades priced from real
snapshots). It turns `AT RISK` when either falls within 5 points of the 80%
requirement, before the requirement itself fails.

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

## Priority focus and pre-declared tests (hypothesis_tests.py)

Primary levels: futures VWAP, 1m 200 EMA, 5m 200 EMA, daily CPR, the PDH/R1
zone and the PDL/S1 zone (zones = the band between the two levels). Each
backtest re-verifies C001 (duplicate check, field-by-field from data incl.
`golden_pivot_zone`, mechanical reproduction), reports the base rule split by
position at the signal bar for each primary level (descriptive), the C001
exact-match and one-field-at-a-time counts, and the pre-declared tests declared
in `config.json` -> `"predeclared_tests"` (H-A golden pivot zone, H-B Camarilla
sideways zones, H-C final 1m close vs 1m 200 EMA -> next-session gap, H-D short mirror of R001 with ATM
puts; trades in `results/latest/h_d_short_trades.csv`). Pre-declared tests are
added to `test_ledger.json`; the priority-focus splits are recorded as
descriptive looks. None of this touches R001's frozen definition.
