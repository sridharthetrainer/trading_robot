"""
run_extended_validation.py — re-run the EXISTING validation_harness.py's
walk-forward + deflated-Sharpe + parameter-stability + locked-holdout gate
against the full available NIFTY/BANKNIFTY history, with STRICT source and
period separation.

This deliberately does NOT build a new backtesting system. validation_harness.py
already does exactly what was asked (parameter grid search across strategies,
deflated Sharpe correcting for the multiple-trial search, locked holdout never
touched during search, buy-and-hold benchmark gate) -- it was just starved of
history. Nothing here auto-promotes a result to live; it only re-runs the
existing measurement with more data and reports.

Data sources
------------
  external_backtest_data.db  source="external_2015_2024"
    2015-01-09..2024-01-25, unverified third-party origin, structurally
    validated (see external_data_loader.py) -- zero duplicate timestamps,
    zero OHLC-invalid rows, day-count matches the real NSE calendar.
  candle_cache.db  source="live_broker"
    2025-05-19..present for NIFTY 5m; BANKNIFTY's 5m live coverage starts
    later still (2026-06-01 as of this writing) -- check per-symbol, don't
    assume both symbols have the same live window.

There is a real gap between the two sources with NO data from either --
~16 months for NIFTY (2024-01-25..2025-05-19), longer for BANKNIFTY. A
previous version of this module concatenated both sources into one
DataFrame and relied on validation_harness's in-window gap guard
(_has_disqualifying_history_gap) to catch any single train/test window that
happened to straddle the gap -- but that guard only inspects timestamps
*within* one window. A window whose train slice ends the day external data
stops and whose test slice starts the day live data begins contains no
internal gap in either half, so it silently passed: "trained on the last
known market state before a 16-month blackout, tested immediately after"
was being scored as an ordinary next-period walk-forward step, which
overstates how much a real strategy could have known.

This module now prevents that structurally instead of relying on a
downstream check: data is split into physically-contiguous, single-source
segments *before* validation ever sees it (split_into_segments), and
validation_harness.run_validation() is called once per (symbol, strategy,
segment) -- never once across concatenated segments. Each source's rows
also keep an explicit _source tag through loading (load_labeled_history)
so a segment's provenance is never inferred after the fact.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import validation_harness as vh

logger = logging.getLogger(__name__)

EXTERNAL_DB = "external_backtest_data.db"
LIVE_DB = "candle_cache.db"

# Mirrors validation_harness._has_disqualifying_history_gap's default -- same
# threshold, same rationale (weekends/holidays are normal, anything longer
# means no strategy could have traded through the missing period).
MAX_GAP_DAYS = 10

# A segment needs at minimum this many bars before it's worth running a grid
# search over at all. This is a compute-saving pre-filter only -- the real
# sufficiency authority is validation_harness.run_validation() itself, which
# independently returns INSUFFICIENT_DATA for anything too thin to support
# WF_MIN_WINDOWS walk-forward windows.
MIN_SEGMENT_BARS = 200

_FN_MAP = {
    "trend":          ("backtest_trend", "backtest_trend"),
    "mean_reversion": ("backtest_mr_enhanced", "backtest_mr"),
    "breakout":       ("backtest_breakout", "backtest_breakout"),
    "ma_cross":       ("backtest_ma_cross", "backtest_ma_cross"),
    "scalping":       ("backtest_scalping", "backtest_scalping"),
    "ema_5min":       ("backtest_5min_ema", "backtest_5min_ema"),
    "cpr":            ("backtest_cpr", "backtest_cpr"),
    "orb":            ("backtest_orb", "backtest_orb"),
    "vwap_reversion": ("backtest_vwap_reversion", "backtest_vwap_reversion"),
    "supertrend_mtf": ("backtest_supertrend_mtf", "backtest_supertrend_mtf"),
    "fibonacci":      ("backtest_fibonacci",    "backtest_fibonacci"),
}


def _load_source(db_path: str, source_tag: str, symbol: str, interval: str) -> pd.DataFrame:
    """Load one (symbol, interval) series from one database, tagged with its
    source in a _source column. Returns an empty DataFrame if the file/table/
    symbol doesn't exist -- never raises for a missing optional source."""
    if not Path(db_path).exists():
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND interval=? ORDER BY timestamp",
            conn, params=(symbol.upper(), interval),
        )
    except Exception as exc:
        logger.debug("Could not read %s from %s: %s", symbol, db_path, exc)
        df = pd.DataFrame()
    finally:
        conn.close()
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False)
    # candle_cache.db stores tz-aware (+05:30) timestamps; external_backtest_
    # data.db stores naive ones. Normalise to naive so segments from different
    # sources compare/sort correctly -- pandas 3.0 raises TypeError comparing
    # tz-aware vs tz-naive instead of the older lenient behaviour (same class
    # of bug found and fixed in backtest_orb.py's _get_orb this session).
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df["_source"] = source_tag
    return df


