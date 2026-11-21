"""Stable identity of the physical triSurface publication batch.

Caller holds the case execution lock. Names and content are bound; timestamps
are used only to detect mutation while reading. Auxiliary regular files are
included so the whole published directory has one identity.
"""
import hashlib
import os
from pathlib import Path
import stat


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def surface_batch_sha256(case_dir) -> str:
    case = Path(case_dir).expanduser().resolve()
    tri = case / 'Mesh' / 'constant' / 'triSurface'
    def require_namespace():
        current = case
        for part in ('Mesh', 'constant', 'triSurface'):
            current = current / part
            if current.is_symlink():
                raise ValueError('STL batch namespace contains a symlink')
    require_namespace()
    records, directories = [], []
    if tri.exists():
        if not tri.is_dir():
            raise ValueError('STL batch namespace must be a directory')
        for parent, dirs, names, dir_fd in os.fwalk(tri, follow_symlinks=False):
            parent = Path(parent)
            before_dir = _identity(os.fstat(dir_fd))
            directories.append((parent, before_dir))
            dirs.sort(); names.sort()
            for name in dirs:
                if not stat.S_ISDIR(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
                    raise ValueError('STL batch directory member is a symlink or special file')
            for name in names:
                before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('STL batch file member is a symlink or special file')
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
                digest = hashlib.sha256()
                with os.fdopen(fd, 'rb') as stream:
                    if _identity(os.fstat(stream.fileno())) != _identity(before):
                        raise ValueError('STL batch file changed before read')
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                    after_fd = _identity(os.fstat(stream.fileno()))
                after = _identity(os.stat(name, dir_fd=dir_fd, follow_symlinks=False))
                if after_fd != _identity(before) or after != after_fd:
                    raise ValueError('STL batch file changed during read')
                records.append((str((parent / name).relative_to(tri)).encode('utf-8'), before.st_size, digest.digest()))
            if _identity(os.fstat(dir_fd)) != before_dir:
                raise ValueError('STL batch directory changed during read')
        # Directory timestamps may be coarse. A second metadata-only census
        # also refuses additions/removals that were absent from the first walk.
        current_names, current_dirs = [], []
        for parent, dirs, names, dir_fd in os.fwalk(tri, follow_symlinks=False):
            parent = Path(parent)
            current_dirs.append(parent)
            for name in dirs:
                if not stat.S_ISDIR(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
                    raise ValueError('STL batch directory member is a symlink or special file')
            for name in names:
                if not stat.S_ISREG(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
                    raise ValueError('STL batch file member is a symlink or special file')
                current_names.append(str((parent / name).relative_to(tri)).encode('utf-8'))
        if (sorted(current_names) != sorted(item[0] for item in records)
                or set(current_dirs) != {item[0] for item in directories}):
            raise ValueError('STL batch namespace changed during capture')
        require_namespace()
        for directory, expected in directories:
            if _identity(directory.stat(follow_symlinks=False)) != expected:
                raise ValueError('STL batch directory changed during capture')
    digest = hashlib.sha256(b'dcdtuni.triSurface-batch/v1\0')
    digest.update(len(records).to_bytes(8, 'big'))
    for name, size, content_sha in sorted(records):
        digest.update(len(name).to_bytes(8, 'big')); digest.update(name)
        digest.update(size.to_bytes(8, 'big')); digest.update(content_sha)
    return digest.hexdigest()
