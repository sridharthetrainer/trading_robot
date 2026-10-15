import itertools
import json
import sqlite3

import pytest

from combination_research import parameter_inventory, research, supported_combinations
from strategy_pair_edge_miner import _stat, run


def test_supported_search_matches_brute_force_all_sizes():
    index = {"a": {1, 2, 3}, "b": {1, 2, 4}, "c": {1, 2, 5}, "d": {3, 4}}
    expected = {}
    for size in range(2, len(index) + 1):
        for combo in itertools.combinations(sorted(index), size):
            ids = set.intersection(*(index[name] for name in combo))
            if len(ids) >= 2:
                expected[combo] = ids
    actual = dict(supported_combinations(index, 2))
    assert actual == expected
    assert ("a", "b", "c") in actual


def make_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE signal_log (signal_date TEXT,agreeing_strats TEXT,tb_label INTEGER,training_eligible INTEGER,tb_r_multiple_net REAL)")
        for day in range(1, 21):
            for j in range(6):
                names = ["a", "b", "c"] if day <= 14 else ["a", "b", "c", "holdout_only"]
                conn.execute("INSERT INTO signal_log VALUES (?,?,?,?,?)", (f"2026-01-{day:02}", json.dumps(names), 1, 1, .1 + .01 * (day % 3) + .001 * j))


def test_budget_limit_reports_incomplete_and_corrects_full_family(tmp_path):
    db = tmp_path / "signals.db"
    make_db(db)
    result = research(db, max_tests=1)
    assert not result["complete"]
    assert result["stop_reason"] == "trial_budget"
    assert result["tested"] == 1
    assert result["completed_subset_sizes"] == []
    assert result["partially_searched_subset_size"] == 2
    assert result["multiple_testing_correction"] == 4
    assert result["training_strategy_count"] == 3
    assert not result["income_target_supported"]
    assert not result["live_approved"]


def test_full_supported_search_covers_pairs_and_triples(tmp_path):
    db = tmp_path / "signals.db"
    make_db(db)
    result = research(db)
    assert result["complete"]
    assert result["tested"] == 4
    assert result["completed_subset_sizes"] == [2, 3]
    assert result["counts_by_size"] == {2: 3, 3: 1}
    assert result["multiple_testing_correction"] == 4
    assert result["all_tested"][0]["train_daily"]["n"] == 14


def test_existing_miner_selects_universe_from_train_only(tmp_path):
    db = tmp_path / "signals.db"
    make_db(db)
    assert "holdout_only" not in run(str(db), top_n=10)["candidate_strategies"]


def test_tiny_p_values_are_not_rounded_to_zero_before_correction():
    stats = _stat([1 + .05 * (i % 3) for i in range(20)])
    assert 0 < stats["p"] < .00001


def test_parameter_inventory_enumerates_current_finite_grids():
    report = parameter_inventory()
    assert len(report["families"]) == 31
    assert report["total_combinations"] == 2364
    for family in report["families"]:
        assert family["combination_count"] == len(family["combinations"])
        assert not family["evaluated_by_this_inventory"]


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        research(path)
    assert not path.exists()
