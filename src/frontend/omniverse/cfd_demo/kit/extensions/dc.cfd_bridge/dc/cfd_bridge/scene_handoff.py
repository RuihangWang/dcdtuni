"""Scene handoff: the editor scene reaches Kit while the stream is still connecting.

Over the WebRTC data channel a scene could only start moving once the stream
was up, and Kit only started authoring it after the last chunk: on a first
entry the browser waited for the handshake, then the transfer, then the USD
authoring, one after another. When the page picks OV it now also posts the
scene to the dcdtuni server, which writes it here, in Kit's runtime directory,
under a display grant signed with the per-launch key (display_grant, kind
renderScene). Kit's bridge watches the pending grant, verifies it and authors
the scene; once the channel is open the page asks Kit to reuse that scene.

Pure module: no omni/pxr imports. The server loads this very file to write,
the bridge imports it to read, and the CPU tests exercise both halves.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

DIRECTORY = 'scene-handoff'
PENDING = 'pending.json'
GRANT_TTL_SECONDS = 120
MAX_SCENE_BYTES = 128 * 1024 * 1024
_HEX64 = re.compile(r'[0-9a-f]{64}')


def _grants():
    try:
        from . import display_grant  # inside the bridge package
        return display_grant
    except ImportError:  # loaded by path from the server
        path = Path(__file__).with_name('display_grant.py')
        spec = importlib.util.spec_from_file_location('_dcdtuni_display_grant_handoff', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def directory(runtime_dir: str) -> str:
    return os.path.join(os.path.realpath(runtime_dir), DIRECTORY)


def _replace(path: str, data: bytes) -> None:
    temporary = f'{path}.{secrets.token_hex(6)}.tmp'
    with open(temporary, 'xb') as stream:
        stream.write(data)
    os.replace(temporary, path)


def write(runtime_dir: str, packet: bytes, project_hash: str, scene_key: str, key: str,
          now: float | None = None) -> dict:
    """Server side: store the scene bytes and the signed grant naming them."""
    if not isinstance(packet, bytes) or not 0 < len(packet) <= MAX_SCENE_BYTES:
        raise ValueError('render scene is empty or exceeds capacity')
    for name, value in (('projectHash', project_hash), ('sceneKey', scene_key)):
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise ValueError(f'invalid {name}')
    target = directory(runtime_dir)
    os.makedirs(target, exist_ok=True)
    sha = hashlib.sha256(packet).hexdigest()
    path = os.path.join(target, f'scene-{sha}.json')
    if not os.path.isfile(path):
        _replace(path, packet)
    now = time.time() if now is None else now
    grant = _grants().sign({'kind': 'renderScene', 'path': path, 'packetSha256': sha,
                            'projectHash': project_hash, 'sceneKey': scene_key,
                            'issuedAt': now, 'expiresAt': now + GRANT_TTL_SECONDS,
                            'nonce': uuid.uuid4().hex}, key)
    _replace(os.path.join(target, PENDING), json.dumps(grant).encode('ascii'))
    # Only the scene the pending grant names is kept.
    for name in os.listdir(target):
        if name.startswith('scene-') and name.endswith('.json') and os.path.join(target, name) != path:
            try:
                os.unlink(os.path.join(target, name))
            except FileNotFoundError:
                pass
    return grant


def pending_stamp(runtime_dir: str):
    """What changes when the server writes a new pending grant, or None."""
    try:
        stat = os.stat(os.path.join(directory(runtime_dir), PENDING))
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_ino, stat.st_size


def read(runtime_dir: str, key: str, now: float | None = None):
    """Kit side: (verified grant payload, scene bytes); ValueError when the
    grant does not verify, has expired or the bytes are not the signed ones."""
    target = directory(runtime_dir)
    with open(os.path.join(target, PENDING), 'rb') as stream:
        grant = json.loads(stream.read(64 * 1024))
    payload = _grants().verify(grant, key, time.time() if now is None else now, [target], kind='renderScene')
    with open(payload['path'], 'rb') as stream:
        packet = stream.read(MAX_SCENE_BYTES + 1)
    if len(packet) > MAX_SCENE_BYTES or hashlib.sha256(packet).hexdigest() != payload['packetSha256']:
        raise ValueError('handed-off scene differs from its grant')
    return payload, packet
