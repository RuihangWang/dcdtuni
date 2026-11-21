"""Bounded, reproducible Shinaide gradient check using the production Torch path.

Run from the dcdtuni Python environment. This appends a directional verification
to existing evidence; it never replaces the earlier heat/runtime evidence.
Example:
  python docs/benchmarks/verify_shinaide_cpu_gradient.py \
    --fixture /tmp/dcdtuni-shinaide-v2.liquidrun --direction pump

The input is exported from the real Shinaide .cfdproj, with duration=10 s,
dt=1 s, manual control, a 5 s load step and load multiplier=1.4. Re-export it
with the React UI or the liquidSavedExperiment.test.ts LIQUID_SCHEMA_FIXTURE
hook. Its SHA-256 must match the existing evidence when appending.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=Path('/tmp/dcdtuni-shinaide-v2.liquidrun'))
    parser.add_argument('--direction', choices=('pump', 'heat'), default='pump')
    parser.add_argument('--budget-seconds', type=float, default=90)
    parser.add_argument('--output-prefix', type=Path,
                        default=Path(__file__).with_name('shinaide-cpu-gradient-2026-09-26'))
    args = parser.parse_args()
    if not 0 < args.budget_seconds <= 90:
        parser.error('--budget-seconds must be positive and at most 90')
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'src'))
    for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[variable] = '1'
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    if hasattr(os, 'sched_getaffinity'):
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    if hasattr(os, 'getpriority'):
        os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    started = time.perf_counter()
    record = {
        'status': 'running', 'direction': args.direction,
        'pid': os.getpid(), 'budget_seconds': args.budget_seconds,
        'cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
        'nice': os.getpriority(os.PRIO_PROCESS, 0) if hasattr(os, 'getpriority') else None,
        'walltime_seconds': {}, 'script': str(Path(__file__).resolve()),
        'fixture_path': str(args.fixture.resolve()),
    }
    json_path = args.output_prefix.with_suffix('.json')
    markdown_path = args.output_prefix.with_suffix('.md')
    previous = json.loads(json_path.read_text()) if json_path.exists() else {}

    def timeout(_signum, _frame):
        raise TimeoutError(f'{args.budget_seconds:g}-second verification deadline exhausted')

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, args.budget_seconds)
    print(json.dumps({'pid': record['pid'], 'cpu_affinity': record['cpu_affinity'],
                      'budget_seconds': args.budget_seconds}), flush=True)

    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def stage(name, function):
        record['current_stage'] = name
        begin = time.perf_counter()
        print('START ' + name, flush=True)
        result = function()
        record['walltime_seconds'][name] = time.perf_counter() - begin
        print('DONE ' + name + f" {record['walltime_seconds'][name]:.4f} s", flush=True)
        return result

    try:
        import torch
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        from backend.liquidcooling.liquidrun_schema import parse_liquidrun
        from backend.liquidcooling.graphcompiling.compiler import compile_graph
        from backend.liquidcooling.solver.rollout import differentiable_rollout
        record['torch'] = {'version': torch.__version__, 'num_threads': torch.get_num_threads(),
                           'num_interop_threads': torch.get_num_interop_threads()}
        record['git_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        record['fixture_sha256'] = sha(args.fixture)
        if previous.get('fixture', {}).get('sha256') not in (None, record['fixture_sha256']):
            raise ValueError('input digest differs from the original heat evidence; use a separate output prefix')
        source_files = (
            'src/backend/liquidcooling/liquidrun_schema.py',
            'src/backend/liquidcooling/graphcompiling/compiler.py',
            'src/backend/liquidcooling/solver/rollout.py',
            'src/backend/liquidcooling/solver/differentiable_hydraulic.py',
            'src/backend/liquidcooling/solver/thermal_step.py',
        )
        record['source_sha256'] = {name: sha(root / name) for name in source_files}
        record['script_sha256'] = sha(__file__)
        raw = json.loads(args.fixture.read_text())
        if raw['time'] != {'dt_s': 1, 'N_t': 11, 'bdf_order': 1} or raw['controllers'] != []:
            raise ValueError('requires the same 10 s / 11-sample manual Shinaide fixture')
        config = stage('schema_parse', lambda: parse_liquidrun(raw))
        graph = stage('compile_graph', lambda: compile_graph(config, device='cpu'))
        if (graph.N_nodes, graph.N_edges) != (187, 242):
            raise ValueError('the fixture is not the expected 187-node, 242-edge Shinaide graph')
        record['graph'] = {'nodes': graph.N_nodes, 'edges': graph.N_edges, 'cold_plates': graph.N_cp,
                           'thermal_cells': graph.N_cells, 'dt_s': graph.dt, 'samples': graph.N_t}
        record['initial_conditions'] = config.initial_conditions.model_dump()
        record['events'] = {'count': len(raw['events']), 'retained_unchanged': True,
                            'times_s': sorted(set(item['time_s'] for item in raw['events']))}
        base = (graph.pump_params.rpm_ratio if args.direction == 'pump' else graph.cp_params.Q_W).detach().clone()
        schedule_key = 'pump_rpm_ratio' if args.direction == 'pump' else 'heat_W'
        record['perturbation'] = {'input': 'beta' if args.direction == 'pump' else 'alpha', 'base_value': 1.0,
            'schedule_key': schedule_key, 'formula': f'{schedule_key} = scalar * original schedule',
            'schedule_shape': list(base.shape), 'baseline_min': float(base.min()), 'baseline_max': float(base.max()),
            'epsilon': 1e-4, 'objective': 'mean cold-plate solid temperature at t=10 s', 'objective_units': 'K',
            'rtol': 3e-4, 'atol': 1e-7}
        scalar = torch.tensor(1.0, dtype=torch.float64, device='cpu', requires_grad=True)
        trajectory = stage('tensor_forward', lambda: differentiable_rollout(graph, **{schedule_key: base * scalar}))
        objective = trajectory.solid_temperature[:, -1].mean()
        record['autograd_path'] = {'pressure_requires_grad': trajectory.pressure.requires_grad,
            'flow_requires_grad': trajectory.flow.requires_grad, 'objective_requires_grad': objective.requires_grad,
            'kernel': 'differentiable_rollout -> hydraulic_step -> _ImplicitHydraulic.backward -> thermal_step'}
        gradient = stage('autograd_backward', lambda: torch.autograd.grad(objective, scalar)[0])
        record['forward'] = {name: {'all_finite': bool(torch.isfinite(getattr(trajectory, name)).all()),
                                  'shape': list(getattr(trajectory, name).shape)} for name in
                            ('pressure', 'flow', 'node_temperature', 'water_temperature', 'solid_temperature', 'edge_outlet')}
        if not all(value['all_finite'] for value in record['forward'].values()):
            raise AssertionError('non-finite forward state')
        eps = record['perturbation']['epsilon']

        def value_at(value):
            with torch.no_grad():
                result = differentiable_rollout(graph, **{schedule_key: base * value})
                return float(result.solid_temperature[:, -1].mean())

        plus = stage('finite_difference_plus', lambda: value_at(1.0 + eps))
        minus = stage('finite_difference_minus', lambda: value_at(1.0 - eps))
        expected, actual = (plus - minus) / (2 * eps), float(gradient.detach())
        error = abs(actual - expected)
        passed = bool(torch.isfinite(gradient)) and abs(actual) > 1e-8 and error <= 1e-7 + 3e-4 * abs(expected)
        record['gradient'] = {'objective_K': float(objective.detach()), 'autograd_directional_derivative': actual,
            'centered_difference': expected, 'objective_plus_K': plus, 'objective_minus_K': minus,
            'absolute_error': error, 'relative_error': error / max(abs(actual), abs(expected), 1e-30), 'passed': passed}
        if not passed:
            raise AssertionError('directional gradient is zero, non-finite, or outside centered-difference tolerance')
        if args.direction == 'pump' and not trajectory.flow.requires_grad:
            raise AssertionError('pump direction did not activate differentiable hydraulics')
        record['status'] = 'passed'
    except BaseException as error:
        record['status'] = 'timeout' if isinstance(error, TimeoutError) else 'failed'
        record['error'] = {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
        print(traceback.format_exc(), flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        record['total_walltime_seconds'] = time.perf_counter() - started
        previous[args.direction + '_verification'] = record
        previous['verification_summary'] = {'original_heat_and_runtime': previous.get('status'),
                                            args.direction: record['status']}
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(previous, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        title = '泵速' if args.direction == 'pump' else '热负载'
        lines = ['', f'## {title}方向补充验证', '',
                 f"状态：**{record['status']}**；耗时 {record['total_walltime_seconds']:.3f} s；单核、单线程、nice 10、CUDA 禁用。", '',
                 f'可复现脚本：[verify_shinaide_cpu_gradient.py](verify_shinaide_cpu_gradient.py)。输入：`{args.fixture}`（SHA-256 与原热负载证据一致）。', '',
                 '在仓库的 dcdtuni Python 环境执行：', '', '```sh',
                 f'python docs/benchmarks/verify_shinaide_cpu_gradient.py --fixture {args.fixture} --direction {args.direction}', '```', '',
                 '保持原有 10 s / 11 个样本、初值和负载事件不变；全部泵速轨迹乘 beta，基线 1，中心差分 beta±1e-4；目标仍是末时刻平均冷板固体温度。'
                 if args.direction == 'pump' else '全部热负载轨迹乘 alpha，中心差分 alpha±1e-4；目标为末时刻平均冷板固体温度。', '']
        if 'gradient' in record:
            g = record['gradient']
            lines += [f"目标 {g['objective_K']:.12g} K；autograd {g['autograd_directional_derivative']:.12g}，中心差分 {g['centered_difference']:.12g}；绝对误差 {g['absolute_error']:.3g}，相对误差 {g['relative_error']:.3g}。", '']
        lines += [f'- {name}: {seconds:.4f} s。' for name, seconds in record['walltime_seconds'].items()]
        if 'error' in record:
            lines += ['', f"停止于 {record.get('current_stage')}：{record['error']['type']} — {record['error']['message']}。"]
        lines += ['', '范围仅为该工况的平滑方向一阶导数；不代表现场精度、跨流向/阀门关闭切换导数或 agent 后训练已经完成。']
        original = markdown_path.read_text() if markdown_path.exists() else '# Shinaide CPU gradient verification\n'
        marker = f'\n## {title}方向补充验证'
        original = original.split(marker)[0]
        markdown_path.write_text(original.rstrip() + '\n' + '\n'.join(lines) + '\n')
        print(json.dumps({'status': record['status'], 'total_walltime_seconds': record['total_walltime_seconds'],
                          'evidence': str(json_path)}), flush=True)
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
