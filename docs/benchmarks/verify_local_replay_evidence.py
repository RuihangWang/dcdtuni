"""Verify transferred replay evidence without importing or running a solver.

The two original browser archives predate automatic runtime sidecars. Derived
posthoc records explicitly identify that provenance; they are not represented
as files produced by the original live run. All original inputs, checkpoints
and physical results remain read-only. Uses only Python's standard library.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIELDS = (
    'node_pressure_Pa', 'edge_flow_kg_s', 'node_temperature_K', 'chip_temp_K',
    'cell_temp_K', 'primary_return_K', 'secondary_supply_K', 'edge_outlet_temp_K',
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    def reject(value):
        raise ValueError(f'Non-finite JSON constant {value} in {path}')
    return json.loads(path.read_text(), parse_constant=reject)


def config_hash(manifest, controllers):
    value = {'liquidrun': manifest, 'controllers': controllers}
    data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def scalar_count(value):
    if isinstance(value, list):
        return sum(scalar_count(child) for child in value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Physical field contains a non-finite or nonnumeric value')
    return 1


def write_derived(path, value):
    """Create new evidence, or verify an identical previously created record."""
    data = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    if path.exists():
        if path.read_text() != data:
            raise ValueError(f'Existing evidence differs; refusing to replace {path}')
    else:
        path.write_text(data)


def main():
    previous = read(ROOT / 'docs/benchmarks/accepted-input-replay-2026-09-26.json')
    rows = []
    for item in previous['cases']:
        executed_path = ROOT / item['executed_input']
        original_dir = executed_path.parent
        replay_result_path = ROOT / item['replay_result']
        replay_dir = replay_result_path.parent
        originals = [original_dir / name for name in
                     ('input.liquidrun', 'input.json', 'checkpoint.json', 'result.json')]
        before = {str(path.relative_to(ROOT)): digest(path) for path in originals}
        checkpoint = read(original_dir / 'checkpoint.json')
        original = read(original_dir / 'result.json')
        executed = read(executed_path)
        replay = read(replay_result_path)
        replay_input = read(replay_dir / 'input.liquidrun')
        replay_checkpoint = read(replay_dir / 'checkpoint.json')

        assert digest(executed_path) == item['executed_sha256']
        assert original['run_id'] == checkpoint['run_id'] == item['original_run_id']
        assert replay['run_id'] == replay_checkpoint['run_id'] == item['replay_run_id']
        assert original['config_hash'] == checkpoint['config_hash'] == config_hash(
            checkpoint['liquidrun'], checkpoint['controllers'])
        assert replay['config_hash'] == replay_checkpoint['config_hash'] == config_hash(executed, [])
        assert replay_input == executed  # Byte formatting need not be identical.
        assert executed['source_project'] == checkpoint['liquidrun']['source_project']
        assert executed['events'] == []
        assert original['time_s'] == replay['time_s']
        assert len(original['time_s']) == executed['time']['N_t'] == item['samples']
        assert original['time_s'][-1] == item['horizon_s']
        assert checkpoint['seq'] + 1 == item['samples']
        assert checkpoint['heat_multipliers'] == original['heat_multipliers']
        assert len(original['heat_multipliers']) == item['samples']
        assert original['converged'] and replay['converged']

        checked = {}
        for field in FIELDS:
            count = scalar_count(original[field])
            assert count == scalar_count(replay[field])
            assert original[field] == replay[field], f'{item["case"]}: {field} differs'
            checked[field] = {'exactly_equal': True, 'finite_scalar_count': count}

        artifact_paths = originals + [executed_path] + [replay_dir / name for name in
            ('input.liquidrun', 'executed.liquidrun', 'checkpoint.json', 'result.json')]
        sidecar = original_dir / 'accepted-input-provenance.posthoc.json'
        evidence = {
            'format': 'dcdtuni.accepted-input-provenance/v1',
            'provenance_kind': 'posthoc_archive_verification',
            'verification_date_sgt': '2026-09-26',
            'original_runtime_sidecar_present': (original_dir / 'executed-inputs.json').exists(),
            'original_run_id': original['run_id'], 'replay_run_id': replay['run_id'],
            'source_project': executed['source_project'],
            'original_config_hash': original['config_hash'],
            'executed_config_hash': replay['config_hash'],
            'accepted_sample_count': item['samples'],
            'original_sample_count': checkpoint['liquidrun']['time']['N_t'],
            'accepted_time_s': original['time_s'][-1],
            'complete_original_horizon': item['samples'] == checkpoint['liquidrun']['time']['N_t'],
            'accepted_heat_multipliers': original['heat_multipliers'],
            'scope': 'Accepted inputs only; pending commands and equilibrium queries excluded',
            'artifacts_sha256': {str(path.relative_to(ROOT)): digest(path) for path in artifact_paths},
            'physical_field_checks': checked,
            'numerical_solver_executed_by_this_verifier': False,
            'verifier_sha256': digest(Path(__file__)),
        }
        write_derived(sidecar, evidence)
        assert before == {str(path.relative_to(ROOT)): digest(path) for path in originals}
        rows.append({'case': item['case'], 'posthoc_provenance': str(sidecar.relative_to(ROOT)),
                     'posthoc_sha256': digest(sidecar), 'samples': item['samples'],
                     'horizon_s': item['horizon_s'], 'physical_field_checks': checked,
                     'original_artifacts_unchanged': True})

    report = {
        'status': 'passed', 'scope': 'Local inspection of transferred accepted-input replay artifacts',
        'numerical_solver_executed': False, 'gpu_accessed': False,
        'verifier_sha256': digest(Path(__file__)), 'cases': rows,
        'limits': ['Posthoc provenance is not an original runtime-generated sidecar',
                   'This verifies archived evidence; it does not perform a new numerical solve'],
    }
    path = ROOT / 'docs/benchmarks/local-replay-evidence-2026-09-26.json'
    write_derived(path, report)
    print(json.dumps({'status': report['status'], 'cases': len(rows),
                      'fields_per_case': len(FIELDS), 'report': str(path)}))


if __name__ == '__main__':
    main()
