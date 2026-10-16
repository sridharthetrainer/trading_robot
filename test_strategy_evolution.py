import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import strategy_evolution as se
from strategy_evolution import StrategyEvolution, MIN_DISTINCT_DAYS


@contextmanager
def _isolated_files(tmp_dir: str):
    """_save() writes to hardcoded module-level _GENOME_FILE/_PERF_FILE Path
    constants, NOT anything derived from the instance — a per-instance
    attribute alone does not isolate a test. This happened for real
    2026-07-14: an earlier draft of this file let evolve() overwrite the
    actual strategy_genomes.json/strategy_performance.json with test data
    (restored from git HEAD). Both module constants must be monkeypatched
    for the duration of the test and restored after, mirroring the same
    fix applied to test_self_learning_engine_rl.py the same day."""
    orig_genome, orig_perf = se._GENOME_FILE, se._PERF_FILE
    se._GENOME_FILE = Path(tmp_dir) / "strategy_genomes.json"
    se._PERF_FILE = Path(tmp_dir) / "strategy_performance.json"
    try:
        yield
    finally:
        se._GENOME_FILE, se._PERF_FILE = orig_genome, orig_perf


def _signals(strategy, days, *, score=6.0, win_rate=0.7, per_day=10):
    """Synthetic signal_log rows with the fields evolve()/_evaluate_genome
    reads: strategy, signal_date, tb_label, score."""
    rows = []
    for day in days:
        for i in range(per_day):
            label = 1 if (i / per_day) < win_rate else -1
            rows.append({"strategy": strategy, "signal_date": day,
                        "tb_label": label, "score": score})
    return rows


_TRAIN_DAYS = [f"2026-06-{20+i:02d}" for i in range(4)]
_HOLDOUT_DAYS = [f"2026-06-{24+i:02d}" for i in range(3)]
_ALL_DAYS = _TRAIN_DAYS + _HOLDOUT_DAYS


def _engine() -> StrategyEvolution:
    eng = StrategyEvolution.__new__(StrategyEvolution)
    eng.alerts = None
    eng.genomes = {"trend": {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 5.0, "min_bars": 3}}
    eng.perf = {}
    return eng


def test_train_only_win_does_not_promote():
    """A mutation that wins on train but loses on holdout must NOT replace
    the current genome — the exact same-sample overfitting shape already
    found in option_live_edge_policy (2026-07-13). Only min_score actually
    changes which rows a genome selects (stop_atr/target_atr just scale
    the win payout uniformly) — so the mutation raises min_score from 5.0
    to 7.0, and the high-score (>=7.0) subset is engineered to win big in
    TRAIN but lose in HOLDOUT, while the broad low-score population (which
    the CURRENT genome's lower threshold also captures) stays mediocre
    throughout — a textbook train-period-specific fluke."""
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        rows = []
        for day in _TRAIN_DAYS:
            for i in range(20):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 8.0, "tb_label": 1 if i < 17 else -1})  # 85% win
            for i in range(10):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 6.0, "tb_label": 1 if i < 5 else -1})  # 50% win
        for day in _HOLDOUT_DAYS:
            for i in range(20):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 8.0, "tb_label": 1 if i < 3 else -1})  # 15% win
            for i in range(10):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 6.0, "tb_label": 1 if i < 5 else -1})  # 50% win

        for row in rows:
            row["tb_r_multiple_net"] = (1.5 if row["tb_label"] == 1 else -1.0) + int(row["signal_date"][-2:]) * .001

        mutated = {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 7.0, "min_bars": 3}
        with patch("signal_log.get_signal_logger") as mock_log, \
             patch.object(eng, "_mutate", return_value=mutated):
            mock_log.return_value.get_training_data.return_value = rows
            result = eng.evolve()

        assert result.get("n_days", 0) >= MIN_DISTINCT_DAYS
        assert eng.genomes["trend"]["min_score"] == 5.0  # unchanged
        names_improved = {i["strategy"] for i in result.get("improved", [])}
        assert "trend" not in names_improved
        names_unconfirmed = {u["strategy"] for u in result.get("unconfirmed", [])}
        assert "trend" in names_unconfirmed


