"""
TDD tests for cfdrun_reader.py — resolve a V2 .cfdrun manifest → ResolvedRun (P3 T5).

Run with:
  conda run -n dcdtuni python -m pytest src/backend/tests/test_cfdrun_reader.py -q

The reader does NO physics derivation. The JS translators (Task 2/3) already
computed every boundary condition; the reader only MATERIALISES the explicit
V2 bindings into the existing BridgeBCRegistry by applying the AD8 camelCase →
BCSpec snake_case mapping IN REVERSE.

Test groups:
  (a) Discovery contract: 0 .cfdrun → FileNotFoundError; 2 → RuntimeError; 1 → ok.
  (b) A12: schemaVersion=1 raises ValueError containing "re-export".
  (c) Materialisation — acu_supply reproduces the Base_leakage baseline entry
      EXACTLY (the AD8-reverse proof); fan_outlet, nonfan_heat_flux, through_zone.
  (d) Porous master/slave: perforated pair → perforated_baffle + PerforatedSpec;
      wallBaffle pair → wall_baffle.
  (e) sourceTerm porousZone → fv_options_inputs[region] with sigma/d/f/origin/e1/e2.
  (f) STL resolution: matching areaM2 → ok; missing solid / wrong areaM2 → error.
  (g) dump_resolved_registry helper returns a sorted {sanitized_name: dump} dict.
"""
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.cfdrun_reader import (
    ResolvedRun,
    resolve_run,
    dump_resolved_registry,
)

BASELINE_DIR = (
    Path(__file__).resolve().parents[3]
    / "docs/superpowers/specs/baselines/2026-06-12-v4-refactor/cfdrun_v1"
)


# ---------------------------------------------------------------------------
# Helpers for hand-building V2 manifests
# ---------------------------------------------------------------------------

def _minimal_manifest(**overrides):
    """A minimal but schema-valid V2 manifest."""
    m = {
        "schemaVersion": 2,
        "runId": "r1",
        "name": "hand_built",
        "sourceProject": {
            "uid": "u",
            "schemaVersion": 4,
            "projectName": "p",
            "filePath": None,
            "contentHash": "h",
            "exportedAt": "2026-01-01T00:00:00Z",
        },
        "boundaryBindings": {},
        "sourceTerms": {},
        "porousBindings": {},
        "geometryExport": {"coordinateSystem": "Y-up", "unit": "m", "stlFiles": {}, "solids": {}},
    }
    m.update(overrides)
    return m


