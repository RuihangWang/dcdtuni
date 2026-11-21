"""Bounded, independent-process CPU1/CPU4 NV physical DAE comparison.

Run under an external 60-second timeout. The parent enforces a 55-second
internal deadline and launches only the two requested CPU workers, sequentially.
No CUDA availability query, GPU allocation, renderer, or fixture mutation.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import traceback
from zoneinfo import ZoneInfo


SOURCES = (
    'src/backend/liquidcooling/solver/dae_newton.py',
    'src/backend/liquidcooling/solver/equation_graph.py',
    'src/backend/liquidcooling/solver/differentiable_hydraulic.py',
    'src/backend/liquidcooling/solver/newton_hydraulic.py',
    'src/backend/liquidcooling/dae_experiment.py',
    'src/backend/liquidcooling/liquidrun_dae_schema.py',
    'src/backend/liquidcooling/liquidrun_schema.py',
    'src/backend/liquidcooling/graphcompiling/compiler.py',
    'src/backend/liquidcooling/graphcompiling/compiled_graph.py',
    'src/backend/liquidcooling/graphcompiling/time_slice.py',
)
THREAD_ENV = ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def worker(args):
    requested = args.worker
    cpus = {0} if requested == 1 else {0, 1, 2, 3}
    if not cpus <= set(os.sched_getaffinity(0)):
        raise RuntimeError(f'requested CPU mask unavailable: {sorted(cpus)}')
    os.sched_setaffinity(0, cpus)
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    for key in THREAD_ENV:
        os.environ[key] = str(requested)
    sys.path.insert(0, str(args.repo / 'src'))
    began = time.perf_counter()
    report = {'status': 'running', 'pid': os.getpid(), 'requested_threads': requested,
              'affinity': sorted(os.sched_getaffinity(0)), 'nice': os.getpriority(os.PRIO_PROCESS, 0),
              'environment': {key: os.environ[key] for key in (*THREAD_ENV, 'CUDA_VISIBLE_DEVICES')},
              'stage_seconds': {}, 'source_sha256': {p: digest(args.repo / p) for p in SOURCES},
              'input_sha256': digest(args.fixture)}

    def stage(name, action):
        report['current_stage'] = name
        print(f'CPU{requested} {name}', flush=True)
        start = time.perf_counter()
        try:
            return action()
        finally:
            report['stage_seconds'][name] = time.perf_counter() - start

    try:
        start = time.perf_counter()
        import torch
        torch.set_num_threads(requested)
        torch.set_num_interop_threads(1)
        from backend.liquidcooling.dae_experiment import DAEExperiment
        from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
        from backend.liquidcooling.solver.equation_graph import EquationGraph
        report['stage_seconds']['import_and_thread_setup'] = time.perf_counter() - start
        if Path(inspect.getfile(EquationGraph)).resolve() != args.repo / SOURCES[1]:
            raise RuntimeError('EquationGraph imported outside the requested worktree')
        report.update(actual_threads=torch.get_num_threads(), interop_threads=torch.get_num_interop_threads(),
                      torch_version=str(torch.__version__), cpu_capability=torch.backends.cpu.get_cpu_capability())
        raw = json.loads(args.fixture.read_text())
        if (len(raw['nodes']), len(raw['edges']), len(raw['racks'])) != (316, 348, 34):
            raise ValueError('requires the full saved 34-rack NV fixture')
        if raw['execution']['device'] != 'cpu' or raw['execution']['dtype'] != 'float64':
            raise ValueError('requires the original CPU float64 fixture')
        dt = raw['time']['dt_s']
        if any(event['time_s'] <= dt for event in raw.get('events', [])):
            raise ValueError('the one-step probe must finish before authored events')
        report['manifest_execution'] = raw['execution']
        report['source_project'] = raw['source_project']
        report['dt_s'] = dt
        definition = stage('load_and_compile', lambda: load_dae_input(raw))
        eq = definition.equation_graph
        report['graph'] = {'states': eq.size, 'parameters': eq.parameter_count,
                           'nodes': eq.graph.N_nodes, 'edges': eq.graph.N_edges,
                           'pipe_cells': eq.graph.N_cells, 'cold_plates': eq.graph.N_cp}
        run = stage('steady_initialization', lambda: DAEExperiment(definition, record_frames=False))
        initial = run.tensor_state.detach().clone()
        heat = torch.tensor(1.2, dtype=torch.float64, device='cpu', requires_grad=True)
        run.set_heat_multiplier(heat)
        stage('one_backward_euler_step', run.step)
        final = run.tensor_state
        values = eq.unpack(final, run._parameters[-1])
        objective = values['solid_temperature'].mean() + values['primary_return_temperature'].mean()
        gradient, = stage('heat_adjoint', lambda: torch.autograd.grad(objective, heat))
        if not bool(torch.isfinite(gradient)) or not bool(gradient > 0):
            raise RuntimeError('heat gradient must be finite and positive')
        if run.seq != 1 or run.frames:
            raise RuntimeError('expected exactly one tensor-only accepted transient step')
        if final.device.type != 'cpu' or final.dtype != torch.float64:
            raise RuntimeError('unexpected numerical execution device/precision')
        report['diagnostics'] = {}
        for name, state, rate, theta, current_time in (
            ('steady', initial, torch.zeros_like(initial), run._parameters[0], 0.),
            ('step_1', final, torch.where(eq.differential_mask, (final - initial) / dt,
                                        torch.zeros_like(final)), run._parameters[-1], dt),
        ):
            diagnostics = {k: float(v.detach()) for k, v in eq.diagnostics(
                state.new_tensor(current_time), state, rate, theta).items()}
            if (not all(math.isfinite(v) for v in diagnostics.values())
                    or abs(diagnostics['energy_balance_W']) > 1e-4
                    or diagnostics['mass_residual_kg_s'] > 1e-8
                    or diagnostics['scaled_residual'] > 1e-8):
                raise RuntimeError(f'{name} failed physical residual/conservation checks: {diagnostics}')
            report['diagnostics'][name] = diagnostics
        report.update(objective_K=float(objective.detach()), heat_gradient_K=float(gradient.detach()),
                      initializer=run.initializer, applied_inputs=run.applied_inputs,
                      initial_primary_return_K=eq.unpack(initial)['primary_return_temperature'].tolist(),
                      final_primary_return_K=values['primary_return_temperature'].detach().tolist(),
                      peak_rss_MiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                      device=str(final.device), dtype=str(final.dtype))
        # Only the completed result boundary materializes arrays, for parent
        # comparisons. No transferred values feed a worker's physical solve.
        report['_state_values'] = {'steady': initial.tolist(), 'step_1': final.detach().tolist(),
                                   'scale': eq.state_scale.tolist()}
        report['parameter_values_sha256'] = hashlib.sha256(json.dumps(
            [v.detach().tolist() for v in run._parameters], separators=(',', ':')).encode()).hexdigest()
        if report['input_sha256'] != digest(args.fixture) or report['source_sha256'] != {p: digest(args.repo / p) for p in SOURCES}:
            raise RuntimeError('input or physical sources changed during the worker')
        if torch.get_num_threads() != requested or set(os.sched_getaffinity(0)) != cpus:
            raise RuntimeError('solver changed the requested thread/affinity budget')
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
    report['worker_wall_seconds'] = time.perf_counter() - began
    write_json(args.worker_output, report)
    return 0 if report['status'] == 'passed' else 1


def main(args):
    started = time.perf_counter()
    report = {'date_sgt': datetime.now(ZoneInfo('Asia/Singapore')).isoformat(),
              'status': 'running', 'host': os.uname().nodename, 'worktree': str(args.repo),
              'scope': 'same 34-rack mock; independent CPU1/CPU4 processes, one accepted heat step and heat adjoint only',
              'caller_hard_cap_s': 60, 'internal_deadline_s': 55,
              'input_path': str(args.fixture.relative_to(args.repo)), 'input_sha256': digest(args.fixture),
              'source_sha256': {p: digest(args.repo / p) for p in SOURCES}, 'script_sha256': digest(__file__),
              'thread_policy': 'input is unchanged; only process execution overrides PyTorch/BLAS threads and CPU affinity',
              'objective': 'mean solid temperature K + mean primary return temperature K at t=1 s',
              'heat_multiplier': 1.2, 'finite_difference': 'not run; compare the same adjoint between thread settings',
              'gpu_activity': 'none; CUDA hidden and availability/device enumeration never called',
              'load_average_at_start': os.getloadavg(), 'cases': []}
    try:
        with tempfile.TemporaryDirectory(prefix='nv-cpu1-cpu4-') as temporary:
            for count in (1, 4):
                remaining = 55 - (time.perf_counter() - started)
                if remaining <= 0:
                    raise TimeoutError('55-second internal deadline exhausted')
                output = Path(temporary) / f'cpu{count}.json'
                env = os.environ.copy()
                env['CUDA_VISIBLE_DEVICES'] = ''
                env.pop('DCDTUNI_RUN_CUDA_TESTS', None)
                for key in THREAD_ENV:
                    env[key] = str(count)
                command = [sys.executable, str(Path(__file__).resolve()), '--repo', str(args.repo),
                           '--fixture', str(args.fixture), '--worker', str(count), '--worker-output', str(output)]
                try:
                    completed = subprocess.run(command, env=env, timeout=remaining, capture_output=True, text=True)
                except subprocess.TimeoutExpired as exc:
                    report['cases'].append({'requested_threads': count, 'status': 'timeout',
                                            'progress': (exc.stdout or b'').decode() if isinstance(exc.stdout, bytes) else exc.stdout})
                    raise TimeoutError(f'CPU{count} exceeded the shared 55-second budget') from exc
                case = json.loads(output.read_text()) if output.is_file() else {
                    'requested_threads': count, 'status': 'failed', 'error': {'message': completed.stderr[-3000:]}}
                report['cases'].append(case)
                print(completed.stdout, end='', flush=True)
                if completed.returncode or case['status'] != 'passed':
                    raise RuntimeError(f'CPU{count} failed; physical model remains unchanged')
            left, right = report['cases']
            if left['pid'] == right['pid']:
                raise RuntimeError('thread cases must execute in independent processes')
            for case in (left, right):
                if case['source_sha256'] != report['source_sha256'] or case['input_sha256'] != report['input_sha256']:
                    raise RuntimeError('source/input identity differs between workers')
            if left['parameter_values_sha256'] != right['parameter_values_sha256']:
                raise RuntimeError('workers used different physical parameter values')
            comparison = {}
            for name in ('steady', 'step_1'):
                a, b = left['_state_values'][name], right['_state_values'][name]
                scales = left['_state_values']['scale']
                if scales != right['_state_values']['scale'] or len(a) != len(b):
                    raise RuntimeError('state layout/scales differ')
                comparison[name] = {'max_abs_difference': max(abs(x-y) for x, y in zip(a, b)),
                                    'max_state_scaled_difference': max(abs(x-y)/s for x, y, s in zip(a, b, scales))}
                if comparison[name]['max_state_scaled_difference'] > 1e-8:
                    raise RuntimeError(f'{name} exceeds 1e-8 scaled thread parity tolerance')
            difference = abs(left['heat_gradient_K'] - right['heat_gradient_K'])
            relative = difference / max(abs(left['heat_gradient_K']), 1e-12)
            comparison['heat_gradient'] = {'absolute_difference': difference, 'relative_difference': relative}
            if difference > 1e-10 + 1e-6 * abs(left['heat_gradient_K']):
                raise RuntimeError('heat adjoints fail thread parity tolerance')
            comparison['single_run_cpu1_over_cpu4_time'] = {
                name: left['stage_seconds'][name] / right['stage_seconds'][name]
                for name in ('steady_initialization', 'one_backward_euler_step', 'heat_adjoint')}
            report['comparison'] = comparison
            report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'timeout' if isinstance(exc, TimeoutError) else 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    for case in report['cases']:
        case.pop('_state_values', None)
    report['source_and_input_unchanged'] = (report['input_sha256'] == digest(args.fixture)
        and report['source_sha256'] == {p: digest(args.repo / p) for p in SOURCES})
    if not report['source_and_input_unchanged']:
        report['status'] = 'failed'
    report['total_wall_seconds'] = time.perf_counter() - started
    write_json(args.output_prefix.with_suffix('.json'), report)
    lines = ['# NV mock CPU 1 / 4 comparison — 2026-09-27 SGT', '',
             f"Status: **{report['status']}**. Wall time {report['total_wall_seconds']:.3f} s; 60 s outer cap, 55 s shared internal deadline.", '',
             'The unchanged 316-node / 348-edge NV fixture runs in two independent processes: CPU affinity [0] / [0,1,2,3], one / four PyTorch and BLAS threads, interop 1, nice 10. Each performs steady initialization, one t=1 s backward-Euler step at heat multiplier 1.2, and the heat adjoint of mean solid temperature plus mean primary return. The authored t=5 s event is not reached.', '']
    for case in report['cases']:
        lines.append(f"- CPU {case['requested_threads']}: {case['status']}; affinity {case.get('affinity')}; actual threads {case.get('actual_threads')}; PID {case.get('pid')}.")
        if case['status'] == 'passed':
            timings = case['stage_seconds']
            lines.append(f"  Steady {timings['steady_initialization']:.4f} s; step {timings['one_backward_euler_step']:.4f} s; adjoint {timings['heat_adjoint']:.4f} s; heat gradient {case['heat_gradient_K']:.12g} K per multiplier unit.")
            lines.append(f"  Largest energy residual {max(abs(v['energy_balance_W']) for v in case['diagnostics'].values()):.6g} W; peak RSS {case['peak_rss_MiB']:.2f} MiB.")
    if 'comparison' in report:
        comparison = report['comparison']
        lines += ['', f"Maximum scaled state differences: steady {comparison['steady']['max_state_scaled_difference']:.6g}, one step {comparison['step_1']['max_state_scaled_difference']:.6g}. Heat-adjoint relative difference {comparison['heat_gradient']['relative_difference']:.6g}."]
    if report.get('error'):
        lines += ['', f"Failure: {report['error']['type']}: {report['error']['message']}."]
    lines += ['', 'These are one-shot timings on a shared host, not a general speedup claim. No finite differences, extra scenarios, full-duration transient, CUDA availability/device query, GPU, RTX, Kit, Xvfb or NSCC operation ran. Mock data do not establish site calibration. Full source/input hashes, execution settings, stage timings and conservation values are in the JSON report.']
    args.output_prefix.with_suffix('.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'status': report['status'], 'wall_seconds': report['total_wall_seconds']}), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--output-prefix', type=Path, default=Path(__file__).with_name('nv-cpu1-cpu4-2026-09-27'))
    parser.add_argument('--worker', type=int, choices=(1, 4))
    parser.add_argument('--worker-output', type=Path)
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    args.fixture = (args.fixture or args.repo / 'docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun').resolve()
    raise SystemExit(worker(args) if args.worker else main(args))
