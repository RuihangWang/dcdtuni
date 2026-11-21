"""Bound a Shinaide OpenFOAM solve and clean only processes in its scratch case.

This is a ten-iteration runtime smoke on an already generated mesh, not a
convergence claim. The real project and its original .cfdrun remain read-only.
Never point this script at the production Shinaide case directory.
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


def owned_processes(case_dir: Path) -> list[int]:
    """Find only this user's processes whose cwd is the unique scratch case."""
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            cwd = Path(os.readlink(entry / 'cwd')).resolve()
            if cwd == case_dir or case_dir in cwd.parents:
                result.append(pid)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    return sorted(result)


def clean_owned(case_dir: Path) -> dict:
    candidates = owned_processes(case_dir)
    for pid in candidates:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if candidates:
        time.sleep(2)
    stubborn = [pid for pid in candidates if pid in owned_processes(case_dir)]
    for pid in stubborn:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if stubborn:
        time.sleep(.2)
    remaining = owned_processes(case_dir)
    return {'term_pids': candidates, 'kill_pids': stubborn, 'remaining_owned_pids': remaining}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    case_dir = args.case_dir.resolve()
    expected = 'dcdtuni-shinaide-air-smoke-corrected-20260927-'
    if case_dir.parent != Path('/tmp') or not case_dir.name.startswith(expected):
        raise ValueError('Only the dedicated /tmp Shinaide air-smoke case is permitted')
    if not 1 <= args.seconds <= 150:
        raise ValueError('Solve smoke deadline must be in 1..150 seconds')
    root = Path(__file__).resolve().parents[2]
    manifest = case_dir / 'model' / 'Shinaide.cfdrun'
    mesh_faces = case_dir / 'Mesh' / 'constant' / 'polyMesh' / 'faces'
    if not manifest.is_file() or not mesh_faces.is_file():
        raise ValueError('The scratch .cfdrun and completed mesh must both exist')
    config = json.loads(manifest.read_text())
    top = config['simulationConfig']
    blob = config['meshGeometry']['simulationConfig']
    if any(value['solveProcess'] != 4 or value['endTime'] != 10 or
           value['parallelSolve'] is not True for value in (top, blob)):
        raise ValueError('This smoke requires the explicit 4-rank/10-step derived input')
    cpus = {0, 1, 2, 3}
    if not cpus <= os.sched_getaffinity(0):
        raise ValueError('CPU 0..3 are not available to the caller')
    if owned_processes(case_dir):
        raise RuntimeError('An owned process is already working in this scratch case')
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    command = [sys.executable, '-m', 'backend.aircooling.runner.run_cfd',
               '--from-cfdrun', str(manifest), '--solve-only']
    plan = {'case_dir': str(case_dir), 'manifest_sha256': manifest_sha,
            'cpu_affinity': sorted(cpus), 'nice': 10, 'cuda_visible_devices': '',
            'max_solve_seconds': args.seconds, 'command': command,
            'mesh_quality_gate': 'checkMesh reported three failed checks; runtime smoke only'}
    if args.plan_only:
        print(json.dumps(plan))
        return 0

    os.sched_setaffinity(0, cpus)
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    env = os.environ.copy()
    env.update(PYTHONPATH=str(root / 'src'), CUDA_VISIBLE_DEVICES='',
               OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               OPENBLAS_NUM_THREADS='1', FOAM_SIGFPE='false')
    env['PATH'] = str(Path(sys.prefix) / 'bin') + ':' + env.get('PATH', '')
    log_file = case_dir / 'solve.stdout.log'
    report_path = case_dir / 'solve-smoke.json'
    report = {'status': 'running', **plan, 'started_unix_s': time.time(),
              'actual_cpu_affinity': sorted(os.sched_getaffinity(0)),
              'actual_nice': os.getpriority(os.PRIO_PROCESS, 0),
              'log_file': str(log_file)}
    def interrupt(_signal, _frame):
        raise InterruptedError('Supervisor interrupted; task-owned children will be cleaned')
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupt)
    proc = None
    try:
        with log_file.open('w') as stream:
            proc = subprocess.Popen(command, cwd=root, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            report['runner_pid'] = proc.pid
            try:
                report['runner_exit_code'] = proc.wait(timeout=args.seconds)
                report['status'] = 'completed' if proc.returncode == 0 else 'failed'
            except subprocess.TimeoutExpired:
                report['status'] = 'timed_out'
                report['runner_exit_code'] = None
    except BaseException as exc:
        report['status'] = 'interrupted'
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        # Stop the Python runner first so it cannot spawn a fresh MPI command
        # after the scratch-cwd sweep below. Its OpenFOAM children use their own
        # sessions, hence the separate ownership check is still required.
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        report['cleanup'] = clean_owned(case_dir)
        if proc is not None:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        report['finished_unix_s'] = time.time()
        report['elapsed_s'] = report['finished_unix_s'] - report['started_unix_s']
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        print(json.dumps({'status': report['status'], 'return_code': report.get('runner_exit_code'),
                          'cleanup': report['cleanup'], 'report': str(report_path)}), flush=True)
    return 0 if report['status'] == 'completed' and not report['cleanup']['remaining_owned_pids'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
