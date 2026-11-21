"""Backend-side reproduction of buildCfdrunJson()'s config derivation
(frontend STL_generate.html:33861-33974). Pure functions, no I/O.

Stage 1 of the cfdrun V3 migration (see
docs/superpowers/specs/2026-07-08-cfdrun-v3-thin-contract-design.md): the
backend, not the frontend, owns the derived meshConfig/simulationConfig. The
three geometry/UI-dependent fields (blockMeshBounds, roomTemperature,
monitoring) are passed in as parameters — Stage 1 sources them from the existing
meshGeometry blob to preserve exact parity; Stages 2-3 re-source them.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .stlgen_runner import _resolve_mesh_engine, _resolve_mesh_mode

#: Steady iterations of a project that saved none: the editors' new-project
#: default (cfdproj_state_v4.js DEFAULT_SIM_CONFIG.endTime, cfdrun_build.js), so
#: a run executes what the solver panel shows. 1500 since 2026-10-22: the
#: return-air verdict needs at least 1000 (convergence.minimum_run_iterations).
NEW_PROJECT_STEADY_END_TIME = 1500


def _or(value, default):
    """Frontend `value || default` semantics (None/""/0/[]/{} fall back)."""
    return value if value not in (None, "") else default


def derive_mesh_config(
    root_mesh_config: Dict[str, Any],
    root_sim_config: Dict[str, Any],
    *,
    block_mesh_bounds: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Reproduce buildCfdrunJson().meshConfig (STL_generate.html:33891-33930).

    `block_mesh_bounds` is geometry-dependent (frontend `_getBMBounds()`), so it
    is passed through verbatim in Stage 1 and re-sourced in P3.
    """
    mc = root_mesh_config or {}
    sc = root_sim_config or {}
    preset = str(mc.get("meshSizePreset") or "").strip() or None
    coarse_preset = preset == "coarsePatchPreserving"
    out: Dict[str, Any] = {
        "baseSize": mc.get("meshBaseSize") if mc.get("meshBaseSize") is not None else (0.4 if coarse_preset else 0.25),
        "maxGlobalCells": int(mc.get("maxGlobalCells") or 0) or 5000000,
        "refinementLevel": _or(mc.get("refinementLevel"), [1, 1]),
        "rackRegionLevel": _or(mc.get("rackRegionLevel"), 2 if coarse_preset else 3),
        "meshMode": _resolve_mesh_mode(mc),
        "floorRefinementLevel": _or(mc.get("floorRefinementLevel"), [1, 1]),
        "acuRefinementLevel": _or(mc.get("acuRefinementLevel"), [1, 1]),
        "boxRefinementLevel": _or(mc.get("boxRefinementLevel"), [1, 1]),
        "partitionWallRefinementLevel": _or(sc.get("partitionWallRefinementLevel"), [1, 1]),
        "baffleRefinementLevel": _or(mc.get("wallBafflesRefinementLevel"), [1, 1]),
        "defaultFeatureLevel": _or(mc.get("defaultFeatureLevel"), 2),
        "floorFeatureLevel": _or(mc.get("floorFeatureLevel"), 2),
        "acuFeatureLevel": _or(mc.get("acuFeatureLevel"), 2),
        "boxFeatureLevel": _or(mc.get("boxFeatureLevel"), 2),
        "partitionWallFeatureLevel": _or(sc.get("partitionWallFeatureLevel"), 2),
        "baffleFeatureLevel": _or(mc.get("baffleFeatureLevel"), 2),
        "parallelMesh": mc.get("parallelMesh"),
        "meshProcess": mc.get("meshProcess"),
        "blockMeshBounds": block_mesh_bounds,
        "refinementZones": [
            {
                "name": z.get("name"),
                "level": z.get("level"),
                "min": {"x": z["min"]["x"], "y": z["min"]["y"], "z": z["min"]["z"]},
                "max": {"x": z["max"]["x"], "y": z["max"]["y"], "z": z["max"]["z"]},
            }
            for z in (mc.get("refinementZones") or [])
        ],
    }
    # detectBaseSize / firstCellSize are omitted when null/0 (frontend parity).
    dbs = mc.get("meshDetectBaseSize")
    if dbs and dbs > 0:
        out["detectBaseSize"] = dbs
    fcs = mc.get("firstCellSize")
    if fcs and fcs > 0:
        out["firstCellSize"] = fcs
    # Match the frontend: export the product default and retain explicit choices.
    out["meshEngine"] = _resolve_mesh_engine(mc)
    # Same rule for the dcCart weld tolerance: absent stays absent (the runner
    # then applies its 1.2e-3 default), a project value travels as given.
    if mc.get("weldTolerance") is not None:
        out["weldTolerance"] = mc.get("weldTolerance")
    # And for the first-layer floor keys that ride on the weld.
    for key in ("firstLayerMinFraction", "firstLayerMax", "firstLayerSealGap", "rackCellMinSize", "meshTimeLimitSeconds"):
        if mc.get(key) is not None:
            out[key] = mc.get(key)
    # The rack-region shape, likewise only when the project chooses one.
    for key in ("rackRegionMode", "rackRegionDepth"):
        if mc.get(key):
            out[key] = mc.get(key)
    if isinstance(mc.get("slotAlignedBackground"), bool):
        out["slotAlignedBackground"] = mc["slotAlignedBackground"]
    elif coarse_preset:
        out["slotAlignedBackground"] = True
    if preset is not None:
        out["meshSizePreset"] = preset
    # dcCart coarsening of the fine mesh (L1 / L0 meshes that keep every patch), only when the project asks
    for key in ("coarsenLevel", "coarsenRatio", "meshSource"):
        if mc.get(key):
            out[key] = mc.get(key)
    if coarse_preset:
        out.setdefault("rackRegionMode", "servers")
        out.setdefault("rackRegionDepth", "slots")

    return out


