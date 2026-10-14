"""Unified strategy coverage and income-target research. Never places orders."""
from __future__ import annotations
import argparse
import ast
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
REPORT = 'reports/system_strategy_research/summary.json'


def source_inventory(root=ROOT):
    root = Path(root)
    backtests, errors = [], []
    for path in sorted(root.glob('backtest*.py')):
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
            functions = [{'function': n.name,
                          'arguments': [a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs]}
                         for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name.startswith('backtest')]
            backtests.append({'source': path.name, 'sha256': hashlib.sha256(source.encode()).hexdigest(),
                              'entry_points': functions,
                              'status': 'SOURCE_PRESENT_NOT_VALIDATED'})
        except (OSError, SyntaxError) as exc:
            errors.append({'source': path.name, 'error': str(exc)})
    strategies = []
    path = root / 'signal_engine.py'
    if path.exists():
        try:
            for node in ast.parse(path.read_text(encoding='utf-8')).body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'STRATEGIES' for t in node.targets):
                    strategies = sorted({n.id for n in ast.walk(node.value) if isinstance(n, ast.Name) and n.id.startswith('run_')})
        except (OSError, SyntaxError) as exc:
            errors.append({'source': path.name, 'error': str(exc)})
    return {'backtest_modules': backtests, 'signal_registry_declared_functions': strategies,
            'registry_names_are_conditional_not_runtime_availability': True, 'source_errors': errors}


