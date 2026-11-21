"""Resolve the browser's grouped ASCII STLs into a checked v2 geometry index.

The React exporter currently leaves ``geometryExport.stlFiles`` empty and all
solid areas unknown. A thermal handoff cannot divide watts by a guessed rack
rectangle: it must use the actual triangles delivered to the air mesher.
Some wall/baffle solids are generated later by the backend rather than present
in browser STLs, so thermal callers index only their selected source patches.
This function enriches a *derived* .cfdrun; it never edits the .cfdproj.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path

from backend.aircooling.runner.cfdrun_reader import (
    _read_ascii_stl_triangles,
    _stl_solid_area,
)
from backend.aircooling.runner.cfdrun_schema import CfdrunV2


def verify_exported_stl_hashes(raw: dict, tri_surface_dir: str | Path) -> dict:
    """Check the original grouped STL bytes before any mesh-stage rewrite."""
    manifest = CfdrunV2.model_validate(raw)
    expected = manifest.geometryExport.stlFiles
    if not expected:
        raise ValueError("geometryExport.stlFiles needs export-time STL content hashes")
    directory = Path(tri_surface_dir)
    if not directory.is_dir():
        raise ValueError(f"STL directory does not exist: {directory}")
    names: set[str] = set()
    for file_id, record in expected.items():
        name = record.path
        if (not isinstance(name, str) or not name or Path(name).name != name or
                not name.lower().endswith(".stl") or name in names):
            raise ValueError(f"STL identity has duplicate or unsafe path at {file_id}")
        names.add(name)
        if (not isinstance(record.contentHash, str) or len(record.contentHash) != 64 or
                any(character not in "0123456789abcdef" for character in record.contentHash)):
            raise ValueError(f"STL {name} lacks an export-time content hash")
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"exported STL {name} is missing or symlinked")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != record.contentHash:
            raise ValueError(f"exported STL {name} content hash differs from the .cfdrun")
    actual = {path.name for path in directory.iterdir()
              if path.is_file() and path.suffix.lower() == ".stl"}
    if actual != names:
        raise ValueError(f"STL file set differs from export-time manifest: missing={sorted(names-actual)}, extra={sorted(actual-names)}")
    parsed: dict[str, dict] = {}
    for key, binding in manifest.geometryExport.sourceSurfaces.items():
        file_id = binding.stlFileId
        if file_id not in parsed:
            path = directory / expected[file_id].path
            parsed[file_id] = _read_ascii_stl_triangles(path)
        if binding.solidName not in parsed[file_id]:
            raise ValueError(f"sourceSurfaces[{key}] STL solid {binding.solidName!r} is absent from {expected[file_id].path}")
        triangles = parsed[file_id][binding.solidName]
        area = _stl_solid_area(triangles) if triangles else 0.0
        if not math.isfinite(area) or area <= 0:
            raise ValueError(f"sourceSurfaces[{key}] STL solid has degenerate triangles/area")
    # The same literal bytes that were hashed above retain actual emitted-region
    # ancestry independently of sourceSurfaces' declared physical-face semantics.
    import re
    region_pattern = re.compile(r"(?m)^solid[ \t]+([^\r\n]+)\r?\n([\s\S]*?)^endsolid[ \t]+([^\r\n]+)[ \t]*\r?\n?")
    region_cache = {}
    for key, binding in manifest.geometryExport.sourceRegions.items():
        file_id = binding.stlFileId
        if file_id not in region_cache:
            path = directory / expected[file_id].path
            text = path.read_bytes().decode("utf-8")
            rows = {}
            for match in region_pattern.finditer(text):
                name = match[1].strip()
                if name != match[3].strip() or name in rows:
                    raise ValueError("source STL region is malformed or duplicated")
                rows[name] = (hashlib.sha256(match[0].encode("utf-8")).hexdigest(),
                              len(re.findall(r"\bfacet[ \t]+normal\b", match[2])))
            region_cache[file_id] = rows
        actual = region_cache[file_id].get(binding.solidName)
        if actual != (binding.sourceRegionSha256, binding.sourceFacetCount):
            raise ValueError(f"sourceRegions[{key}] literal region/facet identity mismatch")
        if file_id not in parsed:
            parsed[file_id] = _read_ascii_stl_triangles(directory / expected[file_id].path)
        triangles = parsed[file_id].get(binding.solidName)
        if not triangles or len(triangles) != binding.sourceFacetCount:
            raise ValueError(f"sourceRegions[{key}] has no complete literal facet inventory")
        if not math.isfinite(_stl_solid_area(triangles)) or _stl_solid_area(triangles) <= 0:
            raise ValueError(f"sourceRegions[{key}] has degenerate or non-finite facet area")
    return {"file_count": len(names), "filenames": sorted(names)}


def index_cfdrun_stls(raw: dict, tri_surface_dir: str | Path, *,
                       target_solid_names: set[str] | None = None) -> dict:
    """Return strict v2 input with target solids linked to real STL areas.

    Duplicate names, missing or degenerate referenced solids, changed file
    identity and declared areas outside the reader's 5% tolerance are errors.
    The final area is always computed from STL triangles in metres squared.
    Omit ``target_solid_names`` only when every manifest solid is exported in
    the supplied STLs; later-generated backend aggregate solids must be left
    untouched by a targeted call.
    """
    manifest = CfdrunV2.model_validate(raw)
    geom = manifest.geometryExport
    if geom.unit != "m" or geom.coordinateSystem != "Y-up":
        raise ValueError("geometryExport requires metre units and Y-up coordinates")
    directory = Path(tri_surface_dir)
    if not directory.is_dir():
        raise ValueError(f"STL directory does not exist: {directory}")
    paths = sorted(path for path in directory.iterdir()
                   if path.is_file() and path.suffix.lower() == ".stl")
    if not paths:
        raise ValueError("STL directory has no grouped .stl files")

    files: dict[str, dict] = {}
    solids: dict[str, tuple[str, float]] = {}
    for index, path in enumerate(paths):
        if path.is_symlink():
            raise ValueError(f"STL file may not be a symlink: {path.name}")
        file_id = f"stl_{index:03d}"
        data = path.read_bytes()
        files[file_id] = {"path": path.name, "objectPath": None,
                          "kind": "grouped_ascii", "contentHash": hashlib.sha256(data).hexdigest()}
        groups = _read_ascii_stl_triangles(path)
        if not groups:
            raise ValueError(f"STL file has no ASCII solid triangles: {path.name}")
        for name, triangles in groups.items():
            if name in solids:
                raise ValueError(f"duplicate STL solid {name!r} in {path.name}")
            solids[name] = (file_id, _stl_solid_area(triangles))

    prior = raw.get("geometryExport", {}).get("stlFiles", {})
    if prior and prior != files:
        raise ValueError("geometryExport.stlFiles disagrees with actual grouped STL files")
    output = deepcopy(raw)
    output["geometryExport"]["stlFiles"] = files
    targets = (set(binding.get("solidName") for binding in output["geometryExport"]["solids"].values())
               if target_solid_names is None else target_solid_names)
    if not isinstance(targets, set) or not targets or any(not isinstance(name, str) or not name for name in targets):
        raise ValueError("target_solid_names must be a nonempty set of solid names")
    seen_names: set[str] = set()
    for key, binding in output["geometryExport"]["solids"].items():
        name = binding.get("solidName")
        if name not in targets:
            continue
        if not isinstance(name, str) or not name or name not in solids:
            raise ValueError(f"geometryExport.solids[{key}] missing STL solid {name!r}")
        if name in seen_names:
            raise ValueError(f"geometryExport repeats solidName {name!r}")
        seen_names.add(name)
        file_id, area = solids[name]
        if not math.isfinite(area) or area <= 0:
            raise ValueError(f"geometryExport.solids[{key}] has degenerate area")
        declared = binding.get("areaM2")
        if declared is not None:
            if isinstance(declared, bool) or not isinstance(declared, (int, float)) or not math.isfinite(declared):
                raise ValueError(f"geometryExport.solids[{key}].areaM2 is not finite")
            if abs(area - declared) / max(abs(area), abs(declared), 1e-9) > .05:
                raise ValueError(f"geometryExport.solids[{key}].areaM2 disagrees with STL area")
        old_id = binding.get("stlFileId")
        if old_id is not None and old_id != file_id:
            raise ValueError(f"geometryExport.solids[{key}].stlFileId disagrees with STL file")
        binding["stlFileId"] = file_id
        binding["areaM2"] = area

    if seen_names != targets:
        raise ValueError(f"target solid names missing from geometryExport: {sorted(targets - seen_names)}")

    CfdrunV2.model_validate(output)
    return output