def derive_sim_config(
    root_sim_config: Dict[str, Any],
    *,
    room_temperature: Any,
    monitoring: Dict[str, Any],
) -> Dict[str, Any]:
    """Reproduce buildCfdrunJson().simulationConfig (STL_generate.html:33931-33967).

    `room_temperature` (frontend `state.geometry.temperature`) and `monitoring`
    (frontend `smGetMonitoringConfig()`) have no clean root-V2 source today, so
    they are passed through in Stage 1 and re-sourced in P2.
    """
    sc = root_sim_config or {}
    rc = sc.get("residualControl") or {}
    return {
        **({"pressureLinearSolver": dict(sc["pressureLinearSolver"])}
           if sc.get("pressureLinearSolver") is not None else {}),
        # no saved iterations: the editors' new-project default (2026-10-22)
        "endTime": int(sc.get("endTime") or 0) or NEW_PROJECT_STEADY_END_TIME,
        "writeInterval": int(sc.get("writeInterval") or 0) or 100,
        "doublePrecisionWrite": sc.get("doublePrecisionWrite") or False,
        "writePrecision": _or(sc.get("writePrecision"), "single"),
        "endTimeSeconds": _or(sc.get("endTimeSeconds"), 300),
        "deltaT": _or(sc.get("deltaT"), 0.1),
        "maxDeltaT": _or(sc.get("maxDeltaT"), 1.0),
        "maxCo": _or(sc.get("maxCo"), 1.0),
        "writeIntervalSeconds": _or(sc.get("writeIntervalSeconds"), 10),
        "adjustTimeStep": sc.get("adjustTimeStep") is not False,
        "startFrom": _or(sc.get("startFrom"), "latestTime"),
        "nOuterCorrectors": _or(sc.get("nOuterCorrectors"), 1),
        "nCorrectors": _or(sc.get("nCorrectors"), 2),
        "ddtScheme": _or(sc.get("ddtScheme"), "Euler"),
        "crankNicolsonCoeff": _or(sc.get("crankNicolsonCoeff"), 0.9),
        "solverType": _or(sc.get("solverType"), "steady"),
        "roomTemperature": room_temperature,
        "parallelSolve": sc.get("parallelSolve") or False,
        "solveProcess": _or(sc.get("solveProcess"), 1),
        "gravity": {
            "x": sc["gravityX"] if sc.get("gravityX") is not None else 0,
            "y": sc["gravityY"] if sc.get("gravityY") is not None else -9.81,
            "z": sc["gravityZ"] if sc.get("gravityZ") is not None else 0,
        },
        "monitoring": monitoring,
        "residualControl": {
            "p_rgh": rc.get("p_rgh", 1e-3) if rc else 1e-3,
            "U": rc.get("U", 1e-3) if rc else 1e-3,
            "T": rc.get("T", 1e-3) if rc else 1e-3,
            "k_epsilon": rc.get("k_epsilon", 1e-3) if rc else 1e-3,
        },
        "schemes": sc.get("schemes") or {},
        "relaxationFactors": sc.get("relaxationFactors")
        or {"p_rgh": 0.3, "U": 0.7, "T": 0.95, "k_epsilon": 0.7},
        "nNonOrthogonalCorrectors": _or(sc.get("nNonOrthogonalCorrectors"), 1),
        "solverPreset": _or(sc.get("solverPreset"), "fast"),
        "nonOrthoCorrection": _or(sc.get("nonOrthoCorrection"), True),
        # E8c: the transient per-step set, only when the project carries it
        # (goldens predate it and must stay byte-exact).
        **({"transientResidualControl": sc["transientResidualControl"]}
           if sc.get("transientResidualControl") else {}),
        # The steady stop tolerance, only when the project carries it (same reason).
        **({"stopToleranceK": sc["stopToleranceK"]} if sc.get("stopToleranceK") is not None else {}),
    }


