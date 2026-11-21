"""configure_runtime_caches(root) is explicit: it confines writable caches to the given root only."""
from __future__ import annotations

import os
import tempfile

from agent.records import configure_runtime_caches
from agent.records.runtime import CACHE_VARIABLES


def test_caches_are_set_under_the_injected_root(tmp_path, monkeypatch):
    root = tmp_path / "caches"
    inside = root / "my-own-hf"
    monkeypatch.setenv("HF_HOME", str(inside))
    monkeypatch.setenv("TMPDIR", "/tmp")
    monkeypatch.delenv("TORCH_HOME", raising=False)
    old_tempdir = tempfile.tempdir
    try:
        report = configure_runtime_caches(root)
        assert os.environ["HF_HOME"] == str(inside.resolve())  # already inside: kept
        assert os.environ["TMPDIR"] == str((root / "tmp").resolve())  # outside: replaced
        assert os.environ["TORCH_HOME"] == str((root / "torch").resolve())
        assert report["replaced"]["TMPDIR"] == "/tmp"
        assert set(report["variables"]) == set(CACHE_VARIABLES)
        for value in report["variables"].values():
            assert os.path.isdir(value) and value.startswith(str(root.resolve()))
        assert tempfile.tempdir == os.environ["TMPDIR"]
    finally:
        tempfile.tempdir = old_tempdir
