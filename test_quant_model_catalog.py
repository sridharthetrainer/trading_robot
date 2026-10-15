import copy
import hashlib
import json
import math
import re

import pytest

from quant_model_catalog import (
    DEFAULT_CATALOG, ROOT, SpecificationIncomplete, build_integration_plan,
    configuration_fingerprint, declared_configurations, grid_size, load_catalog,
    main, require_backtest_adapter, validate_catalog,
)


@pytest.fixture
def catalog():
    return load_catalog()


def test_all_supplied_headings_are_preserved(catalog):
    source = (ROOT / catalog['source']['path']).read_text()
    headings = re.findall(r'^#### \*\*(\d+)\. (.*?)\*\*\s*$', source, re.M)
    assert len(headings) == 28
    for model, (number, title) in zip(catalog['models'], headings):
        assert model['number'] == int(number)
        assert model['name'] == title
        assert f'#### **{number}. {title}**' in model['supplied_definition']
    assert hashlib.sha256((ROOT / catalog['source']['path']).read_bytes()).hexdigest() == catalog['source']['sha256']


def test_families_and_execution_are_not_counted_as_complete_alpha(catalog):
    plan = build_integration_plan(catalog)
    assert plan['group_count'] == 28
    assert plan['named_variant_count'] == 60
    assert plan['declared_configuration_count'] == 86
    assert plan['kind_counts']['signal_strategy'] == 14
    assert plan['models'][23]['kind'] == 'risk_overlay'
    assert plan['models'][27]['kind'] == 'execution_algorithm'
    assert plan['configurations_evaluated'] == plan['runnable_backtests'] == plan['live_eligible'] == 0
    assert plan['probability_of_monthly_target'] is None
    assert all(not r['backtest_schedulable'] and not r['live_approved'] for r in plan['models'])


@pytest.mark.parametrize('key,value', [
    ('research_only', False), ('auto_schedule', True), ('auto_promote', True),
    ('live_enabled', True), ('require_user_order_confirmation', False),
    ('source_claims_are_validation_evidence', True), ('income_target_is_optimization_objective', True),
])
def test_inventory_cannot_override_execution_policy(catalog, key, value):
    catalog['policy'][key] = value
    with pytest.raises(ValueError, match='policy'):
        validate_catalog(catalog)


@pytest.mark.parametrize('key,value', [
    ('enabled', True), ('live_approved', True), ('adapter', 'angel.place_order'),
    ('implementation_status', 'IMPLEMENTED'),
])
def test_model_cannot_register_a_callable_or_live_strategy(catalog, key, value):
    catalog['models'][0][key] = value
    with pytest.raises(ValueError, match='cannot enable'):
        validate_catalog(catalog)


def test_bad_source_fingerprint_rejected(catalog):
    catalog['source']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='fingerprint'):
        validate_catalog(catalog)


@pytest.mark.parametrize('relative', ['../outside.txt', '/tmp/outside.txt'])
def test_catalog_paths_cannot_escape_project(catalog, relative):
    catalog['source']['path'] = relative
    with pytest.raises(ValueError, match='relative|escapes'):
        validate_catalog(catalog)


def test_missing_or_duplicate_group_rejected(catalog):
    catalog['models'][1]['id'] = 'Q01'
    with pytest.raises(ValueError, match='unique'):
        validate_catalog(catalog)
    catalog['models'].pop()
    with pytest.raises(ValueError, match='28'):
        validate_catalog(catalog)


def test_missing_definitions_cannot_be_silently_emptied(catalog):
    catalog['models'][0]['missing_definitions'] = []
    with pytest.raises(ValueError, match='missing definitions'):
        validate_catalog(catalog)


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_nonfinite_parameters_rejected(catalog, value):
    catalog['models'][0]['parameters']['adx_min'] = value
    with pytest.raises(ValueError, match='Nonfinite'):
        validate_catalog(catalog)


