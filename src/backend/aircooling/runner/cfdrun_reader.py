"""
cfdrun_reader.py — resolve a V2 .cfdrun manifest into an in-memory ResolvedRun.

This is the backend half of the P3 cfdrun-V2 refactor. The browser (Task 2/3)
emits a V2 manifest in which the JS translators have ALREADY computed every
boundary condition (the "device → patch role → bcType → parameters" derivation
that used to live in STLGenRunner._build_bridge_bc). This reader does NO physics:
it only MATERIALISES the explicit V2 bindings into the existing BridgeBCRegistry
by mapping the V2 camelCase `parameters` back onto BCSpec snake_case fields (the
AD8 table, applied IN REVERSE).

resolve_run(case_dir) does four things:
  1. Discover the manifest (glob model/*.cfdrun; 0→FileNotFoundError, 2+→
     RuntimeError, 1→use; or an explicit cfdrun_path).
  2. Validate it strictly via CfdrunV2.model_validate (raises the A12 "re-export"
     error on schemaVersion≠2).
  3. (Optional) resolve geometryExport against the case triSurface STL files:
     each referenced STL must exist and contain the solidName; if areaM2 is
     present it is cross-checked against the STL-computed area within tolerance.
  4. Materialise boundaryBindings / porousBindings into a BridgeBCRegistry and
     sourceTerms(porousZone) into fv_options_inputs.

It imports only `CfdrunV2` from cfdrun_schema for validation; it does NOT use the
v1 `CFDExport` geometry models. Any small geometry helper it needs (ASCII-STL
area math) is local to this module.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from loguru import logger

from backend.aircooling.runner.bridge_bc import (
    BCSpec,
    BridgeBCRegistry,
    PerforatedSpec,
    _sanitize,
)
from backend.aircooling.runner.cfdrun_schema import CfdrunV2

if TYPE_CHECKING:
    from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot

Vec3 = Tuple[float, float, float]
Triangle = Tuple[Vec3, Vec3, Vec3]

_GEOM_EPS = 1e-9
# Relative tolerance for the geometryExport.areaM2 vs STL-computed-area cross-check.
_AREA_REL_TOL = 0.05


# ---------------------------------------------------------------------------
# AD8 — reverse parameter mapping (V2 camelCase → BCSpec snake_case)
# ---------------------------------------------------------------------------
# Every BCSpec field has exactly ONE V2 parameter home. Keys whose value is
# None are NEVER passed to BCSpec (so the registry dump keeps the baseline's
# omit-None shape — BCSpec defaults the field to None anyway).
_AD8_PARAM_MAP: Dict[str, str] = {
    "temperatureC": "temperature",
    "flowRateM3s": "flow_rate",
    "powerW": "power",
    "sigma": "sigma",
    "heatFluxWm2": "heat_flux",
    "physicalFlowAreaM2": "physical_flow_area",
    "coolingCapacityW": "cooling_capacity",
    "minSupplyTempC": "min_supply_temp",
    "setPointTempC": "set_point_temp",
    "coolantTempC": "coolant_temp",
    "returnPatchName": "return_patch_name",
    "coolingModelMode": "cooling_model_mode",
    "ratedReturnTempC": "rated_return_temp",
    "coolingPerformanceTable": "cooling_performance_table",
    "timeVarying": "timeVarying",
    "surfaceAreaM2": "surface_area",
    "directionWorld": "direction",
    "patchAreaM2": "patch_area",
    "supplyAngleDeg": "supply_angle_deg",
    # `perforation` is handled specially (→ PerforatedSpec).
}


# ---------------------------------------------------------------------------
# ResolvedRun — the materialised, in-memory result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedRun:
    """Everything the OpenFOAM runner needs from a V2 .cfdrun, materialised.

    Attributes:
        bridge_registry:   patch → BCSpec (the materialised boundary conditions).
        fv_options_inputs: cellzone region → porous-zone fvOptions input dict
                           (the runner formats these into the OpenFOAM fvOptions).
        probes:            postprocessConfig.probes (probe definitions).
        surface_monitors:  postprocessConfig.surfaceMonitors.
        mesh_config:        meshConfig as a plain dict.
        simulation_config:  simulationConfig as a plain dict.
        geometry_solids:    geometryExport.solids as a plain dict.
        artifacts:          run artifacts (paths/logs) as a plain dict.
        source_terms:       non-porous source terms (heatSource etc.), kept for
                            downstream use (keyed by source-term id).
    """
    bridge_registry: BridgeBCRegistry
    fv_options_inputs: Dict[str, Any]
    probes: Any
    surface_monitors: Any
    mesh_config: Dict[str, Any]
    simulation_config: Dict[str, Any]
    geometry_solids: Dict[str, Any]
    artifacts: Dict[str, Any]
    source_terms: Dict[str, Any] = field(default_factory=dict)
    # cfdrun v3: the timeline (start, duration) of a transient run; None for v2.
    transient: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Local ASCII-STL area helper (no cfdrun_schema / stlgen_runner import)
# ---------------------------------------------------------------------------

def _read_ascii_stl_triangles(stl_path: Path) -> Dict[str, List[Triangle]]:
    """Parse triangles grouped by solid name from an ASCII STL file.

    Mirrors stlgen_runner._read_ascii_stl_triangles (kept local so the reader
    has no runner dependency).
    """
    solids: Dict[str, List[Triangle]] = {}
    current_name: Optional[str] = None
    vertices: List[Vec3] = []

    with open(stl_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("solid "):
                current_name = stripped[6:].strip()
                if current_name:
                    solids.setdefault(current_name, [])
                vertices = []
                continue
            if stripped.startswith("endsolid"):
                current_name = None
                vertices = []
                continue
            if current_name and stripped.startswith("vertex "):
                parts = stripped.split()
                if len(parts) >= 4:
                    vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    if len(vertices) == 3:
                        solids[current_name].append((vertices[0], vertices[1], vertices[2]))
                        vertices = []
    return solids


def _triangle_area(triangle: Triangle) -> float:
    a, b, c = triangle
    ux, uy, uz = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    vx, vy, vz = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def _stl_solid_area(triangles: List[Triangle]) -> float:
    total = 0.0
    for tri in triangles:
        a = _triangle_area(tri)
        if a > _GEOM_EPS:
            total += a
    return total


# ---------------------------------------------------------------------------
# Discovery (ported from STLGenRunner.__init__, stlgen_runner.py:915-949)
# ---------------------------------------------------------------------------

def _discover_cfdrun(model_dir: Path) -> Path:
    candidates = sorted(model_dir.glob("*.cfdrun"))
    if len(candidates) == 0:
        raise FileNotFoundError(
            f"no *.cfdrun file found in {model_dir}. Re-export from the STL UI "
            f"(or POST /api/browser/export-cfdrun for the manifest), or pass "
            f"cfdrun_path= explicitly."
        )
    if len(candidates) > 1:
        raise RuntimeError(
            f"multiple *.cfdrun files in {model_dir}: {[c.name for c in candidates]}. "
            f"Pass cfdrun_path= explicitly to disambiguate."
        )
    return candidates[0]


# ---------------------------------------------------------------------------
# STL geometry resolution
# ---------------------------------------------------------------------------

def _resolve_geometry(model: CfdrunV2, tri_surface_dir: Path) -> None:
    """Cross-check geometryExport.solids against the case triSurface STLs.

    For every solid:
      - its referenced STL file must exist under tri_surface_dir;
      - the STL must contain a `solid <solidName>` block;
      - if areaM2 is present it must agree with the STL-computed area within
        a relative tolerance.

    Raises a path-addressed RuntimeError (naming the offending solid key) on
    any mismatch. A cache keeps each STL parsed at most once.
    """
    geom = model.geometryExport
    stl_files = geom.stlFiles or {}
    triangle_cache: Dict[Path, Dict[str, List[Triangle]]] = {}

    def _solids_for(stl_path: Path) -> Dict[str, List[Triangle]]:
        if stl_path not in triangle_cache:
            triangle_cache[stl_path] = _read_ascii_stl_triangles(stl_path)
        return triangle_cache[stl_path]

    for solid_key, binding in (geom.solids or {}).items():
        solid_name = binding.solidName
        if not solid_name:
            # No solid name to resolve — nothing to cross-check.
            continue

        # Locate the STL file for this solid.
        stl_rel: Optional[str] = None
        if binding.stlFileId and binding.stlFileId in stl_files:
            stl_rel = stl_files[binding.stlFileId].path
        if not stl_rel:
            raise RuntimeError(
                f"geometryExport.solids[{solid_key!r}] (solidName={solid_name!r}) "
                f"has no resolvable stlFileId/path "
                f"(stlFileId={binding.stlFileId!r})."
            )

        stl_path = tri_surface_dir / stl_rel
        if not stl_path.exists():
            raise RuntimeError(
                f"geometryExport.solids[{solid_key!r}] references STL "
                f"{stl_rel!r} (file id {binding.stlFileId!r}), but "
                f"{stl_path} does not exist under {tri_surface_dir}."
            )

        solids = _solids_for(stl_path)
        if solid_name not in solids:
            raise RuntimeError(
                f"geometryExport.solids[{solid_key!r}]: solid {solid_name!r} "
                f"is not present in {stl_rel!r}; found "
                f"{sorted(solids.keys())[:8]}."
            )

        if binding.areaM2 is not None:
            computed = _stl_solid_area(solids[solid_name])
            expected = float(binding.areaM2)
            denom = max(abs(expected), abs(computed), _GEOM_EPS)
            rel_err = abs(computed - expected) / denom
            if rel_err > _AREA_REL_TOL:
                raise RuntimeError(
                    f"geometryExport.solids[{solid_key!r}] (solidName="
                    f"{solid_name!r}): declared areaM2={expected:.6g} disagrees "
                    f"with STL-computed area={computed:.6g} "
                    f"(rel_err={rel_err:.3%} > {_AREA_REL_TOL:.0%})."
                )


# ---------------------------------------------------------------------------
# Materialisation — AD8-reverse, NO physics
# ---------------------------------------------------------------------------

def _reverse_params(parameters: Any) -> Dict[str, Any]:
    """Map a V2 BoundaryBindingParameters object → BCSpec snake_case kwargs.

    `perforation` becomes a PerforatedSpec; every other field maps via the AD8
    table. None values are dropped so the resulting BCSpec.model_dump() keeps
    the baseline's omit-None shape.
    """
    kwargs: Dict[str, Any] = {}
    if parameters is None:
        return kwargs
    data = parameters.model_dump() if hasattr(parameters, "model_dump") else dict(parameters)

    for camel, snake in _AD8_PARAM_MAP.items():
        value = data.get(camel)
        if value is None:
            continue
        kwargs[snake] = value

    perforation = data.get("perforation")
    if perforation is not None:
        # Drop None keys so PerforatedSpec uses its own defaults where unset.
        perf_kwargs = {k: v for k, v in dict(perforation).items() if v is not None}
        kwargs["perforated"] = PerforatedSpec(**perf_kwargs)

    return kwargs


def _schedule_value(rows, t, side):
    """A corner-row schedule at t: linear between rows, the left or right
    value at a step (two rows at one t), clamped at both ends."""
    at = [i for i, (u, _) in enumerate(rows) if u == t]
    if at:
        return rows[at[0]][1] if side == "left" else rows[at[-1]][1]
    if t < rows[0][0]:
        return rows[0][1]
    if t > rows[-1][0]:
        return rows[-1][1]
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        if t0 < t < t1:
            w = (t - t0) / (t1 - t0)
            if isinstance(v0, list):
                return [a + w * (b - a) for a, b in zip(v0, v1)]
            return v0 + w * (v1 - v0)
    return rows[-1][1]


def _velocity_rows(data: Dict[str, Any], schedules: Dict[str, Any]) -> List[Dict[str, Any]]:
    """U(t) = Q(t)/A · dir(t) for a wall-mounted supply, on the union of the
    flow and direction schedules' rows (a step in either is a step in U)."""
    area = float(data["patchAreaM2"])
    flow = schedules.get("flowRateM3s") or [[0.0, data.get("flowRateM3s") or 0.0]]
    direction = schedules.get("directionWorld") or [[0.0, list(data["directionWorld"])]]
    steps = {a[0] for rows in (flow, direction) for a, b in zip(rows, rows[1:]) if a[0] == b[0]}
    out: List[Dict[str, Any]] = []
    for t in sorted({row[0] for rows in (flow, direction) for row in rows}):
        for side in (("left", "right") if t in steps else ("right",)):
            q = float(_schedule_value(flow, t, side))
            d = [float(x) for x in _schedule_value(direction, t, side)]
            mag = sum(x * x for x in d) ** 0.5 or 1.0
            out.append({"t": t, "value": [q / area * x / mag for x in d]})
    return out


