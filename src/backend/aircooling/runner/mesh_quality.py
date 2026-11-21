"""Parse OpenFOAM checkMesh output into structured, severity-leveled warnings.

Warning levels:
  1 (CRITICAL) — solver will definitely fail (e.g. isolated cells, zero determinant)
  2 (SEVERE)   — solver may diverge (e.g. high skewness, bad tet decomposition)
  3 (WARNING)  — accuracy may be affected (e.g. high non-orthogonality)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

_RE_ISOLATED = re.compile(
    r"Writing\s+(\d+)\s+cells?\s+with\s+zero\s+or\s+one\s+non-boundary\s+face"
)
_RE_REGION = re.compile(
    r"Writing\s+region\s+\d+\s+\((point connected|fully disconnected)\)\s+with\s+(\d+)\s+cells?\s+to\s+cellSet\s+(\S+)"
)
_RE_SMALL_DET = re.compile(
    r"\*\*\*Cells\s+with\s+small\s+determinant.*?number\s+of\s+cells:\s+(\d+)"
)
_RE_BAD_TETS = re.compile(
    r"\*\*\*Error\s+in\s+face\s+tets:\s+(\d+)\s+faces"
)
_RE_SKEWNESS_FAIL = re.compile(
    r"Max\s+skewness\s*=\s*([\d.]+)\s*--\s*\*\*\*FAILED"
)
_RE_SKEWNESS_HIGH_FACES = re.compile(
    r"\*\*\*Max\s+skewness\s*=\s*([\d.]+)\s*,\s*(\d+)\s+highly\s+skew\s+faces?\b"
)
_RE_SKEWNESS_INTERNAL = re.compile(
    r"Max\s+internal\s+skewness\s+exceeded:\s*([\d.]+)"
)
_RE_CONCAVE_CELLS = re.compile(
    r"\*\*\*Concave\s+cells\s+\(using\s+face\s+planes\)\s+found,\s*number\s+of\s+cells:\s*(\d+)"
)
_RE_NON_ORTHO = re.compile(
    r"non-orthogonality\s+Max:\s*([\d.]+)"
)
_RE_NEGATIVE_VOL = re.compile(
    r"\*\*\*Error\s+in\s+cell\s+volumes.*?(\d+)\s+negative"
)
_RE_LOW_WEIGHT = re.compile(
    r"\*\*\*Faces\s+with\s+small\s+interpolation\s+weight\s*\(<\s*([\d.eE+-]+)\)\s+found,\s*number\s+of\s+faces:\s*(\d+)"
)
_RE_LOW_VOL_RATIO = re.compile(
    r"\*\*\*Faces\s+with\s+small\s+volume\s+ratio\s*\(<\s*([\d.eE+-]+)\)\s+found,\s*number\s+of\s+faces:\s*(\d+)"
)
_RE_NUM_REGIONS = re.compile(
    r"Number\s+of\s+regions:\s+(\d+)"
)


def parse_checkmesh_warnings(output: str) -> List[Dict[str, Any]]:
    """Parse checkMesh stdout text into a list of warning dicts.

    Each dict contains:
      level (int): 1=critical, 2=severe, 3=warning
      code (str): machine-readable identifier
      message (str): human-readable description
      count (int): number of affected cells/faces (where applicable)
      max_value (float): max measured value (where applicable)
    """
    warnings: List[Dict[str, Any]] = []

    m = _RE_ISOLATED.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 1,
            "code": "isolated_cells",
            "message": (
                f"Detected {n} isolated cell(s) with no internal connectivity. "
                f"Solver will fail due to singular matrix. "
                f"Usually caused by snappyHexMesh artifacts near rack frame corners."
            ),
            "count": n,
        })

    m = _RE_SMALL_DET.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 1,
            "code": "small_determinant",
            "message": (
                f"Detected {n} degenerate cell(s) (determinant < 0.001). "
                f"These cells are severely distorted and may cause solver failure."
            ),
            "count": n,
        })

    m = _RE_BAD_TETS.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 2,
            "code": "bad_face_tets",
            "message": f"Detected {n} face(s) with poor tet decomposition quality or negative volume.",
            "count": n,
        })

    high_faces = _RE_SKEWNESS_HIGH_FACES.search(output)
    legacy_skew = _RE_SKEWNESS_FAIL.search(output) if high_faces is None else None
    if high_faces and int(high_faces.group(2)) > 0:
        val = float(high_faces.group(1))
        count = int(high_faces.group(2))
        warnings.append({
            "level": 2,
            "code": "high_skewness",
            "message": f"Max mesh skewness {val:.1f} affects {count} face(s) and may cause solver divergence.",
            "max_value": val,
            "count": count,
        })
    elif legacy_skew:
        val = float(legacy_skew.group(1))
        warnings.append({
            "level": 2,
            "code": "high_skewness",
            "message": f"Max mesh skewness {val:.1f} exceeds threshold, may cause solver divergence.",
            "max_value": val,
        })
    else:
        m2 = _RE_SKEWNESS_INTERNAL.search(output)
        if m2:
            val = float(m2.group(1))
            warnings.append({
                "level": 2,
                "code": "high_skewness",
                "message": f"Max internal mesh skewness {val:.1f} exceeds threshold, may cause solver divergence.",
                "max_value": val,
            })

    m = _RE_CONCAVE_CELLS.search(output)
    if m and int(m.group(1)) > 0:
        n = int(m.group(1))
        warnings.append({
            "level": 3,
            "code": "concave_cells",
            "message": f"Detected {n} concave cell(s); mesh quality and solution accuracy may be affected.",
            "count": n,
        })

    for pattern, code, quantity in (
        (_RE_LOW_WEIGHT, "low_interpolation_weight", "interpolation weight"),
        (_RE_LOW_VOL_RATIO, "low_volume_ratio", "neighbouring cell volume ratio"),
    ):
        match = pattern.search(output)
        if match and int(match.group(2)) > 0:
            threshold, count = float(match.group(1)), int(match.group(2))
            warnings.append({
                "level": 2, "code": code, "count": count, "threshold": threshold,
                "message": f"Detected {count} face(s) with {quantity} below {threshold:g}.",
            })

    m = _RE_NEGATIVE_VOL.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 1,
            "code": "negative_volume",
            "message": f"Detected {n} negative-volume cell(s). Mesh has severe topological errors.",
            "count": n,
        })

    m = _RE_NON_ORTHO.search(output)
    if m:
        val = float(m.group(1))
        if val > 70:
            warnings.append({
                "level": 3,
                "code": "high_non_orthogonality",
                "message": f"Max non-orthogonality {val:.1f}° is high, may affect solution accuracy.",
                "max_value": val,
            })

    # --- Multi-region detection ---
    # Detailed per-region info from -allTopology output (runner path).
    regions = parse_checkmesh_regions(output)
    # Fallback: basic "Number of regions: N" (server checkMesh path).
    n_regions_basic = 0
    for m_nr in _RE_NUM_REGIONS.finditer(output):
        n_regions_basic = max(n_regions_basic, int(m_nr.group(1)))

    if len(regions) > 1:
        total = sum(r["cells"] for r in regions)
        largest = max(regions, key=lambda r: r["cells"])
        others = [r for r in regions if r["name"] != largest["name"]]
        others_cells = sum(r["cells"] for r in others)
        smallest_other = min(others, key=lambda r: r["cells"])
        # Tiny orphan regions (< 1% of largest) → critical; they crash the solver.
        # Large secondary regions → likely containment/raised-floor; informational.
        orphan_threshold = largest["cells"] * 0.01
        has_tiny_orphan = smallest_other["cells"] < orphan_threshold
        severity = 1 if has_tiny_orphan else 3
        warnings.append({
            "level": severity,
            "code": "disconnected_regions",
            "message": (
                f"Detected {len(regions)} disconnected mesh regions "
                f"(largest: {largest['cells']} cells, "
                f"{len(others)} other region(s) with {others_cells} cells). "
                f"Multiple regions are usually caused by STL gaps or overlapping faces. "
                f"The solver cannot transfer fields between disconnected regions. "
                f"For cold/hot aisle containment scenarios, multiple regions are expected."
            ),
            "count": len(regions),
            "regions": [
                {"name": r["name"], "type": r["type"], "cells": r["cells"]}
                for r in regions
            ],
        })
    elif n_regions_basic > 1:
        warnings.append({
            "level": 2,
            "code": "disconnected_regions",
            "message": (
                f"Detected {n_regions_basic} disconnected mesh regions. "
                f"Multiple regions are usually caused by STL gaps or overlapping faces. "
                f"The solver cannot transfer fields between disconnected regions. "
                f"For cold/hot aisle containment scenarios, multiple regions are expected."
            ),
            "count": n_regions_basic,
            "regions": [],
        })

    warnings.sort(key=lambda w: w["level"])
    return warnings


def parse_checkmesh_regions(output: str) -> List[Dict[str, Any]]:
    """Extract region connectivity info from checkMesh -allTopology output.

    Returns a list of dicts, each with:
      name (str): cellSet name written by checkMesh (e.g. "region2")
      type (str): "point connected" or "fully disconnected"
      cells (int): number of cells in this region

    Only populated when checkMesh reports multiple regions.
    Single-region meshes produce no region lines → empty list.
    """
    return [
        {"name": m.group(3), "type": m.group(1), "cells": int(m.group(2))}
        for m in _RE_REGION.finditer(output)
    ]
