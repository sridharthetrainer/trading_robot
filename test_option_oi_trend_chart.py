"""Trending OI: per-leg OI added since the day's first snapshot."""
import json
import sqlite3

from option_oi_chart import _load_snapshot_rows
from option_oi_trend_chart import generate_oi_trend_chart, oi_added_series, parse_legs


def _db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE option_chain_snapshots (ts REAL, snapshot_time TEXT, underlying TEXT, spot REAL, "
                "atm_strike REAL, rows_json TEXT, summary_json TEXT, ok INTEGER)")
    snaps = [
        ("09:20:00", {25000: (1000, 2000)}),
        ("09:25:00", {25000: (800, 2600), 25050: (500, 100)}),   # 25050 appears late
        ("09:30:00", {25000: (700, 3000), 25050: (900, 150)}),
    ]
    for i, (t, strikes) in enumerate(snaps):
        rows = [{"strikePrice": float(k), "CE_openInterest": ce, "PE_openInterest": pe}
                for k, (ce, pe) in strikes.items()]
        con.execute("INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?,?,1)",
                    (i, f"2026-10-15T{t}+0530", "NIFTY", 25010, 0, json.dumps(rows), "{}"))
    con.commit()
    con.close()


def test_series_measures_from_first_sighting(tmp_path):
    db = tmp_path / "s.db"
    _db(db)
    labels, s = oi_added_series(_load_snapshot_rows(db_path=str(db), underlying="NIFTY", day="2026-10-15"))
    assert labels == ["09:20", "09:25", "09:30"]
    assert s[(25000.0, "CE")] == [0, -200, -300]
    assert s[(25000.0, "PE")] == [0, 600, 1000]
    nan, a, b = s[(25050.0, "CE")]
    assert nan != nan and (a, b) == (0, 400)


def test_parse_legs():
    assert parse_legs("23000CE, 22800pe 22900 CE") == [(23000.0, "CE"), (22800.0, "PE"), (22900.0, "CE")]


def test_auto_pick_balances_sides_and_explicit_legs(tmp_path):
    db = tmp_path / "s.db"
    _db(db)
    res = generate_oi_trend_chart(day="2026-10-15", top=1, db_path=str(db), output_dir=str(tmp_path))
    assert res.ok and "PE 25000" in res.caption and "CE 25050" in res.caption
    assert "OI read: PE +0.0L vs CE +0.0L -> put writing dominant" in res.caption
    res = generate_oi_trend_chart(day="2026-10-15", legs=[(25000.0, "PE")], db_path=str(db), output_dir=str(tmp_path))
    assert res.ok and "PE 25000: +0.0L" in res.caption and "CE" not in res.caption.split("\n")[1]
    assert not generate_oi_trend_chart(day="2026-10-15", legs=[(1.0, "CE")], db_path=str(db)).ok