def _schedules_to_time_varying(bc_type: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """cfdrun v3 binding schedules → the runner's time-varying inputs."""
    schedules = data.get("schedules") or {}
    if not schedules:
        return None

    def rows(name, k=1.0):
        return [{"t": t, "value": v * k} for t, v in schedules[name]]

    tv: Dict[str, Any] = {}
    used = set()
    if bc_type == "acu_supply":
        if "temperatureC" in schedules:
            tv["supplyTemp"] = rows("temperatureC")
            used.add("temperatureC")
        wall = data.get("directionWorld") is not None and (data.get("patchAreaM2") or 0) > 0
        if wall and ({"flowRateM3s", "directionWorld"} & set(schedules)):
            tv["velocity"] = _velocity_rows(data, schedules)
            used |= {"flowRateM3s", "directionWorld"}
        elif "flowRateM3s" in schedules:
            tv["airflow"] = rows("flowRateM3s")
            used.add("flowRateM3s")
        used.add("supplyAngleDeg")  # recorded only; the direction carries the angle
    elif bc_type in ("fan_inlet", "fan_outlet"):
        if "powerW" in schedules:
            tv["powerRows"] = rows("powerW")
            used.add("powerW")
        if "flowRateM3s" in schedules:
            tv["airflow"] = rows("flowRateM3s")
            used.add("flowRateM3s")
        used.add("temperatureC")  # the exporter's outlet estimate; the runner derives its own
    elif bc_type == "heat_source_flux" and "heatFluxWm2" in schedules:
        tv["heatOutput"] = rows("heatFluxWm2", float(data.get("surfaceAreaM2") or 1.0))
        used.add("heatFluxWm2")
    left = set(schedules) - used
    if left:
        raise ValueError(f"a {bc_type} boundary cannot follow a schedule of {sorted(left)}")
    return tv


def _materialise_boundary_bindings(
    model: CfdrunV2, registry: BridgeBCRegistry
) -> None:
    """Register each boundaryBinding as a BCSpec under its patchName."""
    for binding in model.boundaryBindings.values():
        if not binding.patchName:
            logger.warning(
                f"boundaryBinding {binding!r} has no patchName; skipping."
            )
            continue
        kwargs = _reverse_params(binding.parameters)
        data = binding.parameters.model_dump() if binding.parameters is not None else {}
        scheduled = _schedules_to_time_varying(binding.bcType, data)
        if scheduled is not None:
            kwargs["timeVarying"] = scheduled
        spec = BCSpec(bc_type=binding.bcType, **kwargs)
        registry.register(binding.patchName, spec)


def _materialise_porous_bindings(
    model: CfdrunV2, registry: BridgeBCRegistry
) -> None:
    """Register each porousBinding as a BCSpec under its patchName.

    patchNames are already the `<solid>_master` / `<solid>_slave` pair emitted
    by the JS translator (mirroring createBaffles); the reader registers both
    verbatim. perforatedBaffle → perforated_baffle (sigma + PerforatedSpec);
    wallBaffle → wall_baffle.
    """
    for binding in model.porousBindings.values():
        if not binding.patchName:
            logger.warning(
                f"porousBinding {binding!r} has no patchName; skipping."
            )
            continue
        params = binding.parameters
        ptype = binding.type
        if ptype == "wallBaffle":
            spec = BCSpec(bc_type="wall_baffle")
        elif ptype == "perforatedBaffle":
            kwargs: Dict[str, Any] = {}
            if params is not None and params.sigma is not None:
                kwargs["sigma"] = params.sigma
            if params is not None and params.perforation is not None:
                perf_kwargs = {
                    k: v
                    for k, v in params.perforation.model_dump().items()
                    if v is not None
                }
                kwargs["perforated"] = PerforatedSpec(**perf_kwargs)
            spec = BCSpec(bc_type="perforated_baffle", **kwargs)
        else:
            # Other porous types (cyclicOpening / porousZone / leakageZone) are
            # not part of the registry materialisation contract; skip with a log.
            logger.warning(
                f"porousBinding type {ptype!r} (patchName="
                f"{binding.patchName!r}) is not materialised into the registry."
            )
            continue
        registry.register(binding.patchName, spec)


def _materialise_source_terms(
    model: CfdrunV2,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split sourceTerms into (fv_options_inputs, other_source_terms).

    porousZone source terms become fv_options_inputs keyed by region
    (cellzone name); the runner formats these into the OpenFOAM fvOptions dict.
    All other source-term types (heatSource etc.) are passed through verbatim,
    keyed by source-term id, for downstream use.
    """
    fv_options_inputs: Dict[str, Any] = {}
    other: Dict[str, Any] = {}

    for st_id, term in model.sourceTerms.items():
        if term.type == "porousZone":
            region = term.region or st_id
            params = term.parameters
            fv_options_inputs[region] = {
                "cellzone_name": region,
                "sigma": params.sigma if params is not None else None,
                "d": params.d if params is not None else None,
                "f": params.f if params is not None else None,
                "origin": params.origin if params is not None else None,
                "e1": params.e1 if params is not None else None,
                "e2": params.e2 if params is not None else None,
            }
        else:
            other[st_id] = term.model_dump()

    return fv_options_inputs, other


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_run(
    case_dir,
    cfdrun_path=None,
    tri_surface_dir=None,
    resolve_stl: bool = True,
    *,
    input_snapshot: Optional["CfdrunSnapshot"] = None,
) -> ResolvedRun:
    """Discover, validate, (optionally) resolve geometry, and materialise a
    V2 .cfdrun manifest into a ResolvedRun.

    Args:
        case_dir:        the CFD case directory (containing model/ and Mesh/).
        cfdrun_path:     explicit path to the .cfdrun manifest; if None, glob
                         model/*.cfdrun (0→FileNotFoundError, 2+→RuntimeError).
        tri_surface_dir: override for the triSurface directory; defaults to
                         <case_dir>/Mesh/constant/triSurface.
        resolve_stl:     when True (default) cross-check geometryExport.solids
                         against the triSurface STLs; set False to materialise
                         the registry without real STL files (unit tests).

    Returns:
        ResolvedRun.

    Raises:
        FileNotFoundError / RuntimeError: discovery contract.
        ValueError (from CfdrunV2): A12 re-export on schemaVersion≠2; strict
            validation failures.
        RuntimeError: STL resolution failures (path-addressed).
    """
    case_dir = Path(case_dir).resolve()
    model_dir = case_dir / "model"

    if input_snapshot is not None:
        from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
        if not isinstance(input_snapshot, CfdrunSnapshot):
            raise TypeError("input_snapshot must be an owned CfdrunSnapshot")
        input_snapshot.require_context(case_dir, cfdrun_path)
        raw = input_snapshot.parse()
    else:
        if cfdrun_path is None:
            cfdrun_path = _discover_cfdrun(model_dir)
        cfdrun_path = Path(cfdrun_path).resolve()
        raw = json.loads(cfdrun_path.read_text(encoding="utf-8"))
    model = CfdrunV2.model_validate(raw)  # A12 re-export raised here on bad version

    if resolve_stl:
        if tri_surface_dir is None:
            tri_surface_dir = case_dir / "Mesh" / "constant" / "triSurface"
        tri_surface_dir = Path(tri_surface_dir)
        _resolve_geometry(model, tri_surface_dir)

    registry = BridgeBCRegistry()
    _materialise_boundary_bindings(model, registry)
    _materialise_porous_bindings(model, registry)
    fv_options_inputs, other_source_terms = _materialise_source_terms(model)

    return ResolvedRun(
        bridge_registry=registry,
        fv_options_inputs=fv_options_inputs,
        probes=model.postprocessConfig.probes,
        surface_monitors=model.postprocessConfig.surfaceMonitors,
        mesh_config=model.meshConfig.model_dump(by_alias=True),
        simulation_config=model.simulationConfig.model_dump(by_alias=True),
        geometry_solids=model.geometryExport.model_dump().get("solids", {}),
        artifacts=model.artifacts.model_dump(),
        transient=model.transient.model_dump() if model.transient is not None else None,
        source_terms=other_source_terms,
    )


def dump_resolved_registry(case_dir, cfdrun_path=None) -> Dict[str, Any]:
    """Resolve a case and return a sorted {sanitized_name: BCSpec.model_dump} dict.

    Reusable equivalence helper for Task 3/7: once a REAL V2 manifest exists,
    they assert dump_resolved_registry(case) == cfdrun_v1/<key>.registry.json.
    Resolves WITHOUT STL cross-check so it works on a model-only fixture; the
    registry materialisation is independent of STL resolution.
    """
    resolved = resolve_run(case_dir, cfdrun_path=cfdrun_path, resolve_stl=False)
    dump: Dict[str, Any] = {}
    for sanitized_name, spec in resolved.bridge_registry.items():
        dump[sanitized_name] = spec.model_dump(mode="json")
    return {k: dump[k] for k in sorted(dump.keys())}
