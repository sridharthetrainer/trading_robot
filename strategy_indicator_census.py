"""
strategy_indicator_census.py — runtime + static audit of every strategy and
indicator (2026-07-12, operator: "check each and every indicator, strategy
and all").

Two past bug classes drive the design:
  - 16 of 75 strategies raised TypeError on EVERY engine call for weeks,
    silently swallowed (fixed by the signature adapter) — so: CALL every
    registry strategy on realistic enriched data and flag any that error
    or return malformed results.
  - crsi_mod/nr_mod were dead for their whole life because strategies
    CONSUMED indicator columns (df.get with silent defaults) that the live
    path never PRODUCED — so: diff consumed column names against what
    _ensure_indicator_columns actually provides.

Report: strategy_indicator_census_report.json.
The nightly wiring_watchdog alerts on ANY strategy error (errors are always
regressions, no baseline needed) and reports the consumed-not-produced list
for review (heuristic; regex-based).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

REPORT_FILE = Path("strategy_indicator_census_report.json")

# Base OHLCV + engine metadata that are not "indicators".
_BASE_COLS = {"open", "high", "low", "close", "volume", "date", "timestamp",
              "symbol", "oi"}
# df access patterns that indicate a column CONSUMED by strategy code.
_CONSUME_RE = re.compile(
    r"(?:df|_df\w*|data|frame|out|candles)\s*(?:\[|\.get\(\s*)['\"]([a-z][a-z0-9_]{2,})['\"]")
_PRODUCE_RE = re.compile(
    r"(?:df|_df\w*|data|frame|out|candles)\s*\[\s*['\"]([a-z][a-z0-9_]{2,})['\"]\s*\]\s*=")

# Strategy source files whose df-column reads matter for the live scan path.
_STRATEGY_FILES = (
    "signal_engine.py", "strategies_new.py", "advanced_strategies.py",
    "chart_patterns.py", "candlestick_signals.py", "holy_grail.py",
    "volume_profile_advanced.py", "mean_reversion_signal.py",
    "signal_score.py", "mtf.py", "pivot_scalping_strategy.py",
)


def _synthetic_df(bars: int = 600, seed: int = 7):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-07-09 09:15", periods=bars, freq="5min")
    close = 24000 + np.cumsum(rng.normal(0.5, 14.0, bars))
    high = close + abs(rng.normal(8, 5, bars))
    low = close - abs(rng.normal(8, 5, bars))
    opn = close + rng.normal(0, 5, bars)
    vol = rng.integers(10_000, 400_000, bars).astype(float)
    return pd.DataFrame({"open": opn, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def run_strategy_census() -> Dict[str, Any]:
    """Call EVERY registry strategy through the live adapter on enriched
    synthetic data. An exception or malformed return is a wiring bug."""
    import signal_engine as se

    df = _synthetic_df()
    df_htf = _synthetic_df(bars=300, seed=8)
    try:
        df = se._ensure_indicator_columns(df, "CENSUS")
        df_htf = se._ensure_indicator_columns(df_htf, "CENSUS_HTF")
    except Exception as exc:
        logger.warning("enrichment failed: %s", exc)

    ok, quiet, errors = [], [], []
    for fn in getattr(se, "STRATEGIES", []):
        name = getattr(fn, "__name__", str(fn))
        try:
            res = se._invoke_strategy(fn, df, df_htf, {}, "NIFTY")
            if not isinstance(res, dict):
                errors.append({"strategy": name,
                               "error": f"non-dict return: {type(res).__name__}"})
            elif res.get("direction") or res.get("side") or res.get("action"):
                ok.append(name)
            else:
                quiet.append(name)   # valid dict, no signal on this data — fine
        except Exception as exc:
            errors.append({"strategy": name, "error": f"{type(exc).__name__}: {exc}"})
    return {"n_strategies": len(getattr(se, "STRATEGIES", [])),
            "fired": len(ok), "quiet": len(quiet),
            "errors": errors, "fired_names": ok[:40]}


def run_indicator_census() -> Dict[str, Any]:
    """Diff indicator columns CONSUMED by strategy code against those the
    live enrichment path PRODUCES. consumed-not-produced = the silent-default
    class (crsi_mod/nr_mod sat dead exactly this way)."""
    import signal_engine as se

    df = _synthetic_df(bars=400)
    try:
        enriched = se._ensure_indicator_columns(df.copy(), "IND_CENSUS")
        produced_live: Set[str] = {str(c).lower() for c in enriched.columns}
    except Exception as exc:
        logger.warning("enrichment failed: %s", exc)
        produced_live = set(df.columns)

    consumed: Set[str] = set()
    produced_locally: Set[str] = set()
    for fname in _STRATEGY_FILES:
        p = Path(fname)
        if not p.exists():
            continue
        src = p.read_text(errors="replace")
        consumed.update(m.group(1) for m in _CONSUME_RE.finditer(src))
        produced_locally.update(m.group(1) for m in _PRODUCE_RE.finditer(src))

    missing = sorted(consumed - produced_live - produced_locally - _BASE_COLS)
    # Heuristic filter: only names that look like indicator columns, not
    # dict keys (score/direction/etc.) that the same regex also matches.
    _NON_INDICATOR = {"strategy", "score", "direction", "side", "action",
                      "reason", "confidence", "signal", "pattern", "entry",
                      "target", "stop", "stop_loss", "price", "meta",
                      "metadata", "regime", "bias", "name", "type", "status",
                      # keys of EXTERNAL context dicts (cross_asset /
                      # option_data / frame containers) that the same regex
                      # matches but which are not candle columns — triaged
                      # by hand 2026-07-12:
                      "crude", "spy", "us10y", "usdinr", "data",
                      "fii_futures_net", "frames", "max_pain", "oi_bias",
                      "pivot_levels", "stocks", "timeframes"}
    missing = [m for m in missing if m not in _NON_INDICATOR]
    return {"produced_live": len(produced_live),
            "consumed_in_strategy_files": len(consumed),
            "consumed_not_produced": missing}


def build() -> Dict[str, Any]:
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategies": run_strategy_census(),
        "indicators": run_indicator_census(),
    }
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("report write: %s", exc)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    r = build()
    s = r["strategies"]
    print(f"strategies: {s['n_strategies']} | fired={s['fired']} quiet={s['quiet']} "
          f"ERRORS={len(s['errors'])}")
    for e in s["errors"]:
        print(f"  ❌ {e['strategy']}: {e['error']}")
    i = r["indicators"]
    print(f"indicators: {i['produced_live']} produced | "
          f"{len(i['consumed_not_produced'])} consumed-but-NOT-produced:")
    for m in i["consumed_not_produced"][:30]:
        print(f"  ⚠️ {m}")
