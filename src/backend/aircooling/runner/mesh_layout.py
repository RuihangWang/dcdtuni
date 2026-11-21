"""Cheap filesystem discovery for serial and decomposed OpenFOAM cases.

A root mesh remains authoritative for older reconstructed runs which also
retain processor directories. The dcCart workflow removes that root mesh
when its processor meshes become the final output.
"""

from __future__ import annotations

import math
import re
from pathlib import Path


_MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary")
_RESULT_FIELDS = ("T", "U", "p_rgh", "p")
_PROCESSOR = re.compile(r"processor(0|[1-9]\d*)$")


def _complete_mesh(poly_mesh: Path) -> bool:
    try:
        return all(
            (poly_mesh / name).is_file() and (poly_mesh / name).stat().st_size > 0
            for name in _MESH_FILES
        )
    except OSError:
        # The status poll can overlap a mesh replacement.
        return False


def processor_mesh_dirs(case_dir: Path) -> list[Path]:
    """Return complete, contiguous processor meshes in numerical rank order.

    Ignore backup directories such as ``processor0.old`` and reject a partial
    decomposition rather than silently displaying or solving a subset of it.
    This is raw discovery: a reconstructed root mesh does not hide processors.
    """
    case_dir = Path(case_dir)
    if not case_dir.is_dir():
        return []
    processors = sorted(
        (int(match.group(1)), child)
        for child in case_dir.iterdir()
        if child.is_dir() and (match := _PROCESSOR.fullmatch(child.name))
    )
    if not processors or [rank for rank, _ in processors] != list(range(len(processors))):
        return []
    decomposition = case_dir / "system" / "decomposeParDict"
    if decomposition.is_file():
        text = decomposition.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)
        expected = re.search(r"\bnumberOfSubdomains\s+(\d+)\s*;", text)
        if expected and int(expected.group(1)) != len(processors):
            return []
    meshes = [directory / "constant" / "polyMesh" for _, directory in processors]
    return meshes if all(_complete_mesh(mesh) for mesh in meshes) else []


def mesh_directories(case_dir: Path) -> list[Path]:
    """Return the authoritative mesh directories, with a serial-root preference."""
    root = Path(case_dir) / "constant" / "polyMesh"
    if (root / "faces").is_file():
        return [root] if _complete_mesh(root) else []
    return processor_mesh_dirs(case_dir)


def mesh_exists(case_dir: Path) -> bool:
    return bool(mesh_directories(case_dir))


def mesh_boundary_paths(case_dir: Path) -> list[Path]:
    return [directory / "boundary" for directory in mesh_directories(case_dir)]


def common_result_times(case_dir: Path, *, require_fields: bool = False) -> list[float]:
    """List numeric times available on every rank (or in a serial case root).

    Requiring fields excludes empty/in-progress output directories from UI
    result readiness. Original directory spellings such as ``1e+02`` remain
    usable while comparing times numerically.
    """
    meshes = mesh_directories(case_dir)
    if not meshes:
        return []
    common: set[float] | None = None
    for mesh in meshes:
        times = set()
        for child in mesh.parent.parent.iterdir():
            if not child.is_dir():
                continue
            try:
                value = float(child.name)
            except ValueError:
                continue
            if not math.isfinite(value):
                continue
            if require_fields and not any(
                (child / field).is_file() or (child / (field + ".gz")).is_file()
                for field in _RESULT_FIELDS
            ):
                continue
            times.add(value)
        common = times if common is None else common & times
        if not common:
            break
    return sorted(common or ())


def available_result_times(case_dir: Path, *, require_fields: bool = True) -> list[float]:
    """Expose complete result times even when an older root mesh is retained.

    Mesh generation still treats a root mesh as authoritative. Postprocessing
    can additionally read newer times written to every processor, so the UI
    must not hide those results merely because the root has no matching time.
    """
    times = set(common_result_times(case_dir, require_fields=require_fields))
    partitions = processor_mesh_dirs(case_dir)
    if partitions and (Path(case_dir) / "constant/polyMesh/faces").is_file():
        shared = None
        for mesh in partitions:
            local = set(common_result_times(mesh.parent.parent, require_fields=require_fields))
            shared = local if shared is None else shared & local
        times.update(shared or ())
    return sorted(times)
