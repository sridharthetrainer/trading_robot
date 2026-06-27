"""
db_util.py — small SQLite helpers that guarantee the connection is CLOSED.

Why this exists
---------------
A scan found ~191 `sqlite3.connect()` call sites, only ~46 inside a `with`.
Most of the rest are connection-factory helpers (`_conn()/_connect()`) used as
`with factory() as conn:` — but note that `with <sqlite connection>:` only
COMMITS/ROLLS BACK on exit, it does **not** close the connection. Under CPython
refcounting the connection is still closed when `conn` leaves scope, so this is
a robustness/best-practice gap rather than an active leak. These helpers make the
close explicit (and don't depend on the GC), for new code and incremental
adoption.

Usage
-----
    from db_util import db_conn, db_query

    with db_conn("trades.db") as conn:          # committed + CLOSED on exit
        conn.execute("INSERT ...", (...))

    rows = db_query("trades.db", "SELECT * FROM trades WHERE status=?", ("OPEN",))
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, List, Optional, Tuple


@contextmanager
def db_conn(db_path: str, *, timeout: float = 10.0, row_factory=None,
            wal: bool = False, read_only: bool = False, **kwargs):
    """Yield a sqlite3 connection that is ALWAYS closed (commit on clean exit,
    rollback on error). `wal=True` sets journal_mode=WAL; `read_only=True` opens
    the file in immutable read-only mode (no accidental writes/locks)."""
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                               timeout=timeout, **kwargs)
    else:
        conn = sqlite3.connect(db_path, timeout=timeout, **kwargs)
    if row_factory is not None:
        conn.row_factory = row_factory
    if wal and not read_only:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass  # PRAGMA failures are non-fatal; connection still usable
    try:
        yield conn
        if not read_only:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def db_query(db_path: str, sql: str, params: Iterable[Any] = (),
             *, row_factory=None, timeout: float = 10.0) -> List[Tuple]:
    """Run a read query and return all rows, guaranteeing the connection closes."""
    with db_conn(db_path, timeout=timeout, row_factory=row_factory,
                 read_only=True) as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def db_query_one(db_path: str, sql: str, params: Iterable[Any] = (),
                 *, default: Optional[Any] = None, timeout: float = 10.0):
    """Run a read query and return the first row (or `default` if none)."""
    with db_conn(db_path, timeout=timeout, read_only=True) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return row if row is not None else default
