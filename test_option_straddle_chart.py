"""ATM straddle chart: rolling-ATM premium, opening-ATM premium and BS IV."""
import json
import sqlite3

from option_straddle_chart import generate_straddle_chart, straddle_series


def _db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE option_chain_snapshots (ts REAL, snapshot_time TEXT, underlying TEXT, spot REAL, "
                "atm_strike REAL, rows_json TEXT, summary_json TEXT, ok INTEGER)")
    for i, (t, spot, prices) in enumerate([
        ("09:20:00", 25010, {25000: (120, 110), 25050: (95, 135)}),
        ("10:20:00", 25060, {25000: (150, 85), 25050: (112, 104)}),
    ]):
        rows = [{"expiryDate": "2026-10-20", "strikePrice": float(k), "CE_lastPrice": ce, "PE_lastPrice": pe}
                for k, (ce, pe) in prices.items()]
        con.execute("INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?,?,1)",
                    (i, f"2026-10-15T{t}+0530", "NIFTY", spot, 0, json.dumps(rows), "{}"))
    con.commit()
    con.close()


def test_series_rolls_atm_and_tracks_opening_strike(tmp_path):
    db = tmp_path / "s.db"
    _db(db)
    from option_oi_chart import _load_snapshot_rows
    s = straddle_series(_load_snapshot_rows(db_path=str(db), underlying="NIFTY", day="2026-10-15"))
    assert [p["atm"] for p in s] == [25000.0, 25050.0]
    assert [p["straddle"] for p in s] == [230.0, 216.0]
    assert [p["open_straddle"] for p in s] == [230.0, 235.0]
    assert all(0.05 < p["iv"] < 0.6 for p in s)


def test_chart_renders_with_context_only_caption(tmp_path):
    db = tmp_path / "s.db"
    _db(db)
    res = generate_straddle_chart(underlying="NIFTY", day="2026-10-15", db_path=str(db), output_dir=str(tmp_path))
    assert res.ok and res.points == 2
    assert "not a trade signal" in res.caption


def test_no_snapshots_is_reported(tmp_path):
    res = generate_straddle_chart(underlying="NIFTY", day="2026-10-15", db_path=str(tmp_path / "none.db"))
    assert not res.ok and "not_enough_straddle_points" in res.reason
