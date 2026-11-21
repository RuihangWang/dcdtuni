"""
TDD tests for the strict Pydantic CfdrunV2 (the V2 layer of cfdrun_schema.py; P3 T4).

Run with:
  conda run -n dcdtuni python -m pytest src/backend/tests/test_cfdrun_v2_schema.py -q

Test groups:
  (a) Valid: all 5 JS-generated fixture manifests parse cleanly.
  (b) Rich fixture: a hand-built manifest covering the full AD8 param set.
  (c) extra="forbid": unknown keys raise ValidationError at top level and inside parameters.
  (d) A12: schemaVersion=1 raises ValidationError containing "re-export".
  (e) Rider completeness: every field the runner reads is declared on MeshConfig/SimulationConfig.
  (f) Unique patch names: duplicate patchNames across boundaryBindings raise ValidationError.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError

# ---- the module under test (will be missing until TDD GREEN step) ----
from backend.aircooling.runner.cfdrun_schema import (
    CfdrunV2,
    BoundaryBinding,
    BoundaryBindingParameters,
    MeshConfig,
    SimulationConfig,
    SourceTerm,
    SourceTermParameters,
    PorousBinding,
    PorousBindingParameters,
    Perforation,
)

# ---- fixture helpers -------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cfdrun_v2"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file as a raw dict."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


# ============================================================
# (a) Valid — all 5 JS-generated fixtures validate cleanly
# ============================================================

@pytest.mark.parametrize("case_name", [
    "Base_leakage",
    "Shinaide",
    "CMI_China",
    "STDCT",
    "Liquid",
])
def test_fixture_validates_ok(case_name: str):
    """CfdrunV2.model_validate succeeds for all 5 JS buildManifest fixtures."""
    data = load_fixture(case_name)
    obj = CfdrunV2.model_validate(data)
    # Basic typed-field smoke checks
    assert obj.schemaVersion == 2
    assert isinstance(obj.runId, str)
    assert isinstance(obj.name, str)
    assert isinstance(obj.boundaryBindings, dict)
    assert isinstance(obj.sourceTerms, dict)
    assert isinstance(obj.porousBindings, dict)
    assert obj.inputs is not None
    assert obj.geometryExport is not None
    assert obj.meshConfig is not None
    assert obj.simulationConfig is not None
    assert obj.postprocessConfig is not None
    assert obj.artifacts is not None


# ============================================================
# (b) Rich fixture — full AD8 param set + porousZone sourceTerm
# ============================================================

def _minimal_manifest(**overrides) -> dict:
    """Build a minimal valid V2 manifest dict for testing."""
    base: Dict[str, Any] = {
        "schemaVersion": 2,
        "runId": "run_test",
        "name": "test",
        "sourceProject": {
            "uid": "proj-uid-1",
            "schemaVersion": 4,
            "projectName": "TestProject",
            "filePath": None,
            "contentHash": "abc123def456abc123def456abc123def456abc123def456abc123def456abcd",
            "exportedAt": "2026-01-01T00:00:00Z",
        },
        "inputs": {
            "racks": {},
            "acus": {},
            "servers": {},
            "cdus": {},
            "partitionWalls": {},
            "pipeSegments": {},
            "liquid": {"enabled": False, "inheritResults": False},
            "labels": {"sensorMeasurements": {}},
        },
        "geometryExport": {
            "coordinateSystem": "Y-up",
            "unit": "m",
            "stlFiles": {},
            "solids": {},
        },
        "boundaryBindings": {},
        "sourceTerms": {},
        "porousBindings": {},
        "meshConfig": {},
        "simulationConfig": {},
        "postprocessConfig": {
            "probes": {},
            "surfaceMonitors": {},
            "outputFields": ["T", "U", "p_rgh"],
            "labels": {},
        },
        "artifacts": {
            "caseDir": "",
            "modelDir": "",
            "triSurfaceDir": "",
            "openfoamCaseDir": "",
            "liquidResultsPath": None,
            "logs": {},
        },
    }
    base.update(overrides)
    return base


def test_rich_fixture_full_ad8_params():
    """A manifest with the COMPLETE AD8 param set + porousZone sourceTerm validates."""
    data = _minimal_manifest(
        boundaryBindings={
            # acu_supply with FULL AD8 params
            "bb_acu_supply_1": {
                "patchName": "acu_supply_1",
                "solidName": "ACU_supply_1",
                "objectPath": "constructions.acus.acu1",
                "surfaceUid": "face1_patch1",
                "role": "acu_supply",
                "bcType": "acu_supply",
                "parameters": {
                    "temperatureC": 15.0,
                    "flowRateM3s": 4.0,
                    "coolingCapacityW": 50000.0,
                    "minSupplyTempC": 12.3,
                    "setPointTempC": 22.0,
                    "coolantTempC": 6.0,
                    "returnPatchName": "acu_return_1",
                    "coolingModelMode": "linearCapacity",
                    "ratedReturnTempC": 35.0,
                    "coolingPerformanceTable": [{"returnTemp": 30, "capacity": 40000}],
                    "timeVarying": {},
                },
            },
            # fan_outlet with physicalFlowAreaM2 + powerW
            "bb_fan_outlet_srv1": {
                "patchName": "fan_outlet_srv1",
                "solidName": "Server_outlet_srv1",
                "objectPath": "constructions.racks.R1.servers.S1",
                "surfaceUid": "outlet_face_patch",
                "role": "fan_outlet",
                "bcType": "fan_outlet",
                "parameters": {
                    "flowRateM3s": 0.5,
                    "powerW": 1600.0,
                    "temperatureC": 36.4,
                    "physicalFlowAreaM2": 0.04,
                    "timeVarying": {},
                },
            },
        },
        porousBindings={
            # perforated baffle with perforation block + sigma
            "pb_floor_opening_master": {
                "patchName": "floor_opening_master",
                "objectPath": "constructions.floors.F1",
                "surfaceUid": "floor_opening_uid",
                "type": "perforatedBaffle",
                "parameters": {
                    "sigma": 0.64,
                    "perforation": {
                        "shape": "circle",
                        "holeDiameterMm": 3.0,
                        "openAreaPct": 64.0,
                    },
                },
            },
        },
        sourceTerms={
            # porousZone sourceTerm with sigma + d + f + origin + e1 + e2
            "st_partition_hole_1": {
                "type": "porousZone",
                "objectPath": "constructions.partitionWalls.PW1",
                "region": "partition_hole_zone",
                "parameters": {
                    "sigma": 0.8,
                    "d": [1234.56, 0.0, 0.0],
                    "f": [56.78, 0.0, 0.0],
                    "origin": [1.0, 1.0, 2.0],
                    "e1": [1.0, 0.0, 0.0],
                    "e2": [0.0, 0.0, 1.0],
                },
            },
        },
    )
    obj = CfdrunV2.model_validate(data)
    assert obj.schemaVersion == 2
    # Verify AD8 acu_supply params round-trip correctly
    bb = obj.boundaryBindings["bb_acu_supply_1"]
    assert bb.bcType == "acu_supply"
    p = bb.parameters
    assert p.temperatureC == 15.0
    assert p.flowRateM3s == 4.0
    assert p.coolingCapacityW == 50000.0
    assert p.minSupplyTempC == 12.3
    assert p.setPointTempC == 22.0
    assert p.coolantTempC == 6.0
    assert p.returnPatchName == "acu_return_1"
    assert p.coolingModelMode == "linearCapacity"
    assert p.ratedReturnTempC == 35.0
    assert isinstance(p.coolingPerformanceTable, list)
    assert p.timeVarying == {}
    # fan_outlet
    bb2 = obj.boundaryBindings["bb_fan_outlet_srv1"]
    assert bb2.parameters.physicalFlowAreaM2 == 0.04
    assert bb2.parameters.powerW == 1600.0
    # porousBinding perforation
    pb = obj.porousBindings["pb_floor_opening_master"]
    assert pb.parameters.sigma == 0.64
    assert pb.parameters.perforation is not None
    assert pb.parameters.perforation.openAreaPct == 64.0
    # porousZone sourceTerm
    st = obj.sourceTerms["st_partition_hole_1"]
    assert st.type == "porousZone"
    assert st.parameters.d == [1234.56, 0.0, 0.0]
    assert st.parameters.f == [56.78, 0.0, 0.0]
    assert st.parameters.origin == [1.0, 1.0, 2.0]
    assert st.parameters.e1 == [1.0, 0.0, 0.0]
    assert st.parameters.e2 == [0.0, 0.0, 1.0]


# ============================================================
# (c) extra="forbid" — unknown keys raise ValidationError
# ============================================================

def test_extra_forbidden_at_top_level():
    """Unknown top-level key raises ValidationError (extra='forbid')."""
    data = _minimal_manifest(bogusField=42)
    with pytest.raises(ValidationError) as exc_info:
        CfdrunV2.model_validate(data)
    # Pydantic v2 extra=forbid error type is "extra_forbidden"
    errors = exc_info.value.errors()
    assert any(e.get("type") == "extra_forbidden" for e in errors)


def test_extra_forbidden_in_boundary_parameters():
    """Unknown key inside BoundaryBindingParameters raises ValidationError."""
    data = _minimal_manifest(
        boundaryBindings={
            "bb_wall_1": {
                "patchName": "wall_1",
                "solidName": "wall_solid_1",
                "objectPath": "room.walls.W1",
                "surfaceUid": "uid_w1",
                "role": "wall",
                "bcType": "wall",
                "parameters": {
                    "unknownParamKey": 999,
                },
            },
        }
    )
    with pytest.raises(ValidationError) as exc_info:
        CfdrunV2.model_validate(data)
    errors = exc_info.value.errors()
    assert any(e.get("type") == "extra_forbidden" for e in errors)


def test_extra_forbidden_in_source_term_parameters():
    """Unknown key inside SourceTermParameters raises ValidationError."""
    data = _minimal_manifest(
        sourceTerms={
            "st_heat_1": {
                "type": "heatSource",
                "objectPath": None,
                "region": "zone1",
                "parameters": {
                    "powerW": 1000.0,
                    "bogusSourceKey": "nope",
                },
            },
        }
    )
    with pytest.raises(ValidationError) as exc_info:
        CfdrunV2.model_validate(data)
    errors = exc_info.value.errors()
    assert any(e.get("type") == "extra_forbidden" for e in errors)


# ============================================================
# (d) A12 — schemaVersion not 2, 3 or 4 raises with "re-export" in message
# ============================================================

def test_schema_version_1_raises_reexport():
    """A manifest with schemaVersion=1 raises ValidationError containing 're-export'."""
    data = _minimal_manifest(schemaVersion=1)
    with pytest.raises(ValidationError) as exc_info:
        CfdrunV2.model_validate(data)
    assert "re-export" in str(exc_info.value).lower()


def test_schema_version_5_raises_reexport():
    """A manifest from a newer editor (schemaVersion=5) raises with 're-export'.

    3 is the transient format (cfdrun v3, test_cfdrun_v3_schedules.py); 4 is one
    simulationConfig (test_cfdrun_v4_single_config.py).
    """
    data = _minimal_manifest(schemaVersion=5)
    with pytest.raises(ValidationError) as exc_info:
        CfdrunV2.model_validate(data)
    assert "re-export" in str(exc_info.value).lower()


# ============================================================
# (e) Rider completeness — every runner-read field is declared
# ============================================================

# Fields the runner reads from meshConfig (from stlgen_runner.py grep analysis)
MESH_CONFIG_RIDER_FIELDS = [
    "blockMeshBounds",
    "refinementZones",
    "acuRefinementLevel",
    "boxRefinementLevel",
    "baffleFeatureLevel",
    "maxGlobalCells",
    "parallelMesh",
    "meshProcess",
    "locationInMesh",
    # Additional fields emitted by JS buildManifest (confirmed from all 5 fixtures)
    "meshBaseSize",
    "meshDetectBaseSize",
    "rackRegionLevel",
    "refinementLevel",
    "floorRefinementLevel",
    "floorFeatureLevel",
    "acuFeatureLevel",
    "boxFeatureLevel",
    "wallBafflesRefinementLevel",
    "defaultFeatureLevel",
    "locInMeshVisible",
    "blockMeshEditActive",
    "blockMeshMoveMode",
]

# Fields the runner reads from simulationConfig
SIM_CONFIG_RIDER_FIELDS = [
    "startFrom",
    "monitoring",
    "solverPreset",
    "writePrecision",
    "solverType",
    "solverName",
    "endTime",
    "roomTemperatureC",
    # Additional fields emitted by JS buildManifest (confirmed from all 5 fixtures)
    "writeInterval",
    "doublePrecisionWrite",
    "endTimeSeconds",
    "deltaT",
    "maxDeltaT",
    "maxCo",
    "writeIntervalSeconds",
    "adjustTimeStep",
    "nOuterCorrectors",
    "nCorrectors",
    "ddtScheme",
    "crankNicolsonCoeff",
    "solverType",
    "gravityX",
    "gravityY",
    "gravityZ",
    "parallelSolve",
    "solveProcess",
    "residualControl",
    "nonOrthoCorrection",
    "schemes",
    "relaxationFactors",
    "nNonOrthogonalCorrectors",
    # Legacy/extra fields emitted by JS (must not cause extra_forbidden)
    "rackRefinementLevel",
    "serverFaceRefinementLevel",
    "rackDoorRefinementLevel",
    "rackDoorFeatureLevel",
    "leakageRefinementLevel",
    "serverFacePerUnitLevel",
    "serverFaceFeaturePerUnitLevel",
    "blankingPlateRefinementLevel",
    "blankingPlatePerUnitLevel",
    "blankingPlateFeatureLevel",
    "blankingPlateFeaturePerUnitLevel",
    "rackPartitionRefinementLevel",
    "rackPartitionPerUnitLevel",
    "rackPartitionFeatureLevel",
    "rackPartitionFeaturePerUnitLevel",
    "rackThroughZoneFeatureLevel",
    "rackThroughZoneFeaturePerUnitLevel",
    "extraLocations",
    "_nextExtraLocId",
    "rackLeakageFeatureLevel",
    "rackLeakageFeaturePerUnitLevel",
    "rackLeakagePerUnitLevel",
    "rackThroughZonePerUnitLevel",
    "partitionWallRefinementLevel",
    "partitionWallFeatureLevel",
    "rackDoorPerMinUnitLevel",
    "rackFeatureLevel",
    "serverFaceFeatureLevel",
    "leakageFeatureLevel",
    "meshConfig",
]


@pytest.mark.parametrize("field_name", MESH_CONFIG_RIDER_FIELDS)
def test_mesh_config_rider_field_declared(field_name: str):
    """Each runner-read MeshConfig field is declared (setting it does not raise extra_forbidden)."""
    # Use a trivially valid value for each field type
    value: Any
    if field_name in ("parallelMesh", "locInMeshVisible", "blockMeshEditActive", "blockMeshMoveMode"):
        value = True
    elif field_name in ("maxGlobalCells", "meshProcess", "rackRegionLevel",
                        "floorFeatureLevel", "acuFeatureLevel", "boxFeatureLevel",
                        "baffleFeatureLevel", "defaultFeatureLevel"):
        value = 4
    elif field_name in ("meshBaseSize", "meshDetectBaseSize"):
        value = 0.34
    elif field_name in ("refinementLevel", "acuRefinementLevel", "boxRefinementLevel",
                        "floorRefinementLevel", "wallBafflesRefinementLevel"):
        value = [1, 1]
    elif field_name == "locationInMesh":
        value = {"x": 5.0, "y": 1.5, "z": 5.0}
    elif field_name == "blockMeshBounds":
        value = None  # it can be null
    elif field_name == "refinementZones":
        value = []
    else:
        value = None

    data = _minimal_manifest(meshConfig={field_name: value})
    # Should not raise extra_forbidden for declared fields
    try:
        obj = CfdrunV2.model_validate(data)
        assert obj.meshConfig is not None
    except ValidationError as e:
        errors = e.errors()
        extra_errors = [err for err in errors if err.get("type") == "extra_forbidden"]
        assert not extra_errors, (
            f"Field '{field_name}' triggered extra_forbidden on MeshConfig: {extra_errors}"
        )


@pytest.mark.parametrize("field_name", SIM_CONFIG_RIDER_FIELDS)
def test_sim_config_rider_field_declared(field_name: str):
    """Each runner-read SimulationConfig field is declared (setting it does not raise extra_forbidden)."""
    value: Any
    # Determine a sensible value per field
    bool_fields = {"parallelSolve", "doublePrecisionWrite", "adjustTimeStep", "nonOrthoCorrection"}
    int_fields = {"endTime", "writeInterval", "nOuterCorrectors", "nCorrectors",
                  "nNonOrthogonalCorrectors", "solveProcess", "_nextExtraLocId",
                  "rackDoorFeatureLevel", "blankingPlateFeatureLevel", "rackPartitionFeatureLevel",
                  "rackThroughZoneFeatureLevel", "rackLeakageFeatureLevel",
                  "leakageFeatureLevel", "rackFeatureLevel", "serverFaceFeatureLevel",
                  "rackDoorPerMinUnitLevel", "partitionWallFeatureLevel"}
    float_fields = {"deltaT", "maxDeltaT", "maxCo", "writeIntervalSeconds", "endTimeSeconds",
                    "crankNicolsonCoeff", "gravityX", "gravityY", "gravityZ"}
    str_fields = {"writePrecision", "solverType", "solverName", "solverPreset", "startFrom",
                  "ddtScheme", "roomTemperatureC"}
    list_fields = {"extraLocations", "rackRefinementLevel", "serverFaceRefinementLevel",
                   "rackDoorRefinementLevel", "leakageRefinementLevel", "blankingPlateRefinementLevel",
                   "rackPartitionRefinementLevel", "partitionWallRefinementLevel"}
    dict_fields = {"monitoring", "schemes", "relaxationFactors", "residualControl",
                   "serverFacePerUnitLevel", "serverFaceFeaturePerUnitLevel",
                   "blankingPlatePerUnitLevel", "blankingPlateFeaturePerUnitLevel",
                   "rackPartitionPerUnitLevel", "rackPartitionFeaturePerUnitLevel",
                   "rackThroughZoneFeaturePerUnitLevel", "rackLeakageFeaturePerUnitLevel",
                   "rackLeakagePerUnitLevel", "rackThroughZonePerUnitLevel",
                   "meshConfig"}

    if field_name in bool_fields:
        value = False
    elif field_name in int_fields:
        value = 1
    elif field_name in float_fields:
        value = 0.0
    elif field_name in str_fields:
        value = "test_value"
    elif field_name in list_fields:
        value = []
    elif field_name in dict_fields:
        value = {}
    else:
        value = None

    data = _minimal_manifest(simulationConfig={field_name: value})
    try:
        obj = CfdrunV2.model_validate(data)
        assert obj.simulationConfig is not None
    except ValidationError as e:
        errors = e.errors()
        extra_errors = [err for err in errors if err.get("type") == "extra_forbidden"]
        assert not extra_errors, (
            f"Field '{field_name}' triggered extra_forbidden on SimulationConfig: {extra_errors}"
        )


# ============================================================
# (f) Unique patch names — duplicates raise ValidationError
# ============================================================

def test_duplicate_patch_names_in_boundary_bindings_raise():
    """Two boundaryBindings entries sharing the same patchName raise ValidationError."""
    data = _minimal_manifest(
        boundaryBindings={
            "bb_dup_patch_A": {
                "patchName": "duplicated_patch",
                "solidName": "solid_A",
                "objectPath": "constructions.racks.R1.servers.S1",
                "surfaceUid": "uid_A",
                "role": "fan_outlet",
                "bcType": "fan_outlet",
                "parameters": {"flowRateM3s": 0.5, "powerW": 1000.0, "temperatureC": 35.0},
            },
            "bb_dup_patch_B": {
                "patchName": "duplicated_patch",  # SAME patchName
                "solidName": "solid_B",
                "objectPath": "constructions.racks.R2.servers.S2",
                "surfaceUid": "uid_B",
                "role": "fan_outlet",
                "bcType": "fan_outlet",
                "parameters": {"flowRateM3s": 0.3, "powerW": 500.0, "temperatureC": 33.0},
            },
        }
    )
    with pytest.raises(ValidationError) as exc_info:
        CfdrunV2.model_validate(data)
    # The error message should mention the duplicate name
    assert "duplicated_patch" in str(exc_info.value)


def test_no_false_positives_for_unique_patch_names():
    """Two boundaryBindings with DIFFERENT patchNames do not raise."""
    data = _minimal_manifest(
        boundaryBindings={
            "bb_patch_A": {
                "patchName": "patch_A",
                "solidName": "solid_A",
                "objectPath": "constructions.racks.R1.servers.S1",
                "surfaceUid": "uid_A",
                "role": "fan_outlet",
                "bcType": "fan_outlet",
                "parameters": {"flowRateM3s": 0.5, "powerW": 1000.0},
            },
            "bb_patch_B": {
                "patchName": "patch_B",
                "solidName": "solid_B",
                "objectPath": "constructions.racks.R2.servers.S2",
                "surfaceUid": "uid_B",
                "role": "fan_inlet",
                "bcType": "fan_inlet",
                "parameters": {"flowRateM3s": 0.5},
            },
        }
    )
    obj = CfdrunV2.model_validate(data)
    assert len(obj.boundaryBindings) == 2


# ============================================================
# Additional: Pydantic model structure smoke tests
# ============================================================

def test_cfdrunv2_empty_manifest_validates():
    """A minimal skeleton manifest (no bindings, no sources) validates cleanly."""
    data = _minimal_manifest()
    obj = CfdrunV2.model_validate(data)
    assert obj.schemaVersion == 2
    assert obj.boundaryBindings == {}
    assert obj.sourceTerms == {}
    assert obj.porousBindings == {}


def test_source_project_optional_fields():
    """sourceProject allows optional filePath=None and exportedAt=None."""
    data = _minimal_manifest()
    data["sourceProject"]["filePath"] = None
    data["sourceProject"]["exportedAt"] = None
    obj = CfdrunV2.model_validate(data)
    assert obj.sourceProject.filePath is None
    assert obj.sourceProject.exportedAt is None


def test_artifacts_liquid_results_path_optional():
    """artifacts.liquidResultsPath can be None."""
    data = _minimal_manifest()
    assert data["artifacts"]["liquidResultsPath"] is None
    obj = CfdrunV2.model_validate(data)
    assert obj.artifacts.liquidResultsPath is None


def test_geometry_export_area_m2_optional():
    """StlSolidBinding.areaM2 and perimeterM can be None or float."""
    data = _minimal_manifest(
        geometryExport={
            "coordinateSystem": "Y-up",
            "unit": "m",
            "stlFiles": {},
            "solids": {
                "sol_my_solid": {
                    "solidName": "my_solid",
                    "patchName": "my_solid",
                    "stlFileId": None,
                    "objectPath": None,
                    "surfaceUid": None,
                    "role": "wall",
                    "areaM2": 1.5,
                    "perimeterM": 4.9,
                },
                "sol_no_area": {
                    "solidName": "no_area_solid",
                    "patchName": "no_area_solid",
                    "stlFileId": None,
                    "objectPath": None,
                    "surfaceUid": None,
                    "role": "fan_outlet",
                    "areaM2": None,
                },
            },
        }
    )
    obj = CfdrunV2.model_validate(data)
    assert obj.geometryExport.solids["sol_my_solid"].areaM2 == 1.5
    assert obj.geometryExport.solids["sol_my_solid"].perimeterM == 4.9
    assert obj.geometryExport.solids["sol_no_area"].areaM2 is None


def test_bctype_literal_validates_all_12_values():
    """All 12 valid bcType strings are accepted by BoundaryBinding."""
    valid_bc_types = [
        "wall", "acu_supply", "acu_return", "fan_inlet", "fan_outlet",
        "nonfan_fixed_temp", "nonfan_heat_flux", "fixedTemperature",
        "heat_source_flux", "perforated_baffle", "wall_baffle", "through_zone",
    ]
    for bc_type in valid_bc_types:
        data = _minimal_manifest(
            boundaryBindings={
                f"bb_{bc_type}": {
                    "patchName": f"patch_{bc_type}",
                    "solidName": f"solid_{bc_type}",
                    "objectPath": "test",
                    "surfaceUid": "uid1",
                    "role": "wall",
                    "bcType": bc_type,
                    "parameters": {},
                },
            }
        )
        obj = CfdrunV2.model_validate(data)
        assert obj.boundaryBindings[f"bb_{bc_type}"].bcType == bc_type


def test_bctype_invalid_raises():
    """An invalid bcType value raises ValidationError."""
    data = _minimal_manifest(
        boundaryBindings={
            "bb_bad": {
                "patchName": "bad_patch",
                "solidName": "bad_solid",
                "objectPath": "test",
                "surfaceUid": "uid1",
                "role": "wall",
                "bcType": "not_a_real_bc_type",
                "parameters": {},
            },
        }
    )
    with pytest.raises(ValidationError):
        CfdrunV2.model_validate(data)


def test_source_term_type_literal_validates():
    """All valid SourceTerm type strings are accepted."""
    valid_types = ["heatSource", "heatSink", "liquidHeatExchange", "rdhxHeatExchange", "porousZone"]
    for st_type in valid_types:
        data = _minimal_manifest(
            sourceTerms={
                f"st_{st_type}": {
                    "type": st_type,
                    "objectPath": None,
                    "region": None,
                    "parameters": {},
                }
            }
        )
        obj = CfdrunV2.model_validate(data)
        assert obj.sourceTerms[f"st_{st_type}"].type == st_type


def test_porous_binding_type_literal_validates():
    """All valid PorousBinding type strings are accepted."""
    valid_types = ["perforatedBaffle", "wallBaffle", "cyclicOpening", "porousZone", "leakageZone"]
    for pb_type in valid_types:
        data = _minimal_manifest(
            porousBindings={
                f"pb_{pb_type}": {
                    "patchName": f"pb_patch_{pb_type}",
                    "objectPath": None,
                    "surfaceUid": None,
                    "type": pb_type,
                    "parameters": {},
                }
            }
        )
        obj = CfdrunV2.model_validate(data)
        assert obj.porousBindings[f"pb_{pb_type}"].type == pb_type


def test_run_inputs_liquid_fields():
    """inputs.liquid {enabled, inheritResults} validates as booleans."""
    data = _minimal_manifest()
    data["inputs"]["liquid"] = {"enabled": True, "inheritResults": False}
    obj = CfdrunV2.model_validate(data)
    assert obj.inputs.liquid.enabled is True
    assert obj.inputs.liquid.inheritResults is False


def test_run_inputs_sensor_measurements():
    """inputs.labels.sensorMeasurements accepts temperatureC and relativeHumidityPct."""
    data = _minimal_manifest()
    data["inputs"]["labels"]["sensorMeasurements"] = {
        "sensor_1": {"temperatureC": 22.5},
        "sensor_2": {"temperatureC": 21.0, "relativeHumidityPct": 45.0},
        "sensor_3": {},
    }
    obj = CfdrunV2.model_validate(data)
    assert obj.inputs.labels.sensorMeasurements["sensor_1"].temperatureC == 22.5
    assert obj.inputs.labels.sensorMeasurements["sensor_2"].relativeHumidityPct == 45.0
    assert obj.inputs.labels.sensorMeasurements["sensor_3"].temperatureC is None
