"""The paper tracker must select and settle exactly as the tested backtest does.

Integration test: needs the local reports/research_2026-10-14/adjusted_returns.db
(built by build_adjusted_returns.py); skipped when it is absent.
"""
from pathlib import Path

import pytest

DB = Path(__file__).resolve().parent / "reports" / "research_2026-10-14" / "adjusted_returns.db"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="adjusted_returns.db not built")


def test_tracker_matches_backtest_selection_and_settlement(tmp_path, monkeypatch):
    import momentum_paper_tracker as mpt
    t = mpt.t
    monkeypatch.setattr(mpt, "LEDGER", tmp_path / "ledger.json")
    R, C, TV = t.load()
    Rv, Cv, TVv = R.to_numpy(), C.to_numpy(), TV.to_numpy()
    dates, cols = [str(d) for d in R.index], list(R.columns)
    ends = t.month_end_rows(R.index)
    jul, aug = [p for p in ends if dates[p][:7] in ("2026-07", "2026-08")]

    def entry(p):
        uni, mom = t.select(Rv, Cv, TVv, p)
        return {"month_end": dates[p], "names": [cols[j] for j in mom],
                "universe": [cols[j] for j in uni], "result": None}

    ledger = {"entries": [entry(jul), entry(aug)]}
    monkeypatch.setattr(mpt, "FORWARD_START", "9999-12-31")   # settle only; record nothing new
    mpt.update(ledger)

    bt = t.simulate(R, C, TV, 252, t.COST).set_index("start")
    for e in ledger["entries"]:
        res = e["result"]
        assert res is not None
        row = bt.loc[res["from"]]
        assert sorted(row["names"]) == sorted(e["names"])
        # same gross return; costs follow the same turnover rule
        bt_gross = row["momentum"] + 2 * t.COST * row["turnover"]
        tr_turn = res["turnover"]
        tr_gross = res["momentum"] + 2 * t.COST * tr_turn - (t.COST if e is ledger["entries"][0] else 0)
        assert abs(bt_gross - tr_gross) < 1e-4
    # the second month's turnover is measured against the first month's picks
    assert ledger["entries"][1]["result"]["turnover"] == pytest.approx(
        bt.loc[ledger["entries"][1]["result"]["from"], "turnover"])