def load_labeled_history(symbol: str = "NIFTY", interval: str = "5m") -> pd.DataFrame:
    """Load external (2015-2024) + live-collected (2025-present) history for
    one symbol with an explicit _source column carried through. This function
    does attribution only -- it does NOT decide what's safe to backtest
    together; split_into_segments does that. Columns are Title-Case OHLCV to
    match backtest_*() expectations, plus the lowercase _source tag."""
    frames = [
        _load_source(EXTERNAL_DB, "external_2015_2024", symbol, interval),
        _load_source(LIVE_DB, "live_broker", symbol, interval),
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
    })
    return combined


def split_into_segments(
    df: pd.DataFrame, max_gap_days: int = MAX_GAP_DAYS,
) -> List[Dict[str, Any]]:
    """Split a _source-tagged, time-sorted DataFrame into physically-
    contiguous, single-source segments. A new segment starts wherever
    consecutive rows are more than max_gap_days apart OR the _source tag
    changes -- either condition alone is sufficient; a run of same-source
    rows with no internal gap is one segment, a source change with no time
    gap is still two segments (provenance discontinuity, not just time).

    Returns a list of {"source", "start", "end", "bars", "data"} dicts in
    chronological order; "data" holds only the OHLCV columns (no _source),
    ready to hand straight to validation_harness.run_validation().
    """
    if df.empty or "_source" not in df.columns:
        return []

    idx = df.index
    gap_break = pd.Series(idx).diff().gt(pd.Timedelta(days=max_gap_days)).to_numpy()
    source_break = (df["_source"] != df["_source"].shift()).to_numpy()
    breaks = gap_break | source_break
    if len(breaks):
        breaks[0] = False  # the first row is never a break from a row that doesn't exist
    segment_id = pd.Series(breaks).cumsum().to_numpy()

    segments: List[Dict[str, Any]] = []
    for seg_id in range(int(segment_id.max()) + 1) if len(segment_id) else []:
        mask = segment_id == seg_id
        if not mask.any():
            continue
        seg_df = df.loc[mask].drop(columns=["_source"])
        segments.append({
            "source": str(df.loc[mask, "_source"].iloc[0]),
            "start":  seg_df.index.min(),
            "end":    seg_df.index.max(),
            "bars":   len(seg_df),
            "data":   seg_df,
        })
    return segments


def time_one_backtest_call(full_data: pd.DataFrame, train_bars: int = 4500) -> float:
    """Measure real per-call latency of a single backtest_trend() call on a
    representative train-window-sized slice, to estimate total grid-search
    runtime before committing to the full multi-hour run."""
    import backtest_trend
    slice_df = full_data.iloc[:train_bars].reset_index(drop=True)
    start = time.time()
    backtest_trend.backtest_trend(
        symbol="NIFTY", data=slice_df, initial_capital=100_000.0,
        interval_minutes=5, verbose=False, close_at_end=True,
    )
    return time.time() - start


