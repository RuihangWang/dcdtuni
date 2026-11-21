"""Shared test setup for the backend tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_repo_built_checkmesh(monkeypatch, tmp_path_factory):
    """Run checkMesh by its plain name, whatever this checkout has built.

    The runner prefers the repo-built dcCheckMesh when it exists, so a test
    that asserts the command line would pass on a fresh clone and fail on a
    machine that ran ./Allwmake. Tests of the resolver itself set
    ``_REPO_PLATFORMS`` again.
    """
    from backend.aircooling.runner import air_mesh_quality_receipt as quality

    monkeypatch.setattr(quality, "_REPO_PLATFORMS", tmp_path_factory.getbasetemp() / "no-platforms")
