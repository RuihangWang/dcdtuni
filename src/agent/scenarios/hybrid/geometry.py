"""Exported air geometry as the zonal LF networks read it: STL solids, areas, slices, and the server face ledger.

Ports ``agent_legacy/scenario_hybrid/air_network.py`` (``_triangles``, ``_geometry``,
``_slice_area``) and ``agent_legacy/harness/shinaide_six_acu_prepare_v3.py``
(``geometry_ledger``), unchanged in arithmetic. The geometry is what the editor
exported (:func:`agent.tools.project.export_air`): each STL file is read only when
its SHA-256 matches the manifest's ``geometryExport.stlFiles[*].contentHash``.

- :func:`load_solids` reads every ``solid <name>`` block of the bound files into a
  ``(triangles, 3, 3)`` array (one name may occur once across all files);
- :func:`area_and_centre` is a solid's area and area-weighted centre;
- :func:`slice_area` is the area of a solid between two heights (``y``), the
  aperture a zonal layer sees through it;
- :func:`geometry_ledger` binds every server's intake (``inlet``) and exhaust
  (its single outlet patch) to its exported solid, with area and centre.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

import numpy as np

LEDGER_FORMAT = "agent.hybrid.server-face-ledger/v1"
_SOLID = re.compile(r"^solid ([^\n]+)\n(.*?)^endsolid", re.M | re.S)
_VERTEX = re.compile(r"^\s*vertex (.+)$", re.M)


def parse_solids(text: str) -> Dict[str, np.ndarray]:
    """``{solid name: (n, 3, 3) triangles}`` of one ASCII STL text."""
    solids: Dict[str, np.ndarray] = {}
    for name, block in _SOLID.findall(text):
        values = np.array([list(map(float, v.split())) for v in _VERTEX.findall(block)])
        if name in solids:
            raise ValueError("ambiguous exported solid: " + name)
        solids[name] = values.reshape(-1, 3, 3)
    return solids


def load_solids(folder, hashes: Mapping[str, str]) -> Dict[str, np.ndarray]:
    """Every solid of the STL files ``hashes`` names (``{file: sha256}``) in ``folder``; a changed file is refused."""
    solids: Dict[str, np.ndarray] = {}
    for name, digest in sorted(hashes.items()):
        data = (Path(folder) / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("exported geometry STL changed: " + name)
        for key, triangles in parse_solids(data.decode()).items():
            if key in solids:
                raise ValueError("ambiguous exported solid: " + key)
            solids[key] = triangles
    return solids


def triangle_areas(triangles: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2


def area_and_centre(triangles: np.ndarray) -> Tuple[float, np.ndarray]:
    """Total area (m^2) and area-weighted centre (m) of a solid."""
    triangles = np.asarray(triangles, dtype=float)
    area = triangle_areas(triangles)
    total = float(area.sum())
    if total <= 0:
        raise ValueError("positive exported area required")
    return total, np.sum(triangles.mean(1) * area[:, None], axis=0) / total


def _clip_y(polygon, level, keep_above):
    out = []
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        inside_a = a[1] >= level if keep_above else a[1] <= level
        inside_b = b[1] >= level if keep_above else b[1] <= level
        if inside_a:
            out.append(a)
        if inside_a != inside_b:
            out.append(a + (level - a[1]) / (b[1] - a[1]) * (b - a))
    return out


def slice_area(triangles: np.ndarray, lo: float, hi: float) -> float:
    """Area of the part of a solid with ``lo <= y <= hi`` (each triangle clipped by both planes)."""
    result = 0.
    for face in np.asarray(triangles, dtype=float):
        polygon = _clip_y(list(face), lo, True)
        if not polygon:
            continue
        polygon = _clip_y(polygon, hi, False)
        for j in range(1, len(polygon) - 1):
            result += float(np.linalg.norm(np.cross(polygon[j] - polygon[0], polygon[j + 1] - polygon[0])) / 2)
    return result


def geometry_ledger(project: Mapping[str, Any], manifest: Mapping[str, Any],
                    solids: Mapping[str, np.ndarray]) -> Dict[str, Any]:
    """Each server's exported intake and exhaust solid, with area and centre; no heat, no physics.

    The intake is the server's ``inlet`` face solid (surface ``inlet___face__``);
    the exhaust is the solid of its one outlet patch (``outlet_<patch id>``),
    both found among ``manifest.geometryExport.solids`` by the server's object path.
    """
    exported = list(manifest["geometryExport"]["solids"].values())
    rows = []
    for rid, rack in project["constructions"]["racks"].items():
        for sid, server in rack["constructions"]["servers"].items():
            path = "constructions.racks." + rid + ".servers." + sid
            bindings = [b for b in exported if b["objectPath"] == path]
            intake = [b for b in bindings if b["surfaceUid"] == "inlet___face__"]
            outlet = server["faces"]["outlet"]["patches"]
            if len(intake) != 1 or len(outlet) != 1:
                raise ValueError("server " + rid + "/" + sid + ": one intake face and one outlet patch required")
            exhaust = [b for b in bindings if b["surfaceUid"] == "outlet_" + outlet[0]["id"]]
            if len(exhaust) != 1:
                raise ValueError("server " + rid + "/" + sid + ": its outlet patch was not exported")
            faces = []
            for label, binding in (("front", intake[0]), ("rear", exhaust[0])):
                area, centre = area_and_centre(solids[binding["solidName"]])
                faces.append({"face": label, "solid_name": binding["solidName"], "area_m2": area,
                              "center_xyz_m": centre.tolist(), "source_object_path": path,
                              "source_surface_uid": binding["surfaceUid"]})
            rows.append({"server_uid": rid + "/" + sid, "faces": faces})
    return {"format": LEDGER_FORMAT, "project_content_hash": manifest["sourceProject"]["contentHash"],
            "servers": rows, "heat_data_included": False, "physics_evaluation": False}


def reduced_solids(solids: Mapping[str, np.ndarray], names: Sequence[str]) -> Dict[str, list]:
    """The named solids as plain lists (a small, JSON-able geometry for test fixtures)."""
    return {name: np.asarray(solids[name]).tolist() for name in names}
