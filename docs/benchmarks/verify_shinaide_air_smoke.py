"""Verify archived Shinaide OpenFOAM smoke artifacts without running a solver."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def changed_paths(left, right, prefix='') -> set[str]:
    if left == right:
        return set()
    if isinstance(left, dict) and isinstance(right, dict):
        return set().union(*(changed_paths(left.get(key), right.get(key),
                                            f'{prefix}.{key}' if prefix else key)
                             for key in left.keys() | right.keys()))
    return {prefix}


def last_number(text: str, pattern: str, cast=int):
    matches = re.findall(pattern, text, re.I | re.M)
    if not matches:
        raise ValueError(f'Missing log evidence for pattern {pattern}')
    return cast(matches[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve()
    if archive.name != 'dcdtuni_shinaide_air_smoke_20260927':
        raise ValueError('Unexpected archive identity')
    exported = archive / 'source/Shinaide.exported.cfdrun'
    mesh_input = archive / 'source/Shinaide.mesh-only.cfdrun'
    solve_input = archive / 'model/Shinaide.cfdrun'
    export_report = json.loads((archive / 'export-report.json').read_text())
    provenance = json.loads((archive / 'air-smoke-provenance.json').read_text())
    solve_report = json.loads((archive / 'solve-smoke.json').read_text())
    original_manifest = json.loads(exported.read_text())
    mesh_manifest = json.loads(mesh_input.read_text())
    solve_manifest = json.loads(solve_input.read_text())
    assert digest(args.project) == export_report['project_sha256'] == provenance['source_cfdproj_sha256']
    assert digest(exported) == export_report['cfdrun_sha256'] == provenance['original_export_sha256']
    assert digest(mesh_input) == provenance['mesh_only_cfdrun_sha256']
    assert digest(solve_input) == provenance['solve_smoke_cfdrun_sha256'] == solve_report['manifest_sha256']
    assert original_manifest['sourceProject']['uid'] == solve_manifest['sourceProject']['uid']

    mesh_override = {
        'meshConfig.meshProcess', 'meshGeometry.meshConfig.meshProcess',
        'meshGeometry.meshConfig.meshTimeLimitSeconds',
    }
    solve_override = mesh_override | {
        f'{prefix}.{name}'
        for prefix in ('simulationConfig', 'meshGeometry.simulationConfig')
        for name in ('solveProcess', 'endTime', 'writeInterval', 'startFrom')
    }
    assert changed_paths(original_manifest, mesh_manifest) == mesh_override
    assert changed_paths(original_manifest, solve_manifest) == solve_override
    assert mesh_manifest['meshGeometry']['meshConfig']['meshProcess'] == 4
    assert mesh_manifest['meshGeometry']['meshConfig']['meshTimeLimitSeconds'] == 240
    assert solve_manifest['meshGeometry']['simulationConfig']['solveProcess'] == 4
    assert solve_manifest['meshGeometry']['simulationConfig']['endTime'] == 10

    # The runner may replace or add baffle STL surfaces while generating the
    # mesh. Compare against the archived *exported* set, not the mutated
    # triSurface working directory.
    stls = sorted((archive / 'source/triSurface').glob('*.stl'))
    assert len(stls) == len(export_report['stl_sha256']) == 12
    assert {file.name: digest(file) for file in stls} == export_report['stl_sha256']
    working_stls = {path.name for path in (archive / 'Mesh/constant/triSurface').glob('*.stl')}
    exported_names = set(export_report['stl_sha256'])
    surface_report_path = archive / 'source/surfaceCheck/summary.json'
    surface_report = json.loads(surface_report_path.read_text())
    assert len(surface_report['files']) == len(stls)
    assert {row['stl'] for row in surface_report['files']} == exported_names
    assert all(row['status'] == 'exit_0' for row in surface_report['files'])
    assert all(any('Surface has no illegal triangles.' in line for line in row['highlighted'])
               for row in surface_report['files'])
    open_stls = sorted(row['stl'] for row in surface_report['files']
                       if any('Surface is not closed' in line for line in row['highlighted']))

    mesh_text = (archive / 'mesh.stdout.log').read_text(errors='replace')
    solve_text = (archive / 'solve.stdout.log').read_text(errors='replace')
    assert (archive / 'mesh.exit-code').read_text().strip() == '0'
    assert 'Parallel mesh enabled: 4 processes' in mesh_text
    assert 'Running: mpirun -np 4 snappyHexMesh' in mesh_text
    assert 'Mesh complete. Complete case' in mesh_text
    assert 'Solve complete' in solve_text
    assert 'Running buoyantBoussinesqSimpleFoam_new in parallel with 4 processes' in solve_text
    assert solve_report['status'] == 'completed' and solve_report['runner_exit_code'] == 0
    assert solve_report['cleanup']['remaining_owned_pids'] == []
    assert solve_report['actual_cpu_affinity'] == [0, 1, 2, 3]
    assert solve_report['cuda_visible_devices'] == ''

    run_dirs = [path for path in (archive / 'log').iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    convergence = json.loads((run_dir / 'convergence.json').read_text())
    assert convergence['verdict'] == 'NOT_CONVERGED'
    assert (run_dir / '10/U').stat().st_size > 0
    assert (run_dir / '10/T').stat().st_size > 0
    assert 'writeExplicitBC is not built' in solve_text

    quality = {
        'cells': last_number(mesh_text, r'\| INFO \|\s+cells:\s+(\d+)\s*$', int),
        'boundary_patches': last_number(mesh_text, r'^.*boundary patches:\s+(\d+)\s*$', int),
        'failed_check_count': last_number(mesh_text, r'Failed (\d+) mesh checks', int),
        'max_skewness': last_number(mesh_text, r'\*\*\*Max skewness = ([0-9.eE+\-]+)', float),
        'under_determined_cells': last_number(mesh_text, r'Cells with small determinant .*? number of cells:\s*(\d+)', int),
        'concave_cells': last_number(mesh_text, r'Concave cells .*? number of cells:\s*(\d+)', int),
        'protected_nonmanifold_edges': last_number(mesh_text, r'Protected from visiting (\d+) non-manifold edges', int),
    }
    assert quality['cells'] > 0 and quality['failed_check_count'] == 3
    report = {
        'status': 'runtime_chain_executed_quality_not_accepted',
        'case': 'Shinaide saved DAE project; air side only',
        'project_sha256': export_report['project_sha256'],
        'source_project': export_report['source_project'],
        'archive': str(archive),
        'exported_cfdrun_sha256': digest(exported),
        'mesh_input_sha256': digest(mesh_input),
        'solve_input_sha256': digest(solve_input),
        'only_execution_overrides': sorted(solve_override),
        'stl_count': len(stls),
        'stl_sha256': export_report['stl_sha256'],
        'surface_check': {'file_count': len(stls), 'invalid_triangle_files': [],
                          'nonclosed_grouped_stls': open_stls,
                          'interpretation': 'Open baffle, wall and interface groups may be intentional; this does not isolate the mesh-quality root cause'},
        'mesh_generated_stl_names': sorted(working_stls - exported_names),
        'mesh_removed_or_replaced_stl_names': sorted(exported_names - working_stls),
        'mesh': {'exit_code': 0, 'mpi_ranks': 4, 'quality': quality,
                 'accepted': False, 'internal_budget_s': 240},
        'solve': {'exit_code': 0, 'mpi_ranks': 4, 'iterations_requested': 10,
                  'supervisor_elapsed_s': solve_report['elapsed_s'],
                  'convergence_verdict': convergence['verdict'],
                  'convergence_residuals_passed': convergence['residuals']['passed'],
                  'convergence_sensors_status': convergence['sensors']['status'],
                  'explicit_bc_utility_built': False,
                  'final_U_bytes': (run_dir / '10/U').stat().st_size,
                  'final_T_bytes': (run_dir / '10/T').stat().st_size,
                  'supervisor_cleanup': solve_report['cleanup']},
        'artifact_sha256': {str(path.relative_to(archive)): digest(path) for path in
            (archive / 'mesh.stdout.log', archive / 'solve.stdout.log',
             archive / 'solve-smoke.json', run_dir / 'convergence.json',
             surface_report_path,
             archive / 'Mesh/constant/polyMesh/boundary')},
        'original_project_modified': False,
        'gpu_run': False,
        'limits': [
            'Mesh generation and a 10-iteration OpenFOAM run executed; three mesh quality checks failed.',
            'The run is explicitly NOT_CONVERGED, with too few sensor samples for convergence acceptance.',
            'The missing writeExplicitBC utility limits explicit reconstructed boundary-value serialization.',
            'No air field prediction, NV calibration, full air/liquid coupling or physically accepted mesh is claimed.',
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps({'status': report['status'], 'cells': quality['cells'],
                      'failed_mesh_checks': quality['failed_check_count'],
                      'convergence': convergence['verdict'], 'report': str(args.report)}))


if __name__ == '__main__':
    main()
