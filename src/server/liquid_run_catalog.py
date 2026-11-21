"""Saved liquid runs of the selected case and its hybrid heat ledger.

Reads only the files the run manager and the hybrid handoff already write,
without importing the solver, so listing runs costs JSON reads and never
starts torch. Restoring a run (``POST /api/liquid/runs/<id>/restore``) is the
run manager's job (``LiquidRunManager.restore``); this module only says which
runs exist, what they are and whether they can be restored.

Endpoints (wired in liquid_runtime_api / server.py):
  GET /api/liquid/runs              every saved run, newest result first
  GET /api/liquid/runs/<id>/saved   one run's header, metadata and receipt
  GET /api/hybrid/heat-ledger       model/heat-ledger.json of the case
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from liquid_result_files import liquid_result_files

RUN_ARTIFACTS = ('input.liquidrun', 'input.json', 'checkpoint.json', 'result.json',
                 'executed.liquidrun', 'executed-inputs.json')
TERMINAL_STATUSES = ('completed', 'cancelled', 'failed')
DAE_CHECKPOINT_FORMAT = 'liquid-dae-replay-checkpoint'
LEDGER_FORMAT_PREFIX = 'hybrid-air-heat-handoff/'
_RUN_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
_CACHE: dict[tuple, dict] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 512


class CatalogError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _stamp(path: Path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _case(case_dir) -> Path:
    if not case_dir:
        raise CatalogError(400, 'case_dir_missing', 'No case directory set')
    return Path(case_dir)


def _runtime_summary(run_dir: Path) -> dict:
    """Header fields of a runtime run directory, cached on file stamps."""
    key = (str(run_dir),) + tuple(_stamp(run_dir / name) for name in RUN_ARTIFACTS)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    present = {name: (run_dir / name).is_file() for name in RUN_ARTIFACTS}
    checkpoint = _json(run_dir / 'checkpoint.json') if present['checkpoint.json'] else None
    header = _json(run_dir / 'input.json') if present['input.json'] else None
    receipt = _json(run_dir / 'executed-inputs.json') if present['executed-inputs.json'] else None
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    header = header if isinstance(header, dict) else {}
    receipt = receipt if isinstance(receipt, dict) else {}
    metadata = header.get('metadata') if isinstance(header.get('metadata'), dict) else {}
    liquidrun = header.get('liquidrun') if isinstance(header.get('liquidrun'), dict) else {}
    is_dae = checkpoint.get('format') == DAE_CHECKPOINT_FORMAT
    status = checkpoint.get('run_status')
    seq = checkpoint.get('seq')
    accepted = receipt.get('accepted_sample_count')
    if not isinstance(accepted, int) or isinstance(accepted, bool):
        accepted = seq + 1 if isinstance(seq, int) and not isinstance(seq, bool) else None
    total = metadata.get('total_sample_count', receipt.get('original_sample_count'))
    if not is_dae:
        blocker = 'not a differentiable Newton DAE run; only DAE runs restore'
    elif status not in TERMINAL_STATUSES:
        blocker = f'run was {status!r} when last saved; only completed, cancelled or failed runs restore'
    else:
        missing = [name for name, ok in present.items() if not ok]
        blocker = f'missing {", ".join(missing)}' if missing else None
    summary = {
        'kind': 'dae' if is_dae else 'runtime',
        'status': status,
        'error': checkpoint.get('error'),
        'solver': metadata.get('solver'),
        'analysis': metadata.get('analysis') if isinstance(metadata.get('analysis'), dict) else None,
        'samples': {'accepted': accepted, 'total': total},
        'dt_s': metadata.get('dt_s'),
        'accepted_time_s': receipt.get('accepted_time_s'),
        'config_hash': header.get('config_hash', checkpoint.get('config_hash')),
        'project_revision': header.get('project_revision'),
        'source_project': liquidrun.get('source_project') if isinstance(liquidrun.get('source_project'), dict) else None,
        'execution': liquidrun.get('execution') if isinstance(liquidrun.get('execution'), dict) else None,
        'restorable': blocker is None,
        'restore_blocker': blocker,
    }
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.clear()
        _CACHE[key] = summary
    return summary


def _entry(result: Path, loaded: dict) -> dict:
    directory = result.parent
    is_runtime = result.name == 'result.json' and directory.name.startswith('run-')
    run_id = directory.name[4:] if is_runtime else None
    try:
        modified = result.stat().st_mtime
    except OSError:
        modified = None
    entry = {
        'name': directory.name,
        'run_id': run_id,
        'location': 'Liquid/log' if directory.parent.name == 'log' and directory.parent.parent.name == 'Liquid' else 'log',
        'modified': modified,
    }
    if is_runtime:
        entry.update(_runtime_summary(directory))
    else:
        entry.update({'kind': 'legacy', 'status': None, 'error': None, 'solver': None, 'analysis': None,
                      'samples': {'accepted': None, 'total': None}, 'dt_s': None, 'accepted_time_s': None,
                      'config_hash': None, 'project_revision': None, 'source_project': None,
                      'execution': None, 'restorable': False,
                      'restore_blocker': 'batch result without a run record; open it with /api/liquid/results'})
    entry['loaded'] = loaded.get(run_id) if run_id else None
    return entry


def list_liquid_runs(case_dir, loaded: dict | None = None) -> list[dict]:
    """Every saved liquid result of the case, newest first (the order
    /api/liquid/results uses for its default). ``loaded`` maps run ids the
    server holds in memory to their live ``{status, read_only}``."""
    case = _case(case_dir)
    return [_entry(result, loaded or {}) for result in liquid_result_files(case)]


def saved_run_metadata(case_dir, run_id: str) -> dict:
    """One runtime run's header, record metadata and accepted-input receipt."""
    case = _case(case_dir)
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise CatalogError(400, 'run_id_invalid', 'run id contains unsafe characters')
    run_dir = case / 'Liquid' / 'log' / f'run-{run_id}'
    if not (run_dir / 'result.json').is_file():
        raise CatalogError(404, 'liquid_run_not_found', f'No saved liquid run {run_id} in this case')
    header = _json(run_dir / 'input.json')
    receipt = _json(run_dir / 'executed-inputs.json')
    result = run_dir / 'result.json'
    return {
        'run': _entry(result, {}),
        'metadata': header.get('metadata') if isinstance(header, dict) else None,
        'receipt': receipt if isinstance(receipt, dict) else None,
        'artifacts': {name: (run_dir / name).is_file() for name in RUN_ARTIFACTS},
    }


