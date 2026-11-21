"""Server-issued display grants for Kit dataset loads (OV display campaign).

The browser never hands Kit a filesystem path it made up. The dcdtuni server
picks a run from its own case directory, converts it, and signs a short-lived
grant naming the converted file; the browser relays that grant verbatim and Kit
verifies it here before touching the disk. Both processes are launched by
``start_server.sh``, which generates the shared HMAC key per launch
(``DCDTUNI_OV_GRANT_KEY``), so a page cannot mint one.

A grant says only "the server selected this display dataset from its case"; it
is not a result-provenance receipt. Result layers stay behind their own
fail-closed lineage check (``result_layers_usd.require_trusted_result_context``).

Pure module: no omni/pxr imports, so the server loads this very file for
signing and the CPU tests exercise both halves against each other.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os

GRANT_SCHEMA = 'dcdtuni.ov-display-grant/v1'
MAX_TTL_SECONDS = 900
CLOCK_SKEW_SECONDS = 30
MIN_KEY_LENGTH = 32
DATASET_SUFFIXES = ('.case', '.vtu', '.vtm')
_COMMON_KEYS = {'schema', 'kind', 'path', 'runId', 'issuedAt', 'expiresAt', 'nonce'}
# Each grant kind has its own exact payload keys and file suffixes, so a grant
# for one purpose can never be replayed for another (a dataset grant is not a
# liquid-result grant, and a result.json is never a volume dataset).
KINDS = {
    'dataset': (_COMMON_KEYS | {'run', 'time'}, DATASET_SUFFIXES),
    'liquidResult': (_COMMON_KEYS | {'resultSha256', 'configHash', 'solver', 'status', 'samples',
                                     'projectUid', 'projectContentSha256'}, ('.json',)),
}
_PAYLOAD_KEYS = KINDS['dataset'][0]


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _require_key(key):
    if not isinstance(key, str) or len(key) < MIN_KEY_LENGTH:
        raise ValueError('display grant key is not configured')
    return key.encode('utf-8')


def sign(payload: dict, key: str) -> dict:
    """Server side: stamp the schema and HMAC-SHA256 the canonical payload."""
    raw_key = _require_key(key)
    body = dict(payload)
    body['schema'] = GRANT_SCHEMA
    mac = hmac.new(raw_key, _canonical(body), hashlib.sha256).hexdigest()
    return {'payload': body, 'signature': mac}


def roots_from_env(value) -> list:
    """``os.pathsep``-separated allow-list of dataset roots; blanks dropped."""
    return [p for p in (value or '').split(os.pathsep) if p.strip()]


def _inside(path: str, root: str) -> bool:
    root = os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def verify(grant, key, now: float, roots, kind: str = 'dataset') -> dict:
    """Kit side: return the verified payload (with ``path`` resolved) or raise
    ValueError. Every failure is closed — no key, no roots, bad shape, bad
    signature, wrong kind for this command, outside the validity window,
    outside the roots, wrong format."""
    if kind not in KINDS:
        raise ValueError(f'unknown display grant kind {kind!r}')
    expected_keys, suffixes = KINDS[kind]
    raw_key = _require_key(key)
    if not isinstance(grant, dict) or set(grant) != {'payload', 'signature'}:
        raise ValueError('display grant must carry exactly payload and signature')
    payload, signature = grant['payload'], grant['signature']
    if not isinstance(payload, dict) or not isinstance(signature, str) or len(signature) != 64:
        raise ValueError('display grant shape is invalid')
    try:
        expected = hmac.new(raw_key, _canonical(payload), hashlib.sha256).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValueError(f'display grant payload is not canonical JSON: {exc}') from None
    if not hmac.compare_digest(expected, signature):
        raise ValueError('display grant signature does not verify')
    if payload.get('kind') != kind:
        raise ValueError(f"display grant kind {payload.get('kind')!r} is not {kind!r}")
    if set(payload) != expected_keys or payload['schema'] != GRANT_SCHEMA:
        raise ValueError('display grant payload fields are unexpected')
    issued, expires = payload['issuedAt'], payload['expiresAt']
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (issued, expires)):
        raise ValueError('display grant times must be finite numbers')
    if expires - issued > MAX_TTL_SECONDS or expires <= issued:
        raise ValueError('display grant lifetime is out of bounds')
    if now < issued - CLOCK_SKEW_SECONDS:
        raise ValueError('display grant is not yet valid')
    if now > expires:
        raise ValueError('display grant has expired')
    path = payload['path']
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError('display grant path must be absolute')
    real = os.path.realpath(path)
    if not any(_inside(real, r) for r in roots or []):
        raise ValueError('display grant path is outside every configured dataset root')
    if os.path.splitext(real)[1].lower() not in suffixes:
        raise ValueError('display grant names an unsupported dataset format')
    if not os.path.isfile(real):
        raise ValueError('display grant dataset does not exist')
    return {**payload, 'path': real}
