"""Prepare an OpenFOAM run for the OV display bridge and sign a display grant.

The browser picks a run by the same ``runId`` ``/api/pp/runs`` lists; nothing
it sends becomes a path. The server converts the run's latest time (or an explicit complete frame) with
``foamToEnsight`` (Kit's bridge turns the EnSight ``.case`` into its own .vtu)
and signs a short-lived grant naming the converted file. Kit verifies the grant
with the key both processes received from ``start_server.sh``.

The conversion runs inside a derived mirror, ``<run>/.ov-display/<sig>/``:
symlinks to the run's ``constant/`` and ``system/`` and to the latest time's
field files. ``foamToEnsight`` silently skips a time that has no
``uniform/time`` (manufactured display fixtures carry none), so the mirror
supplies one when the source lacks it — the run's own directories are never
written. The mirror name is a signature of the source files, so a re-solve
produces a new mirror instead of serving a stale conversion.

A partitioned run (the dcCart default: mesh and solve on N ranks, never
reconstructed) holds its results only in ``processor*/``. Its mirror repeats
the layout per processor and ``foamToEnsight -parallel`` runs on N ranks, which
writes the same single EnSight case a serial run produces.

Display plumbing only: a grant is not a result-provenance receipt, and
``/api/pp/render-source`` stays fail-closed.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

MIRROR_DIRNAME = '.ov-display'
GRANT_TTL_SECONDS = 600
_LOCK = threading.Lock()
_NUMERIC = re.compile(r'^[0-9]+(\.[0-9]+)?(e[-+]?[0-9]+)?$')


class DatasetError(Exception):
    def __init__(self, status: int, code: str, error: str):
        super().__init__(error)
        self.status, self.code, self.error = status, code, error


@lru_cache(maxsize=1)
def _grant_module():
    # The single grant implementation lives with its verifier in the Kit
    # bridge; load the pure file directly (importing the Kit package would
    # pull in omni).
    path = Path(__file__).resolve().parents[1] / (
        'frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/display_grant.py')
    spec = importlib.util.spec_from_file_location('_dcdtuni_display_grant', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('display grant module is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_run(case_dir: Path, run_id: str) -> Path:
    """Resolve a /api/pp/runs identity back to its run directory."""
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{64}', run_id):
        raise DatasetError(400, 'run_id_invalid', 'runId must be a 64-hex identity from /api/pp/runs')
    log_dir = Path(case_dir) / 'log'
    if log_dir.is_dir():
        for d in log_dir.iterdir():
            if d.is_dir() and not d.name.startswith('.'):
                resolved = d.resolve()
                if hashlib.sha256(str(resolved).encode('utf-8')).hexdigest() == run_id:
                    return resolved
    raise DatasetError(404, 'run_not_found', 'No run with that identity in the selected case')


def _field_files(time_dir: Path):
    return sorted(p for p in time_dir.iterdir() if p.is_file() and not p.name.startswith('.'))


def latest_field_time(run: Path):
    """(name, path) of the numerically latest time directory holding fields."""
    times = []
    for d in Path(run).iterdir():
        if d.is_dir() and _NUMERIC.match(d.name) and _field_files(d):
            times.append((float(d.name), d.name, d))
    if not times:
        if any(p.is_dir() and p.name.startswith('processor') for p in Path(run).iterdir()):
            raise DatasetError(409, 'run_decomposed',
                               'Run holds only decomposed processor* results; reconstruct it before OV display')
        raise DatasetError(404, 'run_has_no_fields', 'Run has no time directory with field files')
    times.sort()
    return times[-1][1], times[-1][2]


_PROCESSOR = re.compile(r'^processor([0-9]+)$')


def processor_dirs(run: Path):
    """processor0..N-1 of a decomposed run, in rank order ([] when serial)."""
    found = [(int(m.group(1)), d) for d in Path(run).iterdir()
             if d.is_dir() and (m := _PROCESSOR.match(d.name))]
    return [d for _, d in sorted(found)]


def latest_decomposed_time(run: Path):
    """(name, [per-processor time dirs]) of the latest time every processor holds fields for."""
    procs = processor_dirs(run)
    if not procs:
        raise DatasetError(404, 'run_has_no_fields', 'Run has no time directory with field files')
    common = None
    for proc in procs:
        times = {d.name: float(d.name) for d in proc.iterdir()
                 if d.is_dir() and _NUMERIC.match(d.name) and _field_files(d)}
        common = times if common is None else {k: v for k, v in common.items() if k in times}
    if not common:
        raise DatasetError(409, 'run_decomposed_incomplete',
                           'No time holds field files on every processor of this decomposed run')
    name = max(common, key=common.get)
    return name, [proc / name for proc in procs]


_LATEST = object()

def validate_frame_time(value):
    """Explicit frames are positive finite JSON numbers, never coercions."""
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise DatasetError(400, 'frame_time_invalid', 'time must be a positive finite numeric result time')
    return value

def _matching_time(parent: Path, value):
    matches = []
    for child in parent.iterdir():
        if not child.is_dir():
            continue
        try:
            numeric = float(child.name)
        except ValueError:
            continue
        if math.isfinite(numeric) and numeric == value:
            matches.append(child)
    if len(matches) > 1:
        raise DatasetError(409, 'frame_time_ambiguous', 'Multiple directory spellings name the selected time')
    if not matches or not any((matches[0] / field).is_file() or (matches[0] / (field + '.gz')).is_file()
                              for field in ('T', 'U', 'p_rgh', 'p')):
        raise DatasetError(409, 'frame_time_unavailable', 'Selected time has no complete result fields')
    return matches[0]

def _selected_source(run: Path, time_value):
    # Share normal postprocessing discovery: complete root or every contiguous
    # complete processor mesh. Readiness is not a quality/provenance certificate.
    from backend.aircooling.runner.mesh_layout import available_result_times, mesh_directories, processor_mesh_dirs
    value = validate_frame_time(time_value)
    if value not in available_result_times(run):
        raise DatasetError(409, 'frame_time_unavailable', 'Selected time is not complete in this run')
    root = run / 'constant/polyMesh'
    meshes = mesh_directories(run)
    if meshes == [root]:
        try:
            td = _matching_time(run, value)
            return td.name, td, None
        except DatasetError as exc:
            if exc.code != 'frame_time_unavailable':
                raise
    meshes = processor_mesh_dirs(run)
    if not meshes:
        raise DatasetError(409, 'frame_time_unavailable', 'Selected time has no complete mesh')
    dirs = [_matching_time(mesh.parent.parent, value) for mesh in meshes]
    return dirs[0].name, dirs[0], dirs

def _resolve_source(run: Path, time_value):
    if time_value is not _LATEST:
        return _selected_source(run, time_value)
    try:
        name, td = latest_field_time(run)
        return name, td, None
    except DatasetError as exc:
        if exc.code != 'run_decomposed':
            raise
        name, dirs = latest_decomposed_time(run)
        return name, dirs[0], dirs

# Bumped when the conversion itself changes: v2 exports the boundary patches
# too (the OV surface field), so a cached internal-only conversion is not reused.
_CONVERSION_FORMAT = 'ensight-internal+boundary-v2'


def _resolved_signature(run: Path, selection):
    name, td, dirs = selection
    base = _source_signature(run, name, td) if dirs is None else _decomposed_signature(run, name, dirs)
    return hashlib.sha256(f'{_CONVERSION_FORMAT}:{base}'.encode()).hexdigest()


def _stat_material(prefix: str, files):
    out = []
    for p in files:
        st = p.stat()
        out.append([f'{prefix}/{p.name}', st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino])
    return out


def _source_signature(run: Path, time_name: str, time_dir: Path) -> str:
    material = [time_name]
    mesh = run / 'constant' / 'polyMesh'
    material += _stat_material('mesh', [p for p in sorted(mesh.iterdir()) if p.is_file()] if mesh.is_dir() else [])
    material += _stat_material('field', _field_files(time_dir))
    material += _stat_material('uniform', [time_dir / 'uniform/time'] if (time_dir / 'uniform/time').is_file() else [])
    material += _stat_material('system', [p for p in sorted((run / 'system').iterdir()) if p.is_file()] if (run / 'system').is_dir() else [])
    return hashlib.sha256(json.dumps(material, separators=(',', ':')).encode()).hexdigest()


def _decomposed_signature(run: Path, time_name: str, time_dirs) -> str:
    material = [time_name, 'decomposed', [str(td.relative_to(run)) for td in time_dirs]]
    for td in time_dirs:
        proc = td.parent
        mesh = proc / 'constant' / 'polyMesh'
        material += _stat_material(f'{proc.name}/mesh',
                                   [p for p in sorted(mesh.iterdir()) if p.is_file()] if mesh.is_dir() else [])
        material += _stat_material(f'{proc.name}/field', _field_files(td))
        material += _stat_material(f'{proc.name}/uniform', [td / 'uniform/time'] if (td / 'uniform/time').is_file() else [])
    material += _stat_material('system', [p for p in sorted((run / 'system').iterdir()) if p.is_file()] if (run / 'system').is_dir() else [])
    return hashlib.sha256(json.dumps(material, separators=(',', ':')).encode()).hexdigest()


def _uniform_time(time_name: str) -> str:
    return ('FoamFile\n{\n    version 2.0;\n    format ascii;\n    class dictionary;\n'
            '    object time;\n}\n'
            f'value {time_name};\nname "{time_name}";\nindex 0;\ndeltaT 1;\ndeltaT0 1;\n')


def _ensight_output(mirror: Path):
    case_file = mirror / 'EnSight' / f'{mirror.name}.case'
    data = mirror / 'EnSight' / 'data'
    has_fields = data.is_dir() and any(
        p.is_file() for d in data.iterdir() if d.is_dir() and d.name != 'constant' for p in d.iterdir())
    return case_file, case_file.is_file() and has_fields


def _link_time(target: Path, time_name: str, time_dir: Path):
    (target / time_name).mkdir(parents=True)
    for f in _field_files(time_dir):
        os.symlink(f, target / time_name / f.name)
    uniform = time_dir / 'uniform'
    if (uniform / 'time').is_file():
        os.symlink(uniform, target / time_name / 'uniform')
    else:
        (target / time_name / 'uniform').mkdir()
        (target / time_name / 'uniform' / 'time').write_text(_uniform_time(time_name))


def _build_mirror(run: Path, mirror: Path, time_name: str, time_dir: Path):
    if mirror.exists():
        shutil.rmtree(mirror)
    mirror.mkdir(parents=True)
    for name in ('constant', 'system'):
        if (run / name).is_dir():
            os.symlink(run / name, mirror / name)
    _link_time(mirror, time_name, time_dir)


def _build_decomposed_mirror(run: Path, mirror: Path, time_name: str, time_dirs):
    if mirror.exists():
        shutil.rmtree(mirror)
    mirror.mkdir(parents=True)
    for name in ('constant', 'system'):
        if (run / name).is_dir():
            os.symlink(run / name, mirror / name)
    for td in time_dirs:
        proc = td.parent
        (mirror / proc.name).mkdir()
        if (proc / 'constant').is_dir():
            os.symlink(proc / 'constant', mirror / proc.name / 'constant')
        _link_time(mirror / proc.name, time_name, td)


def prepare_ensight(run: Path, convert, *, time_value=_LATEST) -> dict:
    """Convert latest (legacy) or an explicitly selected complete frame.

    The injected converter needs no OpenFOAM in CPU tests. Its mirror contains
    just this frame on all required ranks. Explicit frames never fall back.
    """
    run = Path(run).resolve()
    with _LOCK:
        selection = _resolve_source(run, time_value)
        time_name, time_dir, time_dirs = selection
        sig = _resolved_signature(run, selection)
        mirror = run / MIRROR_DIRNAME / sig[:16]
        def unchanged():
            try:
                current = _resolve_source(run, time_value)
                return _resolved_signature(run, current) == sig
            except (DatasetError, OSError):
                return False
        case_file, ready = _ensight_output(mirror)
        if ready:
            if not unchanged():
                raise DatasetError(409, 'dataset_source_changed', 'Result source changed while preparing display')
            return {'path': str(case_file), 'time': time_name, 'cached': True,
                    'fields': [p.name for p in _field_files(time_dir)], 'signature': sig}
        if time_dirs is None:
            _build_mirror(run, mirror, time_name, time_dir)
            rc, log = convert(mirror)
        else:
            _build_decomposed_mirror(run, mirror, time_name, time_dirs)
            rc, log = convert(mirror, nprocs=len(time_dirs))
        if not unchanged():
            # A conversion from a moving source must not become a cache hit if
            # source stats are later restored.
            shutil.rmtree(mirror)
            raise DatasetError(409, 'dataset_source_changed', 'Result source changed while preparing display')
        if rc != 0:
            shutil.rmtree(mirror)
            raise DatasetError(500, 'ensight_conversion_failed',
                               f'foamToEnsight exited {rc}: {str(log)[-400:]}')
        case_file, ready = _ensight_output(mirror)
        if not ready:
            raise DatasetError(500, 'ensight_conversion_empty',
                               f'foamToEnsight wrote no field data: {str(log)[-400:]}')
    return {'path': str(case_file), 'time': time_name, 'cached': False,
            'fields': [p.name for p in _field_files(time_dir)], 'signature': sig}


def foam_to_ensight(foam_prefix: str, timeout: float = 600):
    """The production converter: foamToEnsight on the mirror, internal mesh and
    boundary patches (the patches are the OV surface field); a decomposed
    mirror converts on its N ranks (-parallel)."""
    import shlex
    import subprocess

    def convert(mirror: Path, nprocs: int | None = None):
        launch = f'mpirun -np {int(nprocs)} ' if nprocs else ''
        parallel = ' -parallel' if nprocs else ''
        cmd = (f'{foam_prefix}{launch}foamToEnsight -case {shlex.quote(str(mirror))} '
               f'-latestTime{parallel}')
        proc = subprocess.run(['bash', '-c', cmd], capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or '') + (proc.stderr or '')
    return convert


def issue_grant(prepared: dict, run: Path, run_id: str, key: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    grants = _grant_module()
    try:
        return grants.sign({
            'kind': 'dataset', 'path': prepared['path'], 'runId': run_id, 'run': Path(run).name,
            'time': prepared['time'], 'issuedAt': now, 'expiresAt': now + GRANT_TTL_SECONDS,
            'nonce': uuid.uuid4().hex,
        }, key)
    except ValueError as exc:
        raise DatasetError(503, 'ov_grant_unavailable', str(exc)) from None
