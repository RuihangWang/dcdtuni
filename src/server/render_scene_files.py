"""Persist a validated, derived render snapshot inside the captured server case.

No solver or USD runtime imports. This writer accepts no output path and never
changes an editable project. The HTTP caller owns authentication, the raw body
size limit, and capturing CASE_DIR for this request.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import uuid

MAX_REQUEST_BYTES = 64 * 1024 * 1024


@lru_cache(maxsize=1)
def _validator():
    # Load the pure file directly: importing its Kit package __init__ would try
    # to import the extension and its optional omni runtime unnecessarily.
    path = Path(__file__).resolve().parents[1] / (
        'frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/render_scene.py'
    )
    spec = importlib.util.spec_from_file_location('_dcdtuni_render_scene_validation', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Render scene validator is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_snapshot


def _encoded(value):
    """Bound bytes while encoding, including escaped content and Unicode."""
    output = bytearray()
    try:
        for fragment in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':')).iterencode(value):
            data = fragment.encode('utf-8')
            if len(output) + len(data) > MAX_REQUEST_BYTES:
                raise ValueError('Render export exceeds the 64 MiB size limit')
            output.extend(data)
    except (TypeError, UnicodeError, RecursionError) as error:
        raise ValueError('Render export must contain finite JSON data') from error
    return bytes(output)


def _directory(path: Path):
    """Open the resolved root without following any newly substituted symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except OSError as error:
        os.close(fd)
        raise ValueError('Captured case directory is unavailable or contains a symlink') from error


def _child_directory(parent_fd, name):
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        raise ValueError('Render export directory must be a real directory, not a symlink') from error


def _same_directory(path, fd):
    try:
        actual, opened = path.stat(follow_symlinks=False), os.fstat(fd)
    except OSError as error:
        raise ValueError('Render export directory changed during this request') from error
    if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
        raise ValueError('Render export directory changed during this request')


def export_render_scene(body, *, captured_case_dir: Path):
    """Return a receipt only after an atomic, non-overwriting JSON publication.

    Invalid request/schema/case identity/path raises ValueError. Filesystem I/O
    failures propagate as OSError; they are never returned as successful saves.
    """
    if not isinstance(body, dict) or set(body) != {'scene', 'case_dir'}:
        raise ValueError('Render export requires exactly scene and case_dir')
    requested = body['case_dir']
    if not isinstance(requested, str) or not requested.strip() or not Path(requested).is_absolute():
        raise ValueError('case_dir must be the absolute captured project case directory')
    # Enforce the helper boundary too, even if invoked outside the HTTP handler.
    _encoded(body)
    try:
        case = Path(captured_case_dir).resolve(strict=True)
        request_case = Path(requested).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError('case_dir must resolve to an existing captured case directory') from error
    if case != request_case or not case.is_dir():
        raise ValueError('case_dir differs from the captured server case directory')
    packet = body['scene']
    _validator()(packet)  # Includes full camera/view, topology and texture semantics.
    encoded = _encoded(packet)
    digest = hashlib.sha256(encoded).hexdigest()
    project = re.sub(r'[^A-Za-z0-9_-]+', '_', packet['project']['name']).strip('_-')[:64] or 'project'
    time = packet['view']['timeSeconds']
    time_name = 'no-time' if time is None else re.sub(r'[^A-Za-z0-9_-]', 'p', format(time, '.12g')) + 's'
    descriptors = []
    temp_name = None
    scene_fd = None
    try:
        case_fd = _directory(case)
        descriptors.append(case_fd)
        render_fd = _child_directory(case_fd, 'Render')
        descriptors.append(render_fd)
        scene_fd = _child_directory(render_fd, 'scenes')
        descriptors.append(scene_fd)
        directory = case / 'Render/scenes'
        for _ in range(8):
            name = f'{project}_{time_name}_{uuid.uuid4().hex}.renderscene.json'
            candidate = '.' + uuid.uuid4().hex + '.tmp'
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=scene_fd)
            except FileExistsError:
                continue
            temp_name = candidate
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _same_directory(case, case_fd)
            _same_directory(case / 'Render', render_fd)
            _same_directory(directory, scene_fd)
            try:
                # Unlike replace/rename, link fails if the final name exists,
                # including a symlink. The complete file appears atomically.
                os.link(temp_name, name, src_dir_fd=scene_fd, dst_dir_fd=scene_fd, follow_symlinks=False)
            except FileExistsError:
                os.unlink(temp_name, dir_fd=scene_fd)
                temp_name = None
                continue
            os.unlink(temp_name, dir_fd=scene_fd)
            temp_name = None
            os.fsync(scene_fd)
            return {'path': str(directory / name), 'sha256': digest,
                    'project_uid': packet['project']['uid'], 'time_seconds': time,
                    'primitives': len(packet['primitives']), 'textures': len(packet['textures'])}
        raise FileExistsError('Could not allocate a unique render artifact filename')
    finally:
        if temp_name is not None and scene_fd is not None:
            try:
                os.unlink(temp_name, dir_fd=scene_fd)
            except FileNotFoundError:
                pass
        for fd in reversed(descriptors):
            os.close(fd)
