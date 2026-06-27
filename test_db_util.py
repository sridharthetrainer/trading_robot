"""Contract tests for db_util: connections always close, commit on success,
rollback on error, read-only refuses writes."""
import sqlite3
from db_util import db_conn, db_query, db_query_one


def test_commits_and_closes(tmp_path):
    db = str(tmp_path / "t.db")
    with db_conn(db) as c:
        c.execute("CREATE TABLE t(x int)")
    with db_conn(db) as c:
        c.execute("INSERT INTO t VALUES (1),(2)")
    # data persisted (committed) and we can read it back
    assert db_query(db, "SELECT count(*) FROM t")[0][0] == 2
    assert db_query_one(db, "SELECT x FROM t ORDER BY x")[0] == 1
    assert db_query_one(db, "SELECT x FROM t WHERE x=99", default="none") == "none"


def test_rollback_on_error(tmp_path):
    db = str(tmp_path / "t.db")
    with db_conn(db) as c:
        c.execute("CREATE TABLE t(x int)")
    try:
        with db_conn(db) as c:
            c.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db_query(db, "SELECT count(*) FROM t")[0][0] == 0  # rolled back


def test_read_only_blocks_writes(tmp_path):
    db = str(tmp_path / "t.db")
    with db_conn(db) as c:
        c.execute("CREATE TABLE t(x int)")
    raised = False
    try:
        with db_conn(db, read_only=True) as c:
            c.execute("INSERT INTO t VALUES (1)")
    except sqlite3.OperationalError:
        raised = True
    assert raised
