"""Thermal metrics read back from one finished air solve directory.

Inputs are the solver's own written fields (latest time, serial or
decomposed, read by the post-processor's ``read_openfoam_case`` without
reconstruction) and the executed ``.cfdrun`` boundary bindings. Nothing is
re-solved and no OpenFOAM utility is run.

Face fluxes are the solver's own conservative face flux ``phi`` (m^3/s,
outward positive), read with the face temperature ``T`` from the same native
ASCII time directory (per processor when the run is decomposed), so each
``phi`` is paired with its own face's ``T``. ``U . S`` (``S`` the outward area
vector from Newell's formula on the face's point loop) is reported next to it
as ``velocityAreaFlowM3s``: at a pressure outlet such as an ACU return
(``pressureInletOutletVelocity``) the face velocity is the adjacent cell's,
so ``U . S`` is not the flux the solver conserved. Area-weighted statistics
(rack inlet means, min/max) use the post-processor's patch read. Reported:

* rack inlet temperature, per rack and per fan-server inlet patch: area- and
  flux-weighted means, face min/max;
* per ACU: supply and return patch temperatures and volumetric flows, and the
  heat removed ``rho cp sum phi (T - T_ref)`` over its supply+return patches,
  ``T_ref`` the flux-weighted ACU supply temperature;
* energy balance: declared IT power (sum of fan-server ``powerW``; heat-flux
  servers as ``q'' x mesh area``) against the ACU heat removal, and the
  enthalpy flux through the server patches, with ``rho`` and ``cp`` the same
  constants the runner uses for the fan-outlet temperature rise;
* mass balance over every physical patch (a check on the face orientation).

Heat paths these sums do not cover (fixed-temperature patches, volumetric
source terms) are listed under ``energyBalance.notCovered`` instead of being
estimated.
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

KELVIN = 273.15
#: Held while the post-processor's stdout is redirected (see compute_air_metrics).
_STDOUT_SWAP = threading.Lock()
_SERVER_PATH = re.compile(r"^constructions\.racks\.([^.]+)\.servers\.([^.]+)$")


def _area_vectors(surface) -> np.ndarray:
    """Outward area vector of every polygon of a boundary patch (Newell)."""
    from vtkmodules.util.numpy_support import vtk_to_numpy

    polys = surface.GetPolys()
    if polys.GetNumberOfCells() != surface.n_cells:
        raise ValueError("boundary patch contains non-polygon cells")
    offsets = vtk_to_numpy(polys.GetOffsetsArray()).astype(np.int64)
    connectivity = vtk_to_numpy(polys.GetConnectivityArray()).astype(np.int64)
    points = np.asarray(surface.points, dtype=np.float64)
    following = np.arange(1, connectivity.size + 1)
    following[offsets[1:] - 1] = offsets[:-1]  # close every loop on its first point
    crosses = np.cross(points[connectivity], points[connectivity[following]])
    return 0.5 * np.add.reduceat(crosses, offsets[:-1], axis=0)


def _patch_table(boundary) -> Dict[str, Dict[str, np.ndarray]]:
    table: Dict[str, Dict[str, np.ndarray]] = {}
    for index, name in enumerate(boundary.keys()):
        surface = boundary[index]
        if surface is None or surface.n_cells == 0:
            continue
        vectors = _area_vectors(surface)
        row = {"S": vectors, "A": np.linalg.norm(vectors, axis=1)}
        for field in ("T", "U"):
            if field in surface.cell_data:
                row[field] = np.asarray(surface.cell_data[field], dtype=np.float64)
        if "U" in row:
            row["Uphi"] = np.einsum("ij,ij->i", row["U"], vectors)
        table[name] = row
    return table


_COMPACT = re.compile(r"List<scalar>\s*(\d+)\s*\{\s*([^}\s]+)\s*\}")
_ENTRY = re.compile(r"\s*\"?([^\s{}();\"]+)\"?\s*\{")


def _value_entry(block: str, faces: int) -> Optional[np.ndarray]:
    """``value`` of one boundaryField entry: ``uniform x``, ``nonuniform List<scalar> N (...)`` or ``N{x}``."""
    match = re.search(r"\bvalue\s+uniform\s+([^;\s]+)\s*;", block)
    if match:
        return np.full(faces, float(match.group(1)))
    match = re.search(r"\bvalue\s+nonuniform\s+List<scalar>\s*(\d+)\s*@(\S+)@", block)
    if match:
        return np.full(int(match.group(1)), float(match.group(2)))
    match = re.search(r"\bvalue\s+nonuniform\s+List<scalar>\s*(\d+)\s*\(", block)
    if match:
        count = int(match.group(1))
        values = np.array(block[match.end():block.index(")", match.end())].split(), dtype=np.float64)
        if values.size != count:
            raise ValueError(f"value list declares {count} entries, holds {values.size}")
        return values
    return None


def _boundary_values(path: Path, nfaces: Dict[str, int]) -> Dict[str, np.ndarray]:
    """Per-patch face values of one ASCII scalar field file, for the patches in *nfaces*."""
    text = path.read_text(encoding="utf-8", errors="replace")
    position = text.index("{", text.index("boundaryField")) + 1
    text = _COMPACT.sub(lambda m: f"List<scalar> {m.group(1)} @{m.group(2)}@", text[position:])
    text = re.sub(r"//[^\n]*", "", text)
    out: Dict[str, np.ndarray] = {}
    position = 0
    while True:
        entry = _ENTRY.match(text, position)
        if entry is None:
            break
        depth, cursor = 1, entry.end()
        while depth:
            nxt = min((k for k in (text.find("{", cursor), text.find("}", cursor)) if k >= 0), default=-1)
            if nxt < 0:
                raise ValueError(f"unbalanced boundaryField in {path}")
            depth += 1 if text[nxt] == "{" else -1
            cursor = nxt + 1
        name = entry.group(1)
        if name in nfaces:
            values = _value_entry(text[entry.end():cursor - 1], nfaces[name])
            if values is not None:
                out[name] = values
        position = cursor
    return out


def _native_fluxes(run_dir: Path, time_value: float, decomposed: bool) -> Optional[Dict[str, Dict[str, np.ndarray]]]:
    """``{patch: {"phi", "T"}}`` concatenated over ranks; None if a field is missing."""
    from backend.aircooling.runner.mesh_layout import processor_mesh_dirs
    from backend.aircooling.runner.postprocess import MeshPreviewLoader

    meshes = processor_mesh_dirs(run_dir) if decomposed else [run_dir / "constant" / "polyMesh"]
    table: Dict[str, Dict[str, List[np.ndarray]]] = defaultdict(lambda: {"phi": [], "T": []})
    for mesh in meshes:
        rank = mesh.parent.parent
        times = [child for child in rank.iterdir() if child.is_dir() and _is_time(child.name, time_value)]
        if len(times) != 1:
            return None
        nfaces = {row["name"]: int(row.get("nFaces", 0)) for row in MeshPreviewLoader._parse_boundary(mesh / "boundary")
                  if not str(row.get("type", "")).startswith("processor")}
        fields = {}
        for field in ("phi", "T"):
            path = times[0] / field
            if not path.is_file():
                return None
            fields[field] = _boundary_values(path, nfaces)
        for patch, count in nfaces.items():
            if count == 0:
                continue
            if patch not in fields["phi"] or patch not in fields["T"]:
                continue
            if fields["phi"][patch].size != count or fields["T"][patch].size != count:
                raise ValueError(f"{rank.name}/{patch}: field sizes disagree with nFaces {count}")
            table[patch]["phi"].append(fields["phi"][patch])
            table[patch]["T"].append(fields["T"][patch])
    return {patch: {key: np.concatenate(parts) for key, parts in row.items()} for patch, row in table.items()}


def _is_time(name: str, value: float) -> bool:
    try:
        return abs(float(name) - value) <= 1e-9 * max(1.0, abs(value))
    except ValueError:
        return False


def _stats(row: Optional[Dict[str, np.ndarray]]) -> Optional[Dict[str, Any]]:
    if row is None or "T" not in row:
        return None
    area, temperature = row["A"], row["T"]
    total = float(area.sum())
    out: Dict[str, Any] = {
        "faces": int(area.size), "areaM2": total,
        "areaMeanK": float((area * temperature).sum() / total) if total > 0 else None,
        "minK": float(temperature.min()), "maxK": float(temperature.max()),
    }
    if "Uphi" in row:
        out["velocityAreaFlowM3s"] = float(row["Uphi"].sum())
    if "nativePhi" in row:
        phi, face_t = row["nativePhi"], row["nativeT"]
        flow = float(phi.sum())
        out["outwardFlowM3s"] = flow
        out["fluxMeanK"] = float((phi * face_t).sum() / flow) if abs(flow) > 1e-12 else None
    return out


def _celsius(value: Optional[float]) -> Optional[float]:
    return None if value is None else value - KELVIN


def compute_air_metrics(run_dir: Path, manifest: Dict[str, Any], project: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from backend.aircooling.runner.postprocess import read_openfoam_case
    from backend.aircooling.utils.const import air_specific_heat, rho_air

    # The post-processor reports its timing on stdout; keep a CLI's stdout clean.
    # redirect_stdout swaps the process-wide sys.stdout, so concurrent reads (a
    # batch) take turns: unsynchronised swaps that end out of order leave one
    # read's buffer installed as stdout for good.
    chatter = io.StringIO()
    with _STDOUT_SWAP, contextlib.redirect_stdout(chatter):
        multiblock, source = read_openfoam_case(run_dir)
    table = _patch_table(multiblock["boundary"])
    native = None
    native_error = None
    try:
        native = _native_fluxes(Path(run_dir), float(source["timeValue"]), bool(source.get("decomposed")))
    except (OSError, ValueError) as exc:
        native_error = f"{type(exc).__name__}: {exc}"
    flux_source = "native phi and face T from the time directory" if native else "U.S from the patch read (phi unreadable)"
    for name, row in table.items():
        if native and name in native:
            row["nativePhi"], row["nativeT"] = native[name]["phi"], native[name]["T"]
        elif not native and "Uphi" in row and "T" in row:
            row["nativePhi"], row["nativeT"] = row["Uphi"], row["T"]
    fluxes = ({name: (row["phi"], row["T"]) for name, row in native.items()} if native else
              {name: (row["nativePhi"], row["nativeT"]) for name, row in table.items() if "nativePhi" in row})
    rho_cp = rho_air * air_specific_heat
    bindings = list((manifest.get("boundaryBindings") or {}).values())
    rack_names = {uid: ((model or {}).get("meta") or {}).get("name") or uid
                  for uid, model in (((project or {}).get("constructions") or {}).get("racks") or {}).items()}

    missing: List[str] = []

    def patch(name: str) -> Optional[Dict[str, np.ndarray]]:
        row = table.get(name)
        if row is None:
            missing.append(name)
        return row

    # -- rack inlet temperature ------------------------------------------
    racks: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"servers": []})
    for binding in bindings:
        if binding.get("bcType") != "fan_inlet":
            continue
        match = _SERVER_PATH.match(binding.get("objectPath") or "")
        if not match:
            continue
        rack_uid, server_uid = match.groups()
        row = patch(binding["patchName"])
        stats = _stats(row)
        racks[rack_uid]["servers"].append({"serverUid": server_uid, "patch": binding["patchName"],
                                           "declaredFlowM3s": (binding.get("parameters") or {}).get("flowRateM3s"),
                                           **(stats or {"missing": True})})
    rack_rows = []
    for rack_uid, entry in sorted(racks.items()):
        rows = [table[s["patch"]] for s in entry["servers"] if s["patch"] in table and "T" in table[s["patch"]]]
        if rows:
            area = np.concatenate([r["A"] for r in rows])
            temperature = np.concatenate([r["T"] for r in rows])
            flux_rows = [r for r in rows if "nativePhi" in r]
            phi = np.concatenate([r["nativePhi"] for r in flux_rows]) if len(flux_rows) == len(rows) else None
            face_t = np.concatenate([r["nativeT"] for r in flux_rows]) if phi is not None else None
            mean = float((area * temperature).sum() / area.sum())
            flux_mean = float((phi * face_t).sum() / phi.sum()) if phi is not None and abs(phi.sum()) > 1e-12 else None
            summary = {"areaMeanK": mean, "areaMeanC": _celsius(mean), "fluxMeanK": flux_mean,
                       "fluxMeanC": _celsius(flux_mean), "minK": float(temperature.min()),
                       "maxK": float(temperature.max()), "maxC": _celsius(float(temperature.max()))}
        else:
            summary = {"areaMeanK": None, "missing": True}
        rack_rows.append({"rackUid": rack_uid, "name": rack_names.get(rack_uid, rack_uid), **summary,
                          "servers": entry["servers"]})
    measured = [r for r in rack_rows if r.get("areaMeanK") is not None]
    rack_summary = {
        "count": len(rack_rows),
        "maxAreaMeanC": max((r["areaMeanC"] for r in measured), default=None),
        "minAreaMeanC": min((r["areaMeanC"] for r in measured), default=None),
        "meanOfAreaMeansC": (sum(r["areaMeanC"] for r in measured) / len(measured)) if measured else None,
        "maxFaceC": max((r["maxC"] for r in measured), default=None),
        "hottestRack": max(measured, key=lambda r: r["areaMeanC"])["name"] if measured else None,
    }

    # -- ACU supply / return ------------------------------------------------
    # Enthalpy is counted relative to the flux-weighted ACU supply temperature.
    # The total is unchanged (the net flux over all openings vanishes), but a
    # single ACU whose return draws more or less air than it supplies (returns
    # are pressure outlets sharing one room) is then not charged rho*cp*dphi*T.
    supply_fluxes = [fluxes[b["patchName"]] for b in bindings
                     if b.get("bcType") == "acu_supply" and b.get("patchName") in fluxes]
    supply_flow = sum(float(phi.sum()) for phi, _ in supply_fluxes)
    t_ref = (sum(float((phi * t).sum()) for phi, t in supply_fluxes) / supply_flow
             if supply_fluxes and abs(supply_flow) > 1e-12 else 0.0)
    acus = []
    acu_heat = 0.0
    acu_complete = True
    for binding in bindings:
        if binding.get("bcType") != "acu_supply":
            continue
        parameters = binding.get("parameters") or {}
        supply_row = patch(binding["patchName"])
        return_name = parameters.get("returnPatchName")
        return_row = patch(return_name) if return_name else None
        supply, ret = _stats(supply_row), _stats(return_row)
        heat = None
        flux_supply, flux_return = fluxes.get(binding["patchName"]), fluxes.get(return_name)
        if flux_supply is not None and flux_return is not None:
            heat = rho_cp * float((flux_supply[0] * (flux_supply[1] - t_ref)).sum()
                                  + (flux_return[0] * (flux_return[1] - t_ref)).sum())
            acu_heat += heat
        else:
            acu_complete = False
        acus.append({
            "objectPath": binding.get("objectPath"), "supplyPatch": binding["patchName"], "returnPatch": return_name,
            "declaredSupplyC": parameters.get("temperatureC"), "declaredFlowM3s": parameters.get("flowRateM3s"),
            "supply": supply, "return": ret,
            "supplyC": _celsius((supply or {}).get("areaMeanK")), "returnC": _celsius((ret or {}).get("areaMeanK")),
            "heatRemovedW": heat,
        })

    # -- energy balance -----------------------------------------------------
    declared_it = 0.0
    server_enthalpy = 0.0
    not_covered: List[str] = []
    for binding in bindings:
        kind = binding.get("bcType")
        parameters = binding.get("parameters") or {}
        if kind == "fan_outlet":
            declared_it += float(parameters.get("powerW") or 0.0)
        elif kind == "nonfan_heat_flux":
            row = table.get(binding["patchName"])
            if row is None:
                missing.append(binding["patchName"])
            else:
                declared_it += float(parameters.get("heatFluxWm2") or 0.0) * float(row["A"].sum())
        elif kind in ("nonfan_fixed_temp", "fixedTemperature"):
            not_covered.append(f"{binding['patchName']} ({kind}: heat follows from the solution)")
        if kind in ("fan_inlet", "fan_outlet"):
            flux = fluxes.get(binding["patchName"])
            if flux is not None:
                # Outward from the room: heat added to the room is minus this flux.
                server_enthalpy -= rho_cp * float((flux[0] * (flux[1] - t_ref)).sum())
    if manifest.get("sourceTerms"):
        not_covered.append(f"{len(manifest['sourceTerms'])} volumetric source term(s)")
    total_phi = sum(float(phi.sum()) for phi, _ in fluxes.values())
    inflow = sum(float(-phi[phi < 0].sum()) for phi, _ in fluxes.values())
    balance = {
        "rhoKgM3": rho_air, "cpJkgK": air_specific_heat, "referenceTemperatureK": t_ref,
        "declaredItPowerW": declared_it,
        "acuHeatRemovedW": acu_heat if acu_complete else None,
        "serverHeatToAirW": server_enthalpy,
        "acuVsDeclaredRelativeError": ((acu_heat - declared_it) / declared_it
                                       if acu_complete and declared_it > 0 else None),
        "acuVsServerFluxRelativeError": ((acu_heat - server_enthalpy) / server_enthalpy
                                         if acu_complete and abs(server_enthalpy) > 0 else None),
        "notCovered": not_covered,
    }
    return {
        "format": "dcdtuni.api.air-metrics/v1",
        "runDir": str(run_dir),
        "fieldSource": {**{k: v for k, v in source.items() if k in ("sourceFormat", "decomposed", "timeValue")},
                        "fluxSource": flux_source, "nativeFluxError": native_error},
        "rackInlet": {"summary": rack_summary, "racks": rack_rows},
        "acus": acus,
        "energyBalance": balance,
        "massBalance": {"netOutwardFlowM3s": total_phi, "totalInflowM3s": inflow,
                        "relativeImbalance": total_phi / inflow if inflow > 0 else None},
        "missingPatches": sorted(set(missing)),
        "patchCount": len(table),
    }
