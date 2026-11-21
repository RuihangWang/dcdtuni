#!/usr/bin/env python3
"""Independent CPU-only replay in a new private directory; source repo is read-only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

B = Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
F = Path(__file__).resolve().parent
FE = ['sharedOvPreviewInteraction.test.ts', 'sharedOvPostprocessAdapter.test.ts', 'ovProtocol.test.ts',
      'sharedPostprocessDriver.test.ts', 'sharedDisplayUiOwnership.test.tsx', 'commonRenderIntent.test.ts']
NATIVE = 'src/frontend/omniverse/cfd_demo/tests/test_common_postprocess_previews.py'


def owned(path):
    path = Path(path).resolve()
    if not path.is_relative_to(B) or path == B:
        raise ValueError('Private replay path must stay inside dcdtuni')
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--node-bin', required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--node-modules', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source, node, python, output = map(owned, (args.source, args.node_bin, args.python, args.output))
    if output.exists():
        raise ValueError('Use a new private output directory; existing files are never overwritten')
    gitroot = subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--show-toplevel'], text=True).strip()
    if Path(gitroot).resolve() != source:
        raise ValueError('Source must be an actual repository root')
    bindings = json.loads((F/'TARGETS.json').read_text())['targets']
    for entry in bindings:
        path = source/entry['path']
        if path.exists():
            if not path.resolve().is_relative_to(B):
                raise ValueError('Source target escapes repository')
            if sha(path) not in (entry.get('beforeSha256'), entry['afterSha256']):
                raise ValueError('Source before/after guard mismatch: '+entry['path'])
        elif entry['beforeExists']:
            raise ValueError('Source input missing: '+entry['path'])
    modules = owned(args.node_modules)
    if not modules.is_dir() or not modules.resolve().is_relative_to(B):
        raise ValueError('Use repo-owned installed node_modules')
    output.mkdir(parents=True)
    tree, logs, tmp = output/'reviewtree', output/'logs', output/'tmp'
    tree.mkdir(); logs.mkdir(); tmp.mkdir()
    tracked = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z', '--', 'src/frontend', 'src/backend', 'tools']).decode().split('\0')
    closure = []
    for rel in tracked:
        if not rel or '/Ref_code/' in rel:
            continue
        src, dst = source/rel, tree/rel
        if not src.is_file():
            continue
        if not src.resolve().is_relative_to(B):
            raise ValueError('Tracked source escapes repository')
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        closure.append(dict(path=rel, bytes=src.stat().st_size, sha256=sha(src)))
    for entry in bindings:
        src, dst = F/'after'/entry['path'], tree/entry['path']
        if sha(src) != entry['afterSha256']:
            raise ValueError('Frozen after hash mismatch')
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (tree/'src/frontend/react/node_modules').symlink_to(modules.resolve(), target_is_directory=True)
    cache = output/'npm-cache'; cache.mkdir()
    env = dict(os.environ, PATH=str(node)+':'+str(python.parent)+':'+os.environ['PATH'],
        CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2',
        NUMEXPR_NUM_THREADS='2', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
        TMPDIR=str(tmp), npm_config_cache=str(cache), NPM_CONFIG_CACHE=str(cache))
    versions = {}
    for name, argv in [('node',[str(node/'node'),'--version']), ('npm',[str(node/'npm'),'--version']), ('python',[str(python),'--version'])]:
        versions[name] = subprocess.check_output(argv, env=env, text=True).strip()
    results = []
    def run(name, argv, cwd):
        print('START '+name, flush=True)
        log = logs/(name+'.log'); started = time.time()
        with log.open('wb') as stream:
            process = subprocess.Popen(['taskset','-c','36,37',*argv], cwd=cwd, env=env,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
                rc = 124
        result = dict(name=name, argv=argv, cwd=str(cwd), rc=rc, elapsedSeconds=time.time()-started,
            log=str(log), bytes=log.stat().st_size, sha256=sha(log))
        results.append(result)
        print(json.dumps(result), flush=True)
        return rc
    code = run('frontend-139', [str(node/'node'),'node_modules/vitest/vitest.mjs','run',
        *['test/'+name for name in FE], '--maxWorkers=2','--minWorkers=2'], tree/'src/frontend/react')
    code |= run('native-38', [str(python),'-B','-m','pytest','-q','-p','no:cacheprovider',NATIVE], tree)
    code |= run('production-build', [str(node/'npm'),'run','build'], tree/'src/frontend/react')
    result = dict(source=str(source), sourceHead=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip(),
        sourceClosure=closure, nodeModules=str(modules), sourcePackageLockSha256=sha(source/'src/frontend/react/package-lock.json'), versions=versions, cpu=[36,37], cuda='', npmCache=str(cache),
        candidateTargets=bindings, results=results,
        limitations=['CPU API-contract controls only; no Kit app/video/native hit acceptance',
            '8 unchanged legacy native failures are preserved separately, not silently skipped from their original run',
            'output_layout.capture_current remains fail-closed; no encoded framebuffer transform proof'])
    (output/'REPLAY.json').write_text(json.dumps(result, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