def run_all(
    symbols: Optional[List[str]] = None,
    strategies: Optional[list] = None,
    min_segment_bars: int = MIN_SEGMENT_BARS,
) -> Dict[str, Any]:
    """Run validation_harness.run_validation() once per (symbol, strategy,
    contiguous single-source segment). Results are keyed
    "{symbol}__{strategy}__{source}_{start_date}_{end_date}" and written to
    extended_validation_report.json -- this function deliberately does NOT
    call validation_harness.save_result(), since that writes into the shared
    validation_results.json keyed by strategy name ALONE (one slot per
    strategy, not per symbol/segment); calling it here would make different
    symbols' or segments' results silently overwrite each other in a file
    other code (system_readiness_report.py, autonomous_param_trainer.py)
    reads assuming one authoritative result per strategy. This module's own
    report is the authoritative multi-segment record; nothing currently
    reads it automatically, by design -- it's meant for a human/agent to
    look at, not to feed the live promotion gate.
    """
    symbols = symbols or ["NIFTY", "BANKNIFTY"]
    strategies = strategies or list(_FN_MAP)
    all_results: Dict[str, Any] = {}
    coverage: Dict[str, Any] = {}

    for symbol in symbols:
        labeled = load_labeled_history(symbol)
        if labeled.empty:
            logger.warning("No data at all for %s in either source", symbol)
            coverage[symbol] = {"segments": []}
            continue

        segments = split_into_segments(labeled)
        coverage[symbol] = {
            "total_bars": len(labeled),
            "segments": [
                {"source": s["source"], "start": str(s["start"]), "end": str(s["end"]), "bars": s["bars"]}
                for s in segments
            ],
        }
        logger.info("%s: %d bars across %d segment(s)", symbol, len(labeled), len(segments))
        for s in segments:
            logger.info("  %s: %s .. %s (%d bars)", s["source"], s["start"], s["end"], s["bars"])

        for seg in segments:
            if seg["bars"] < min_segment_bars:
                logger.info(
                    "%s segment %s (%s..%s) skipped: %d bars < %d minimum",
                    symbol, seg["source"], seg["start"].date(), seg["end"].date(),
                    seg["bars"], min_segment_bars,
                )
                continue
            seg_label = f"{seg['source']}_{seg['start'].date()}_{seg['end'].date()}"

            for strategy in strategies:
                mod_name, fn_name = _FN_MAP[strategy]
                try:
                    import importlib
                    mod = importlib.import_module(mod_name)
                    backtest_fn = getattr(mod, fn_name)
                except Exception as exc:
                    logger.error("Could not import %s.%s: %s", mod_name, fn_name, exc)
                    continue

                param_grid = vh._build_default_param_grid(strategy)
                if not param_grid:
                    logger.warning("No param grid for %s, skipping", strategy)
                    continue

                t0 = time.time()
                result = vh.run_validation(
                    strategy_name=strategy, backtest_fn=backtest_fn, full_data=seg["data"],
                    param_grid=param_grid, symbol=symbol, interval_minutes=5,
                )
                elapsed = time.time() - t0
                key = f"{symbol}__{strategy}__{seg_label}"
                logger.info(
                    "%s done in %.1fs: verdict=%s dev_sharpe=%.3f dsr=%.3f holdout_pnl=%s",
                    key, elapsed, result.verdict, result.dev_avg_sharpe,
                    result.deflated_sharpe, result.holdout_pnl,
                )
                all_results[key] = {
                    **result.to_dict(),
                    "segment_source": seg["source"],
                    "segment_start": str(seg["start"]),
                    "segment_end": str(seg["end"]),
                    "segment_bars": seg["bars"],
                }

    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "coverage": coverage, "results": all_results}
    Path("extended_validation_report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="NIFTY,BANKNIFTY", help="comma-separated")
    parser.add_argument("--strategies", default=None, help="comma-separated subset")
    parser.add_argument("--time-only", action="store_true",
                        help="just measure per-call latency and estimate total runtime")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.time_only:
        df = load_labeled_history(symbols[0])
        segs = split_into_segments(df)
        print(f"Loaded {len(df)} bars for {symbols[0]} across {len(segs)} segment(s):")
        for s in segs:
            print(f"  {s['source']}: {s['start']} .. {s['end']} ({s['bars']} bars)")
        if segs:
            per_call = time_one_backtest_call(segs[-1]["data"])
            print(f"One backtest_trend() call on 4500 bars: {per_call:.4f}s")
    else:
        strategies = args.strategies.split(",") if args.strategies else None
        run_all(symbols, strategies)