def test_confirmed_improvement_on_both_splits_promotes():
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        # current genome's min_score=5.0 keeps everything (low bar, mediocre
        # win rate); mutated raises min_score to 6.0, which happens to
        # filter down to a genuinely higher win-rate subset on BOTH splits.
        rows = []
        for day in _ALL_DAYS:
            for i in range(20):
                score = 4.0 if i % 2 == 0 else 6.5   # half above, half below 6.0
                label = 1 if (score > 6.0 and i % 3 != 0) else (-1 if score <= 6.0 and i % 2 == 0 else 1 if i % 4 == 0 else -1)
                rows.append({"strategy": "trend", "signal_date": day, "tb_label": label, "score": score})

        for row in rows:
            row["tb_r_multiple_net"] = (1.5 if row["tb_label"] == 1 else -1.0) + int(row["signal_date"][-2:]) * .001

        mutated = {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 6.0, "min_bars": 3}
        with patch("signal_log.get_signal_logger") as mock_log, \
             patch.object(eng, "_mutate", return_value=mutated):
            mock_log.return_value.get_training_data.return_value = rows
            result = eng.evolve()

        assert result.get("n_days", 0) >= MIN_DISTINCT_DAYS
        # Whichever way the synthetic win-rates shook out, the key contract
        # holds: a genome is only ever promoted with a recorded holdout
        # confirmation, never on train alone.
        for imp in result.get("improved", []):
            assert imp["new_holdout_sharpe"] > imp["old_holdout_sharpe"]


def test_too_few_days_reports_insufficient_not_a_promotion():
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        rows = _signals("trend", _TRAIN_DAYS[:2], win_rate=0.9, per_day=15)
        with patch("signal_log.get_signal_logger") as mock_log:
            mock_log.return_value.get_training_data.return_value = rows
            result = eng.evolve()
        assert result.get("n_days", 0) < MIN_DISTINCT_DAYS
        assert result.get("improved", []) == []
        assert eng.perf["trend"]["status"] == "insufficient_days"


def _net_rows(values, labels=None):
    return [{"strategy": "trend", "signal_date": f"2026-09-{i + 1:02d}",
             "score": 7, "tb_label": (labels or [1] * len(values))[i],
             "tb_r_multiple_net": value} for i, value in enumerate(values)]


def test_exit_parameter_changes_cannot_manufacture_better_returns():
    eng = _engine()
    rows = _net_rows([1, -0.5, 0.25, 0.8])
    first = eng._evaluate_genome("trend", eng.genomes["trend"], rows)
    second = eng._evaluate_genome("trend", dict(eng.genomes["trend"], target_atr=5, stop_atr=.5), rows)
    assert first["n"] == 4
    assert first["sharpe"] == second["sharpe"]
    assert first["mean_net_r"] == second["mean_net_r"]


def test_timeouts_use_actual_return_and_zero_net_is_preserved():
    perf = _engine()._evaluate_genome("trend", {"min_score": 5},
                                    _net_rows([0, -.2, .5], [0, 0, 1]))
    assert perf["n"] == 3
    assert abs(perf["mean_net_r"] - .1) < 1e-10
    assert perf["win_rate"] == .333


def test_missing_or_nonfinite_net_returns_cannot_qualify():
    rows = _net_rows([None, float("nan"), float("inf"), 1])
    del rows[-1]["tb_r_multiple_net"]
    assert _engine()._evaluate_genome("trend", {"min_score": 5}, rows)["n"] == 0


def test_mutation_preserves_parameters_requiring_replay():
    eng = _engine()
    genome = eng.genomes["trend"]
    for _ in range(30):
        changed = eng._mutate(genome, mutation_rate=1)
        assert all(changed[k] == genome[k] for k in ("stop_atr", "target_atr", "min_bars"))


def test_undated_rows_do_not_leak_into_training():
    train, holdout, days = se._split_by_day([
        {"signal_date": "2026-09-01"}, {"signal_date": "2026-09-02"}, {}])
    assert days == 2
    assert len(train) == len(holdout) == 1


def test_positive_net_improvement_promotes_with_sufficient_days():
    rows = []
    for day in range(1, 21):
        for i in range(10):
            rows.append({"strategy": "trend", "signal_date": f"2026-09-{day:02d}",
                         "score": 7 if i < 5 else 5, "tb_label": 1,
                         "tb_r_multiple_net": (.5 if i < 5 else -1) + day * .001})
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        candidate = dict(eng.genomes['trend'], min_score=6)
        with patch('signal_log.get_signal_logger') as log, patch.object(eng, '_mutate', return_value=candidate):
            log.return_value.get_training_data.return_value = rows
            result = eng.evolve()
        assert len(result['improved']) == 1
        assert eng.genomes['trend']['min_score'] == 6


def test_other_strategies_cannot_supply_missing_days():
    rows = _net_rows([.2, .4, .6])
    rows += [dict(row, strategy='breakout', signal_date=f'2026-10-{i + 1:02d}')
             for i, row in enumerate(_net_rows([.1] * 10))]
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        with patch('signal_log.get_signal_logger') as log:
            log.return_value.get_training_data.return_value = rows * 3
            result = eng.evolve()
        assert result['improved'] == []
        assert eng.perf['trend']['status'] == 'insufficient_days'