def _write_case(tmp_path: Path, manifest: dict, *, name: str = "case.cfdrun") -> Path:
    """Create a case dir with model/<name> = manifest."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / name).write_text(json.dumps(manifest), encoding="utf-8")
    return case_dir


def _write_ascii_stl_rect(path: Path, solid_name: str, w: float, h: float) -> float:
    """Write a single-solid ASCII STL of a w×h rectangle in the XY plane.
    Returns the exact area (w*h)."""
    # Two triangles forming a w×h rectangle at z=0.
    v = [
        (0.0, 0.0, 0.0),
        (w, 0.0, 0.0),
        (w, h, 0.0),
        (0.0, h, 0.0),
    ]
    tris = [(v[0], v[1], v[2]), (v[0], v[2], v[3])]
    lines = [f"solid {solid_name}"]
    for a, b, c in tris:
        lines.append("facet normal 0 0 1")
        lines.append("outer loop")
        for vert in (a, b, c):
            lines.append(f"vertex {vert[0]} {vert[1]} {vert[2]}")
        lines.append("endloop")
        lines.append("endfacet")
    lines.append(f"endsolid {solid_name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return w * h


# ---------------------------------------------------------------------------
# (a) Discovery contract
# ---------------------------------------------------------------------------

def test_discovery_zero_cfdrun_raises_filenotfound(tmp_path):
    case_dir = tmp_path / "case"
    (case_dir / "model").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        resolve_run(case_dir, resolve_stl=False)


def test_discovery_two_cfdrun_raises_runtimeerror(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "a.cfdrun").write_text(json.dumps(_minimal_manifest()), encoding="utf-8")
    (model_dir / "b.cfdrun").write_text(json.dumps(_minimal_manifest()), encoding="utf-8")
    with pytest.raises(RuntimeError):
        resolve_run(case_dir, resolve_stl=False)


def test_discovery_one_cfdrun_ok(tmp_path):
    case_dir = _write_case(tmp_path, _minimal_manifest())
    resolved = resolve_run(case_dir, resolve_stl=False)
    assert isinstance(resolved, ResolvedRun)
    assert len(resolved.bridge_registry) == 0


def test_explicit_cfdrun_path_used(tmp_path):
    # Two manifests in model/, but an explicit path disambiguates.
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    p = model_dir / "chosen.cfdrun"
    p.write_text(json.dumps(_minimal_manifest()), encoding="utf-8")
    (model_dir / "other.cfdrun").write_text(json.dumps(_minimal_manifest()), encoding="utf-8")
    resolved = resolve_run(case_dir, cfdrun_path=p, resolve_stl=False)
    assert isinstance(resolved, ResolvedRun)


# ---------------------------------------------------------------------------
# (b) A12 hard cutover
# ---------------------------------------------------------------------------

def test_schema_version_1_raises_reexport(tmp_path):
    bad = _minimal_manifest()
    bad["schemaVersion"] = 1
    case_dir = _write_case(tmp_path, bad)
    with pytest.raises(Exception) as exc:
        resolve_run(case_dir, resolve_stl=False)
    assert "re-export" in str(exc.value)


# ---------------------------------------------------------------------------
# (c) Materialisation — AD8-reverse mapping
# ---------------------------------------------------------------------------

def test_acu_supply_reproduces_base_leakage_baseline(tmp_path):
    """The AD8-reverse proof: a hand-built acu_supply binding with the full
    AD8 camelCase param set materialises into a BCSpec whose model_dump()
    equals the Base_leakage.registry.json baseline entry EXACTLY."""
    baseline = json.loads((BASELINE_DIR / "Base_leakage.registry.json").read_text())
    expected = baseline["ACU_1_Bottom_patch"]

    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_acu": {
                "patchName": "ACU_1_Bottom_patch",
                "bcType": "acu_supply",
                "parameters": {
                    "temperatureC": 15.0,
                    "flowRateM3s": 4.0,
                    "coolingCapacityW": 30000.0,
                    "minSupplyTempC": 13.3,
                    "setPointTempC": 15.0,
                    "coolantTempC": 7.0,
                    "returnPatchName": "ACU_1_Top_patch",
                    "timeVarying": {},
                },
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    spec = resolved.bridge_registry.get("ACU_1_Bottom_patch")
    assert spec is not None
    assert spec.model_dump(mode="json") == expected


def test_acu_return_materialises(tmp_path):
    baseline = json.loads((BASELINE_DIR / "Base_leakage.registry.json").read_text())
    expected = baseline["ACU_1_Top_patch"]
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_ret": {
                "patchName": "ACU_1_Top_patch",
                "bcType": "acu_return",
                "parameters": {"flowRateM3s": 4.0, "timeVarying": {}},
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    assert resolved.bridge_registry.get("ACU_1_Top_patch").model_dump(mode="json") == expected


def test_fan_outlet_materialises(tmp_path):
    baseline = json.loads((BASELINE_DIR / "Base_leakage.registry.json").read_text())
    expected = baseline["Default_Rack_1_fan_server_1_outlet_p0"]
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_fan": {
                "patchName": "Default_Rack_1_fan_server_1_outlet_p0",
                "bcType": "fan_outlet",
                "parameters": {
                    "temperatureC": 45.0,
                    "flowRateM3s": 0.1,
                    "powerW": 2391.9,
                    "physicalFlowAreaM2": 0.29904000000000003,
                },
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    assert resolved.bridge_registry.get(
        "Default_Rack_1_fan_server_1_outlet_p0"
    ).model_dump(mode="json") == expected


def test_fan_inlet_physical_flow_area(tmp_path):
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_in": {
                "patchName": "fan_in",
                "bcType": "fan_inlet",
                "parameters": {
                    "flowRateM3s": 0.1,
                    "powerW": 2391.9,
                    "physicalFlowAreaM2": 0.29904,
                },
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    spec = resolved.bridge_registry.get("fan_in")
    assert spec.bc_type == "fan_inlet"
    assert spec.physical_flow_area == pytest.approx(0.29904)
    assert spec.power == pytest.approx(2391.9)
    assert spec.flow_rate == pytest.approx(0.1)


def test_nonfan_heat_flux_materialises(tmp_path):
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_hf": {
                "patchName": "server_face_hf",
                "bcType": "nonfan_heat_flux",
                "parameters": {"heatFluxWm2": 1234.5},
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    spec = resolved.bridge_registry.get("server_face_hf")
    assert spec.bc_type == "nonfan_heat_flux"
    assert spec.heat_flux == pytest.approx(1234.5)
    # None params must NOT be passed → they stay None in the dump.
    assert spec.temperature is None


def test_nonfan_fixed_temp_materialises(tmp_path):
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_ft": {
                "patchName": "server_face_ft",
                "bcType": "nonfan_fixed_temp",
                "parameters": {"temperatureC": 35.0},
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    spec = resolved.bridge_registry.get("server_face_ft")
    assert spec.bc_type == "nonfan_fixed_temp"
    assert spec.temperature == pytest.approx(35.0)


def test_through_zone_sigma_materialises(tmp_path):
    baseline = json.loads((BASELINE_DIR / "Base_leakage.registry.json").read_text())
    expected = baseline["Default_Rack_1_u1_u15_through_zone"]
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_tz": {
                "patchName": "Default_Rack_1_u1_u15_through_zone",
                "bcType": "through_zone",
                "parameters": {"sigma": 0.05},
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    assert resolved.bridge_registry.get(
        "Default_Rack_1_u1_u15_through_zone"
    ).model_dump(mode="json") == expected


def test_heat_source_flux_surface_area(tmp_path):
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_hs": {
                "patchName": "wall_obj",
                "bcType": "heat_source_flux",
                "parameters": {"heatFluxWm2": 500.0, "surfaceAreaM2": 2.5},
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    spec = resolved.bridge_registry.get("wall_obj")
    assert spec.bc_type == "heat_source_flux"
    assert spec.heat_flux == pytest.approx(500.0)
    assert spec.surface_area == pytest.approx(2.5)


def test_cooling_model_table_params(tmp_path):
    table = [{"returnTemp": 24.0, "capacity": 30000.0}]
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_acu2": {
                "patchName": "acu_tbl_supply",
                "bcType": "acu_supply",
                "parameters": {
                    "temperatureC": 18.0,
                    "flowRateM3s": 3.0,
                    "coolingModelMode": "tableCapacity",
                    "ratedReturnTempC": 24.0,
                    "coolingPerformanceTable": table,
                    "timeVarying": {},
                },
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    spec = resolved.bridge_registry.get("acu_tbl_supply")
    assert spec.cooling_model_mode == "tableCapacity"
    assert spec.rated_return_temp == pytest.approx(24.0)
    assert spec.cooling_performance_table == table


# ---------------------------------------------------------------------------
# (d) Porous master/slave
# ---------------------------------------------------------------------------

def test_perforated_porous_master_slave(tmp_path):
    baseline = json.loads((BASELINE_DIR / "Base_leakage.registry.json").read_text())
    expected = baseline["Raised_Floor_copy_patch_master"]
    perf = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}
    manifest = _minimal_manifest(
        porousBindings={
            "pb_m": {
                "patchName": "Raised_Floor_copy_patch_master",
                "type": "perforatedBaffle",
                "parameters": {"sigma": 0.64, "perforation": perf},
            },
            "pb_s": {
                "patchName": "Raised_Floor_copy_patch_slave",
                "type": "perforatedBaffle",
                "parameters": {"sigma": 0.64, "perforation": perf},
            },
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    m_spec = resolved.bridge_registry.get("Raised_Floor_copy_patch_master")
    s_spec = resolved.bridge_registry.get("Raised_Floor_copy_patch_slave")
    assert m_spec is not None and s_spec is not None
    assert m_spec.model_dump(mode="json") == expected
    # Slave is identical perforated_baffle with the same sigma + perforation.
    assert s_spec.bc_type == "perforated_baffle"
    assert s_spec.sigma == pytest.approx(0.64)
    assert s_spec.perforated.openAreaPct == pytest.approx(64.0)


def test_wall_baffle_porous_master_slave(tmp_path):
    baseline = json.loads((BASELINE_DIR / "Base_leakage.registry.json").read_text())
    expected = baseline["Raised_Floor_copy_part1_master"]
    manifest = _minimal_manifest(
        porousBindings={
            "pb_wm": {
                "patchName": "Raised_Floor_copy_part1_master",
                "type": "wallBaffle",
                "parameters": {},
            },
            "pb_ws": {
                "patchName": "Raised_Floor_copy_part1_slave",
                "type": "wallBaffle",
                "parameters": {},
            },
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    m_spec = resolved.bridge_registry.get("Raised_Floor_copy_part1_master")
    s_spec = resolved.bridge_registry.get("Raised_Floor_copy_part1_slave")
    assert m_spec.model_dump(mode="json") == expected
    assert m_spec.bc_type == "wall_baffle"
    assert s_spec.bc_type == "wall_baffle"


# ---------------------------------------------------------------------------
# (e) sourceTerm porousZone → fv_options_inputs
# ---------------------------------------------------------------------------

def test_source_term_porous_zone_materialises(tmp_path):
    manifest = _minimal_manifest(
        sourceTerms={
            "st_hole": {
                "type": "porousZone",
                "region": "hole_zone_1",
                "parameters": {
                    "sigma": 0.3,
                    "d": [0.0, 0.0, 0.0],
                    "f": [123.4, 0.0, 0.0],
                    "origin": [1.0, 2.0, 3.0],
                    "e1": [0.0, 0.0, 1.0],
                    "e2": [0.0, 1.0, 0.0],
                },
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    assert "hole_zone_1" in resolved.fv_options_inputs
    z = resolved.fv_options_inputs["hole_zone_1"]
    assert z["cellzone_name"] == "hole_zone_1"
    assert z["sigma"] == pytest.approx(0.3)
    assert z["f"] == [123.4, 0.0, 0.0]
    assert z["origin"] == [1.0, 2.0, 3.0]
    assert z["e1"] == [0.0, 0.0, 1.0]
    assert z["e2"] == [0.0, 1.0, 0.0]


def test_source_term_non_porous_stored_separately(tmp_path):
    manifest = _minimal_manifest(
        sourceTerms={
            "st_heat": {
                "type": "heatSource",
                "region": "rack_zone",
                "parameters": {"powerW": 1000.0},
            }
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    # A heat source is NOT a porousZone → must not pollute fv_options_inputs.
    assert "rack_zone" not in resolved.fv_options_inputs


# ---------------------------------------------------------------------------
# (f) Probes / monitors / pass-throughs
# ---------------------------------------------------------------------------

def test_probes_and_monitors_pass_through(tmp_path):
    manifest = _minimal_manifest(
        postprocessConfig={
            "probes": {"p1": {"x": 1.0, "y": 2.0, "z": 3.0}},
            "surfaceMonitors": {"m1": {"patch": "ACU_1_Top_patch"}},
            "outputFields": ["T", "U", "p_rgh"],
            "labels": {},
        },
        meshConfig={"baseSize": 0.1},
        simulationConfig={"solverType": "steady"},
    )
    case_dir = _write_case(tmp_path, manifest)
    resolved = resolve_run(case_dir, resolve_stl=False)
    assert "p1" in resolved.probes
    assert "m1" in resolved.surface_monitors
    assert resolved.mesh_config.get("baseSize") == pytest.approx(0.1)
    assert resolved.simulation_config.get("solverType") == "steady"


# ---------------------------------------------------------------------------
# (g) STL resolution
# ---------------------------------------------------------------------------

def test_stl_resolution_matching_area_ok(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
    area = _write_ascii_stl_rect(tri_dir / "patch.stl", "ACU_1_Bottom", 2.0, 1.5)
    manifest = _minimal_manifest(
        geometryExport={
            "coordinateSystem": "Y-up",
            "unit": "m",
            "stlFiles": {"f1": {"path": "patch.stl"}},
            "solids": {
                "s1": {
                    "solidName": "ACU_1_Bottom",
                    "patchName": "ACU_1_Bottom",
                    "stlFileId": "f1",
                    "areaM2": area,
                }
            },
        }
    )
    (model_dir / "case.cfdrun").write_text(json.dumps(manifest), encoding="utf-8")
    resolved = resolve_run(case_dir, resolve_stl=True)
    assert "s1" in resolved.geometry_solids


def test_stl_resolution_missing_solid_raises(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
    _write_ascii_stl_rect(tri_dir / "patch.stl", "SomethingElse", 2.0, 1.5)
    manifest = _minimal_manifest(
        geometryExport={
            "stlFiles": {"f1": {"path": "patch.stl"}},
            "solids": {
                "s1": {"solidName": "ACU_1_Bottom", "stlFileId": "f1", "areaM2": 3.0}
            },
        }
    )
    (model_dir / "case.cfdrun").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Exception) as exc:
        resolve_run(case_dir, resolve_stl=True)
    # Path-addressed error: names the offending solid key.
    assert "s1" in str(exc.value) or "ACU_1_Bottom" in str(exc.value)


def test_stl_resolution_wrong_area_raises(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
    real_area = _write_ascii_stl_rect(tri_dir / "patch.stl", "ACU_1_Bottom", 2.0, 1.5)
    manifest = _minimal_manifest(
        geometryExport={
            "stlFiles": {"f1": {"path": "patch.stl"}},
            "solids": {
                "s1": {
                    "solidName": "ACU_1_Bottom",
                    "stlFileId": "f1",
                    "areaM2": real_area * 5.0,  # wildly wrong
                }
            },
        }
    )
    (model_dir / "case.cfdrun").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Exception) as exc:
        resolve_run(case_dir, resolve_stl=True)
    assert "s1" in str(exc.value) or "area" in str(exc.value).lower()


def test_stl_resolution_missing_file_raises(tmp_path):
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    (case_dir / "Mesh" / "constant" / "triSurface").mkdir(parents=True)
    manifest = _minimal_manifest(
        geometryExport={
            "stlFiles": {"f1": {"path": "does_not_exist.stl"}},
            "solids": {"s1": {"solidName": "X", "stlFileId": "f1"}},
        }
    )
    (model_dir / "case.cfdrun").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Exception) as exc:
        resolve_run(case_dir, resolve_stl=True)
    assert "does_not_exist.stl" in str(exc.value) or "f1" in str(exc.value)


def test_tri_surface_dir_override(tmp_path):
    """resolve_stl honours an explicit tri_surface_dir override."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    alt_tri = tmp_path / "alt_tri"
    area = _write_ascii_stl_rect(alt_tri / "patch.stl", "ACU_1_Bottom", 2.0, 1.5)
    manifest = _minimal_manifest(
        geometryExport={
            "stlFiles": {"f1": {"path": "patch.stl"}},
            "solids": {
                "s1": {"solidName": "ACU_1_Bottom", "stlFileId": "f1", "areaM2": area}
            },
        }
    )
    (model_dir / "case.cfdrun").write_text(json.dumps(manifest), encoding="utf-8")
    resolved = resolve_run(case_dir, resolve_stl=True, tri_surface_dir=alt_tri)
    assert "s1" in resolved.geometry_solids


# ---------------------------------------------------------------------------
# (h) dump_resolved_registry helper (Task 3/7 will call it)
# ---------------------------------------------------------------------------

def test_dump_resolved_registry_sorted_dict(tmp_path):
    manifest = _minimal_manifest(
        boundaryBindings={
            "bb_z": {
                "patchName": "Zeta_patch",
                "bcType": "wall",
                "parameters": {},
            },
            "bb_a": {
                "patchName": "Alpha_patch",
                "bcType": "acu_return",
                "parameters": {"flowRateM3s": 2.0, "timeVarying": {}},
            },
        }
    )
    case_dir = _write_case(tmp_path, manifest)
    dump = dump_resolved_registry(case_dir)
    assert list(dump.keys()) == sorted(dump.keys())
    assert "Alpha_patch" in dump and "Zeta_patch" in dump
    assert dump["Alpha_patch"]["bc_type"] == "acu_return"
    assert dump["Zeta_patch"]["bc_type"] == "wall"
