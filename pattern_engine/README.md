# pattern_engine

Mathematical chart-pattern recognition for the trading platform. **Isolated and
additive**: it reads an OHLCV DataFrame and returns structured pattern results.
It imports **no** live-trading code, places **no** orders, and is **not** wired
into the autonomous engine. Integrate it deliberately — *after* you've backtested
a pattern's edge. **Detecting a pattern is not the same as having an edge.**

## Install
Dependencies (`numpy, pandas, scipy, scikit-learn`) are already in the platform.
```
pip install -r pattern_engine/requirements.txt
```

## Quick start
```python
import pandas as pd
from pattern_engine import PatternEngine

df = pd.read_csv("ohlcv.csv")            # columns: open, high, low, close, volume
eng = PatternEngine()                    # default config
results = eng.detect_json(df, symbol="NIFTY")
for r in results:
    print(r["pattern"], r["direction"], r["confidence"], r["entry"], r["stop_loss"], r["target"])
```

Output JSON per pattern:
```json
{"symbol":"NIFTY","timestamp":"...","pattern":"double_top","confidence":74.6,
 "direction":"SHORT","entry":23263.15,"stop_loss":24857.75,"target":21668.55,
 "risk_reward":1.0,"volume_confirmation":true,"market_structure":"TRANSITION",
 "breakout_confirmed":true,"breakout_level":23263.15}
```

## Config
All thresholds live in `config/default_config.json` (no magic numbers in code).
Override by passing your own file:
```python
from pattern_engine import PatternEngine, load_config
eng = PatternEngine(load_config("my_overrides.json"))   # deep-merged over defaults
```

## Architecture
```
pattern_engine/
  base.py             PatternResult, Direction, PatternDetector ABC, OHLCV validation
  config_loader.py    JSON load + deep-merge overrides
  pivot_engine.py     configurable fractal swing detection
  market_structure.py HH/HL/LH/LL + regime (BULL/BEAR/RANGE/TRANSITION)
  trendline_engine.py least-squares / RANSAC / r² / touch-count
  channel_engine.py   horizontal / ascending / descending channel geometry
  breakout_engine.py  price / volume / ATR / false-breakout classification
  pattern_scoring.py  weighted 0-100 blend (0.35 quality / 0.20 vol / 0.20 trend
                      / 0.15 structure / 0.10 breakout)
  engine.py           PatternEngine orchestrator (builds context once, runs detectors)
  patterns/           one module per pattern (double_top/bottom, head_shoulders, ...)
  visualization.py    optional matplotlib diagnostics for pivots/entries/stops/targets
  tests/              deterministic unit tests on synthetic data
```

## Implemented patterns
The registry covers the requested classical, channel, breakout and smart-money
families:

- Double top / double bottom
- Triple top / triple bottom
- Head and shoulders / inverse head and shoulders
- Ascending, descending, symmetrical and expanding triangles
- Rising, falling and broadening wedges
- Bull flag, bear flag, bull pennant, bear pennant
- Rectangle, horizontal range, ascending channel, descending channel
- Cup and handle, rounding top, rounding bottom
- Diamond top, diamond bottom
- Liquidity sweep high / low, stop hunt
- Failed breakout / failed breakdown
- Breakout retest, range expansion, volatility compression
- Opening range breakout, trend day structure

Some files are thin compatibility wrappers around grouped detectors
(`triangles.py`, `wedges.py`, `flags.py`) so the package keeps both a clean
implementation and the requested module names.

## INTEGRATION (read before wiring to live)
This package deliberately stops at *detection*. To use it in the platform:
1. **Backtest the pattern first** (`pattern_engine` returns entry/stop/target — run
   it over history and measure win rate / expectancy / drawdown). Every strategy
   in this platform that was tested came back negative after costs, so treat a new
   pattern as unproven until measured.
2. If it has a measurable edge, surface its `confidence`/`direction` as **one
   score input** to the existing `signal_engine` confluence — do **not** let it
   place orders directly, and keep the system in PAPER until validated.
3. Never bypass the kill switch / daily-loss limit / product-type rules that the
   live order path enforces.
```python
from pattern_engine import PatternEngine
_PAT = PatternEngine()
def pattern_score_modifier(df, symbol):
    res = _PAT.detect(df, symbol)
    return res[0] if res else None   # feed into confluence, not into execution
```

The current platform bridge is `chart_patterns.run_chart_pattern_strategy()`.
It calls `PatternEngine.detect_best()` for a fresh, confirmed, risk-aware pattern
and passes all detected pattern metadata into `signal_engine` confluence.

## Visualization
```python
from pattern_engine import PatternEngine, plot_patterns
eng = PatternEngine()
patterns = eng.detect(df, "NIFTY")
plot_patterns(df, patterns[:3], output_path="pattern_report.png")
```

## Performance notes
- Context (pivots/structure/ATR/vol-MA) is computed **once per `detect()`** and
  shared across detectors — O(n) + O(pivots²) for pair-based patterns.
- Pivot pairing is bounded by `patterns.max_pattern_bars`; tighten it for speed.
- For live use, call `detect()` on a rolling window (e.g. last 200 bars), not the
  full history.
