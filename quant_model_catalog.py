"""Validated inventory/templates for the user's 28 quant model groups.

This module imports no brokers, data fetchers or training code. Inventory entries
are specifications, never callables or trading authorization. No backtest runner
is registered until its missing definitions and data/execution contracts exist.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_CATALOG = ROOT / 'quant_model_catalog.json'
KINDS = {'signal_strategy', 'portfolio_allocator', 'predictive_model', 'rl_agent',
         'risk_overlay', 'microstructure_inference', 'optimizer', 'market_making', 'execution_algorithm'}
EXPECTED_POLICY = {'research_only': True, 'auto_schedule': False, 'auto_promote': False,
                   'live_enabled': False, 'require_user_order_confirmation': True,
                   'source_claims_are_validation_evidence': False,
                   'income_target_is_optimization_objective': False}


class SpecificationIncomplete(ValueError):
    """An inventory entry cannot yet be scheduled as an executable experiment."""


def _finite_tree(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Nonfinite numeric parameter')
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def _nonempty_strings(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f'{label} must be a nonempty list of strings')


def _project_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Catalog references must be project-relative paths')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Catalog reference escapes project root')
    return path


def load_catalog(path: str | Path = DEFAULT_CATALOG, *, root: Path = ROOT) -> dict:
    """Read/validate metadata and source provenance, without importing related files."""
    catalog = json.loads(Path(path).read_text(encoding='utf-8'))
    validate_catalog(catalog, root=root)
    return catalog


def validate_catalog(catalog: dict, *, root: Path = ROOT) -> None:
    _finite_tree(catalog)
    if type(catalog.get('schema_version')) is not int or catalog['schema_version'] != 1:
        raise ValueError('Unsupported catalog schema')
    if catalog.get('policy') != EXPECTED_POLICY:
        raise ValueError('Catalog policy must remain research-only with confirmation required')
    source = catalog.get('source', {})
    source_path = _project_path(root, source.get('path'))
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != source.get('sha256'):
        raise ValueError('Source snapshot fingerprint mismatch or source missing')
    if source.get('exhaustive_claim_verified') is not False:
        raise ValueError('The supplied exhaustive claim has not been verified')
    models = catalog.get('models')
    if not isinstance(models, list) or len(models) != 28:
        raise ValueError('Expected exactly the 28 supplied model groups')
    if [m.get('id') for m in models] != [f'Q{i:02d}' for i in range(1, 29)]:
        raise ValueError('Model IDs must be unique and ordered Q01 through Q28')
    for number, model in enumerate(models, 1):
        ident = model['id']
        if type(model.get('number')) is not int or model['number'] != number or model.get('kind') not in KINDS:
            raise ValueError(f'{ident}: invalid number or model kind')
        if not model.get('name') or not model.get('supplied_definition'):
            raise ValueError(f'{ident}: supplied definition required')
        if (model.get('enabled') is not False or model.get('live_approved') is not False
                or model.get('adapter') is not None or model.get('implementation_status') != 'SPECIFICATION_ONLY'):
            raise ValueError(f'{ident}: specifications cannot enable or register execution')
        if model.get('source_verification') != 'USER_SUPPLIED_UNVERIFIED':
            raise ValueError(f'{ident}: original research attribution is unverified')
        if model.get('related_files_are_exact_implementations') is not False:
            raise ValueError(f'{ident}: related modules are not verified exact implementations')
        if type(model.get('options_translation_required')) is not bool or model.get('options_contract_spec') is not None:
            raise ValueError(f'{ident}: options translation is not yet specified')
        _nonempty_strings(model.get('missing_definitions'), f'{ident} missing definitions')
        _nonempty_strings(model.get('data_requirements'), f'{ident} data requirements')
        if not isinstance(model.get('parameters'), dict):
            raise ValueError(f'{ident}: parameters must be a mapping')
        variants = model.get('variants')
        if not isinstance(variants, list) or not variants:
            raise ValueError(f'{ident}: named variants required')
        names = [v.get('id') for v in variants]
        if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
            raise ValueError(f'{ident}: variant IDs must be unique')
        if any(not isinstance(v.get('parameters'), dict) for v in variants):
            raise ValueError(f'{ident}: variant parameters must be mappings')
        grid = model.get('parameter_grid')
        if not isinstance(grid, dict) or any(not isinstance(v, list) or not v for v in grid.values()):
            raise ValueError(f'{ident}: grid dimensions must contain values')
        for values in grid.values():
            canonical = [json.dumps(v, sort_keys=True) for v in values]
            if len(set(canonical)) != len(canonical):
                raise ValueError(f'{ident}: duplicate grid values would double-count trials')
        if grid_size(model) > 100000:
            raise ValueError(f'{ident}: declared grid exceeds inventory budget')
        references = model.get('related_local_files')
        if not isinstance(references, list):
            raise ValueError(f'{ident}: related files must be listed explicitly')
        for relative in references:
            if not _project_path(root, relative).is_file():
                raise ValueError(f'{ident}: related file missing: {relative}')
    _nonempty_strings(catalog.get('common_requirements'), 'Common requirements')
    _nonempty_strings(catalog.get('options_requirements'), 'Options requirements')


def grid_size(model: dict) -> int:
    return math.prod(len(values) for values in model['parameter_grid'].values())


def declared_configurations(model: dict) -> Iterable[dict]:
    """Finite metadata enumeration; no fitting, backtest, dynamic import or orders."""
    keys = sorted(model['parameter_grid'])
    values = [model['parameter_grid'][key] for key in keys]
    for variant in model['variants']:
        for coordinates in itertools.product(*values):
            yield {'model_id': model['id'], 'variant': variant['id'],
                   'parameters': deepcopy(model['parameters']),
                   'variant_parameters': deepcopy(variant['parameters']),
                   'grid_parameters': dict(zip(keys, coordinates)),
                   'research_only': True}


def configuration_fingerprint(model: dict, config: dict, source_sha256: str) -> str:
    """Versioned specification identity, independent of JSON key insertion order."""
    payload = {'definition': model['supplied_definition'], 'config': config,
               'source_sha256': source_sha256, 'missing_definitions': model['missing_definitions']}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def build_integration_plan(catalog: dict, *, available_data: Iterable[str] = ()) -> dict:
    """Describe blockers. Supplied capability names are declarations, not a data audit."""
    if isinstance(available_data, (str, bytes)):
        raise ValueError('available_data must be a collection of capability names')
    capabilities = set(available_data)
    if any(not isinstance(x, str) or not x for x in capabilities):
        raise ValueError('Invalid data capability name')
    rows = []
    for model in catalog['models']:
        rows.append({'id': model['id'], 'name': model['name'], 'kind': model['kind'],
                     'named_variants': len(model['variants']),
                     'grid_points_per_variant': grid_size(model),
                     'declared_configurations': len(model['variants']) * grid_size(model),
                     'status': 'SPECIFICATION_INCOMPLETE',
                     'missing_definitions': deepcopy(model['missing_definitions']),
                     'missing_declared_data': sorted(set(model['data_requirements']) - capabilities),
                     'common_requirements': deepcopy(catalog['common_requirements']),
                     'options_requirements': deepcopy(catalog['options_requirements']) if model['options_translation_required'] else [],
                     'related_local_files': list(model['related_local_files']),
                     'adapter': None, 'backtest_schedulable': False, 'live_approved': False})
    return {'as_of': catalog['as_of'], 'source_sha256': catalog['source']['sha256'],
            'group_count': len(rows), 'named_variant_count': sum(r['named_variants'] for r in rows),
            'declared_configuration_count': sum(r['declared_configurations'] for r in rows),
            'configurations_evaluated': 0, 'capabilities_are_verified': False,
            'kind_counts': dict(Counter(r['kind'] for r in rows)),
            'runnable_backtests': 0, 'live_eligible': 0,
            'probability_of_monthly_target': None, 'models': rows,
            'supplemental_catalogs': [
                {'name':'Pivot Boss', 'path':'pivot_boss_research_catalog.json',
                 'entry_count':25, 'entries_are_independent_alpha_strategies':False,
                 'auto_schedule':False, 'live_approved':False}],
            'research_adaptations': [
                {'model_id': ident, 'adapter': 'quant_research_adapters.backtest',
                 'evidence_kind': 'UNDERLYING_PROXY', 'options_translation_ready': False,
                 'source_replica': False, 'auto_schedule': False, 'live_approved': False}
                for ident in ('Q01', 'Q06')]}


def get_research_adapter(model_id: str):
    """Only explicitly frozen project adaptations; never the original source replica."""
    if model_id not in {'Q01', 'Q06'}:
        raise SpecificationIncomplete(f'{model_id}: no research adaptation implemented')
    from functools import partial
    from quant_research_adapters import backtest
    return partial(backtest, model_id)


def require_backtest_adapter(model_id: str, *, catalog: dict | None = None):
    """Explicit failure instead of silently treating an incomplete model as WAIT."""
    catalog = load_catalog() if catalog is None else catalog
    model = next((m for m in catalog['models'] if m['id'] == model_id), None)
    if model is None:
        raise KeyError(f'Unknown model: {model_id}')
    raise SpecificationIncomplete(f"{model_id} is specification-only: " + '; '.join(model['missing_definitions']))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = build_integration_plan(load_catalog(args.catalog))
    rendered = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
