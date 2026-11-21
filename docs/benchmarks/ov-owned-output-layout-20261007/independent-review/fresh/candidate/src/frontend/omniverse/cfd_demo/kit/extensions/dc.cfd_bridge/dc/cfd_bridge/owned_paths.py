"""Output boundaries for bridge-owned files, independent of cwd and Kit imports."""
import json
import os
from pathlib import Path
import secrets
import stat

CHECKOUT_ROOT = Path(__file__).resolve().parents[9]


def owned_path(value, label):
    """Validate absolute checkout membership and reject existing symlink components."""
    if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
        raise ValueError(f"{label} must name an absolute path inside this checkout")
    root = CHECKOUT_ROOT.resolve()
    candidate = Path(os.path.abspath(value))
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError(f"{label} must stay inside this checkout")
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not use symlink components")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(f"{label} must stay inside this checkout")
    return str(candidate)


def render_texture_cache_path(environment=None):
    """Direct bridge use still requires an explicit owned texture directory."""
    environment = os.environ if environment is None else environment
    return owned_path(environment.get("DCDTUNI_RENDER_TEXTURE_CACHE"), "DCDTUNI_RENDER_TEXTURE_CACHE")


def metadata_path(key, filename, environment=None):
    environment = os.environ if environment is None else environment
    if key in environment:
        value = environment[key]
    elif "DC_CFD_RUNTIME_DIR" in environment:
        runtime = owned_path(environment["DC_CFD_RUNTIME_DIR"], "DC_CFD_RUNTIME_DIR")
        value = str(Path(runtime) / filename)
    else:
        value = str(CHECKOUT_ROOT / ".codex-tmp/ov-bridge-metadata" / filename)
    return owned_path(value, key)


def _directory_fd(directory):
    """Open/create descendants via held directory descriptors without following links."""
    root = CHECKOUT_ROOT.resolve()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in Path(directory).relative_to(root).parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_owned_json(path, payload, label):
    """Atomic JSON output with no-follow directories and an exclusive owned temporary."""
    path = Path(owned_path(str(path), label))
    parent_fd = _directory_fd(path.parent)
    temporary = path.name + ".tmp." + secrets.token_hex(8)
    fd = None
    created = False
    try:
        try:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode):
                raise ValueError(f"{label} destination must be a regular file")
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(payload, stream)
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        created = False
    finally:
        if fd is not None:
            os.close(fd)
        if created:
            os.unlink(temporary, dir_fd=parent_fd)
        os.close(parent_fd)
    return str(path)


def bridge_cache_directory(environment=None):
    environment = os.environ if environment is None else environment
    if "DC_CFD_RUNTIME_DIR" in environment:
        runtime = owned_path(environment["DC_CFD_RUNTIME_DIR"], "DC_CFD_RUNTIME_DIR")
        path = str(Path(runtime) / "dataset-cache")
    else:
        path = str(CHECKOUT_ROOT / ".codex-tmp/ov-bridge-cache")
    path = owned_path(path, "bridge cache")
    fd = _directory_fd(path)
    os.close(fd)
    return path
