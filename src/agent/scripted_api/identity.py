"""Identity of one scripted run: inputs, code revision, tools and timing.

Everything here is read-only observation. Nothing is inferred: a version that
cannot be read is reported as ``None`` with the reason, never guessed.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: src/agent/scripted_api/identity.py -> repo root
REPO = Path(__file__).resolve().parents[3]

try:  # Python >= 3.9 ships zoneinfo; tzdata may still be absent on a host.
    from zoneinfo import ZoneInfo
    SGT = ZoneInfo("Asia/Singapore")
except Exception:  # pragma: no cover - host without tz database
    SGT = timezone(timedelta(hours=8), "SGT")


def now_sgt() -> datetime:
    return datetime.now(SGT)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    """SHA-256 of the sorted, compact JSON text of *value*."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return sha256_bytes(text.encode("utf-8"))


def _run(command: Iterable[str], cwd: Optional[Path] = None, timeout: float = 20.0, *,
         strip: bool = True) -> Optional[str]:
    try:
        done = subprocess.run(list(command), cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() if strip else done.stdout


def changed_paths(porcelain_z: str) -> List[str]:
    """Paths of ``git status --porcelain -z`` entries (``XY <path>``, NUL-terminated).

    Not line-split and stripped: the first entry's status may begin with a
    space (`` M path``), and a strip ate the path's first character. A rename
    or copy entry is followed by its source path, which is skipped.
    """
    fields = porcelain_z.split("\0")
    paths: List[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":
            index += 1
    return paths


def repository_identity(repo: Path = REPO) -> Dict[str, Any]:
    """Commit, branch and whether tracked files differ from that commit."""
    commit = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    status = _run(["git", "status", "--porcelain", "-z", "--untracked-files=no"], cwd=repo, strip=False)
    changed = changed_paths(status or "")
    # Untracked (not ignored) files under src/ also change what runs.
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard", "--", "src"], cwd=repo)
    paths = [line for line in (untracked or "").splitlines() if line.strip()]
    # A symlink (e.g. node_modules pointing at another checkout's install) is
    # environment, not source; it is listed but does not make the tree dirty.
    links = [p for p in paths if (repo / p).is_symlink()]
    extra = [p for p in paths if p not in links]
    return {
        "path": str(repo),
        "commit": commit,
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo),
        "dirty": bool(changed or extra) if status is not None else None,
        "changedTrackedFiles": changed[:50],
        "untrackedSourceFiles": extra[:50],
        "untrackedLinks": {p: os.readlink(repo / p) for p in links[:20]},
    }


def python_identity() -> Dict[str, Any]:
    versions: Dict[str, Any] = {"python": platform.python_version(), "executable": sys.executable}
    for name in ("numpy", "torch", "pydantic", "pyvista", "vtk"):
        module = sys.modules.get(name)
        versions[name] = getattr(module, "__version__", None) if module is not None else None
    return versions


def host_identity() -> Dict[str, Any]:
    return {"hostname": socket.gethostname(), "platform": platform.platform(), "cpuCount": os.cpu_count()}


def binary_identity(path: Optional[str]) -> Dict[str, Any]:
    """Absolute path and SHA-256 of an executable, resolving a bare name on PATH."""
    if not path:
        return {"path": None, "sha256": None}
    resolved = path if os.path.isabs(path) else shutil.which(path)
    if not resolved or not os.path.isfile(resolved):
        return {"path": path, "sha256": None, "error": "not found"}
    return {"path": str(Path(resolved).resolve()), "sha256": sha256_file(resolved)}