def build_system_research(*, dry_run=False, write=False, db_path='signal_log.db', as_of=None,
                          capital=500000, monthly_target=30000, output=REPORT):
    from autonomous_param_trainer import STRATEGY_BACKTESTS
    from option_strategy_registry import catalog_readiness
    from validation_harness import _build_default_param_grid, WF_TRAIN_DAYS, WF_TEST_DAYS, WF_MIN_WINDOWS, HOLDOUT_RATIO
    from autonomous_knowledge import ExperimentMemory
    from research_backtest_adapters import adapter_metadata, DEPRECATED
    from income_target_research import build_report, target_requirements, load_json
    inventory = source_inventory()
    catalog = catalog_readiness()
    adapters = []
    for name, (module, function) in STRATEGY_BACKTESTS.items():
        source = next((r for r in inventory['backtest_modules'] if r['source'] == module + '.py'), {})
        grid = _build_default_param_grid(name)
        combinations = 1
        for values in grid.values():
            combinations *= len(values)
        entry = next((f for f in source.get('entry_points', []) if f['function'] == function), None)
        required = set(grid) | {'symbol', 'data', 'initial_capital', 'interval_minutes', 'verbose', 'close_at_end'}
        missing_arguments = sorted(required - set(entry['arguments'])) if entry else sorted(required)
        adaptation = adapter_metadata(name)
        available = entry is not None and (not missing_arguments or adaptation is not None)
        adapters.append({'strategy': name, 'module': module, 'function': function,
                         'source_sha256': source.get('sha256'), 'grid_combinations': combinations if grid else 0,
                         'missing_adapter_arguments': missing_arguments,
                         'adapter_transforms_arguments': adaptation is not None,
                         'capabilities': adaptation or {'evidence_kind': 'UNDERLYING_PROXY'},
                         'tuning_available': bool(available and grid),
                         'live_approved': False})
    research = None if dry_run else build_report(db_path=db_path, as_of=as_of, capital=capital, monthly_target=monthly_target)
    live_manifest = load_json('live_eligibility.json') if not dry_run else {}
    experiments = {}
    obsolete_attempts = []
    if not dry_run:
        for row in ExperimentMemory(os.getenv('AUTONOMOUS_KNOWLEDGE_DB', 'autonomous_knowledge.db')).history():
            metadata=adapter_metadata(row['strategy'])
            if metadata is not None and row['interval']!=metadata['interval']:
                obsolete_attempts.append({'symbol':row['symbol'],'strategy':row['strategy'],'interval':row['interval'],
                                          'reason':'superseded_timeframe_contract','required_interval':metadata['interval']})
                continue
            experiments[(row['symbol'], row['strategy'], row['interval'])] = json.loads(row['result_json'])
    covered_modules = {r['module'] + '.py' for r in adapters}
    unsupported = [r['source'] for r in inventory['backtest_modules']
                   if r['entry_points'] and r['source'] not in covered_modules and r['source'] not in DEPRECATED]
    report = {'generated_at': datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),
              'ok': not inventory['source_errors'] and all(r['tuning_available'] for r in adapters),
              'ok_scope': 'source_inventory_and_research_adapter_contracts; not live or profit approval',
              'mode': 'RESEARCH_ONLY', 'dry_run': dry_run,
              'target': target_requirements(capital, monthly_target),
              'validation_data_requirements': {
                  'development_train_sessions': WF_TRAIN_DAYS,
                  'development_test_sessions_per_window': WF_TEST_DAYS,
                  'minimum_windows': WF_MIN_WINDOWS,
                  'holdout_fraction': HOLDOUT_RATIO,
                  'approximate_minimum_total_sessions': math.ceil((WF_TRAIN_DAYS + WF_TEST_DAYS * WF_MIN_WINDOWS) / (1 - HOLDOUT_RATIO)),
                  'default_calendar_lookback_days': 400,
                  'note': 'Approximate complete-session requirement; gaps, missing bars and insufficient trades may require more data.'},
              'inventory': inventory, 'tuning_adapters': adapters, 'option_catalog': catalog,
              'backtests_needing_validation_adapter': unsupported,
              'quarantined_deprecated_backtests': DEPRECATED,
              'last_parameter_training': load_json('autonomous_param_training_report.json') if not dry_run else {},
              'latest_parameter_experiments': list(experiments.values()),
              'obsolete_timeframe_attempts': obsolete_attempts,
              'option_feature_weight_candidate': {'source': 'reports/system_strategy_research/option_feature_weight_candidate.json',
                                                 'research_only': True, 'active_weights_replaced': False},
              'other_learning_tracks': {
                  'option_feature_weights': {'source': 'option_strike_autotune.json', 'scheduler': 'autonomous_learning_cycle', 'scope': 'existing bounded feature-weight learning'},
                  'strategy_combinations': {'sources': ['strategy_pair_edge_report.json', 'strategy_triple_edge_report.json'], 'scope': 'exploratory combination evidence; no automatic promotion'},
                  'pairs': {'source': 'pairs_validation_report.json', 'scope': 'statistical arbitrage validation'},
                  'ml': {'sources': ['ml_pipeline_last_run.json', 'training_contract_audit.json', 'prospective_execution_report.json'], 'scope': 'existing model pipeline; preserve prospective freeze'},
              },
              'income_research': research, 'income_target_supported': False,
              'live_eligibility': {'generated_at': live_manifest.get('generated_at'),
                                  'strategies': live_manifest.get('strategies', {}),
                                  'live_ready_count': live_manifest.get('live_ready_count', 0)},
              'live_approved': False,
              'totals': {'backtest_modules': len(inventory['backtest_modules']),
                         'declared_signal_functions': len(inventory['signal_registry_declared_functions']),
                         'tunable_families': sum(r['tuning_available'] for r in adapters),
                         'option_catalog_entries': len(catalog),
                         'option_implemented': sum(r['logic_status'] in {'IMPLEMENTED','RESEARCH_IMPLEMENTED'} for r in catalog),
                         'option_research_hypotheses': sum(r['logic_status'] == 'RESEARCH_IMPLEMENTED' for r in catalog),
                         'option_placeholders': sum(r['logic_status'] not in {'IMPLEMENTED','RESEARCH_IMPLEMENTED'} for r in catalog),
                         'observed_signal_strategies': research['scope']['signal_strategies_with_clean_outcomes'] if research else None,
                         'live_manifest_strategies': live_manifest.get('total_strategies'),
                         'paper_candidates': len(research.get('paper_shortlist', [])) if research else None},
              'policy': 'Test all compatible families within the existing daily budget; stage parameters; require independent forward evidence; do not raise risk to meet an income target.'}
    if write and not dry_run:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            temporary = handle.name
        os.replace(temporary, path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--db', default='signal_log.db')
    parser.add_argument('--output', default=REPORT)
    args = parser.parse_args()
    report = build_system_research(dry_run=args.dry_run, write=not args.dry_run, db_path=args.db, output=args.output)
    print(json.dumps({'ok': report['ok'], 'totals': report['totals'], 'income_target_supported': False, 'report': args.output if not args.dry_run else None}, indent=2))
    return 0 if report['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
