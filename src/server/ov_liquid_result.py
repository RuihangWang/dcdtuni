"""Server-signed display grants for liquid-cooling DAE results (OV Stage 4).

The browser names a run; the server checks the run belongs to the selected
case, that its result carries the object bindings the colour rules need, and
that it names the project it was compiled from, then signs a short-lived
``liquidResult`` grant pinning the result file's SHA-256. Kit re-hashes the
file, binds it to the scene it acknowledged, and computes every colour itself,
so the browser can neither name a file nor forge a colour.

Display lineage only: a run receipt (result hash, config hash, solver, source
project). It says nothing about model calibration; mock/synthetic inputs keep
their own labels.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
import uuid

from ov_result_dataset import DatasetError, _grant_module

GRANT_TTL_SECONDS = 600
REFUSED_STATUSES = {'failed', 'error', 'cancelled', 'canceled'}


@lru_cache(maxsize=1)
def _colour_module():
    path = Path(__file__).resolve().parents[1] / (
        'frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/liquid_colour.py')
    spec = importlib.util.spec_from_file_location('_dcdtuni_liquid_colour', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('liquid colour module is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_liquid_run(case_dir: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise DatasetError(400, 'run_id_invalid', 'runId must be the 32-hex id of a DAE run')
    run = (Path(case_dir) / 'Liquid' / 'log' / f'run-{run_id}').resolve()
    if not (run / 'result.json').is_file():
        raise DatasetError(404, 'liquid_run_not_found', 'No DAE result for that run in the selected case')
    return run


def prepare_grant_payload(run: Path, run_id: str) -> dict:
    raw = (run / 'result.json').read_bytes()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DatasetError(409, 'liquid_result_unreadable', f'result.json is not JSON: {exc}') from None
    missing = _colour_module().missing_keys(data)
    if missing:
        raise _unbound(missing)
    status = str(data.get('status') or 'unknown')
    if status in REFUSED_STATUSES:
        raise DatasetError(409, 'liquid_run_not_displayable', f'Run status is {status!r}')
    if str(data.get('run_id') or run_id) != run_id:
        raise DatasetError(409, 'liquid_run_id_mismatch', 'result.json names a different run')
    try:
        source = json.loads((run / 'input.json').read_text())['liquidrun']['source_project']
        project_uid, project_sha = str(source['project_uid']), str(source['sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        raise _unbound(['source_project'])
    return {
        'kind': 'liquidResult', 'path': str(run / 'result.json'), 'runId': run_id,
        'resultSha256': hashlib.sha256(raw).hexdigest(),
        'configHash': str(data.get('config_hash') or ''), 'solver': str(data.get('solver') or ''),
        'status': status, 'samples': len(data.get('time_s') or []),
        'projectUid': project_uid, 'projectContentSha256': project_sha,
    }


def _unbound(missing):
    exc = DatasetError(422, 'liquid_result_unbound',
                       'Result is not displayable in OV: missing ' + ', '.join(missing))
    exc.missing = list(missing)
    return exc


def issue_grant(payload: dict, key: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    body = dict(payload, issuedAt=now, expiresAt=now + GRANT_TTL_SECONDS, nonce=uuid.uuid4().hex)
    try:
        return _grant_module().sign(body, key)
    except ValueError as exc:
        raise DatasetError(503, 'ov_grant_unavailable', str(exc)) from None
