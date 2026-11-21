"""Audit boundary/baffle patch preservation across OpenFOAM meshing and domain decomposition."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

from backend.aircooling.scano.foam_mesh import COUPLED, parse_boundary, rank_mesh_dirs
from backend.aircooling.runner.mesh_acceptance import (
    MeshAcceptance,
    SYNTHETIC_PATCHES,
    check_patch_solid_correspondence,
)

__all__ = ["verify_patch_preservation"]


def _physical_patch_faces(boundary_path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name, meta in parse_boundary(boundary_path):
        ptype = str(meta.get("type", "patch")).strip()
        if ptype in COUPLED or name in SYNTHETIC_PATCHES:
            continue
        out[name] = {
            "type": ptype,
            "nFaces": int(meta.get("nFaces", 0) or 0),
        }
    return out


def verify_patch_preservation(
    case_dir: Path | str,
    expected_patches: Optional[Iterable[str]] = None,
    *,
    run_acceptance: bool = True,
) -> Dict[str, Any]:
    """Verify every expected/constant patch survives in both constant and decomposed polyMesh."""
    case = Path(case_dir)
    const_boundary = case / "constant" / "polyMesh" / "boundary"
    const_faces = _physical_patch_faces(const_boundary) if const_boundary.is_file() else {}

    decomp_faces: Dict[str, Dict[str, Any]] = {}
    has_decomp = False
    try:
        ranks = rank_mesh_dirs(case, layout="processors")
        has_decomp = True
        for rdir in ranks:
            bpath = rdir / "boundary"
            if not bpath.is_file():
                continue
            for name, info in _physical_patch_faces(bpath).items():
                cur = decomp_faces.setdefault(name, {"type": info["type"], "nFaces": 0})
                cur["nFaces"] += int(info["nFaces"])
    except FileNotFoundError:
        ranks = []

    ref_names: Set[str] = set(const_faces.keys())
    if expected_patches is not None:
        ref_names |= {str(p) for p in expected_patches}

    dropped_from_constant = sorted(
        p for p in (set(expected_patches) if expected_patches is not None else set())
        if const_faces and p not in const_faces
    )
    dropped_from_decomposed = sorted(
        p for p in ref_names
        if has_decomp and (p not in decomp_faces or (p in const_faces and const_faces[p]["nFaces"] > 0 and decomp_faces[p]["nFaces"] <= 0))
    )
    zero_face_patches = sorted(
        p for p, info in (decomp_faces if has_decomp else const_faces).items()
        if int(info["nFaces"]) <= 0
    )
    face_count_mismatches = {}
    if const_faces and has_decomp:
        for p, cinfo in const_faces.items():
            df = decomp_faces.get(p, {}).get("nFaces", 0)
            if int(cinfo["nFaces"]) != int(df):
                face_count_mismatches[p] = {"constant": int(cinfo["nFaces"]), "decomposed": int(df)}

    acceptance_summary: Optional[Dict[str, Any]] = None
    acceptance_ok = True
    if run_acceptance and (case / "constant" / "triSurface").is_dir() and (case / "system" / "snappyHexMeshDict").is_file() and const_boundary.is_file():
        acc: MeshAcceptance = check_patch_solid_correspondence(case)
        acceptance_ok = bool(acc.ok)
        acceptance_summary = {
            "ok": acc.ok,
            "summary": acc.summary(),
            "n_solids": acc.n_solids,
            "n_patches": acc.n_patches,
            "missing_patches": list(acc.missing_patches),
            "unresolved_surfaces": list(acc.unresolved_surfaces),
            "open_server_faces": list(acc.open_server_faces),
            "unexpected_patches": list(acc.unexpected_patches),
            "empty_solids": list(acc.empty_solids),
            "baffle_solids_count": len(acc.baffle_solids),
            "sealed_voids_count": len(acc.sealed_voids),
            "buried_interfaces_count": len(acc.buried_interfaces),
        }

    dropped_patches = sorted(set(dropped_from_constant) | set(dropped_from_decomposed))
    ok = (
        bool(const_faces or has_decomp)
        and not dropped_patches
        and not zero_face_patches
        and not face_count_mismatches
        and acceptance_ok
    )
    return {
        "ok": ok,
        "case": str(case),
        "n_constant_patches": len(const_faces),
        "n_decomposed_patches": len(decomp_faces),
        "n_ranks": len(ranks),
        "dropped_patches": dropped_patches,
        "dropped_from_constant": dropped_from_constant,
        "dropped_from_decomposed": dropped_from_decomposed,
        "zero_face_patches": zero_face_patches,
        "face_count_mismatches": face_count_mismatches,
        "acceptance": acceptance_summary,
    }
