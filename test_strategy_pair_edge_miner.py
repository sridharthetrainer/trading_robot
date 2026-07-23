import json
import sqlite3
import tempfile
from pathlib import Path

import strategy_pair_edge_miner as spem


def _make_db(path, rows) -> None:
    """rows: list of (signal_date, agreeing_list, net_r)."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE signal_log (
            signal_date TEXT, agreeing_strats TEXT, tb_label INTEGER,
            training_eligible INTEGER, tb_r_multiple_net REAL
        )
    """)
    for signal_date, agreeing, net_r in rows:
        conn.execute(
            "INSERT INTO signal_log (signal_date, agreeing_strats, tb_label, "
            "training_eligible, tb_r_multiple_net) VALUES (?,?,?,?,?)",
            (signal_date, json.dumps(agreeing), 1 if net_r >= 0 else -1, 1, net_r),
        )
    conn.commit()
    conn.close()


def _days(n: int):
    return [f"2026-01-{i + 1:02d}" for i in range(n)]


def test_top_strategies_ranks_by_frequency():
    loaded = [
        ("2026-01-01", {"a", "b"}, 0.1),
        ("2026-01-01", {"a", "c"}, 0.1),
        ("2026-01-02", {"a"}, 0.1),
        ("2026-01-02", {"b"}, 0.1),
    ]
    top = spem._top_strategies(loaded, n=3)
    assert top[0] == "a"  # appears in 3 rows, more than b (2) or c (1)
    assert set(top) == {"a", "b", "c"}


def test_pair_cohort_requires_both_strategies_present():
    days = _days(20)
    rows = []
    for day in days:
        # 45 rows where BOTH fire together, 45 rows where only A fires
        for _ in range(3):
            rows.append((day, ["stratA", "stratB"], 0.1))
            rows.append((day, ["stratA"], 0.1))  # B absent -- must not count
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "signal_log.db")
        _make_db(db, rows)
        rep = spem.run(db_path=db, top_n=5)
    combo = next(r for r in rep["all_tested"] if r["combo"] == "stratA+stratB")
    # 14 train days * 3 both-rows/day = 42 -- NOT 84 (which would include the A-only rows)
    assert combo["train"]["n"] == 14 * 3


def test_day_holdout_split_matches_cutoff_day_logic():
    days = _days(20)
    rows = [(d, ["stratA", "stratB"], 0.1) for d in days for _ in range(3)]
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "signal_log.db")
        _make_db(db, rows)
        rep = spem.run(db_path=db, top_n=5)
    cut_idx = max(1, int(len(days) * spem.TRAIN_FRAC) - 1)
    assert rep["train_days"] == cut_idx + 1
    assert rep["holdout_days"] == len(days) - cut_idx - 1
    assert rep["cutoff_day"] == days[cut_idx]


def test_bonferroni_counts_only_pairs_clearing_min_train_n():
    days = _days(20)
    rows = []
    for day in days:
        # stratA+stratB: 3/day * 14 train days = 42 >= MIN_TRAIN_N(40)
        for _ in range(3):
            rows.append((day, ["stratA", "stratB"], 0.1))
        # stratC+stratD: only 1/day * 14 = 14 train rows -- below MIN_TRAIN_N
        rows.append((day, ["stratC", "stratD"], 0.1))
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "signal_log.db")
        _make_db(db, rows)
        rep = spem.run(db_path=db, top_n=5)
    tested_combos = {r["combo"] for r in rep["all_tested"]}
    assert "stratA+stratB" in tested_combos
    assert "stratC+stratD" not in tested_combos
    assert rep["combos_tested"] == rep["bonferroni_tests"] == 1


def test_synthetic_positive_synergy_pair_is_flagged_candidate():
    days = _days(20)
    rows = []
    for day in days:
        # alpha+beta together: strong, stable, positive edge (~0.15, tight jitter)
        for j in range(5):
            rows.append((day, ["alpha", "beta"], 0.15 + 0.01 * ((j % 3) - 1)))
        # alpha alone / beta alone: flat, no edge
        for j in range(4):
            rows.append((day, ["alpha"], 0.01 * ((-1) ** j)))
            rows.append((day, ["beta"], 0.01 * ((-1) ** j)))
        # gamma combos: noisy, no stable edge
        for j in range(4):
            rows.append((day, ["alpha", "gamma"], 0.03 * ((-1) ** j)))
            rows.append((day, ["beta", "gamma"], 0.03 * ((-1) ** (j + 1))))
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "signal_log.db")
        _make_db(db, rows)
        rep = spem.run(db_path=db, top_n=5)

    combo = next(r for r in rep["all_tested"] if r["combo"] == "alpha+beta")
    assert combo["verdict"] == "CANDIDATE", combo
    assert combo["train"]["mean_net_r"] > 0.1
    assert combo["holdout"]["n"] >= 15
    assert combo["holdout"]["mean_net_r"] > 0
    # synergy: both-together clearly beats either alone (~0 mean)
    assert combo["synergy_vs_best_alone"] > 0.1
    assert any(r["combo"] == "alpha+beta" for r in rep["candidates"])


