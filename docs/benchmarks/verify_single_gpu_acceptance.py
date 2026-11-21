"""One explicitly authorized CUDA acceptance phase; one physical GPU only.

Runs the existing opt-in CUDA tests and the full project-derived NV tensor
entry on CPU and CUDA. Original fixtures remain read-only. The phase has a
90-second deadline; its marker gives the shared eight-minute episode deadline
to the subsequent Kit acceptance harness. No renderer is started here.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu-uuid', required=True)
    parser.add_argument('--physical-index', required=True, type=int)
    parser.add_argument('--resume-episode', action='store_true')
    args = parser.parse_args()
    if os.environ.get('CUDA_VISIBLE_DEVICES') != args.gpu_uuid:
        raise ValueError('CUDA visibility must be exactly the approved physical UUID')
    if not args.gpu_uuid.startswith('GPU-'):
        raise ValueError('An explicit physical GPU UUID is required')
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'src'))
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
        os.environ[key] = '1'
    os.environ['DCDTUNI_RUN_CUDA_TESTS'] = '1'
    os.sched_setaffinity(0, {0})
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    output = root / 'docs/benchmarks/single-gpu-numerical-2026-09-26.json'
    marker = root / 'docs/benchmarks/single-gpu-episode-2026-09-26.json'
    start = time.time()
    prior_attempts = []
    if args.resume_episode:
        prior = json.loads(marker.read_text())
        if prior['gpu_uuid'] != args.gpu_uuid or prior['physical_gpu_index'] != args.physical_index:
            raise ValueError('Cannot change the physical GPU during the episode')
        if time.time() >= prior['episode_deadline_unix_s']:
            raise TimeoutError('The original GPU episode has ended; another run is not authorized')
        start = prior['episode_start_unix_s']
        prior_attempts = [json.loads(output.read_text())]
    elif marker.exists():
        raise ValueError('An episode marker already exists; do not silently create a new GPU authorization')
    report = {'status': 'running', 'gpu_uuid': args.gpu_uuid,
              'physical_gpu_index': args.physical_index, 'logical_device': 'cuda:0',
              'episode_start_unix_s': start, 'episode_deadline_unix_s': start + 480,
              'numerical_phase_cap_s': 90, 'cpu_affinity': [0], 'threads': 1,
              'nice': 10, 'cases': [], 'torch_tests': [], 'prior_attempts': prior_attempts}

    def save():
        report['elapsed_s'] = time.time() - start
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        marker.write_text(json.dumps({key: report[key] for key in
            ('status', 'gpu_uuid', 'physical_gpu_index', 'episode_start_unix_s',
             'episode_deadline_unix_s', 'elapsed_s')}, indent=2) + '\n')

    def timeout(*_):
        raise TimeoutError('90-second CUDA numerical phase deadline exhausted')

    save()
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(max(1, min(90, int(start + 480 - time.time()))))
    try:
        import torch
        import pytest
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        if torch.cuda.device_count() != 1 or not torch.cuda.is_available():
            raise RuntimeError('Exactly one visible CUDA device is required; no fallback or skip')
        report['torch_version'] = str(torch.__version__)
        report['gpu_name'] = torch.cuda.get_device_name(0)
        torch.cuda.reset_peak_memory_stats(0)

        evidence = report
        class Results:
            def pytest_runtest_logreport(self, report):
                if report.when == 'call' or report.skipped or report.failed:
                    evidence['torch_tests'].append({'nodeid': report.nodeid,
                        'phase': report.when, 'outcome': report.outcome, 'duration_s': report.duration})

        code = pytest.main(['-q',
            str(root / 'src/backend/tests/test_liquid/test_dae_devices.py') +
            '::test_same_newton_and_adjoint_run_on_cuda_without_cpu_state_transfer',
            str(root / 'src/backend/tests/test_liquid/test_equation_graph_cuda.py') +
            '::test_physical_equation_graph_steady_trajectory_and_gradients_match_cpu_cuda'],
            plugins=[Results()])
        if code != 0 or len(report['torch_tests']) != 2 or any(
                row['outcome'] != 'passed' for row in report['torch_tests']):
            raise RuntimeError('CUDA acceptance tests did not both execute and pass')

        from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
        from backend.liquidcooling.dae_experiment import DAEExperiment
        fixture = root / 'docs/benchmarks/fixtures/nv-pek02-mock-v3.liquidrun'
        source_bytes = fixture.read_bytes()
        raw = json.loads(source_bytes)
        before_hash = hashlib.sha256(source_bytes).hexdigest()
        report['fixture_sha256'] = before_hash
        outputs = {}
        for device in ('cpu', 'cuda:0'):
            config = copy.deepcopy(raw)
            config['execution']['device'] = device
            begin = time.perf_counter()
            definition = load_dae_input(config)
            experiment = DAEExperiment(definition, record_frames=False)
            initial = experiment.tensor_state
            control = initial.new_tensor(1.2, requires_grad=True)
            experiment.set_heat_multiplier(control)
            experiment.step()
            final = experiment.tensor_state
            objective = (experiment.tensor_series('solid_temperature')[:, -1].mean()
                         + experiment.tensor_series('primary_return_temperature')[:, -1].mean())
            gradient, = torch.autograd.grad(objective, control)
            assert initial.device.type == final.device.type == gradient.device.type == device.split(':')[0]
            assert bool(torch.isfinite(gradient)) and float(gradient.abs()) > 1e-7
            if device.startswith('cuda'):
                torch.cuda.synchronize(0)
            elapsed = time.perf_counter() - begin
            outputs[device] = (torch.stack((initial, final)).detach(), gradient.detach(),
                               definition.equation_graph.state_scale)
            report['cases'].append({'device': device, 'states': definition.equation_graph.size,
                'parameters': definition.equation_graph.parameter_count,
                'nodes': definition.equation_graph.graph.N_nodes,
                'edges': definition.equation_graph.graph.N_edges,
                'pipe_cells': definition.equation_graph.graph.N_cells,
                'accepted_steps': experiment.seq, 'dt_s': definition.dt,
                'initializer': experiment.initializer, 'heat_gradient': float(gradient),
                'elapsed_s': elapsed})
            save()
        cpu, cpu_gradient, scale = outputs['cpu']
        cuda, cuda_gradient, _ = outputs['cuda:0']
        # Transfers are after each independent complete root/adjoint trajectory.
        error = (cpu - cuda.cpu()).abs() / scale
        torch.testing.assert_close(cuda_gradient.cpu(), cpu_gradient, rtol=2e-5, atol=2e-7)
        assert float(error.max()) < 1e-8
        report['nv_parity'] = {'max_scaled_state_difference': float(error.max()),
            'heat_gradient_abs_difference': float((cuda_gradient.cpu() - cpu_gradient).abs())}
        report['peak_gpu_allocated_bytes'] = torch.cuda.max_memory_allocated(0)
        report['peak_gpu_reserved_bytes'] = torch.cuda.max_memory_reserved(0)
        report['fixture_unchanged'] = hashlib.sha256(fixture.read_bytes()).hexdigest() == before_hash
        assert report['fixture_unchanged']
        sources = ['solver/dae_newton.py', 'solver/equation_graph.py', 'dae_experiment.py',
                   'liquidrun_dae_schema.py']
        report['source_sha256'] = {name: hashlib.sha256(
            (root / 'src/backend/liquidcooling' / name).read_bytes()).hexdigest() for name in sources}
        report['status'] = 'passed'
    except BaseException as error:
        report['status'] = 'failed'
        report['error'] = {'type': type(error).__name__, 'message': str(error),
                           'traceback': traceback.format_exc()}
        print(traceback.format_exc(), flush=True)
    finally:
        signal.alarm(0)
        save()
        print(json.dumps({'status': report['status'], 'elapsed_s': report['elapsed_s'],
            'episode_deadline_unix_s': report['episode_deadline_unix_s'], 'report': str(output)}), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