def test_ols_declared_grid_has_27_unique_metadata_configurations(catalog):
    model = catalog['models'][10]
    assert grid_size(model) == 27
    configs = list(declared_configurations(model))
    fingerprints = {configuration_fingerprint(model, c, catalog['source']['sha256']) for c in configs}
    assert len(fingerprints) == len(configs) == 27
    assert {c['grid_parameters']['ols_window'] for c in configs} == {50, 100, 200}
    assert {c['grid_parameters']['entry_z'] for c in configs} == {2., 3., 4.}
    assert all(c['research_only'] for c in configs)


def test_duplicate_or_empty_grid_is_not_a_new_trial(catalog):
    model = catalog['models'][10]
    model['parameter_grid']['entry_z'] = [2., 2.]
    with pytest.raises(ValueError, match='double-count'):
        validate_catalog(catalog)
    model['parameter_grid']['entry_z'] = []
    with pytest.raises(ValueError, match='grid dimensions'):
        validate_catalog(catalog)


def test_configuration_identity_is_stable_but_versioned(catalog):
    model = catalog['models'][10]
    config = next(iter(declared_configurations(model)))
    first = configuration_fingerprint(model, config, catalog['source']['sha256'])
    reordered = dict(reversed(list(config.items())))
    assert first == configuration_fingerprint(model, reordered, catalog['source']['sha256'])
    changed = copy.deepcopy(config)
    changed['grid_parameters']['entry_z'] = 9
    assert first != configuration_fingerprint(model, changed, catalog['source']['sha256'])
    assert first != configuration_fingerprint(model, config, 'other source revision')


def test_metadata_enumeration_does_not_mutate_catalog(catalog):
    model = catalog['models'][0]
    emitted = next(iter(declared_configurations(model)))
    emitted['parameters']['sma_periods'].append(999)
    assert model['parameters']['sma_periods'] == [10, 20, 50, 100, 200]


def test_data_declarations_cannot_make_specs_runnable(catalog):
    all_data = {x for m in catalog['models'] for x in m['data_requirements']}
    report = build_integration_plan(catalog, available_data=all_data)
    assert all(not r['missing_declared_data'] for r in report['models'])
    assert not report['capabilities_are_verified']
    assert report['runnable_backtests'] == 0
    with pytest.raises(ValueError):
        build_integration_plan(catalog, available_data='ohlc')


def test_all_unimplemented_requests_fail_explicitly(catalog):
    for model in catalog['models']:
        with pytest.raises(SpecificationIncomplete, match=model['id']):
            require_backtest_adapter(model['id'], catalog=catalog)
    with pytest.raises(KeyError):
        require_backtest_adapter('not-a-model', catalog=catalog)


def test_options_translation_cannot_be_assumed(catalog):
    plan = build_integration_plan(catalog)
    for model in plan['models'][:14]:
        assert model['options_requirements']
        assert 'Exact underlying/contract/expiry/strike selection' in model['options_requirements']
    assert all(m['options_contract_spec'] is None for m in catalog['models'])


def test_inventory_cli_writes_only_requested_report(tmp_path):
    path = tmp_path / 'out' / 'plan.json'
    assert main(['--output', str(path)]) == 0
    report = json.loads(path.read_text())
    assert report['group_count'] == 28 and report['configurations_evaluated'] == 0
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*') if p.is_file()) == ['out/plan.json']


def test_trainer_inventory_never_starts_training_or_registers_models(monkeypatch, capsys, tmp_path):
    import autonomous_param_trainer as trainer
    before = dict(trainer.STRATEGY_BACKTESTS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(trainer, 'run_autonomous_param_training', lambda **kw: pytest.fail('inventory started training'))
    assert trainer.main(['--quant-inventory']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['group_count'] == 28
    assert report['configurations_evaluated'] == 0
    assert trainer.STRATEGY_BACKTESTS == before
    assert not list(tmp_path.iterdir())
