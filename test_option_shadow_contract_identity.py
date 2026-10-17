import json
import sqlite3

from option_shadow_labeller import _eod_close, _snapshot_close, _safe_float


def test_eod_requires_expiry_and_supports_broker_format():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE options_eod(date, expiry, strike, opt_type, close, settle)')
        conn.executemany('INSERT INTO options_eod VALUES (?,?,?,?,?,?)', [
            ('2026-10-16', '2026-10-20', 25000, 'CE', 90, 90),
            ('2026-10-16', '2026-10-27', 25000, 'CE', 150, 150),
        ])
        kwargs = dict(conn=conn, table='options_eod', day='2026-10-16', strike=25000, opt_type='CE')
        assert _eod_close(**kwargs) is None
        assert _eod_close(**kwargs, expiry='27OCT26') == 150
        assert _eod_close(**kwargs, expiry='2026-11-03') is None


def test_snapshot_requires_live_exact_expiry_closing_quote_after_entry(tmp_path):
    db = tmp_path / 'snapshots.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE option_chain_snapshots(ts, snapshot_time, underlying, ok, is_live, source, expiry, rows_json)')
        for ts, clock, live, expiry, price in [
            (10, '15:21:00', 1, '2026-10-20', 100),
            (11, '15:22:00', 0, '2026-10-20', 200),
            (12, '15:23:00', 1, '2026-10-27', 300),
            (13, '14:00:00', 1, '2026-10-20', 400),
        ]:
            conn.execute('INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?,?,?)',
                         (ts, '2026-10-16T'+clock+'+0530', 'NIFTY', 1, live, 'angel', expiry,
                          json.dumps([{'strikePrice': 25000, 'CE_lastPrice': price}])))
    kwargs = dict(day='2026-10-16', underlying='NIFTY', strike=25000, opt_type='CE', snapshot_db=str(db))
    assert _snapshot_close(**kwargs) is None
    assert _snapshot_close(**kwargs, expiry='20OCT26', after_ts=9) == 100
    assert _snapshot_close(**kwargs, expiry='20OCT26', after_ts=10) is None
    assert _snapshot_close(**{**kwargs, 'underlying': 'BANKNIFTY'}, expiry='20OCT26') is None


def test_nonfinite_prices_are_rejected():
    assert _safe_float('NaN') == 0
    assert _safe_float('inf') == 0
