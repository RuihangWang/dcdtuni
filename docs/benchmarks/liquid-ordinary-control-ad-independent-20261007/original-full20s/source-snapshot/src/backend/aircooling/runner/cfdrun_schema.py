"""
Pydantic v2 validation models for the .cfdrun manifest — both schema layers.

This single module holds the two cfdrun schema layers (merged 2026-06-15; the V2
half was previously a separate cfdrun_v2_schema.py, the v1 half was the
misleadingly-named cfdproj_schema.py):

1. Strict V2 manifest ROOT — `CfdrunV2` (extra="forbid", schemaVersion == 2):
   inputs / geometryExport / boundaryBindings / sourceTerms / porousBindings /
   meshConfig / simulationConfig / artifacts. Built by the browser from live V4
   state; materialised by cfdrun_reader.resolve_run(). This is the current format.

2. v1 GEOMETRY blob — `CFDExport` (extra="allow"): the geometry domain the mesh
   pipeline reads. In V2 it travels as the transitional `meshGeometry` block,
   validated via `CFDExport.model_validate(raw["meshGeometry"])` in
   stlgen_runner._load(). Its mesh/sim sub-models are CFDExportMeshConfig /
   CFDExportSimulationConfig (renamed from MeshConfig/SimulationConfig so they do
   not collide with the V2 sub-models of the same role).

Neither layer is the .cfdproj editor format (client-side only, never validated by
the backend).
"""

import re
from collections import Counter
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class PressureLinearSolverConfig(BaseModel):
    """Optional PCG settings; absent values preserve each mode's preset."""
    model_config = ConfigDict(extra="forbid")
    preconditioner: Optional[Literal["DIC", "GAMG"]] = None
    tolerance: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    relTol: Optional[float] = Field(default=None, ge=0, lt=1, allow_inf_nan=False, strict=True)
    maxIter: Optional[int] = Field(default=None, gt=0, strict=True)


# ===========================================================================
# v1 GEOMETRY-BLOB SCHEMA (CFDExport) — extra="allow", validates meshGeometry
# ===========================================================================

