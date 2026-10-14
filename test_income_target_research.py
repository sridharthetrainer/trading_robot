import sqlite3

import pytest

from income_target_research import (
    target_requirements, read_signal_snapshot, rank_strategies,
    funded_model_replay, summarize,
)


def test_monthly_target_requires_real_after_cost_expectancy():
    target = target_requirements(500000, 30000)
    assert target["required_monthly_return"] == .06
    row = next(row for row in target["scenarios"] if row["monthly_trades"] == 40 and row["risk_fraction"] == .005)
    assert row["planned_risk_rupees"] == 2500 and row["required_average_net_r"] == .3


@pytest.mark.parametrize("capital,target", [(0, 30000), (500000, -1), (float("inf"), 30000), (500000, float("nan"))])
def test_invalid_budget_is_rejected(capital, target):
    with pytest.raises(ValueError):
        target_requirements(capital, target)


def test_dedup_does_not_replace_pending_first_signal_with_later_winner(tmp_path):
    db = tmp_path / "signals.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE signal_log(id INTEGER,log_time REAL,signal_date TEXT,
                      signal_time TEXT,strategy TEXT,symbol TEXT,side TEXT,entry_price REAL,
                      stop_loss REAL,target REAL,rr REAL,tb_label INTEGER,tb_r_multiple_net REAL,
                      outcome_time TEXT,training_eligible INTEGER)""")
        conn.executemany("INSERT INTO signal_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (1,1,"2026-10-13","10:00","test","ABC","BUY",100,99,102,2,None,None,None,1),
            (2,2,"2026-10-13","10:05","test","ABC","BUY",100,99,102,2,1,2,"2026-10-13",1),
            (3,3,"2026-10-13","10:10","test","XYZ","BUY",100,99,102,2,0,0,"2026-10-13",1),
            (4,4,"2026-10-13","10:15","test","FUTURE","BUY",100,99,102,2,1,2,"2026-10-15",1),
        ])
    before = db.read_bytes()
    rows, coverage = read_signal_snapshot(db, "2026-10-14")
    assert [row["symbol"] for row in rows] == ["XYZ"]
    assert rows[0]["net_r"] == 0
    assert coverage["distinct_setups"] == 3
    assert coverage["pending_or_invalid_outcomes"] == 2
    assert db.read_bytes() == before


def test_day_concentration_cannot_qualify_for_shortlist():
    rows = [{"strategy": "crowded", "signal_date": "2026-10-01", "net_r": 1} for _ in range(100)]
    for day in range(2, 10):
        rows.append({"strategy": "crowded", "signal_date": f"2026-10-{day:02d}", "net_r": -3})
    days = sorted({row["signal_date"] for row in rows})
    ranked, shortlist = rank_strategies(rows, days, as_of="2026-10-14")
    assert ranked[0]["mean_net_r"] > 0
    assert ranked[0]["equal_active_day_mean_net_r"] < 0
    assert not shortlist


def test_recent_losses_fail_even_when_lifetime_mean_is_positive():
    rows = []
    for day in range(1, 21):
        for _ in range(3):
            rows.append({"strategy": "reversal", "signal_date": f"2026-09-{day:02d}",
                         "net_r": 2 if day <= 10 else -.5})
    days = sorted({row["signal_date"] for row in rows})
    ranked, shortlist = rank_strategies(rows, days, as_of="2026-09-22")
    assert ranked[0]["mean_net_r"] > 0 and ranked[0]["recent"]["mean_net_r"] < 0
    assert not shortlist


def test_clean_positive_sample_is_paper_research_only():
    rows = [{"strategy": "new", "signal_date": f"2026-09-{day:02d}", "net_r": 1}
            for day in range(1, 21) for _ in range(3)]
    days = sorted({row["signal_date"] for row in rows})
    ranked, shortlist = rank_strategies(rows, days, as_of="2026-09-22")
    assert len(shortlist) == 1
    assert not shortlist[0]["live_approved"] and not shortlist[0]["income_target_supported"]


def test_funded_model_includes_initial_loss_and_skips_unaffordable_premium():
    trades = [
        {"entry_date": "2026-09-01", "entry_time": "10:00", "entry_premium": 10,
         "qty": 1, "cost": 1, "pnl": -6},
        {"entry_date": "2026-09-02", "entry_time": "10:00", "entry_premium": 95,
         "qty": 1, "cost": 1, "pnl": 20},
    ]
    result = funded_model_replay(trades, capital=100, monthly_target=30)
    assert result["max_model_closed_trade_drawdown"] == 6
    assert result["skipped_unfunded_or_invalid"] == 1
    assert result["final_model_equity"] == 94
    assert not result["monthly"]["2026-09"]["target_met"]
    assert not result["income_target_supported"]


def test_zero_return_is_included_in_mean():
    assert summarize([1, 0])["mean_net_r"] == .5
