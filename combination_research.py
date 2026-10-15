"""Bounded, all-size co-occurrence research and exhaustive finite-grid inventory.

No orders, runtime parameter changes, or claims of independent forward evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
import itertools
import json
import math
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

from strategy_pair_edge_miner import _load, _stat, TRAIN_FRAC


def supported_combinations(index, minimum):
    """Enumerate each supported subset once; unsupported supersets cannot qualify."""
    names = sorted(name for name, ids in index.items() if len(ids) >= minimum)

    def visit(prefix, ids, start, size):
        for position in range(start, len(names)):
            if len(prefix) + len(names) - position < size:
                break
            name = names[position]
            matches = index[name] if ids is None else ids & index[name]
            if len(matches) < minimum:
                continue
            combo = (*prefix, name)
            if len(combo) == size:
                yield combo, matches
            else:
                yield from visit(combo, matches, position + 1, size)

    # Cover all supported pairs before triples, etc.; a budget must not spend
    # every trial inside one alphabetically early, deeply nested branch.
    for size in range(2, len(names) + 1):
        found = False
        for result in visit((), None, 0, size):
            found = True
            yield result
        if not found:
            break


def parameter_inventory():
    from autonomous_param_trainer import STRATEGY_BACKTESTS
    from validation_harness import _build_default_param_grid
    families = []
    for name in STRATEGY_BACKTESTS:
        grid = _build_default_param_grid(name)
        values = [dict(zip(grid, combo)) for combo in itertools.product(*grid.values())] if grid else []
        families.append({"strategy": name, "grid": grid, "combinations": values,
                         "combination_count": len(values), "evaluated_by_this_inventory": False})
    return {"families": families, "total_combinations": sum(row["combination_count"] for row in families),
            "scope": "all values in current finite default grids; not every possible numeric parameter",
            "permutations": "Parameter key order and unordered strategy agreement do not create distinct trials."}


def research(db_path="signal_log.db", *, min_train=40, max_tests=10000, max_seconds=30):
    if min_train < 2 or max_tests < 1 or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("positive budgets and min_train >= 2 required")
    started = time.monotonic()
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        deadline = started + max_seconds
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        rows = _load(conn)
    days = sorted({row[0] for row in rows})
    if len(days) < 6:
        return {"complete": False, "reason": "insufficient_days", "live_approved": False}
    cutoff = days[max(1, int(len(days) * TRAIN_FRAC) - 1)]
    train = [row for row in rows if row[0] <= cutoff]
    holdout = [row for row in rows if row[0] > cutoff]
    index, holdout_index = defaultdict(set), defaultdict(set)
    for collection, destination in ((train, index), (holdout, holdout_index)):
        for row_id, (_, names, _) in enumerate(collection):
            for name in names:
                destination[name].add(row_id)
    eligible = sum(len(ids) >= min_train for ids in index.values())
    family_upper_bound = max(1, 2 ** eligible - eligible - 1)

    def daily_stats(collection, ids):
        by_day = defaultdict(list)
        for row_id in sorted(ids):
            day, _, value = collection[row_id]
            by_day[day].append(value)
        return _stat([sum(values) / len(values) for _, values in sorted(by_day.items())])

    tested = []
    stop_reason = None
    incomplete_size = None
    for combo, ids in supported_combinations(index, min_train):
        if len(tested) >= max_tests or time.monotonic() >= deadline:
            stop_reason = "trial_budget" if len(tested) >= max_tests else "time_budget"
            incomplete_size = len(combo)
            break
        other_ids = set.intersection(*(holdout_index.get(name, set()) for name in combo))
        tested.append({"strategies": combo, "train_rows": len(ids), "holdout_rows": len(other_ids),
                       "train_daily": daily_stats(train, ids), "holdout_daily": daily_stats(holdout, other_ids)})
    complete = stop_reason is None
    correction = max(1, len(tested)) if complete else family_upper_bound
    candidates = []
    for item in tested:
        tr, ho = item["train_daily"], item["holdout_daily"]
        item["adjusted_train_p"] = min(1.0, tr["p"] * correction)
        qualifies = (tr["n"] >= 8 and tr["mean_net_r"] > 0 and item["adjusted_train_p"] < .05
                     and ho["n"] >= 5 and item["holdout_rows"] >= 15 and ho["mean_net_r"] > 0)
        item["verdict"] = "EXPLORATORY_CANDIDATE" if qualifies else "NOT_QUALIFIED"
        if qualifies:
            candidates.append(item)
    return {"generated_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "complete": complete, "stop_reason": stop_reason, "search_scope": "all subset sizes with minimum training support",
            "training_strategy_count": len(index), "supported_strategy_count": eligible,
            "theoretical_supported_universe_subsets": family_upper_bound,
            "minimum_train_rows": min_train, "tested": len(tested), "max_tests": max_tests,
            "elapsed_seconds": round(time.monotonic() - started, 3), "cutoff_day": cutoff,
            "completed_subset_sizes": sorted({len(row["strategies"]) for row in tested
                                               if incomplete_size is None or len(row["strategies"]) < incomplete_size}),
            "partially_searched_subset_size": incomplete_size,
            "train_days": sum(day <= cutoff for day in days), "holdout_days": sum(day > cutoff for day in days),
            "multiple_testing_correction": correction, "counts_by_size": dict(Counter(len(row["strategies"]) for row in tested)),
            "candidates": candidates, "all_tested": tested, "income_target_supported": False, "live_approved": False,
            "limits": ["Repeated historical exploration; holdout is not untouched future evidence",
                       "Co-occurrence net R is not a funded portfolio or executable-fill backtest",
                       "Daily averages reduce within-day replication; cross-day dependence remains",
                       "Incomplete search uses the full eligible-universe subset count for correction",
                       "No ordered strategy pipelines or arbitrary continuous parameter values tested"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="signal_log.db")
    parser.add_argument("--max-tests", type=int, default=10000)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--output-dir", default="reports/combination_research")
    args = parser.parse_args()
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    inventory = parameter_inventory()
    result = research(args.db, max_tests=args.max_tests, max_seconds=args.seconds)
    (directory / "parameter_inventory.json").write_text(json.dumps(inventory, indent=2, allow_nan=False))
    (directory / "combinations.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({key: value for key, value in result.items() if key not in {"all_tested", "candidates"}}))
    print(json.dumps({"exploratory_candidates": len(result.get("candidates", [])), "finite_parameter_combinations": inventory["total_combinations"]}))


if __name__ == "__main__":
    main()