def _sanitize_patch_name(name: str) -> str:
    """Match the OpenFOAM/STLGenerator sanitization rules for patch names."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _summarize_duplicates(counter: Counter, limit: int = 8) -> str:
    duplicates = [f"{name} x{count}" for name, count in sorted(counter.items()) if count > 1]
    if len(duplicates) <= limit:
        return ", ".join(duplicates)
    head = ", ".join(duplicates[:limit])
    return f"{head}, ... (+{len(duplicates) - limit} more)"


class CFDBaseModel(BaseModel):
    """Base schema model that preserves forward-compatible extra fields."""

    model_config = ConfigDict(extra="allow")


class TimeVaryingEntry(CFDBaseModel):
    t: float
    value: float


class Point3D(CFDBaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class SeedConfig(CFDBaseModel):
    enabled: bool = True
    anchor: Point3D = Field(default_factory=Point3D)
    direction: Point3D = Field(default_factory=lambda: Point3D(x=0.0, y=0.0, z=1.0))
    offset: float = 0.0


class SensorPoint(CFDBaseModel):
    id: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class CFDExportRoom(CFDBaseModel):
    height: float = Field(3.6, description="Room height in meters (vertical extent along Y; Y-up convention)")
    plane: List[Point3D] = Field(default_factory=list)
    locationInMesh: Point3D = Field(default_factory=Point3D)
    extraLocationsInMesh: List[Point3D] = Field(default_factory=list)


class ServerPhysics(CFDBaseModel):
    volumeFlowRate: Optional[float] = 0.5
    power: Optional[float] = 1000.0
    temperatureRise: Optional[float] = None
    serverBCMode: Optional[Literal["powerAndFlow", "tempRiseAndFlow", "powerAndTempRise"]] = "powerAndFlow"
    fixedTemperature: Optional[float] = None
    fixedTempFace: Optional[Literal["inlet", "outlet"]] = "inlet"  # for nonFan servers; None for fanServer


class RackServer(CFDBaseModel):
    id: str
    type: Literal["fanServer", "nonFan"] = "fanServer"
    startSlot: int = 0
    spanSlots: int = 1
    # Legacy single-solid names. Null/empty when the face has been split into
    # multiple patches (see `faces` dict below for the per-patch names).
    inletSolidName: Optional[str] = ""
    outletSolidName: Optional[str] = ""
    topSolidName: Optional[str] = None     # nonFan only: top plate solid (fixedTemperature BC)
    bottomSolidName: Optional[str] = None  # nonFan only: bottom plate solid (fixedTemperature BC)
    # Absolute depth offsets (metres) of the server's inlet/outlet faces,
    # emitted by the V2 export on both the fanServer and nonFan branches
    # (replaces the never-read extraInletOffset/extraOutletOffset keys).
    # The runner consumes server geometry as exported STL surfaces, so these
    # declared fields are for diagnostics and future use; legacy manifests
    # simply lack them.
    inletOffsetM: Optional[float] = None
    outletOffsetM: Optional[float] = None
    physics: ServerPhysics = Field(default_factory=ServerPhysics)


class ThroughZoneGroup(CFDBaseModel):
    # Renamed 2026-04-17 from LeakageGroup. Existing cfdproj files with the
    # old keys will silently fall back to defaults — users need to re-save
    # from the UI to get the migrated keys.
    startUnit: int = 0
    endUnit: int = 0
    sigma: float = 0.05
    stlSolidName: str = ""


# Backward-compat alias (legacy manifests used LeakageGroup)
LeakageGroup = ThroughZoneGroup


class ThroughZoneConfig(CFDBaseModel):
    # Renamed 2026-04-17 from LeakageConfig. Existing cfdproj files with the
    # old keys will silently fall back to defaults — users need to re-save
    # from the UI to get the migrated keys.
    enabled: bool = False
    sigma: float = 0.05          # legacy global sigma (v1.0 compat)
    throughSlots: List[int] = Field(default_factory=list)
    groups: List[ThroughZoneGroup] = Field(default_factory=list)  # per-group sigma (v1.1+)


# Backward-compat alias
LeakageConfig = ThroughZoneConfig


class RackGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=lambda: {"W": 0.6, "D": 1.0, "H": 1.867})
    orientation: float = 0.0
    unitCount: int = 42
    unitHeight: float = 0.04445
    slotWidthRatio: float = 0.8


class RackDoorPerforated(CFDBaseModel):
    """Perforated-plate physical description for a rack inlet/outlet door.

    Shape mirrors `bridge_bc.PerforatedSpec` so the runner can construct one
    directly from the dict. Replaces the legacy fraction-valued `porosity`
    field (2026-04-16 unification).
    """
    shape: Literal["circle", "rect", "diamond", "hexagon"] = "circle"
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


class RackDoor(CFDBaseModel):
    """Rack inlet/outlet door definition (perforated baffle pair)."""
    enabled: bool = True
    perforated: RackDoorPerforated = Field(default_factory=RackDoorPerforated)
    solidName: Optional[str] = None


class CFDExportRack(CFDBaseModel):
    stlFile: str
    leakageStlFile: Optional[str] = None  # legacy schema field; already null in new exports
    geometry: RackGeometry = Field(default_factory=RackGeometry)
    servers: List[RackServer] = Field(default_factory=list)
    leakage: ThroughZoneConfig = Field(default_factory=ThroughZoneConfig)
    wallSolidNames: List[str] = Field(default_factory=list)
    inletDoor: Optional[RackDoor] = None
    outletDoor: Optional[RackDoor] = None


# ---- ACU multi-face support (new arrays) ----

class ACUFacePhysics(CFDBaseModel):
    temperature: Optional[float] = None   # supply face only
    volumeFlowRate: float = 4.0
    velocity: float = 2.0
    coolingCapacity: Optional[float] = None        # kW
    coolantTemperature: Optional[float] = None     # Celsius (chilled water inlet)
    setPointTemperature: Optional[float] = None    # Celsius
    minSupplyTemperature: Optional[float] = None   # Celsius (legacy, prefer coolantTemperature)
    ratedReturnTemp: Optional[float] = None            # Celsius (rated return air temp)
    coolingPerformanceTable: Optional[List[dict]] = None  # [{returnTemp, capacity}]
    timeVarying: Dict[str, Any] = Field(default_factory=dict)


class ACUFaceEntry(CFDBaseModel):
    solidName: str = "supply"
    patchId: Optional[str] = None
    face: Optional[str] = None
    physics: ACUFacePhysics = Field(default_factory=ACUFacePhysics)


# Legacy singular face models (kept for backward compatibility with old exports)
class SupplyFacePhysics(CFDBaseModel):
    temperature: float = 15.0
    volumeFlowRate: float = 4.0


class ReturnFacePhysics(CFDBaseModel):
    volumeFlowRate: float = 4.0


class ACUSupplyFace(CFDBaseModel):
    solidName: str = "supply"
    patchId: Optional[str] = None
    physics: SupplyFacePhysics = Field(default_factory=SupplyFacePhysics)


class ACUReturnFace(CFDBaseModel):
    solidName: str = "return"
    patchId: Optional[str] = None
    physics: ReturnFacePhysics = Field(default_factory=ReturnFacePhysics)


class SimpleGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)
    orientation: float = 0.0


class Quaternion4(CFDBaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


class CFDExportACU(CFDBaseModel):
    stlFile: str
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    # Multi-face arrays (v1.1+ format from frontend)
    supplyFaces: List[ACUFaceEntry] = Field(default_factory=list)
    returnFaces: List[ACUFaceEntry] = Field(default_factory=list)
    # Legacy singular faces (v1.0 compat — only present in old exports)
    supplyFace: Optional[ACUSupplyFace] = None
    returnFace: Optional[ACUReturnFace] = None
    wallSolidNames: List[str] = Field(default_factory=list)

    def get_supply_faces(self) -> List[ACUFaceEntry]:
        """Return supply face list, falling back to legacy supplyFace singular."""
        if self.supplyFaces:
            return self.supplyFaces
        if self.supplyFace:
            return [ACUFaceEntry(
                solidName=self.supplyFace.solidName,
                patchId=self.supplyFace.patchId,
                physics=ACUFacePhysics(
                    temperature=self.supplyFace.physics.temperature,
                    volumeFlowRate=self.supplyFace.physics.volumeFlowRate,
                )
            )]
        return []

    def get_return_faces(self) -> List[ACUFaceEntry]:
        """Return return face list, falling back to legacy returnFace singular."""
        if self.returnFaces:
            return self.returnFaces
        if self.returnFace:
            return [ACUFaceEntry(
                solidName=self.returnFace.solidName,
                patchId=self.returnFace.patchId,
                physics=ACUFacePhysics(
                    volumeFlowRate=self.returnFace.physics.volumeFlowRate,
                )
            )]
        return []

    def get_primary_supply_temperature(self) -> float:
        """Return temperature of first supply face for room.json compat."""
        faces = self.get_supply_faces()
        if faces and faces[0].physics.temperature is not None:
            return faces[0].physics.temperature
        return 15.0

    def get_primary_supply_flow_rate(self) -> float:
        """Return flow rate of first supply face for room.json compat."""
        faces = self.get_supply_faces()
        if faces:
            return faces[0].physics.volumeFlowRate
        return 4.0


class HeatPatchPhysics(CFDBaseModel):
    heatPower: float = 0.0
    fixedTemperature: Optional[float] = None  # direct surface temperature BC


class HeatPatch(CFDBaseModel):
    solidName: str
    patchId: Optional[str] = None
    physics: HeatPatchPhysics = Field(default_factory=HeatPatchPhysics)


class CFDExportWallObject(CFDBaseModel):
    stlFile: str
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    heatPatches: List[HeatPatch] = Field(default_factory=list)
    wallSolidNames: List[str] = Field(default_factory=list)


class CFDExportHeatSource(CFDBaseModel):
    stlFile: str
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    heatMode: Literal["power", "temperature"] = "power"
    totalPower: float = 100.0          # W (used when heatMode = "power")
    fixedTemperature: Optional[float] = None  # °C (used when heatMode = "temperature")
    wallSolidNames: List[str] = Field(default_factory=list)
    timeVarying: Dict[str, Any] = Field(default_factory=dict)


# baffles[*].physics = {
#   "baffleType": "wall" | "perforated",
#   "perforated": {                # required when baffleType == "perforated"
#     "shape": "circle" | "rect" | "diamond" | "hexagon",
#     "holeDiameterMm": float,
#     "openAreaPct": float         # 0..100
#   }
# }
# The `perforated` block carries the full physical description used by the
# Idelchik §8.2 eq. 8-4 pressure-loss formula (see bridge_bc.PerforatedSpec /
# perforated_to_porous_baffle).  When present it supersedes the legacy
# `porosity` scalar for perforated baffles.  `porosity` (0..100 percent) is
# retained for backward-compatibility and is still used by the classifier
# that decides "wall vs perforated" at the createBaffles stage.
class PerforatedConfig(CFDBaseModel):
    """Shared perforated-plate physical description (Idelchik §8.2 inputs).

    Used by baffle physics, floor opening physics, and rack doors. Mirrors
    `bridge_bc.PerforatedSpec` so the runner can construct one directly.
    """
    shape: Literal["circle", "rect", "diamond", "hexagon"] = "circle"
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


class BafflePhysics(CFDBaseModel):
    baffleType: Literal["wall", "perforated"] = "wall"
    porosity: float = 0.0
    perforated: Optional[PerforatedConfig] = None
    seedConfig: Optional[SeedConfig] = None


class BaffleGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)
    orientation: float = 0.0


class CFDExportBaffle(CFDBaseModel):
    stlFile: str
    geometry: BaffleGeometry = Field(default_factory=BaffleGeometry)
    physics: BafflePhysics = Field(default_factory=BafflePhysics)
    seedConfig: Optional[SeedConfig] = None
    wallRegionSeedConfigs: Dict[str, SeedConfig] = Field(default_factory=dict)
    patchPhysics: Dict[str, BafflePhysics] = Field(default_factory=dict)


class PartitionWallHolePhysics(CFDBaseModel):
    # 2026-04-16 simplification: the source-term gate moved entirely to
    # `leakageRate` (< 100% enables the source). `resistanceEnabled` is a
    # legacy field kept for backward compatibility with old cfdproj files
    # but is no longer the source of truth.
    resistanceEnabled: bool = False
    leakageRate: float = 100.0


class PartitionWallHole(CFDBaseModel):
    patchId: str
    shape: Literal["rectangle", "circle"] = "rectangle"
    name: str = ""
    zoneName: str = ""
    zoneStlFile: Optional[str] = None
    physics: PartitionWallHolePhysics = Field(default_factory=PartitionWallHolePhysics)


class PartitionWallGeometry(SimpleGeometry):
    transformQuaternion: Quaternion4 = Field(default_factory=Quaternion4)


class CFDExportPartitionWall(CFDBaseModel):
    stlFile: str
    geometry: PartitionWallGeometry = Field(default_factory=PartitionWallGeometry)
    wallSolidNames: List[str] = Field(default_factory=list)
    holes: List[PartitionWallHole] = Field(default_factory=list)


class FloorOpeningPhysics(CFDBaseModel):
    type: Literal["wall", "wallBaffle", "perforated"] = "wall"
    porosity: float = 0.0
    perforated: Optional[PerforatedConfig] = None


class FloorOpening(CFDBaseModel):
    solidName: str
    patchId: Optional[str] = None
    physics: FloorOpeningPhysics = Field(default_factory=FloorOpeningPhysics)
    seedConfig: Optional[SeedConfig] = None


class FloorGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)


class FloorDefaultBCPerforated(CFDBaseModel):
    """Perforated-plate physical description for a floor remaining-area BC.

    Mirrors `bridge_bc.PerforatedSpec`. 2026-04-16 unification replaces the
    legacy `defaultBCPorosity` scalar.
    """
    shape: Literal["circle", "rect", "diamond", "hexagon"] = "circle"
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


class CFDExportFloor(CFDBaseModel):
    stlFile: str
    geometry: FloorGeometry = Field(default_factory=FloorGeometry)
    roomBoundaryType: Literal["none", "top", "bottom"] = "none"
    defaultBC: Literal["wall", "wallBaffle", "perforated"] = "wall"
    defaultBCPorosity: float = 0.0  # legacy; superseded by defaultBCPerforated
    defaultBCPerforated: Optional[FloorDefaultBCPerforated] = None
    defaultBCSeedConfig: Optional[SeedConfig] = None
    defaultBCSeedConfigs: Dict[str, SeedConfig] = Field(default_factory=dict)
    openings: List[FloorOpening] = Field(default_factory=list)
    wallSolidNames: List[str] = Field(default_factory=list)


class CFDExportWall(CFDBaseModel):
    stlFile: Optional[str] = None
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    solidNames: List[str] = Field(default_factory=list)


class CFDExportMeshConfig(CFDBaseModel):
    baseSize: float = 0.25
    meshTimeLimitSeconds: Optional[float] = 300.0  # whole mesh build; capped at 300 s by runner
    # Auto-detect reference scale (2026-04-19 spec). When None, auto-detect
    # uses baseSize (backward compatible). When set, the client-side
    # ceil(log2(X / (H/2))) uses this value — decouples level-picking from
    # actual mesh baseSize so the user can pick a finer background for
    # solver stability without inflating auto-detect levels.
    detectBaseSize: Optional[float] = None
    refinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    # Single searchableBox refinementRegion level applied per rack.
    # Replaces the old per-component surface+feature level system
    # (rackRefinementLevel, serverFaceRefinementLevel, baffleRefinementLevel,
    # rackDoorRefinementLevel, serverFaceFeatureLevel, blankingPlateFeatureLevel,
    # rackPartition*, rackThroughZone* — all removed 2026-05-08).
    # Old cfdproj files that still carry those keys are accepted silently
    # via CFDBaseModel extra="allow".
    rackRegionLevel: int = 3
    partitionWallRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    partitionWallFeatureLevel: int = 2
    # Floor/ceiling category escaped from defaultFeatureLevel fallback.
    floorRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    floorFeatureLevel: int = 2
    parallelMesh: bool = True
    meshProcess: int = 16
    # snappyHexMesh upper bounds on cell count. Declared explicitly so pydantic
    # type-checks them even though CFDBaseModel sets extra="allow" — guards
    # against a future tightening of the extras policy silently dropping the
    # user's UI value (same class of bug as the perforated unification).
    maxGlobalCells: int = 5000000
    maxLocalCells: int = 5000000
    # --- Mesh generation mode (3-way; see docs/.../mesh-modes spec) ---
    # The mesh panel exposes ONE dropdown that maps to this field:
    #   "bodyFitted"        — original snappyHexMesh body-fitted: snap ON, uniform bg.
    #   "cartesianLocalized" — pure Cartesian: snap OFF, uniform bg + per-object local
    #                         refinementRegions (octree). No domain-spanning grid lines
    #                         (no cross-hatch); fewest cells; octree-stepped boundaries.
    #   "structured"        — grid-aligned Cartesian: snap OFF, blockMesh lines aligned
    #                         on object faces (compute_aligned_grid). Smooth straight
    #                         boundaries, but the lines span the domain (cross-hatch).
    # `_resolve_mesh_mode` (stlgen_runner) reads this, falling back to the legacy
    # cartesianMode/structuredGrid booleans below when meshMode is absent (old files).
    meshMode: Literal["bodyFitted", "cartesianLocalized", "structured"] = "bodyFitted"
    # --- Mesh ENGINE (orthogonal to meshMode): which program builds the mesh.
    #   "snappy"  — blockMesh + snappyHexMesh (default, unchanged behaviour)
    #   "dcCart"  — the repo's cut-cell Cartesian mesher (third_parties/dcCartMesh)
    #   "meshgen" — the vendored MeshGenerator cut-cell engine (runner
    #               _run_meshgen_pipeline: meshgen-cli cfdrun --dc-mode, one
    #               pass, no blockMesh/snappy/createBaffles)
    meshEngine: Literal["snappy", "dcCart", "meshgen"] = "snappy"
    # --- dcCart near-coincidence weld tolerance (m): fuse surface vertices
    # closer than this before the cut. None = runner default (1.2e-3), 0 = off.
    # DAML-Testbed evidence in test_dccart_weld_default.py.
    weldTolerance: Optional[float] = None
    # --- dcCart first-layer floor on top of the weld: the band widens per
    # surface class to firstLayerMinFraction x its level spacing (capped at
    # firstLayerMax m) so no wall carves a slab thinner than that; a gap wider
    # than firstLayerSealGap (< 0: weldTolerance) is never sealed. None = the
    # runner defaults (off / 0.005 / -1). test_dccart_first_layer.py.
    firstLayerMinFraction: Optional[float] = None
    firstLayerMax: Optional[float] = None
    firstLayerSealGap: Optional[float] = None
    # --- dcCart rack-cell floor (m): the thin-server auto-raise of
    # rackRegionLevel never makes the rack-region cell smaller than this
    # (6Sigma's finest cell is 17.5 mm). None/0 = uncapped.
    rackCellMinSize: Optional[float] = None
    # --- Legacy (Approach A) booleans — kept for backward-compatible reading of
    # older .cfdrun manifests (see Ref_code/REPORT_Cartesian_Mesh_Generator.md). New
    # exports use meshMode; these are derived/ignored when meshMode is present. ---
    # cartesianMode: when True, snappyHexMesh runs castellated-only (snap off).
    cartesianMode: bool = False
    # structuredGrid: opt-in "structured" (grid-aligned) sub-mode of cartesianMode.
    # Both sub-modes run snap off (pure Cartesian). They differ only in the blockMesh
    # background grid:
    #   False (DEFAULT) — pure Cartesian: a UNIFORM coarse background; per-object local
    #     octree refinementRegions add the fine detail. No domain-spanning grid lines,
    #     so no cross-hatch ("十字加密") and far fewer cells.
    #   True            — STRUCTURED: background grid lines placed exactly on object
    #     faces (compute_aligned_grid) for perfectly straight boundaries, at the cost
    #     of those lines spanning the whole domain. Non-default second option.
    # Ignored when cartesianMode is False (snap-on always uses the uniform background).
    structuredGrid: bool = False
    # firstCellSize: target near-wall Cartesian cell size in metres. With no prism
    # layers, the first-cell height IS the wall-adjacent cube: first-cell = baseSize/2^L.
    # When set, room-wall and floor surfaces are refined to level
    # ceil(log2(baseSize/firstCellSize)) (only refines, never coarsens) so y+ is
    # controllable. None = derive levels from the explicit per-object levels only.
    firstCellSize: Optional[float] = None


class ResidualControl(CFDBaseModel):
    p_rgh: float | str = "1e-4"
    U: float | str = "1e-5"
    T: float | str = "1e-5"
    k_epsilon: float | str = "1e-4"


class CFDExportSimulationConfig(CFDBaseModel):
    pressureLinearSolver: Optional[PressureLinearSolverConfig] = None
    endTime: int = 500
    writeInterval: int = 100
    doublePrecisionWrite: bool = False
    writePrecision: str = "single"
    endTimeSeconds: float = 300.0
    deltaT: float = 0.1
    maxDeltaT: float = 1.0
    maxCo: float = 1.0
    adjustTimeStep: bool = True
    writeIntervalSeconds: float = 10.0
    nOuterCorrectors: int = 1
    nCorrectors: int = 2
    ddtScheme: str = "Euler"
    crankNicolsonCoeff: float = 0.9
    solveProcess: int = 1
    solverType: str = "steady"
    roomTemperature: float = 22.0
    parallelSolve: bool = False
    gravity: Dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": -9.81, "z": 0})
    monitoring: Dict[str, Any] = Field(default_factory=dict)
    residualControl: ResidualControl = Field(default_factory=ResidualControl)
    schemes: Dict[str, str] = Field(default_factory=dict)
    relaxationFactors: Dict[str, float] = Field(default_factory=dict)
    nNonOrthogonalCorrectors: int = 0
    nonOrthoCorrection: bool = False
    solverPreset: str = "balanced"
    physical_properties: Dict[str, Any] = Field(default_factory=dict)
    unitTemperature: str = "C"


class CFDExport(CFDBaseModel):
    """Root schema for the .cfdrun manifest emitted by frontend."""
    version: str = "1.0"
    source: str = "frontend"
    projectName: str = "CFD_Project"
    room: CFDExportRoom = Field(default_factory=CFDExportRoom)
    racks: Dict[str, CFDExportRack] = Field(default_factory=dict)
    acus: Dict[str, CFDExportACU] = Field(default_factory=dict)
    wallObjects: Dict[str, CFDExportWallObject] = Field(default_factory=dict)
    heatSources: Dict[str, CFDExportHeatSource] = Field(default_factory=dict)
    baffles: Dict[str, CFDExportBaffle] = Field(default_factory=dict)
    partitionWalls: Dict[str, CFDExportPartitionWall] = Field(default_factory=dict)
    floors: Dict[str, CFDExportFloor] = Field(default_factory=dict)
    walls: Dict[str, CFDExportWall] = Field(default_factory=dict)
    sensorPoints: List[SensorPoint] = Field(default_factory=list)
    meshConfig: CFDExportMeshConfig = Field(default_factory=CFDExportMeshConfig)
    simulationConfig: CFDExportSimulationConfig = Field(default_factory=CFDExportSimulationConfig)

    @model_validator(mode="after")
    def validate_unique_rack_server_exports(self) -> "CFDExport":
        """Rack server IDs and solid names must remain unique across the export."""
        server_ids: Counter[str] = Counter()
        server_patch_names: Counter[str] = Counter()

        for rack in self.racks.values():
            for server in rack.servers:
                if server.id:
                    server_ids[server.id] += 1
                for patch_name in (
                    server.inletSolidName,
                    server.outletSolidName,
                    server.topSolidName,
                    server.bottomSolidName,
                ):
                    if patch_name:
                        server_patch_names[_sanitize_patch_name(patch_name)] += 1

        errors: List[str] = []
        if any(count > 1 for count in server_ids.values()):
            errors.append(
                "duplicate rack server ids: "
                + _summarize_duplicates(server_ids)
            )
        if any(count > 1 for count in server_patch_names.values()):
            errors.append(
                "duplicate rack server patch names: "
                + _summarize_duplicates(server_patch_names)
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self


# ===========================================================================
# V2 MANIFEST SCHEMA (CfdrunV2) — strict, extra="forbid", schemaVersion == 2
# (folded in from the former cfdrun_v2_schema.py, 2026-06-15)
# ===========================================================================

# ---------------------------------------------------------------------------
# Base model: strict — no undeclared fields anywhere
# ---------------------------------------------------------------------------

class CfdrunV2BaseModel(BaseModel):
    """Base model for all CfdrunV2 models. Forbids extra fields."""
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# §2 — SourceProject
# ---------------------------------------------------------------------------

class SourceProject(CfdrunV2BaseModel):
    """Tracking information for the originating .cfdproj."""
    uid: Optional[str] = None
    schemaVersion: Optional[int] = None
    projectName: Optional[str] = None
    filePath: Optional[str] = None
    contentHash: Optional[str] = None
    exportedAt: Optional[str] = None


# ---------------------------------------------------------------------------
# §3.8 — SensorMeasurement
# ---------------------------------------------------------------------------

class SensorMeasurement(CfdrunV2BaseModel):
    """A single sensor reading (temperature and/or humidity)."""
    temperatureC: Optional[float] = None
    relativeHumidityPct: Optional[float] = None


class LabelsInput(CfdrunV2BaseModel):
    """inputs.labels — sensor measurements for calibration/validation."""
    sensorMeasurements: Dict[str, SensorMeasurement] = Field(default_factory=dict)


class LiquidInput(CfdrunV2BaseModel):
    """inputs.liquid — liquid cooling coupling flags."""
    enabled: bool = False
    inheritResults: bool = False


# ---------------------------------------------------------------------------
# §3 — Per-device input models (all fields Optional, extra="forbid")
# ---------------------------------------------------------------------------

class RdhxInput(CfdrunV2BaseModel):
    """Rack RDHX run-condition input."""
    enabled: Optional[bool] = None
    waterFlowRateLpm: Optional[float] = None
    waterInletTempC: Optional[float] = None
    fanSpeedRatio: Optional[float] = None


class RackInput(CfdrunV2BaseModel):
    """inputs.racks[rackId] — rack-level run conditions."""
    rdhx: Optional[RdhxInput] = None


class AcuInput(CfdrunV2BaseModel):
    """inputs.acus[acuId] — ACU run conditions."""
    acuSwitch: Optional[bool] = None
    supplyAirTemperatureC: Optional[float] = None
    supplyAirVolumeFlowRateM3s: Optional[float] = None
    coolingMode: Optional[str] = None          # fixedTemp | linearCapacity | tableCapacity
    setPointTemperatureC: Optional[float] = None
    coolantTemperatureC: Optional[float] = None
    surfaceOverrides: Optional[Dict[str, Any]] = None
    schedules: Optional[Dict[str, Any]] = None


class ChipInput(CfdrunV2BaseModel):
    """Per-chip run-condition input inside a server."""
    powerW: Optional[float] = None
    loadFraction: Optional[float] = None
    fixedTemperatureC: Optional[float] = None


class ColdPlateInput(CfdrunV2BaseModel):
    """Per-cold-plate run-condition input inside a server."""
    liquidFlowRateKgs: Optional[float] = None
    inletTemperatureC: Optional[float] = None
    valveOpening: Optional[float] = None


class ServerInput(CfdrunV2BaseModel):
    """inputs.servers[serverId] — server run conditions."""
    serverSwitch: Optional[bool] = None
    inputPowerW: Optional[float] = None
    inputPowerPercentage: Optional[float] = None
    airVolumeFlowRateM3s: Optional[float] = None
    temperatureRiseC: Optional[float] = None
    liquidPercentage: Optional[float] = None
    liquidMassFlowRateKgs: Optional[float] = None
    bcMode: Optional[str] = None              # powerAndFlow | tempRiseAndFlow | powerAndTempRise
    chips: Optional[Dict[str, ChipInput]] = None
    coldPlates: Optional[Dict[str, ColdPlateInput]] = None
    schedules: Optional[Dict[str, Any]] = None


class CduInput(CfdrunV2BaseModel):
    """inputs.cdus[cduId] — CDU run conditions."""
    coolingWaterSupplyTemperatureSpC: Optional[float] = None
    chilledWaterSupplyTemperatureC: Optional[float] = None
    chilledWaterMassFlowRateKgs: Optional[float] = None
    supplyPressureKpa: Optional[float] = None
    valveOpening: Optional[float] = None


class PartitionWallInput(CfdrunV2BaseModel):
    """inputs.partitionWalls[wallId] — partition wall run-time overrides."""
    holeOverrides: Optional[Dict[str, Any]] = None


class PipeSegmentInput(CfdrunV2BaseModel):
    """inputs.pipeSegments[pipeId] — pipe segment run-time overrides."""
    valveOpenings: Optional[Dict[str, float]] = None
    blindEnds: Optional[Dict[str, bool]] = None


class RunInputs(CfdrunV2BaseModel):
    """§3 — frozen run-condition snapshot (not device capacity)."""
    racks: Dict[str, RackInput] = Field(default_factory=dict)
    acus: Dict[str, AcuInput] = Field(default_factory=dict)
    servers: Dict[str, ServerInput] = Field(default_factory=dict)
    cdus: Dict[str, CduInput] = Field(default_factory=dict)
    partitionWalls: Dict[str, PartitionWallInput] = Field(default_factory=dict)
    pipeSegments: Dict[str, PipeSegmentInput] = Field(default_factory=dict)
    liquid: LiquidInput = Field(default_factory=LiquidInput)
    labels: LabelsInput = Field(default_factory=LabelsInput)


# ---------------------------------------------------------------------------
# §4 — GeometryExport
# ---------------------------------------------------------------------------

class StlFile(CfdrunV2BaseModel):
    """An exported STL file record."""
    path: Optional[str] = None
    objectPath: Optional[str] = None
    kind: Optional[str] = None
    contentHash: Optional[str] = None


class StlSolidBinding(CfdrunV2BaseModel):
    """Binding of a STL solid to a domain object and surface uid."""
    solidName: Optional[str] = None
    patchName: Optional[str] = None
    stlFileId: Optional[str] = None
    objectPath: Optional[str] = None
    surfaceUid: Optional[str] = None
    role: Optional[str] = None
    areaM2: Optional[float] = None
    perimeterM: Optional[float] = None


class SourceSurfaceBinding(CfdrunV2BaseModel):
    """Stable source ownership of one exported STL region before meshing.

    A zero-thickness source sheet creates master/slave mesh patches from one
    source; ``surfaceFace`` is an exterior face. No physical-wall pairing is
    inferred or accepted in this first provenance contract.
    """
    stlFileId: str
    solidName: str
    sourceObjectUid: str
    sourceFaceUid: str
    representation: Literal["zeroThicknessSheet", "surfaceFace"]


class SourceRegionBinding(CfdrunV2BaseModel):
    """Original emitted STL region/facet ancestry; no OO face semantics.

    A facet UID is sourceRegionUid + '/facet/' + its zero-based literal STL
    facet ordinal. It remains scoped by the exact region bytes, not an editable
    model/face identity. Legacy sourceSurfaces is a separate physical contract.
    """
    stlFileId: str
    solidName: str
    sourceEntityUid: str
    sourceRegionUid: str
    sourceFacetCount: int = Field(gt=0, strict=True)
    sourceRegionSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["modelBoundary", "cellZone", "doorSheet"]


class GeometryExport(CfdrunV2BaseModel):
    """§4 — exported STL file and solid inventory."""
    coordinateSystem: Optional[str] = None
    unit: Optional[str] = None
    stlFiles: Dict[str, StlFile] = Field(default_factory=dict)
    solids: Dict[str, StlSolidBinding] = Field(default_factory=dict)
    sourceSurfaces: Dict[str, SourceSurfaceBinding] = Field(default_factory=dict)
    sourceRegions: Dict[str, SourceRegionBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_surface_references(self) -> "GeometryExport":
        seen_faces: Dict[str, str] = {}
        for key, binding in self.sourceSurfaces.items():
            if not all((binding.stlFileId, binding.solidName,
                        binding.sourceObjectUid, binding.sourceFaceUid)):
                raise ValueError(f"sourceSurfaces[{key}] requires nonempty source UIDs and STL identity")
            if binding.stlFileId not in self.stlFiles:
                raise ValueError(f"sourceSurfaces[{key}].stlFileId {binding.stlFileId} is missing from stlFiles")
            if key != f"{binding.stlFileId}:{binding.solidName}":
                raise ValueError(f"sourceSurfaces[{key}] key must match stlFileId:solidName")
            previous = seen_faces.setdefault(binding.sourceFaceUid, key)
            if previous != key:
                raise ValueError(
                    f"sourceFaceUid {binding.sourceFaceUid!r} is duplicated by "
                    f"sourceSurfaces[{previous}] and sourceSurfaces[{key}]"
                )
        region_identities = {}
        for key, binding in self.sourceRegions.items():
            if (not all((binding.stlFileId, binding.solidName, binding.sourceEntityUid, binding.sourceRegionUid))
                    or binding.stlFileId not in self.stlFiles
                    or key != f"{binding.stlFileId}:{binding.solidName}"):
                raise ValueError(f"sourceRegions[{key}] has invalid entity/region/STL identity")
            identity = (binding.sourceEntityUid, binding.sourceRegionSha256, binding.sourceFacetCount)
            previous = region_identities.setdefault(binding.sourceRegionUid, identity)
            if previous != identity:
                raise ValueError("sourceRegionUid aliases different literal facet inventories")
        return self


# ---------------------------------------------------------------------------
# §5 — BoundaryBindings
# ---------------------------------------------------------------------------

class Perforation(CfdrunV2BaseModel):
    """Perforated-plate physical description (Idelchik §8.2 inputs)."""
    shape: Optional[Literal["circle", "rect", "diamond", "hexagon"]] = None
    holeDiameterMm: Optional[float] = None
    openAreaPct: Optional[float] = None


class BoundaryBindingParameters(CfdrunV2BaseModel):
    """
    AD8 — the COMPLETE camelCase vocabulary for boundaryBinding.parameters.
    ALL fields are Optional; extra="forbid" means no undeclared fields survive.
    Maps 1:1 to BCSpec snake_case fields in bridge_bc.py (reader materialises).
    """
    # Temperature/flow
    temperatureC: Optional[float] = None         # BCSpec.temperature (°C)
    flowRateM3s: Optional[float] = None          # BCSpec.flow_rate (m³/s)
    powerW: Optional[float] = None               # BCSpec.power (W)
    # Porous / baffle
    sigma: Optional[float] = None               # BCSpec.sigma (porosity coeff)
    perforation: Optional[Perforation] = None   # BCSpec.perforated (PerforatedSpec)
    # Heat
    heatFluxWm2: Optional[float] = None         # BCSpec.heat_flux (W/m²)
    physicalFlowAreaM2: Optional[float] = None  # BCSpec.physical_flow_area (m²)
    surfaceAreaM2: Optional[float] = None       # BCSpec.surface_area (m²)
    # ACU-specific
    coolingCapacityW: Optional[float] = None    # BCSpec.cooling_capacity (W = kW*1000)
    minSupplyTempC: Optional[float] = None      # BCSpec.min_supply_temp (°C)
    setPointTempC: Optional[float] = None       # BCSpec.set_point_temp (°C)
    coolantTempC: Optional[float] = None        # BCSpec.coolant_temp (°C)
    returnPatchName: Optional[str] = None       # BCSpec.return_patch_name
    coolingModelMode: Optional[str] = None      # BCSpec.cooling_model_mode
    ratedReturnTempC: Optional[float] = None    # BCSpec.rated_return_temp (°C)
    coolingPerformanceTable: Optional[List[Any]] = None  # BCSpec.cooling_performance_table
    # Wall-mounted unit outlet: world direction of the jet (louvre normal
    # tilted by supplyAngleDeg) and the patch area, so U is a fixed vector
    directionWorld: Optional[List[float]] = None  # BCSpec.direction (unit vector, world)
    patchAreaM2: Optional[float] = None         # BCSpec.patch_area (m²)
    supplyAngleDeg: Optional[float] = None      # recorded for the report; direction already applies it
    # Time-varying
    timeVarying: Optional[Any] = None           # BCSpec.timeVarying
    # cfdrun v3 (transient design §4): corner rows {param: [[t, v], ...]} —
    # linear between rows, a step is two rows at one t. Only in schemaVersion 3.
    schedules: Optional[Dict[str, List[List[Any]]]] = None


# bcType Literal — must stay in sync with fields.py, bridge_bc.py, cfdrun_schema.js
BcTypeLiteral = Literal[
    "wall",
    "acu_supply",
    "acu_return",
    "fan_inlet",
    "fan_outlet",
    "nonfan_fixed_temp",
    "nonfan_heat_flux",
    "fixedTemperature",
    "heat_source_flux",
    "perforated_baffle",
    "wall_baffle",
    "through_zone",
]


class BoundaryBinding(CfdrunV2BaseModel):
    """§5 — an OpenFOAM patch boundary binding."""
    patchName: Optional[str] = None
    solidName: Optional[str] = None
    objectPath: Optional[str] = None
    surfaceUid: Optional[str] = None
    role: Optional[str] = None
    bcType: BcTypeLiteral
    parameters: BoundaryBindingParameters = Field(default_factory=BoundaryBindingParameters)


# ---------------------------------------------------------------------------
# §6 — SourceTerms
# ---------------------------------------------------------------------------

class SourceTermParameters(CfdrunV2BaseModel):
    """
    Parameters for a source term entry.
    Includes heat-source fields AND the porousZone Darcy-Forchheimer fields
    (from T2d-2 note in the plan: sigma + d/f/origin/e1/e2).
    ALL Optional; extra="forbid".
    """
    # Heat source / sink / exchange parameters
    powerW: Optional[float] = None
    temperatureC: Optional[float] = None
    flowRate: Optional[float] = None
    schedule: Optional[Any] = None
    # PorousZone Darcy-Forchheimer parameters (from partition-hole source term)
    sigma: Optional[float] = None
    d: Optional[List[float]] = None          # [dCoeff, 0, 0]
    f: Optional[List[float]] = None          # [fCoeff, 0, 0]
    origin: Optional[List[float]] = None     # [x, y, z]
    e1: Optional[List[float]] = None         # [x, y, z]
    e2: Optional[List[float]] = None         # [x, y, z]
    # Optional extra fields from heat sources (surfaceAreaM2 for flux calc)
    surfaceAreaM2: Optional[float] = None
    timeVarying: Optional[Any] = None


SourceTermTypeLiteral = Literal[
    "heatSource",
    "heatSink",
    "liquidHeatExchange",
    "rdhxHeatExchange",
    "porousZone",
]


class SourceTerm(CfdrunV2BaseModel):
    """§6 — a heat source, sink, or porous zone source term."""
    type: SourceTermTypeLiteral
    objectPath: Optional[str] = None
    region: Optional[str] = None
    parameters: SourceTermParameters = Field(default_factory=SourceTermParameters)


# ---------------------------------------------------------------------------
# §7 — PorousBindings
# ---------------------------------------------------------------------------

class PorousBindingParameters(CfdrunV2BaseModel):
    """Parameters for a porous/leakage binding."""
    perforation: Optional[Perforation] = None
    sigma: Optional[float] = None
    seedConfig: Optional[Dict[str, Any]] = None


PorousBindingTypeLiteral = Literal[
    "perforatedBaffle",
    "wallBaffle",
    "cyclicOpening",
    "porousZone",
    "leakageZone",
]


class PorousBinding(CfdrunV2BaseModel):
    """§7 — a porous baffle, floor opening, or leakage zone binding."""
    patchName: Optional[str] = None
    objectPath: Optional[str] = None
    surfaceUid: Optional[str] = None
    type: PorousBindingTypeLiteral
    parameters: PorousBindingParameters = Field(default_factory=PorousBindingParameters)


# ---------------------------------------------------------------------------
# §8 — MeshConfig
# ---------------------------------------------------------------------------

class Vertex(CfdrunV2BaseModel):
    """A 3D point / vertex used for locationInMesh etc."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class ResidualControlV2(CfdrunV2BaseModel):
    """Convergence residual thresholds."""
    p_rgh: Optional[Union[float, str]] = None
    U: Optional[Union[float, str]] = None
    T: Optional[Union[float, str]] = None
    k_epsilon: Optional[Union[float, str]] = None


