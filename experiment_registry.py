#!/usr/bin/env python3
"""
experiment_registry.py — append-only registry of validation experiments.

Purpose (audit gap #9): without a registry you silently RE-TEST ideas that
already failed. This records every validation run keyed by a stable config hash
(strategy + symbol + timeframe + params) so you can ask "have we tried this, and
what happened?" before spending another grid search on it.

Backed by SQLite (experiments.db). Additive and decoupled — it imports nothing
from the trading system; it only reads attributes off a ValidationResult-like
object. Failure to log never breaks validation (callers wrap in try/except).

Usage:
    from experiment_registry import log_result, already_tested
    log_result(result, timeframe="5m")          # auto-called by run_validation
    already_tested("breakout", "NIFTY", "5m", {...})  -> (bool, prior_record|None)

    python experiment_registry.py               # print the table
    python experiment_registry.py --check breakout NIFTY 5m
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DB_PATH = "experiments.db"


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiments (
            config_hash   TEXT PRIMARY KEY,
            strategy      TEXT,
            symbol        TEXT,
            timeframe     TEXT,
            params        TEXT,
            n_trials      INTEGER,
            dev_sharpe    REAL,
            holdout_sharpe REAL,
            deflated_sharpe REAL,
            beats_benchmark INTEGER,
            verdict       TEXT,
            run_count     INTEGER DEFAULT 1,
            first_run     TEXT,
            last_run      TEXT
        )
        """
    )
    conn.commit()
    return conn


def config_hash(strategy: str, symbol: str, timeframe: str,
                params: Optional[Dict[str, Any]]) -> str:
    """Stable hash of the experiment identity — order-independent params."""
    payload = json.dumps(
        {
            "strategy":  str(strategy),
            "symbol":    str(symbol),
            "timeframe": str(timeframe),
            "params":    params or {},
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def log_experiment(
    *,
    strategy: str,
    symbol: str,
    timeframe: str,
    params: Optional[Dict[str, Any]],
    n_trials: int = 0,
    dev_sharpe: Optional[float] = None,
    holdout_sharpe: Optional[float] = None,
    deflated_sharpe: Optional[float] = None,
    beats_benchmark: Optional[bool] = None,
    verdict: str = "UNKNOWN",
    db_path: str = DB_PATH,
) -> str:
    """Insert/update one experiment record. Returns the config hash. Re-running
    the same config bumps run_count and refreshes the latest result rather than
    creating a duplicate row."""
    ch = config_hash(strategy, symbol, timeframe, params)
    now = datetime.now().isoformat(timespec="seconds")
    conn = _conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO experiments (
                config_hash, strategy, symbol, timeframe, params, n_trials,
                dev_sharpe, holdout_sharpe, deflated_sharpe, beats_benchmark,
                verdict, run_count, first_run, last_run
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)
            ON CONFLICT(config_hash) DO UPDATE SET
                n_trials        = excluded.n_trials,
                dev_sharpe      = excluded.dev_sharpe,
                holdout_sharpe  = excluded.holdout_sharpe,
                deflated_sharpe = excluded.deflated_sharpe,
                beats_benchmark = excluded.beats_benchmark,
                verdict         = excluded.verdict,
                run_count       = run_count + 1,
                last_run        = excluded.last_run
            """,
            (
                ch, str(strategy), str(symbol), str(timeframe),
                json.dumps(params or {}, sort_keys=True, default=str),
                int(n_trials),
                _f(dev_sharpe), _f(holdout_sharpe), _f(deflated_sharpe),
                None if beats_benchmark is None else int(bool(beats_benchmark)),
                str(verdict), now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return ch


def log_result(result: Any, timeframe: str = "unknown",
               db_path: str = DB_PATH) -> Optional[str]:
    """Adapter: log a validation_harness.ValidationResult. Best-effort."""
    try:
        return log_experiment(
            strategy        = getattr(result, "strategy", "?"),
            symbol          = getattr(result, "symbol", "?"),
            timeframe       = timeframe,
            params          = getattr(result, "best_params", {}) or {},
            n_trials        = getattr(result, "n_trials", 0) or 0,
            dev_sharpe      = getattr(result, "dev_avg_sharpe", None),
            holdout_sharpe  = getattr(result, "holdout_sharpe", None),
            deflated_sharpe = getattr(result, "deflated_sharpe", None),
            beats_benchmark = getattr(result, "beats_benchmark", None),
            verdict         = getattr(result, "verdict", "UNKNOWN"),
            db_path         = db_path,
        )
    except Exception:
        return None


def already_tested(strategy: str, symbol: str, timeframe: str,
                   params: Optional[Dict[str, Any]],
                   db_path: str = DB_PATH) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Has this exact config been validated before? Returns (bool, record)."""
    ch = config_hash(strategy, symbol, timeframe, params)
    conn = _conn(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM experiments WHERE config_hash = ?", (ch,)
        ).fetchone()
    finally:
        conn.close()
    return (row is not None, dict(row) if row is not None else None)


def list_experiments(db_path: str = DB_PATH) -> list:
    conn = _conn(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM experiments ORDER BY last_run DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _f(x: Any) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _print_table(rows: list) -> None:
    if not rows:
        print("(no experiments logged yet)")
        return
    hdr = f"{'strategy':16s} {'symbol':10s} {'tf':5s} {'DSR':>6s} {'hold_sh':>8s} {'bench':>5s} {'verdict':18s} {'runs':>4s} {'last_run':19s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        dsr = r.get("deflated_sharpe")
        hs  = r.get("holdout_sharpe")
        bb  = r.get("beats_benchmark")
        print(
            f"{str(r.get('strategy'))[:16]:16s} {str(r.get('symbol'))[:10]:10s} "
            f"{str(r.get('timeframe'))[:5]:5s} "
            f"{(('%.3f' % dsr) if dsr is not None else 'n/a'):>6s} "
            f"{(('%.2f' % hs) if hs is not None else 'n/a'):>8s} "
            f"{('Y' if bb == 1 else ('N' if bb == 0 else '-')):>5s} "
            f"{str(r.get('verdict'))[:18]:18s} {int(r.get('run_count') or 0):>4d} "
            f"{str(r.get('last_run'))[:19]:19s}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Experiment registry viewer")
    ap.add_argument("--check", nargs=3, metavar=("STRATEGY", "SYMBOL", "TIMEFRAME"),
                    help="Check whether a config was tested (params ignored — name-level)")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    if args.check:
        strategy, symbol, tf = args.check
        rows = [r for r in list_experiments(args.db)
                if r["strategy"] == strategy and r["symbol"] == symbol
                and r["timeframe"] == tf]
        if rows:
            print(f"Found {len(rows)} prior run(s) for {strategy}/{symbol}/{tf}:")
            _print_table(rows)
        else:
            print(f"No prior run for {strategy}/{symbol}/{tf}.")
        return 0

    _print_table(list_experiments(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