def saved_run_exists(case_dir, run_id: str) -> bool:
    if not case_dir or not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        return False
    return (Path(case_dir) / 'Liquid' / 'log' / f'run-{run_id}' / 'result.json').is_file()


def read_heat_ledger(case_dir) -> dict:
    """``model/heat-ledger.json`` and whether it belongs to the active manifest.

    The server writes the ledger beside the derived hybrid .cfdrun when an air
    run is published with ``hybridHeat`` and deletes it on any ordinary
    export, so ``matches_active_cfdrun`` is normally true; false means the
    files were edited outside the server."""
    case = _case(case_dir)
    path = case / 'model' / 'heat-ledger.json'
    if not path.is_file():
        raise CatalogError(404, 'heat_ledger_missing',
                           'No model/heat-ledger.json in this case; it is written when an air run '
                           'is published with hybridHeat from a completed liquid run')
    ledger = _json(path)
    if not isinstance(ledger, dict) or not str(ledger.get('format', '')).startswith(LEDGER_FORMAT_PREFIX):
        raise CatalogError(409, 'heat_ledger_unreadable',
                           f'model/heat-ledger.json is not a {LEDGER_FORMAT_PREFIX}* document')
    manifests = sorted((case / 'model').glob('*.cfdrun'))
    active = None
    if len(manifests) == 1:
        manifest = _json(manifests[0])
        run_id = manifest.get('runId') if isinstance(manifest, dict) else None
        active = {'filename': manifests[0].name, 'run_id': run_id}
    return {
        'path': 'model/heat-ledger.json',
        'modified': path.stat().st_mtime,
        'active_cfdrun': active,
        'matches_active_cfdrun': (None if active is None else
                                  active['run_id'] is not None and
                                  active['run_id'] == ledger.get('air_derived_run_id')),
        'ledger': ledger,
    }