class MeshConfig(CfdrunV2BaseModel):
    """
    §8 — mesh task configuration (all fields declared, no undeclared riders).
    The JS buildManifest deep-copies state.runDefaults.meshConfig verbatim,
    so every key that ever appears in any case must be declared here.
    Confirmed by inspecting all 5 baseline fixtures (2026-06-14).
    """
    # --- Core mesh params (schema §8 named fields) ---
    baseSize: Optional[float] = None                   # background base size (m)
    meshTimeLimitSeconds: Optional[float] = None       # whole mesh pipeline wall-clock budget (s)
    detectBaseSize: Optional[float] = None             # auto-detect reference scale
    refinementLevel: Optional[List[int]] = None        # global surface level [min, max]
    rackRegionLevel: Optional[int] = None              # per-rack searchableBox level
    meshMode: Optional[Literal["bodyFitted", "cartesianLocalized", "structured"]] = None  # 3-way mesh mode
    meshEngine: Optional[Literal["snappy", "dcCart", "meshgen"]] = None  # mesh executable (orthogonal axis)
    weldTolerance: Optional[float] = None              # dcCart near-coincidence weld (m); None = runner default 1.2e-3, 0 = off
    firstLayerMinFraction: Optional[float] = None      # dcCart first-layer floor, fraction of the level spacing; None = runner default (off)
    firstLayerMax: Optional[float] = None              # dcCart first-layer floor cap (m); None = runner default 0.005
    firstLayerSealGap: Optional[float] = None          # dcCart: widest solid-solid gap the floor may seal (m); None/-1 = weldTolerance
    rackCellMinSize: Optional[float] = None            # dcCart: rack-region cell never auto-raised below this (m); None = uncapped
    cartesianMode: Optional[bool] = None               # legacy: snap off → pure octree Cartesian
    structuredGrid: Optional[bool] = None              # legacy: grid-aligned ("structured") cartesian sub-mode
    firstCellSize: Optional[float] = None              # target near-wall cell size (m) → wall/floor level
    parallelMesh: Optional[bool] = None
    meshProcess: Optional[int] = None
    locationInMesh: Optional[Union[Vertex, Dict[str, float]]] = None
    extraLocationsInMesh: Optional[List[Any]] = None

    # --- Fields emitted by JS buildManifest (from all 5 fixtures) ---
    # These were previously carried as undeclared riders in cfdrun_schema.py
    meshBaseSize: Optional[float] = None               # alias used in V4 state
    meshDetectBaseSize: Optional[float] = None         # alias used in V4 state
    maxGlobalCells: Optional[int] = None
    maxLocalCells: Optional[int] = None
    blockMeshBounds: Optional[Any] = None              # null or bounds object
    blockMeshEditActive: Optional[bool] = None
    blockMeshMoveMode: Optional[bool] = None
    locInMeshVisible: Optional[bool] = None
    refinementZones: Optional[List[Any]] = None

    # --- ACU, box, baffle levels (legacy from cfdrun_schema.py rider era) ---
    acuRefinementLevel: Optional[List[int]] = None
    acuFeatureLevel: Optional[int] = None
    boxRefinementLevel: Optional[List[int]] = None
    boxFeatureLevel: Optional[int] = None
    baffleFeatureLevel: Optional[int] = None
    wallBafflesRefinementLevel: Optional[List[int]] = None
    floorRefinementLevel: Optional[List[int]] = None
    floorFeatureLevel: Optional[int] = None
    defaultFeatureLevel: Optional[int] = None
    partitionWallRefinementLevel: Optional[List[int]] = None
    partitionWallFeatureLevel: Optional[int] = None


