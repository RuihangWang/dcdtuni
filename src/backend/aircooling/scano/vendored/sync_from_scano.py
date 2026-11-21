"""Refresh the vendored SCANO forward code (./scano) from a SCANO checkout, verbatim, and record VENDOR.json.

    python src/backend/aircooling/scano/vendored/sync_from_scano.py [--scano ~/mycode/SCANO] [--check]

Copies the import closure of the modules the inference uses (ENTRY) out of <scano>/src/scano, file for file and
unchanged, so dcdtuni deploys exactly the code the checkpoints were evaluated with; VENDOR.json records the SCANO
commit, whether its tree was clean, and the sha256 of every copied file. --check only compares ./scano with
VENDOR.json (a vendored file edited by hand fails it). Never edit ./scano: change SCANO, commit there, sync here.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / 'scano'
ENTRY = ('scano.Main.cfg_common', 'scano.Main.dcts_common', 'scano.Main.eval_dcts', 'scano.Datasets.dcts_source')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def closure(src: Path):
    """Relative paths (under src, the directory holding the scano package) of the import closure of ENTRY."""
    def modfile(m):
        p = src / m.replace('.', '/')
        for c in (p.with_suffix('.py'), p / '__init__.py'):
            if c.is_file():
                return c
    seen, stack = {}, list(ENTRY)
    while stack:
        m = stack.pop()
        f = modfile(m)
        if m in seen or f is None:
            continue
        seen[m] = f
        pkg = m if f.name == '__init__.py' else m.rsplit('.', 1)[0]
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Import):
                stack += [a.name for a in n.names if a.name.startswith('scano')]
            elif isinstance(n, ast.ImportFrom):
                base = pkg.split('.')
                mod = ('.'.join(base[:len(base) - (n.level - 1)] + ([n.module] if n.module else []))
                       if n.level else (n.module or ''))
                if mod.startswith('scano'):
                    stack += [mod] + [f'{mod}.{a.name}' for a in n.names]
    files = {f.relative_to(src) for f in seen.values()}
    for f in list(files):                         # every package on the way needs its __init__.py
        for parent in f.parents:
            if parent != Path('.') and (src / parent / '__init__.py').is_file():
                files.add(parent / '__init__.py')
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--scano', default=str(Path.home() / 'mycode' / 'SCANO'))
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    record = HERE / 'VENDOR.json'
    if a.check:
        rec = json.loads(record.read_text())
        bad = [f for f, h in rec['files'].items() if not (HERE / f).is_file() or _sha(HERE / f) != h]
        extra = sorted(str(p.relative_to(HERE)) for p in DEST.rglob('*.py') if str(p.relative_to(HERE)) not in rec['files'])
        print(f"{len(rec['files'])} vendored files from SCANO {rec['scano_commit'][:10]}; changed {bad}; unrecorded {extra}")
        return 1 if bad or extra else 0
    repo = Path(a.scano).expanduser().resolve()
    src = repo / 'src'
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src/scano'], cwd=repo, capture_output=True,
                           text=True).stdout.strip()
    if dirty:
        print(f'SCANO src/scano has uncommitted changes; commit them first:\n{dirty}', file=sys.stderr)
        return 1
    files = closure(src)
    if DEST.exists():
        shutil.rmtree(DEST)
    for f in files:
        (HERE / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / f, HERE / f)
    rec = {'scano_repository': 'Litianyu141/SCANO', 'scano_commit': commit, 'entry_modules': list(ENTRY),
           'files': {str(f): _sha(HERE / f) for f in files}}
    record.write_text(json.dumps(rec, indent=1) + '\n')
    print(f'{len(files)} files from SCANO {commit[:10]} into {DEST}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
