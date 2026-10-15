# Pivot Boss catalog review — 15 October 2026

Added the pasted material as a separate source-linked research catalog, compared it with existing implementations, and fixed concrete session/touch-state bugs. No orders, live enablement, historical strategy searches or service restarts occurred.

## Inventory and integration

- `quant_specs/pivot_boss_source_request_2026-10-15.txt` preserves the supplied text with a recorded SHA-256 fingerprint.
- `pivot_boss_research_catalog.json` records 25 research concepts across the five supplied sections: candles, CPR, Camarilla, Money Zone/profiles, and confluence. These include formulas, features and strategy hypotheses; the count is not 25 independent complete strategies.
- `pivot_boss_research.py` validates the supplemental catalog and provides causal research features. `quant_model_catalog.build_integration_plan()` points to the supplement while retaining the original 28-group inventory and its counts.
- Each entry records an existing helper or catalog-only status and the missing execution/risk rules. All entries have `live_approved=false`, `backtest_registered=false` and null calibrated win probability.

```bash
.venv/bin/python pivot_boss_research.py --output reports/pivot_boss_catalog_2026-10-15/inventory.json
.venv/bin/python autonomous_param_trainer.py --quant-inventory
```

The full supplied summary is retained as user-provided material, not certified as an exhaustive reproduction of the book. The [author-hosted book](https://www.pivotboss.com/wp-content/uploads/2017/09/Secrets-of-a-Pivot-Boss-by-Frank-Ochoa.pdf) was located and its contents structure inspected; follow-up access to the detailed text timed out. Exact numerical claims were therefore not authenticated from the book in this review. No PDF copy was added to the repository.

## Corrections to apply when interpreting the paste

1. **CPR bounds:** raw TC can lie below raw BC. Use their minimum and maximum for zones and width. Existing `calc_cpr` and `indicators.calculate_cpr` already normalize these correctly.
2. **Outside reversal:** literal `Low < Low` and `High > High` omit time indices. The new research feature explicitly compares current-bar low/close with prior-bar extremes. This is a documented interpretation, not an unqualified claim of exact source equivalence.
3. **Profile terminology:** time-based POC counts TPOs; volume-based POC aggregates executed volume by price. They can differ. The acronym VPOC is ambiguous in the paste, so profile kind and virgin state are represented separately.
4. **Virgin POC:** formation-session activity produces the POC. Virgin status concerns later sessions that have not retested it. [GoCharting's primary documentation](https://gocharting.com/docs/charting/technical-indicator/orderflow/market-profile-aka-tpo-charts) describes naked/virgin POC in terms of subsequent profiles. Missing later history is UNKNOWN, not proof that the level is untouched.
5. **Probabilities:** the reported 63% and 80% touch claims remain attributed, unvalidated claims. They are not trade win rates, net expectancy estimates, or probabilities of earning ₹30,000 per month. No value from those claims enters `/probability`.
6. **Definitions still needed:** narrow/wide thresholds, equality/proximity tolerances, trend rules, pivot-context filters, sizing, stops, target selection, execution costs and options contract selection remain explicit specification gaps where absent.

## Runnable research features

`pivot_boss_research.action_triggers()` emits four candle-feature families at their observation/confirmation close:

- Wick: supplied 2.5–3.5 wick/body ratio and close-location conditions, confirmed by the following close beyond the anchor extreme.
- Extreme: supplied range/body bounds, opposing next-bar color, and prior-only average range. The 20-bar default lookback is a project choice.
- Outside: explicitly indexed prior-bar interpretation with the supplied average-range filter.
- Doji: supplied body/range and SMA(10) conditions; the first qualifying close in the next two bars confirms, without retroactively labeling an earlier executable entry.

The caller must provide timezone-aware close-stamped candles and an aware `as_of`. Future bars are excluded before computing features. Invalid geometry, duplicate/unordered timestamps and absent timestamp semantics are rejected. Zero-range/doji bars do not cause division-by-zero entries. These features have no automatic position sizing, order submission, pivot-zone filter or validated performance claim.

`cpr_relationship()` gives seven disjoint normalized states. Exact equality is the default; the caller may provide a nonnegative price tolerance. Boundary contact is overlap; inside/outside containment with one equal boundary is classified deterministically. The tolerance policy must be fixed before historical evaluation.

`profile_from_price_counts()` requires an explicit `VOLUME_AT_PRICE` or `TIME_PRICE_OPPORTUNITIES` kind and tick-aligned observed counts. TPO counts must be integers. POC ties choose the lower price, and contiguous value-area expansion chooses the greater adjacent count, breaking equal-count ties upward. Those tie policies are declared project choices. The function does not invent volume-at-price from ordinary OHLCV bars.

`virgin_level_status()` ignores the origin session, uses subsequent completed observations only, and returns UNKNOWN unless a touch is observed or the caller explicitly attests complete subsequent coverage. The attestation is not independently verified by this function. No fill probability is assigned.

## Existing-code fixes

- **`is_virgin_cpr`:** fixed invalid Boolean conversion of a pandas Series and conversion of timestamp index labels to integers. Touch age now uses row positions and the latest observed touch, working with timestamp and noncontiguous integer indices. Missing/malformed observations return UNKNOWN. Its scope is explicitly the supplied CPR observation window, distinct from prior-session POC virginity.
- **Prior-period pivots:** removed fallback to the current partial period when prior history is missing. Weekly/monthly/yearly levels require the immediately preceding calendar period. Future daily-source rows are filtered against the intraday observation boundary. Missing periods produce empty packs. These checks establish temporal boundaries, not proof that every historical session/bar within a prior period was collected.
- **Session slicing:** PDH/PDL and Pivot Boss previous/current-session bars now use Indian exchange dates rather than fixed 78-bar slices. Weekly/monthly packs use calendar periods rather than rolling approximations.
- **OHLCV profile:** the existing uniformly allocated profile is labeled `OHLCV_UNIFORM_VOLUME_PROXY`, with `observed_volume_at_price=false`. Flat positive-volume bars now contribute their volume instead of silently disappearing. Nonfinite/invalid profile inputs are rejected. No approximate profile is relabeled as an actual TPO or executed-volume profile.

One existing test used 160 continuously spaced five-minute bars as if they contained a prior session, but they all fell on one date. It now uses two separate 75-bar session windows and verifies that missing weekly/monthly history stays missing.

## Evidence and remaining work

No new historical alpha backtest was run. The features are covered by synthetic causality and boundary tests; existing book-inspired backtests remain adaptations with their previous evidence limitations. The original 28 model groups were not merged with the new 25 concepts or counted twice as newly validated strategies.

A complete tradable version still needs fixed pivot-context/entry/exit/risk rules, complete prior-period history, observed profiles where required, exact option selection, dated contract metadata and conservative executable quote/fill modeling. Independent forward validation and existing readiness gates still apply. New features were not registered in automatic signal generation or live execution. Existing helper fixes will take effect when their processes load the updated source.

Validation artifacts: `reports/pivot_boss_catalog_2026-10-15/focused_tests.txt`, `regression_tests.txt` and `inventory.json`.

Results: **65 focused checks passed** and **1,302 full regression checks passed** (12 warnings) in the isolated network-blocked checkout. Compilation and whitespace checks passed. No service was restarted or order sent.
