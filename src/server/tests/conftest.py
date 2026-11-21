"""Pytest conftest: make ``from server import ...`` resolve to server.py.

Background
----------
``STLGeneratorForOpenFoam/server/`` has an (empty) ``__init__.py``, so pytest's
rootdir/prepend path discovery imports it as a package and caches it in
``sys.modules['server']`` before individual test modules run. The tests want
``from server import parse_probes_file`` to resolve to the module defined in
``server/server.py``, not the empty package.

This conftest runs during collection and:

1. Prepends ``STLGeneratorForOpenFoam/server/`` to ``sys.path`` so plain
   ``import server`` can find ``server.py``.
2. Evicts the cached ``server`` package entry if present.
3. Imports + reloads ``server`` so ``sys.modules['server']`` points at the
   ``server.py`` module (which exposes ``parse_probes_file`` et al.) before
   any test's ``from server import ...`` runs.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent.parent
_server_dir_str = str(_SERVER_DIR)
if _server_dir_str not in sys.path:
    sys.path.insert(0, _server_dir_str)

_cached = sys.modules.get("server")
if _cached is not None:
    _cached_file = getattr(_cached, "__file__", "") or ""
    if _cached_file.endswith("__init__.py"):
        del sys.modules["server"]

# Force a fresh resolution via sys.path (server.py), then keep it cached so
# subsequent `from server import ...` in the test modules re-use it.
import server as _server  # noqa: E402  (must follow sys.path mutation)
importlib.reload(_server)
