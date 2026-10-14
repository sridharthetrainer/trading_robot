import json
from pathlib import Path
import pytest
from system_strategy_research import source_inventory, build_system_research


def test_inventory_parses_without_importing_backtest(tmp_path):
    (tmp_path / 'backtest_example.py').write_text("raise RuntimeError('must not import')\ndef backtest_example(data, symbol):\n    pass\n")
    (tmp_path / 'signal_engine.py').write_text('STRATEGIES = [run_trend_strategy, *([run_orb_strategy] if ENABLED else [])]')
    result = source_inventory(tmp_path)
    assert result['backtest_modules'][0]['entry_points'][0]['arguments'] == ['data', 'symbol']
    assert result['signal_registry_declared_functions'] == ['run_orb_strategy', 'run_trend_strategy']
    assert not result['source_errors']


def test_default_tuning_covers_every_registered_adapter(tmp_path, monkeypatch):
    import autonomous_param_trainer as trainer
    monkeypatch.delenv('PARAM_TRAIN_STRATEGIES', raising=False)
    monkeypatch.setenv('AUTONOMOUS_KNOWLEDGE_DB', str(tmp_path / 'knowledge.db'))
    report = trainer.run_autonomous_param_training(symbols=['NIFTY'], max_runs=100, dry_run=True, write=False)
    assert {r['strategy'] for r in report['planned']} == set(trainer.STRATEGY_BACKTESTS)
    assert 'fibonacci' in {r['strategy'] for r in report['planned']}


def test_explicit_empty_strategy_selection_does_not_train_defaults(tmp_path, monkeypatch):
    from autonomous_param_trainer import run_autonomous_param_training
    monkeypatch.setenv('AUTONOMOUS_KNOWLEDGE_DB', str(tmp_path / 'knowledge.db'))
    assert not run_autonomous_param_training(strategies=[], dry_run=True, write=False)['planned']


def test_report_exposes_research_status_and_does_not_claim_returns(tmp_path):
    output = tmp_path / 'report.json'
    report = build_system_research(dry_run=True, write=True, output=output)
    assert report['totals']['tunable_families'] == 31
    assert report['totals']['option_catalog_entries'] == 42
    assert report['totals']['option_implemented'] == 42
    assert report['totals']['option_research_hypotheses'] == 39
    assert report['totals']['option_placeholders'] == 0
    assert not report['income_target_supported'] and not report['live_approved']
    assert report['income_research'] is None
    assert not output.exists()
    assert not report['backtests_needing_validation_adapter']
    assert 'backtest_iron_condor.py' in report['quarantined_deprecated_backtests']


def test_option_learning_preserves_zero_net_and_ignores_missing_costs(tmp_path):
    import sqlite3
    from datetime import datetime
    from option_multistrike_signals import ensure_multistrike_schema
    from option_strike_autotune import _load_live_strike_outcomes
    db = tmp_path / 'signals.db'
    with sqlite3.connect(db) as conn:
        ensure_multistrike_schema(conn)
        for strike, net in [(24000, 0), (24050, None)]:
            conn.execute('INSERT INTO option_strike_signals (ts,flow,signal,direction,snapshot_time,underlying,expiry,strike,option_type,source,price,entry_price,outcome_label,gross_pnl,net_pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (datetime.now().timestamp(),'MOMENTUM','BUY','BULLISH',datetime.now().isoformat(),'NIFTY','2026-10-20',strike,'CE','angel',100,100,1,500,net))
    rows = _load_live_strike_outcomes(str(db))
    assert len(rows) == 1
    assert rows[0]['outcome']['pnl'] == 0


def test_option_weights_use_profit_after_costs_instead_of_target_label():
    from option_strike_autotune import _outcome
    assert _outcome({'outcome': {'label': 1, 'pnl': -20}}) == (False, -20)
    assert _outcome({'outcome': {'label': 1, 'pnl': 0}}) == (False, 0)
    assert _outcome({'outcome': {'label': 1}}) is None
    assert _outcome({'outcome': {'label': 1, 'pnl': float('nan')}}) is None


def test_bounded_grid_is_reproducible_and_counts_trials():
    import pandas as pd
    from validation_harness import _run_wf_grid
    index = pd.DatetimeIndex([day + pd.Timedelta(minutes=5 * bar) for day in pd.bdate_range('2025-01-01', periods=20) for bar in range(10)])
    data = pd.DataFrame({'Close': 100}, index=index)
    observed = []
    def backtest(**kw):
        observed.append(kw['parameter'])
        return {'total_pnl': kw['parameter'], 'sharpe': 1, 'win_rate': .6, 'num_trades': 30}
    result = _run_wf_grid(backtest, data, {'parameter': list(range(10))}, 3, 3, 3, 5, 500000, 'NIFTY', max_grid_combinations=2)
    assert set(observed) == {0, 9}
    assert result[2] == len(result[1]) * 2
    assert result[0] == {'parameter': 9}


def test_learning_cycle_wires_readonly_coverage_summary(monkeypatch):
    import autonomous_learning_cycle as cycle
    original_step = cycle._step
    monkeypatch.setattr(cycle, '_step', lambda name, fn: original_step(name, fn) if name in {'system_strategy_research', 'knowledge_memory', 'quality_gate'} else {'ok': True, 'result': {}})
    report = cycle.run_autonomous_learning_cycle(dry_run=True)
    result = report['steps']['system_strategy_research']['result']
    assert result['totals']['tunable_families'] == 31
    assert not result['income_target_supported']
    assert 'strategy coverage=' in cycle.render_summary(report)