def derive_run_config(raw_manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 1 facade: derive meshConfig/simulationConfig from ROOT config,
    carrying the 3 geometry/UI-dependent fields from the meshGeometry blob."""
    root_mc = raw_manifest.get("meshConfig") or {}
    root_sc = raw_manifest.get("simulationConfig") or {}
    blob = raw_manifest.get("meshGeometry") or {}
    blob_mc = blob.get("meshConfig") or {}
    blob_sc = blob.get("simulationConfig") or {}
    return {
        "meshConfig": derive_mesh_config(
            root_mc, root_sc, block_mesh_bounds=blob_mc.get("blockMeshBounds")
        ),
        "simulationConfig": derive_sim_config(
            root_sc,
            room_temperature=blob_sc.get("roomTemperature", 22),
            monitoring=blob_sc.get("monitoring", {}),
        ),
    }


def execution_blob(raw_manifest: Dict[str, Any]) -> Dict[str, Any]:
    """The meshGeometry blob the runner executes (validated by ``CFDExport``).

    v2/v3 carry simulationConfig twice and the runner executes the blob's copy.
    cfdrun v4 carries ONE — the root (E8a, 2026-10-01): the blob has no
    simulationConfig and the executed one is derived here from the root, room
    temperature and monitoring included, exactly as ``buildCfdrunJson`` built it.
    """
    blob = dict(raw_manifest.get("meshGeometry") or {})
    if raw_manifest.get("schemaVersion") != 4:
        return blob
    if "simulationConfig" in blob:
        raise ValueError(
            "a schemaVersion 4 .cfdrun carries one simulationConfig (the root); "
            "meshGeometry.simulationConfig must be absent — re-export from the editor"
        )
    root = raw_manifest.get("simulationConfig") or {}
    if root.get("roomTemperatureC") is None:
        raise ValueError("a schemaVersion 4 .cfdrun needs simulationConfig.roomTemperatureC")
    timeline = raw_manifest.get("transient")
    if timeline is not None:
        from .cfdrun_schema import require_air_transient_duration
        require_air_transient_duration(root.get("solverType"), root.get("endTimeSeconds"), timeline.get("durationS"))
    blob["simulationConfig"] = derive_sim_config(
        root,
        room_temperature=root["roomTemperatureC"],
        monitoring=root.get("monitoring") or {},
    )
    return blob


def shadow_drift(raw_manifest: Dict[str, Any]) -> list:
    """Config blocks where the V3-derived config differs from the meshGeometry
    blob config the runner uses today. Empty list = full parity.

    Compares derived vs the RAW blob (not the post-CFDExport ``self._data``):
    the runner does ``self._data = CFDExport(blob).model_dump()``, so if
    ``derived == blob`` then ``CFDExport(derived) == CFDExport(blob) ==
    self._data`` — the CFDExport normalisation cancels out. Used by the runner's
    ``DCDT_CFDRUN_V3_SHADOW`` compare and by tests.
    """
    derived = derive_run_config(raw_manifest)
    blob = raw_manifest.get("meshGeometry") or {}
    # v4 has one simulationConfig (execution_blob derives it): only meshConfig
    # still has a second copy to compare.
    blocks = ("meshConfig",) if raw_manifest.get("schemaVersion") == 4 else ("meshConfig", "simulationConfig")
    return [blk for blk in blocks if derived.get(blk) != blob.get(blk)]