# ---------------------------------------------------------------------------
# §9 — SimulationConfig
# ---------------------------------------------------------------------------

class SimulationConfig(CfdrunV2BaseModel):
    """
    §9 — solver task configuration (all fields declared, no undeclared riders).
    The JS buildManifest deep-copies state.runDefaults.simulationConfig verbatim.
    All fields confirmed by inspecting all 5 baseline fixtures (2026-06-14).
    Many legacy refinement-level fields migrated into simulationConfig in the old
    state schema are included here so the Pydantic round-trip stays clean.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # --- Core solver params (schema §9 named fields) ---
    solverType: Optional[str] = None            # "steady" | "transient"
    solverName: Optional[str] = None            # e.g. "buoyantBoussinesqSimpleFoam"
    pressureLinearSolver: Optional[PressureLinearSolverConfig] = None
    parallelSolve: Optional[bool] = None
    solveProcess: Optional[int] = None
    endTime: Optional[int] = None               # steady iterations or transient steps
    endTimeSeconds: Optional[float] = None      # transient physical end time (s)
    deltaT: Optional[float] = None
    writeInterval: Optional[int] = None
    roomTemperatureC: Optional[float] = None    # initial room temperature (°C)
    gravity: Optional[Dict[str, float]] = None  # {x, y, z}
    residualControl: Optional[ResidualControlV2] = None
    # Per-time-step outer-loop tolerances of a transient (PIMPLE) run (E8c); a
    # separate set from the steady stop thresholds above.
    transientResidualControl: Optional[ResidualControlV2] = None
    schemes: Optional[Dict[str, str]] = None
    relaxationFactors: Optional[Dict[str, float]] = None
    monitoring: Optional[Dict[str, Any]] = None
    startFrom: Optional[str] = None            # "latestTime" | "startTime"
    solverPreset: Optional[str] = None         # "fast" | "balanced" | ...
    writePrecision: Optional[str] = None       # "single" | "double"

    # --- Additional fields emitted by JS buildManifest ---
    doublePrecisionWrite: Optional[bool] = None
    maxDeltaT: Optional[float] = None
    maxCo: Optional[float] = None
    writeIntervalSeconds: Optional[float] = None
    adjustTimeStep: Optional[bool] = None
    nOuterCorrectors: Optional[int] = None
    nCorrectors: Optional[int] = None
    ddtScheme: Optional[str] = None
    crankNicolsonCoeff: Optional[float] = None
    gravityX: Optional[float] = None           # gravity x component (flat)
    gravityY: Optional[float] = None           # gravity y component (flat)
    gravityZ: Optional[float] = None           # gravity z component (flat)
    nonOrthoCorrection: Optional[bool] = None
    nNonOrthogonalCorrectors: Optional[int] = None

    # --- Legacy per-unit refinement dicts (carried in simulationConfig in V4 state) ---
    rackRefinementLevel: Optional[List[int]] = None
    serverFaceRefinementLevel: Optional[List[int]] = None
    serverFacePerUnitLevel: Optional[Dict[str, Any]] = None
    serverFaceFeaturePerUnitLevel: Optional[Dict[str, Any]] = None
    serverFaceFeatureLevel: Optional[int] = None
    rackDoorRefinementLevel: Optional[List[int]] = None
    rackDoorFeatureLevel: Optional[int] = None
    # rackDoorPerMinUnitLevel is a per-unit dict keyed by slot string,
    # e.g. {"2": [3,4], "3": [3,4]} — NOT a scalar int.
    rackDoorPerMinUnitLevel: Optional[Dict[str, Any]] = None
    # Per-unit door FEATURE level dict, e.g. {"1": 4, "5": 2} (surfaced by the
    # P4 all-case sweep on Base_coldAisleIso; the 5 baseline fixtures had no
    # rack doors with per-unit feature levels).
    rackDoorFeaturePerMinUnitLevel: Optional[Dict[str, Any]] = None
    leakageRefinementLevel: Optional[List[int]] = None
    leakageFeatureLevel: Optional[int] = None
    blankingPlateRefinementLevel: Optional[List[int]] = None
    blankingPlatePerUnitLevel: Optional[Dict[str, Any]] = None
    blankingPlateFeatureLevel: Optional[int] = None
    blankingPlateFeaturePerUnitLevel: Optional[Dict[str, Any]] = None
    rackPartitionRefinementLevel: Optional[List[int]] = None
    rackPartitionPerUnitLevel: Optional[Dict[str, Any]] = None
    rackPartitionFeatureLevel: Optional[int] = None
    rackPartitionFeaturePerUnitLevel: Optional[Dict[str, Any]] = None
    rackThroughZoneFeatureLevel: Optional[int] = None
    rackThroughZoneFeaturePerUnitLevel: Optional[Dict[str, Any]] = None
    rackThroughZonePerUnitLevel: Optional[Dict[str, Any]] = None
    rackLeakageFeatureLevel: Optional[int] = None
    rackLeakageFeaturePerUnitLevel: Optional[Dict[str, Any]] = None
    rackLeakagePerUnitLevel: Optional[Dict[str, Any]] = None
    rackFeatureLevel: Optional[int] = None
    partitionWallRefinementLevel: Optional[List[int]] = None
    partitionWallFeatureLevel: Optional[int] = None
    extraLocations: Optional[List[Any]] = None
    # _nextExtraLocId starts with underscore; Pydantic forbids leading-underscore
    # field names, so we use an alias for the JSON key. populate_by_name=True
    # allows the Python attribute name to work too.
    nextExtraLocId: Optional[int] = Field(None, alias="_nextExtraLocId")

    # meshConfig nested inside simulationConfig (legacy V4 state artifact)
    meshConfig: Optional[Dict[str, Any]] = None

    # Vestigial keys that the v3->v4 migrator routes into simulationConfig because
    # they are not in MESH_KEYS/SIM_KEYS (surfaced by the P4 all-case sweep):
    #   baseSize  — a mesh base size leftover (real home is meshConfig.baseSize)
    #   polygon   — a room-polygon leftover (real home is geometry.plane)
    # The runner ignores both; declared here only so extra="forbid" accepts the
    # real saved projects that still carry them.
    baseSize: Optional[float] = None
    polygon: Optional[Any] = None


# ---------------------------------------------------------------------------
# §10 — PostprocessConfig
# ---------------------------------------------------------------------------

class PostprocessConfig(CfdrunV2BaseModel):
    """§10 — post-processing probes, monitors, output fields."""
    probes: Dict[str, Any] = Field(default_factory=dict)
    surfaceMonitors: Dict[str, Any] = Field(default_factory=dict)
    outputFields: List[str] = Field(default_factory=lambda: ["T", "U", "p_rgh"])
    labels: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# §11 — RunArtifacts
# ---------------------------------------------------------------------------

class RunArtifacts(CfdrunV2BaseModel):
    """§11 — paths for files produced or consumed by this run."""
    caseDir: Optional[str] = None
    modelDir: Optional[str] = None
    triSurfaceDir: Optional[str] = None
    openfoamCaseDir: Optional[str] = None
    liquidResultsPath: Optional[str] = None
    logs: Dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# §1 — Root CfdrunV2
# ---------------------------------------------------------------------------

# Binding parameters a cfdrun v3 schedule may drive.
SCHEDULABLE_PARAMETERS = frozenset({
    "temperatureC", "flowRateM3s", "directionWorld", "supplyAngleDeg", "powerW", "heatFluxWm2",
})


class TransientStartUniform(CfdrunV2BaseModel):
    airT_C: Optional[float] = None
    waterT_C: Optional[float] = None
    solidT_C: Optional[float] = None


class TransientStart(CfdrunV2BaseModel):
    kind: Literal["steadyConverged", "uniform"]
    uniform: Optional[TransientStartUniform] = None


class TransientBlock(CfdrunV2BaseModel):
    """cfdrun v3: the project timeline this transient run follows (cfdproj v5)."""
    durationS: float = Field(gt=0)
    start: TransientStart


def require_air_transient_duration(solver_type, end_time_seconds, duration_s):
    """cfdrun v4 has one solver config and follows the project timeline."""
    if solver_type != "transient":
        raise ValueError("a transient timeline requires a transient solver; re-export from the editor")
    if end_time_seconds is None or end_time_seconds != duration_s:
        raise ValueError("simulationConfig.endTimeSeconds must equal transient.durationS; re-export from the editor")


class CfdrunV2(CfdrunV2BaseModel):
    """
    Root model for the .cfdrun V2 manifest.

    Validates that:
      - schemaVersion is exactly 2 (A12 hard-cutover; re-export required otherwise)
      - no undeclared fields exist anywhere (extra="forbid" on all nested models)
      - patchNames are unique across boundaryBindings
    """
    schemaVersion: int
    runId: str = ""
    name: str = ""
    sourceProject: Optional[SourceProject] = None
    inputs: RunInputs = Field(default_factory=RunInputs)
    geometryExport: GeometryExport = Field(default_factory=GeometryExport)
    boundaryBindings: Dict[str, BoundaryBinding] = Field(default_factory=dict)
    sourceTerms: Dict[str, SourceTerm] = Field(default_factory=dict)
    porousBindings: Dict[str, PorousBinding] = Field(default_factory=dict)
    meshConfig: MeshConfig = Field(default_factory=MeshConfig)
    simulationConfig: SimulationConfig = Field(default_factory=SimulationConfig)
    postprocessConfig: PostprocessConfig = Field(default_factory=PostprocessConfig)
    artifacts: RunArtifacts = Field(default_factory=RunArtifacts)

    # ------------------------------------------------------------------
    # TRANSITIONAL mesh-geometry block (P3 T6 — KNOWN spec §12 deviation).
    #
    # Spec §12 deliberately dropped the v1 geometry domain model (geometry now
    # lives in the STL files). BUT the runner's MESH pipeline (watertight
    # enclosure, createBaffles, blockMesh bounds, refinement, partition-hole
    # fvOptions) still reads the v1 geometry blob (`self._data` = racks/floors/
    # baffles/room/partitionWalls/...) across ~60 sites. Migrating all of that
    # to pure-V2 is a large effort DEFERRED to a later plan.
    #
    # Until then, the browser attaches the v1 blob (exactly what
    # `buildCfdrunJson()` produces) here, and STLGenRunner reads `self._data =
    # CFDExport.model_validate(meshGeometry)` for MESH (mesh code unchanged),
    # while reading the V2 boundaryBindings (via cfdrun_reader.resolve_run) for
    # the BC registry. This is a pass-through `Dict[str, Any]` — the runner
    # validates it separately via the existing `CFDExport` schema. It is to be
    # removed when the mesh pipeline migrates to pure-V2 geometry.
    # ------------------------------------------------------------------
    meshGeometry: Optional[Dict[str, Any]] = None
    # cfdrun v3 only: the timeline (start + duration) of a transient run whose
    # boundaryBindings carry schedules.
    transient: Optional[TransientBlock] = None

    # ------------------------------------------------------------------
    # A12 — hard cutover: only schemaVersion=2 is accepted by the backend
    # (and 3, which is v2 plus the transient block and binding schedules).
    # The message MUST contain the substring "re-export" so callers/logs
    # know to re-export from the editor rather than migrate the manifest.
    # ------------------------------------------------------------------

    @field_validator("schemaVersion")
    @classmethod
    def validate_schema_version(cls, v: int) -> int:
        if v not in (2, 3, 4):
            raise ValueError(
                f"unsupported .cfdrun schemaVersion {v}; "
                "re-export from the editor (V2 required)"
            )
        return v

    @model_validator(mode="after")
    def validate_transient_contract(self) -> "CfdrunV2":
        """v3 ⇔ a transient block; schedules only in v3, only on schedulable
        parameters, in time order, at most two rows (a step) per time, inside
        the duration."""
        scheduled = {key: b for key, b in self.boundaryBindings.items()
                     if b.parameters is not None and b.parameters.schedules}
        if self.schemaVersion == 2:
            if self.transient is not None or scheduled:
                raise ValueError("binding schedules and a transient block require .cfdrun schemaVersion 3")
            return self
        if self.schemaVersion == 4:
            # One simulationConfig (the root, E8a): it must carry the room temperature.
            if self.simulationConfig is None or self.simulationConfig.roomTemperatureC is None:
                raise ValueError("a schemaVersion 4 .cfdrun needs simulationConfig.roomTemperatureC")
            if self.transient is None:
                if scheduled:
                    raise ValueError("binding schedules need the transient block")
                return self
        elif self.transient is None:
            raise ValueError("a schemaVersion 3 .cfdrun requires its transient block")
        end = self.transient.durationS
        if self.schemaVersion == 4:
            require_air_transient_duration(self.simulationConfig.solverType, self.simulationConfig.endTimeSeconds, end)
        for key, binding in scheduled.items():
            where = binding.patchName or key
            unknown = set(binding.parameters.schedules) - SCHEDULABLE_PARAMETERS
            if unknown:
                raise ValueError(f"{where} cannot schedule {sorted(unknown)}")
            for name, rows in binding.parameters.schedules.items():
                if not rows:
                    raise ValueError(f"{where}.{name} schedule is empty")
                times = []
                for row in rows:
                    if len(row) != 2 or isinstance(row[0], bool) or not isinstance(row[0], (int, float)):
                        raise ValueError(f"{where}.{name} rows must be [t, value]")
                    times.append(float(row[0]))
                if any(b < a for a, b in zip(times, times[1:])):
                    raise ValueError(f"{where}.{name} schedule rows must be in time order")
                if any(times[i] == times[i + 1] == times[i + 2] for i in range(len(times) - 2)):
                    raise ValueError(f"{where}.{name} has more than two rows at one time")
                if times[0] < 0 or times[-1] > end + 1e-9:
                    raise ValueError(f"{where}.{name} schedule runs beyond the transient duration {end} s")
        return self

    # ------------------------------------------------------------------
    # Unique patch-name validator (ported from CFDExport.validate_unique_rack_server_exports).
    # Collects every patchName in boundaryBindings and raises on duplicates.
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def validate_unique_patch_names(self) -> "CfdrunV2":
        """All boundaryBindings patchNames must be unique across the manifest."""
        patch_names: Counter[str] = Counter()

        for binding in self.boundaryBindings.values():
            if binding.patchName:
                patch_names[_sanitize_patch_name(binding.patchName)] += 1

        duplicates = [name for name, count in patch_names.items() if count > 1]
        if duplicates:
            summary = _summarize_duplicates(patch_names)
            raise ValueError(f"duplicate boundaryBinding patchNames: {summary}")

        return self
