"""Explicit confinement of writable caches (TMPDIR, HF_HOME, TORCH_HOME, ...) to one root.

Importing ``agent`` changes no environment variable. A caller that wants the
old behaviour of ``agent._runtime.configure_runtime_caches`` (every cache under
the repository's ``_tmp/``) calls :func:`configure_runtime_caches` with that
root, or any other.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, Union

#: Environment variable -> subdirectory of the cache root (the frozen ``_runtime`` table).
CACHE_VARIABLES: Dict[str, str] = {
    "TMPDIR": "tmp", "TEMP": "tmp", "TMP": "tmp",
    "XDG_CACHE_HOME": "cache", "HF_HOME": "huggingface",
    "HF_HUB_CACHE": "huggingface/hub",
    "HUGGINGFACE_HUB_CACHE": "huggingface/hub",
    "HF_DATASETS_CACHE": "huggingface/datasets",
    "TRANSFORMERS_CACHE": "huggingface/transformers",
    "TORCH_HOME": "torch", "TORCHINDUCTOR_CACHE_DIR": "torchinductor",
    "TRITON_CACHE_DIR": "triton", "MPLCONFIGDIR": "matplotlib",
    "CUDA_CACHE_PATH": "nvidia", "HF_MODULES_CACHE": "huggingface/modules",
    "NUMBA_CACHE_DIR": "numba", "TORCH_EXTENSIONS_DIR": "torch-extensions",
}


def runtime_cache_path(root: Union[str, os.PathLike], name: str) -> Path:
    """``<root>/<name>``, refusing a name that leaves the root."""
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"cache {name!r} must stay inside {root}")
    return path


def configure_runtime_caches(root: Union[str, os.PathLike]) -> Dict[str, Dict[str, str]]:
    """Point every cache variable inside ``root`` (created as needed); returns what was set.

    A variable that already points inside ``root`` is kept; one that points
    elsewhere (or is unset) is set to ``<root>/<subdir>``, and the old value is
    reported under ``replaced``. ``tempfile.tempdir`` follows ``TMPDIR``.
    """
    root = Path(root).resolve()
    variables: Dict[str, str] = {}
    replaced: Dict[str, str] = {}
    for variable, name in CACHE_VARIABLES.items():
        current = os.environ.get(variable)
        path = Path(current).expanduser().resolve() if current else None
        if path is None or not path.is_relative_to(root):
            if current:
                replaced[variable] = current
            path = runtime_cache_path(root, name)
        path.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(path)
        variables[variable] = str(path)
    tempfile.tempdir = os.environ["TMPDIR"]
    return {"root": str(root), "variables": variables, "replaced": replaced}