def test_synthetic_noise_pair_is_not_flagged():
    days = _days(20)
    rows = []
    for day in days:
        # delta+epsilon: zero-mean, alternating -- no stable edge either way
        for j in range(6):
            rows.append((day, ["delta", "epsilon"], 0.05 * ((-1) ** j)))
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "signal_log.db")
        _make_db(db, rows)
        rep = spem.run(db_path=db, top_n=5)

    combo = next(r for r in rep["all_tested"] if r["combo"] == "delta+epsilon")
    assert combo["verdict"] == "NOISE", combo
    assert not rep["candidates"]


def test_triple_combo_requires_all_three_present():
    days = _days(20)
    rows = []
    for day in days:
        # all three co-fire: this is the only cohort that should count toward
        # the alpha+beta+gamma triple
        for _ in range(3):
            rows.append((day, ["alpha", "beta", "gamma"], 0.1))
        # pairs missing the third member must NOT count toward the triple
        for _ in range(3):
            rows.append((day, ["alpha", "beta"], 0.1))       # gamma absent
            rows.append((day, ["alpha", "gamma"], 0.1))      # beta absent
            rows.append((day, ["beta", "gamma"], 0.1))        # alpha absent
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "signal_log.db")
        _make_db(db, rows)
        rep = spem.run(db_path=db, top_n=5, combo_size=3)

    assert rep["combo_size"] == 3
    combo = next(r for r in rep["all_tested"] if r["combo"] == "alpha+beta+gamma")
    # 14 train days * 3 all-three rows/day = 42 -- not diluted by the 2-of-3 rows
    assert combo["train"]["n"] == 14 * 3


def test_triple_synergy_pair_flagged_candidate():
    days = _days(20)
    rows = []
    for day in days:
        # alpha+beta+gamma together: strong, stable, positive edge
        for j in range(5):
            rows.append((day, ["alpha", "beta", "gamma"], 0.15 + 0.01 * ((j % 3) - 1)))
        # each pairwise subset (missing one member) and each single alone: flat
        for j in range(3):
            rows.append((day, ["alpha", "beta"], 0.01 * ((-1) ** j)))
            rows.append((day, ["alpha", "gamma"], 0.01 * ((-1) ** j)))
            rows.append((day, ["beta", "gamma"], 0.01 * ((-1) ** j)))
            rows.append((day, ["alpha"], 0.01 * ((-1) ** j)))
            rows.append((day, ["beta"], 0.01 * ((-1) ** j)))
            rows.append((day, ["gamma"], 0.01 * ((-1) ** j)))
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "signal_log.db")
        _make_db(db, rows)
        rep = spem.run(db_path=db, top_n=5, combo_size=3)

    combo = next(r for r in rep["all_tested"] if r["combo"] == "alpha+beta+gamma")
    assert combo["verdict"] == "CANDIDATE", combo
    assert combo["synergy_vs_best_alone"] > 0.1


def main() -> int:
    tests = [
        ("top strategies ranked by frequency", test_top_strategies_ranks_by_frequency),
        ("pair cohort requires both present", test_pair_cohort_requires_both_strategies_present),
        ("day holdout split matches cutoff logic", test_day_holdout_split_matches_cutoff_day_logic),
        ("bonferroni counts only min-n pairs", test_bonferroni_counts_only_pairs_clearing_min_train_n),
        ("synthetic synergy pair flagged candidate", test_synthetic_positive_synergy_pair_is_flagged_candidate),
        ("synthetic noise pair not flagged", test_synthetic_noise_pair_is_not_flagged),
        ("triple combo requires all three present", test_triple_combo_requires_all_three_present),
        ("triple synergy combo flagged candidate", test_triple_synergy_pair_flagged_candidate),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
