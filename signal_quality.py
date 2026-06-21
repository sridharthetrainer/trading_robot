"""
signal_quality.py — data-quality guard for the signal-learning dataset.

Purpose
-------
Catch CORRUPT signal_log rows BEFORE they poison ML training, weight calibration,
or autonomous hypothesis testing. The motivating bug (2026-06-11): 14 MIDCPNIFTY
rows were logged with entry_price ~868 while the true index level (and the
triple-barrier outcome_price) was ~13,900 — a 16x price-scale mismatch from an
upstream entry-price / token-resolution error. Those rows created fake -1,500%
"moves" that made a meaningless "fade the signals" strategy look hugely profitable.

This module is purely ADDITIVE. It does not change how signals are logged; it
filters the dataset at the point of CONSUMPTION (feature builder, analytics,
nightly learning). The upstream logging fix is tracked separately.

A single instrument cannot move more than a sane fraction inside one triple-barrier
window, so an entry->outcome gap beyond that is a data error, not a real move.

API
---
    price_row_ok(entry, outcome) -> (bool, reason)
    clean_signal_frame(df) -> (clean_df, report)   # vectorized
    scan_signal_log(db_path) -> report             # quick DB-wide QC report
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

# Max |outcome/entry - 1| treated as a *possible* move within a barrier window.
# Beyond this is a scale error. Generous (60%) so only genuine corruption is cut.
MAX_OUTCOME_MOVE = float(os.getenv("SIGNAL_QC_MAX_MOVE", "0.6"))


def price_row_ok(entry_price: Any, outcome_price: Any,
                 max_move: float = MAX_OUTCOME_MOVE) -> Tuple[bool, str]:
    """True if entry/outcome prices are sane (positive, same scale)."""
    try:
        e = float(entry_price)
        o = float(outcome_price)
    except (TypeError, ValueError):
        return False, "non_numeric"
    if e <= 0:
        return False, "entry<=0"
    if o <= 0:
        return False, "outcome<=0"
    if abs(o / e - 1.0) > max_move:
        return False, "scale_mismatch"
    return True, ""


def clean_signal_frame(df, entry_col: str = "entry_price",
                       outcome_col: str = "outcome_price",
                       max_move: float = MAX_OUTCOME_MOVE,
                       require_outcome: bool = True):
    """Drop corrupt rows from a signal DataFrame (vectorized).

    require_outcome=True  -> rows must have a sane entry AND outcome price.
    require_outcome=False -> rows with no outcome yet are KEPT (can't check),
                             only rows with a present-but-insane outcome are cut.
    Returns (clean_df, report).
    """
    import pandas as pd
    if df is None or len(df) == 0:
        return df, {"total": 0, "kept": 0, "dropped": 0, "reasons": {}}

    e = pd.to_numeric(df.get(entry_col), errors="coerce")
    o = pd.to_numeric(df.get(outcome_col), errors="coerce")

    entry_ok   = e > 0
    has_outcome = o > 0
    ratio_ok   = entry_ok & has_outcome & ((o / e - 1.0).abs() <= max_move)

    if require_outcome:
        keep = ratio_ok
    else:
        # keep rows with sane entry whose outcome is simply not filled yet
        keep = ratio_ok | (entry_ok & ~has_outcome)

    reasons: Dict[str, int] = {}
    bad = ~keep
    if bad.any():
        reasons["entry<=0"]       = int((bad & ~entry_ok).sum())
        reasons["outcome<=0"]     = int((bad & entry_ok & ~has_outcome & require_outcome).sum())
        reasons["scale_mismatch"] = int((bad & entry_ok & has_outcome).sum())
        reasons = {k: v for k, v in reasons.items() if v}

    clean = df[keep].copy()
    report = {"total": int(len(df)), "kept": int(keep.sum()),
              "dropped": int(bad.sum()), "reasons": reasons,
              "max_move": max_move}
    if report["dropped"]:
        logger.warning("signal_quality: dropped %d/%d corrupt rows %s",
                       report["dropped"], report["total"], reasons)
    return clean, report


def scan_signal_log(db_path: str = "signal_log.db",
                    table: str = "signal_log") -> Dict[str, Any]:
    """Run the QC over the whole signal_log and return a report (no writes)."""
    import sqlite3
    import pandas as pd
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql(
            f"SELECT symbol, signal_date, entry_price, outcome_price "
            f"FROM {table} WHERE entry_price > 0 AND outcome_price > 0", con)
    finally:
        con.close()
    _, report = clean_signal_frame(df)
    # which symbols are worst — helps pinpoint the upstream bug
    if report["dropped"]:
        e = pd.to_numeric(df["entry_price"], errors="coerce")
        o = pd.to_numeric(df["outcome_price"], errors="coerce")
        bad = (e > 0) & (o > 0) & ((o / e - 1.0).abs() > MAX_OUTCOME_MOVE)
        report["by_symbol"] = (df[bad]["symbol"].value_counts().head(10).to_dict())
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    rep = scan_signal_log()
    print("\n=== signal_log data-quality scan ===")
    print(f"  rows checked : {rep['total']}")
    print(f"  clean        : {rep['kept']}")
    print(f"  CORRUPT      : {rep['dropped']}  {rep.get('reasons', {})}")
    if rep.get("by_symbol"):
        print(f"  worst symbols: {rep['by_symbol']}")
