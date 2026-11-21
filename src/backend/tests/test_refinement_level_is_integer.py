"""A refinement level must reach the runner as an integer.

The mesh panel's refinement inputs are octree DEPTHS. The runner's schema
declares them `List[int]` / `int`, so a fractional level does not degrade — it
fails Pydantic validation and the mesh job dies before it starts, with an error
that points at the .cfdrun rather than at the box the user typed into.

Both editors used to allow it. The legacy handler read `+this.value` straight
into the config; the React panel rounds. This pins the invariant on both sides
and on the schema itself, so the three cannot drift apart again.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
REACT_MESH = REPO / "src" / "frontend" / "react" / "src" / "components" / "MeshPanel.tsx"


def test_schema_rejects_a_fractional_surface_level():
    """The reason the rounding matters: the runner will not take 1.5."""
    from pydantic import ValidationError

    from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig

    with pytest.raises(ValidationError):
        CFDExportMeshConfig.model_validate({"refinementLevel": [1.5, 1]})


def test_schema_accepts_integers():
    from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig

    cfg = CFDExportMeshConfig.model_validate({"refinementLevel": [2, 3]})
    assert cfg.refinementLevel == [2, 3]


# The legacy counterpart of this test read `window.setMeshRefineLevel` out of
# STL_generate.html and asserted the `Math.round(` was still in its body. That
# file is gone, and the invariant moved somewhere stronger: React rounds inside
# `projectStore.setRunConfig` / `setRefinementBound`, so no caller can skip it —
# not the panel, not the automation contract's raw `setMeshConfig`, not a future
# third one. Its successor is
# `src/frontend/react/test/refinementLevelInteger.test.ts`, which writes a
# fractional level through every path and asserts what lands.
