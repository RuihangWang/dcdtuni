"""Dated, never-overwritten result directories for scripted runs.

``<root>/YYYY-MM-DD_HHMMSS_SGT__<kind>[_<label>]/``. The directory is created
with ``exist_ok=False``; a name collision (two runs in the same second) gets a
``-2``, ``-3`` ... suffix instead of reusing an existing directory.

For a case, ``<root>`` is ``<case>/log/api``. The server and the post-processor
only treat ``<case>/log/<dir>`` as a solve run when it holds a mesh, so this
sibling of the solver's own ``log/<timestamp>_<case>`` directories is never
mistaken for one.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from agent.scripted_api.identity import now_sgt

_LABEL = re.compile(r"[^A-Za-z0-9_.-]+")


def create_run_dir(root: Path | str, kind: str, label: Optional[str] = None) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stem = f"{now_sgt().strftime('%Y-%m-%d_%H%M%S')}_SGT__{kind}"
    if label:
        stem += "_" + _LABEL.sub("-", label).strip("-")[:60]
    for attempt in range(1, 1000):
        candidate = root / (stem if attempt == 1 else f"{stem}-{attempt}")
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(f"could not create a fresh run directory under {root}")


def read_source(path: Path | str) -> bytes:
    """The bytes of a run's source file; a missing or unreadable one is an input error (``ValueError``).

    Read before any result directory is made, so a wrong path leaves nothing behind.
    """
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from exc


def parse_json_source(raw: bytes, path: Path | str) -> Any:
    """``raw`` (the bytes of ``path``) as JSON; text that is not JSON is an input error naming the file."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def read_json_source(path: Path | str) -> tuple[bytes, Any]:
    """``(bytes, parsed JSON)`` of a source file; missing, unreadable or not JSON is a ``ValueError``."""
    raw = read_source(path)
    return raw, parse_json_source(raw, path)


def write_json(path: Path, value: Any) -> Path:
    """Write once: refuse to replace an existing file in a run directory."""
    text = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with open(path, "x", encoding="utf-8") as stream:
        stream.write(text)
    return path


def write_text(path: Path, text: str) -> Path:
    with open(path, "x", encoding="utf-8", errors="replace") as stream:
        stream.write(text)
    return path


def case_api_root(case_dir: Path | str) -> Path:
    return Path(case_dir) / "log" / "api"


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:  # different drives
        return str(path)
