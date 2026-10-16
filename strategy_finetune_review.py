"""Read-only score-threshold research across recorded strategies.

Uses chronological train/holdout evidence and recorded net R. Produces a
review artifact only; never changes genomes, runtime parameters or orders.
Stops, targets and indicator settings require candle replay instead.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from strategy_evolution import (
    DEFAULT_GENOMES, MIN_DISTINCT_DAYS, MIN_TRAIN_N, MIN_HOLDOUT_N,
    StrategyEvolution, _split_by_day,
)


def review(rows, genomes=None):
    genomes = genomes or DEFAULT_GENOMES
    engine = StrategyEvolution.__new__(StrategyEvolution)
    results = []
    names = sorted(set(genomes) | {r['strategy'] for r in rows if r.get('strategy')})
    for name in names:
        genome = dict(genomes.get(name, {'min_score': 5.0}))
        subset = [r for r in rows if r.get('strategy') == name]
        train, holdout, days = _split_by_day(subset)
        baseline_train = engine._evaluate_genome(name, genome, train, MIN_TRAIN_N)
        baseline_test = engine._evaluate_genome(name, genome, holdout, MIN_HOLDOUT_N)
        row = {'strategy': name, 'recorded_rows': len(subset), 'days': days,
               'baseline_train': baseline_train, 'baseline_holdout': baseline_test,
               'status': 'insufficient_evidence'}
        if (days >= MIN_DISTINCT_DAYS and baseline_train['n'] >= MIN_TRAIN_N
                and baseline_test['n'] >= MIN_HOLDOUT_N and baseline_test['days'] >= 3):
            # Select on train only. Holdout is evaluated once for the winner.
            best, best_perf = genome, baseline_train
            for threshold in [3 + i * .5 for i in range(11)]:
                candidate = dict(genome, min_score=threshold)
                perf = engine._evaluate_genome(name, candidate, train, MIN_TRAIN_N)
                if perf['n'] >= MIN_TRAIN_N and perf['sharpe'] > best_perf['sharpe']:
                    best, best_perf = candidate, perf
            row['status'] = 'no_train_improvement'
            if best != genome:
                test = engine._evaluate_genome(name, best, holdout, MIN_HOLDOUT_N)
                confirms = (best_perf['sharpe'] > 0 and best_perf['mean_net_r'] > 0
                            and test['n'] >= MIN_HOLDOUT_N and test['days'] >= 3
                            and test['mean_net_r'] > 0
                            and test['sharpe'] > max(0, baseline_test['sharpe']))
                row.update(status='candidate_requires_forward_validation' if confirms else 'holdout_rejected',
                           candidate=best, candidate_train=best_perf, candidate_holdout=test)
        results.append(row)
    return {'mode': 'research_only', 'evaluation_basis': 'recorded_net_r',
            'limitations': ['Score thresholds only; exits and indicators require candle replay.',
                            'Repeated historical reviews do not provide fresh forward evidence.',
                            'Recorded net R relies on upstream cost and label quality.'],
            'strategies': results,
            'counts': {status: sum(r['status'] == status for r in results)
                       for status in sorted({r['status'] for r in results})}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='signal_log.db')
    parser.add_argument('--output', default='reports/strategy_finetune_review.json')
    args = parser.parse_args()
    # SQLite read-only mode prevents accidental database creation or mutation.
    with sqlite3.connect(Path(args.db).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table = 'signal_log' if 'signal_log' in tables else 'signals'
        if table not in tables:
            raise ValueError('signal log table not found')
        rows = [dict(r) for r in conn.execute(
            f'SELECT strategy, signal_date, score, tb_label, tb_r_multiple_net FROM {table} '
            'WHERE training_eligible=1 AND tb_label IN (-1,0,1) '
            'AND stop_loss>0 AND target>0 AND rr>0 ORDER BY signal_date, signal_time')]
    genomes = dict(DEFAULT_GENOMES)
    genome_file = Path('strategy_genomes.json')
    if genome_file.exists():
        genomes.update(json.loads(genome_file.read_text()))
    report = review(rows, genomes)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'strategies': len(report['strategies']), 'counts': report['counts'],
                      'report': str(output)}))


if __name__ == '__main__':
    main()
