import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import option_signal_research_ledger as osrl
from option_multistrike_signals import ensure_multistrike_schema


@contextmanager
def _isolated(tmp_dir: str):
    """check_candidate()/run_all() touch module-level SNAPSHOT_DB and
    LEDGER_FILE constants -- both must be redirected for a test, not just
    one of them (the exact mistake made three times already this session
    with rl_state.json / strategy_genomes.json / eod_setup_edge_report.json)."""
    orig_db, orig_ledger = osrl.SNAPSHOT_DB, osrl.LEDGER_FILE
    osrl.SNAPSHOT_DB = str(Path(tmp_dir) / "snapshots.db")
    osrl.LEDGER_FILE = Path(tmp_dir) / "ledger.json"
    try:
        yield
    finally:
        osrl.SNAPSHOT_DB, osrl.LEDGER_FILE = orig_db, orig_ledger


def _seed(db_path, *, underlying, snapshot_time, direction, score):
    with sqlite3.connect(db_path) as conn:
        ensure_multistrike_schema(conn)
        conn.execute(
            """INSERT INTO option_strike_signals
               (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,
                direction,score,tradable,price,source)
               VALUES (0,?,?,?,0,'CE','NEUTRAL','WATCH',?,?,0,100,'angel')""",
            (snapshot_time, underlying, "2026-08-01", direction, score),
        )


def test_zero_forward_days_at_discovery_reports_accruing():
    with tempfile.TemporaryDirectory() as tmp, _isolated(tmp):
        # only pre-discovery data seeded -- nothing after the frozen
        # discovery date, so there is genuinely nothing to check yet
        _seed(osrl.SNAPSHOT_DB, underlying="NIFTY",
              snapshot_time="2026-07-10T10:00:00+0530", direction="BULLISH", score=10.0)
        r = osrl.check_candidate("score_inverse_3hr")
        assert r["verdict"] == "ACCRUING"
        assert r["forward_days"] == 0


def test_forward_data_after_discovery_date_is_counted():
    with tempfile.TemporaryDirectory() as tmp, _isolated(tmp):
        # data strictly AFTER the frozen discovery date must be picked up
        _seed(osrl.SNAPSHOT_DB, underlying="NIFTY",
              snapshot_time="2026-07-16T10:00:00+0530", direction="BULLISH", score=10.0)
        # pre-discovery data must NOT leak into the forward count
        _seed(osrl.SNAPSHOT_DB, underlying="NIFTY",
              snapshot_time="2026-07-01T10:00:00+0530", direction="BULLISH", score=10.0)
        obs = osrl._load_scored_observations(("NIFTY",), "2026-07-15")
        dates = {o["snapshot_time"][:10] for o in obs}
        assert dates == {"2026-07-16"}


def test_run_all_writes_ledger_file_only_in_isolated_path():
    with tempfile.TemporaryDirectory() as tmp, _isolated(tmp):
        _seed(osrl.SNAPSHOT_DB, underlying="NIFTY",
              snapshot_time="2026-07-16T10:00:00+0530", direction="BULLISH", score=10.0)
        osrl.run_all()
        assert osrl.LEDGER_FILE.exists()
        assert osrl.LEDGER_FILE.parent == Path(tmp)
