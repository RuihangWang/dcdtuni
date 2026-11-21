"""Frozen Base_leakage browser export for the tests that drive STLGenRunner over
a whole case (snappyHexMeshDict / createBafflesDict generation).

case/*/model and case/*/Mesh are gitignored export output, so a clone has no
live Base_leakage export. These tests read a frozen copy instead:
fixtures/cfdrun_cases/Base_leakage/Base_leakage.cfdrun (export run
2026-08-30, the absolute artifacts/sourceProject paths made repo-relative) and
the *.stl of that case's Mesh/constant/triSurface. `make_case` lays a COPY out
in a scratch dir, so _load()'s in-place STL merge/split passes and the generated
Mesh/system dicts never touch the fixture.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "cfdrun_cases" / "Base_leakage"
CFDRUN = FIXTURE_DIR / "Base_leakage.cfdrun"
TRI_SURFACE = FIXTURE_DIR / "triSurface"


def make_case(
    root: Path,
    data: Optional[Dict[str, Any]] = None,
    name: str = "Base_leakage.cfdrun",
) -> Tuple[Path, Path]:
    """Lay out ``root/Base_leakage/{model/<name>, Mesh/constant/triSurface/*.stl}``.

    ``data`` replaces the manifest (e.g. a modified copy of CFDRUN); by default
    the frozen manifest is copied as-is. Returns ``(case_dir, cfdrun_path)``.
    """
    stls = sorted(TRI_SURFACE.glob("*.stl"))
    assert stls, f"Base_leakage triSurface fixture missing: {TRI_SURFACE}"
    case_dir = root / "Base_leakage"
    model_dir = case_dir / "model"
    tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_dir.mkdir(parents=True)
    cfdrun = model_dir / name
    if data is None:
        shutil.copy(CFDRUN, cfdrun)
    else:
        cfdrun.write_text(json.dumps(data), encoding="utf-8")
    for stl in stls:
        shutil.copy(stl, tri_dir / stl.name)
    return case_dir, cfdrun
