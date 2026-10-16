"""Participant positioning chart: long %, net futures/options per participant."""
import sqlite3

from participant_oi_chart import generate_participant_chart, load_positioning

COLS = ["date", "client_type", "future_index_long", "future_index_short", "future_stock_long", "future_stock_short",
        "opt_index_call_long", "opt_index_put_long", "opt_index_call_short", "opt_index_put_short",
        "opt_stock_call_long", "opt_stock_put_long", "opt_stock_call_short", "opt_stock_put_short",
        "total_long", "total_short"]


def _db(path):
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE participant_oi ({', '.join(COLS)})")
    con.execute("CREATE TABLE nifty_daily (date, close)")
    for d, fii_long in (("2026-10-14", 30000), ("2026-10-15", 40000)):
        for p, fl, fs in (("FII", fii_long, 300000), ("DII", 50000, 15000), ("Pro", 50000, 27000),
                          ("Client", 200000, 40000)):
            con.execute(f"INSERT INTO participant_oi VALUES ({','.join('?' * len(COLS))})",
                        (d, p, fl, fs, 0, 0, 100, 300, 50, 100, 0, 0, 0, 0, 0, 0))
    con.execute("INSERT INTO nifty_daily VALUES ('2026-10-14', 22600)")
    con.commit()
    con.close()


def test_positioning_metrics(tmp_path):
    db = tmp_path / "p.db"
    _db(db)
    data = load_positioning(str(db), days=20)
    assert data["dates"] == ["2026-10-14", "2026-10-15"]
    fii = data["FII"][-1]
    assert round(fii["long_pct"], 2) == round(40000 / 340000 * 100, 2)
    assert fii["net_fut"] == -260000
    assert fii["net_opt"] == (100 - 50) - (300 - 100)


def test_chart_caption_reports_day_change(tmp_path):
    db = tmp_path / "p.db"
    _db(db)
    res = generate_participant_chart(db_path=str(db), output_dir=str(tmp_path))
    assert res.ok and res.points == 2
    assert "FII: 11.8% long | fut +10,000" in res.caption
    assert "not a trade signal" in res.caption


def test_missing_db(tmp_path):
    assert not generate_participant_chart(db_path=str(tmp_path / "x.db")).ok
