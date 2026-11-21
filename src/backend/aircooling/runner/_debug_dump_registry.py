#!/usr/bin/env python3
"""Capture the CURRENT (pre-V2) ``_build_bridge_bc`` output as a golden baseline.

This helper freezes the in-memory ``BridgeBCRegistry`` that the existing
``STLGenRunner`` derives from a ``.cfdrun`` manifest, so the upcoming V2
refactor (which moves BC derivation into JavaScript) can be validated for
equivalence against a faithful reference.

Why a temp copy: the faithful prep sequence the real mesh pipeline runs
(``_ensure_watertight_room_enclosure`` → ``_split_internal_face_solids``)
WRITES/REWRITES STL files into ``Mesh/constant/triSurface/`` (room_wall
closing + per-solid ``baffle_*.stl`` splits). Running on the repo's real case
dir would mutate the checked-in geometry, so we copy the minimal skeleton
(``model/`` + ``Mesh/constant/triSurface/``) into a fresh ``tempfile.mkdtemp``
dir, run the derivation there, and ``shutil.rmtree`` it afterwards.

The registry build sequence mirrors exactly what ``run_mesh_only`` does, up to
(but NOT including) mesh-dict / mesh generation:

    _load()
    validate_geometry()
    _preflight_partition_wall_thickness()
    _ensure_watertight_room_enclosure()
    _prepare_runtime_metadata()   # → _cleanup_stale_compat_files() + _build_bridge_bc()

Usage:
    python _debug_dump_registry.py <case_dir>   # registry JSON → stdout

The leading underscore in the filename keeps pytest from collecting this as a
test module.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _copy_minimal_case(src_case_dir: Path, tmp_case_dir: Path) -> None:
    """Copy the minimal case skeleton needed to build the bridge registry.

    Copies ``model/`` and ``Mesh/constant/triSurface/`` only, preserving the
    relative layout. The rest of ``Mesh/`` (polyMesh, processor* dirs) can be
    huge and is not needed for BC derivation.
    """
    src_case_dir = Path(src_case_dir)

    # model/  (the .cfdrun manifest)
    src_model = src_case_dir / "model"
    if src_model.is_dir():
        shutil.copytree(src_model, tmp_case_dir / "model")

    # Mesh/constant/triSurface/  (discovered STL solids)
    src_tri = src_case_dir / "Mesh" / "constant" / "triSurface"
    dst_tri = tmp_case_dir / "Mesh" / "constant" / "triSurface"
    if src_tri.is_dir():
        dst_tri.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_tri, dst_tri)


def dump_registry(case_dir: Path) -> str:
    """Build the bridge registry on a temp copy and return its JSON dump.

    Returns a pretty-printed, key-sorted JSON string mapping each sanitized
    patch name to ``BCSpec.model_dump(mode="json")``.
    """
    case_dir = Path(case_dir).resolve()
    if not case_dir.is_dir():
        raise FileNotFoundError(f"case dir not found: {case_dir}")

    tmp_case_dir = Path(tempfile.mkdtemp(prefix="dump_registry_"))
    try:
        _copy_minimal_case(case_dir, tmp_case_dir)

        runner = STLGenRunner(tmp_case_dir)
        # Faithful prep sequence, exactly as run_mesh_only() does, up to (but
        # not including) mesh-dict / mesh generation.
        runner._load()
        runner.validate_geometry()
        runner._preflight_partition_wall_thickness()
        runner._ensure_watertight_room_enclosure()
        runner._prepare_runtime_metadata()

        registry = runner._bridge_registry
        if registry is None:
            raise RuntimeError(
                "_bridge_registry is None after _prepare_runtime_metadata(); "
                "_build_bridge_bc() did not populate it."
            )

        payload = {
            name: spec.model_dump(mode="json")
            for name, spec in registry.items()
        }
        return json.dumps(payload, sort_keys=True, indent=2)
    finally:
        shutil.rmtree(tmp_case_dir, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(
            "usage: python _debug_dump_registry.py <case_dir>\n"
        )
        return 2
    print(dump_registry(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
