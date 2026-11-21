"""Parameter provenance and replacement must survive real schema compilation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph


def manifest():
    raw = json.loads((Path(__file__).parent / "minimal.liquidrun").read_text())
    raw["parameter_pack"] = {
        "schema_version": "1.0", "id": "demo-water-loop", "revision": "mock-1",
        "source": "mock", "description": "Synthetic values, not manufacturer data",
        "profiles": {"pipe-a": {
            "type": "pipe", "params": {"D_m": 0.05, "roughness_m": 1e-6},
            "units": {"D_m": "m", "roughness_m": "m"},
        }},
    }
    raw["edges"][0]["parameter_ref"] = "pipe-a"
    del raw["edges"][0]["params"]["D_m"]
    del raw["edges"][0]["params"]["roughness_m"]
    return raw


def test_pack_values_reach_actual_compiled_physics_without_mutating_input():
    raw = manifest()
    before = deepcopy(raw)
    cfg = LiquidrunConfig.model_validate(raw)
    assert cfg.edges[0].params.get("D_m") == pytest.approx(0.05)
    graph = compile_graph(cfg)
    assert graph.pipe_params.D.tolist() == pytest.approx([0.05])
    assert raw == before
    provenance = cfg.parameter_provenance
    assert provenance["source"] == "mock"
    assert provenance["bindings"]["edges"]["pipe1"]["profile"] == "pipe-a"
    assert len(provenance["sha256"]) == 64


def test_replacing_parameter_pack_changes_physics_and_identity_not_topology():
    first = manifest()
    second = deepcopy(first)
    second["parameter_pack"]["source"] = "vendor"
    second["parameter_pack"]["revision"] = "vendor-A"
    second["parameter_pack"]["profiles"]["pipe-a"]["params"]["D_m"] = 0.04
    a, b = map(LiquidrunConfig.model_validate, (first, second))
    ga, gb = map(compile_graph, (a, b))
    assert ga.edge_str_ids == gb.edge_str_ids
    assert ga.node_ids == gb.node_ids
    assert ga.pipe_params.D.tolist() != gb.pipe_params.D.tolist()
    assert a.parameter_provenance["sha256"] != b.parameter_provenance["sha256"]
    assert b.parameter_provenance["source"] == "vendor"


def test_explicit_overrides_are_effective_and_recorded():
    raw = manifest()
    raw["edges"][0]["params"]["D_m"] = 0.03
    cfg = LiquidrunConfig.model_validate(raw)
    assert cfg.edges[0].params["D_m"] == pytest.approx(0.03)
    assert cfg.parameter_provenance["bindings"]["edges"]["pipe1"]["overrides"]["D_m"] == 0.03


@pytest.mark.parametrize("change, message", [
    ("unknown", "unknown parameter profile"),
    ("mismatch", "profile type"),
    ("absent", "parameter_pack is required"),
    ("units", "units"),
    ("nan", "finite"),
])
def test_invalid_pack_binding_is_rejected_before_solving(change, message):
    raw = manifest()
    if change == "unknown": raw["edges"][0]["parameter_ref"] = "missing"
    if change == "mismatch":
        raw["parameter_pack"]["profiles"]["pipe-a"] = {
            "type": "pump", "params": {"H0_m": 6.0}, "units": {"H0_m": "m"},
        }
    if change == "absent": del raw["parameter_pack"]
    if change == "units": del raw["parameter_pack"]["profiles"]["pipe-a"]["units"]["D_m"]
    if change == "nan": raw["parameter_pack"]["profiles"]["pipe-a"]["params"]["D_m"] = float("nan")
    with pytest.raises(ValidationError, match=message):
        LiquidrunConfig.model_validate(raw)


def test_material_profile_populates_coolant_and_records_binding():
    raw = manifest()
    raw["fluid_ref"] = "water-mock"
    values = {"rho_kg_m3": 990.0, "cp_J_kgK": 4100.0, "mu_Pa_s": 0.0007, "k_W_mK": 0.61}
    raw["parameter_pack"]["profiles"]["water-mock"] = {
        "type": "fluid", "params": values,
        "units": {"rho_kg_m3": "kg/m3", "cp_J_kgK": "J/(kg*K)", "mu_Pa_s": "Pa*s", "k_W_mK": "W/(m*K)"},
    }
    del raw["fluid"]
    cfg = LiquidrunConfig.model_validate(raw)
    assert cfg.fluid.rho_kg_m3 == 990.0
    assert cfg.fluid.cp_J_kgK == 4100.0
    assert cfg.parameter_provenance["bindings"]["fluid"]["profile"] == "water-mock"


def test_resolved_snapshot_roundtrips_without_reapplying_profile():
    cfg = LiquidrunConfig.model_validate(manifest())
    snapshot = cfg.model_dump(mode="json", by_alias=True)
    assert "parameter_pack" not in snapshot
    assert "parameter_ref" not in snapshot["edges"][0]
    again = LiquidrunConfig.model_validate(snapshot)
    assert again.parameter_provenance == cfg.parameter_provenance
    assert again.edges[0].params == cfg.edges[0].params


def test_legacy_manifest_remains_valid_without_provenance_claim():
    raw = manifest()
    del raw["parameter_pack"]
    del raw["edges"][0]["parameter_ref"]
    cfg = LiquidrunConfig.model_validate(raw)
    assert cfg.parameter_provenance == {}


def test_unknown_profile_parameter_cannot_be_silently_ignored():
    raw = manifest()
    profile = raw["parameter_pack"]["profiles"]["pipe-a"]
    profile["params"]["diameter_typo"] = 0.05
    profile["units"]["diameter_typo"] = "m"
    with pytest.raises(ValidationError, match="unsupported parameter"):
        LiquidrunConfig.model_validate(raw)


def test_wrong_units_are_not_silently_treated_as_si():
    raw = manifest()
    raw["parameter_pack"]["profiles"]["pipe-a"]["units"]["D_m"] = "mm"
    with pytest.raises(ValidationError, match="units for D_m"):
        LiquidrunConfig.model_validate(raw)


def test_effective_parameter_hash_changes_when_override_changes():
    raw = manifest()
    a = LiquidrunConfig.model_validate(raw)
    raw["edges"][0]["params"]["D_m"] = 0.06
    b = LiquidrunConfig.model_validate(raw)
    assert a.parameter_provenance["sha256"] == b.parameter_provenance["sha256"]
    assert a.parameter_provenance["resolved_sha256"] != b.parameter_provenance["resolved_sha256"]


def test_shipped_mock_pack_can_populate_real_pipeline_inputs():
    from backend.liquidcooling import parameters
    path = Path(parameters.__file__).parent / "data" / "mock-demo-v1.json"
    raw = manifest()
    raw["parameter_pack"] = json.loads(path.read_text())
    raw["edges"][0]["parameter_ref"] = "pipe-25mm"
    raw["fluid_ref"] = "water"
    del raw["fluid"]
    cfg = LiquidrunConfig.model_validate(raw)
    graph = compile_graph(cfg)
    assert graph.pipe_params.D.tolist() == pytest.approx([0.025])
    assert cfg.parameter_provenance["source"] == "mock"
    assert "not NVIDIA or Vertiv" in cfg.parameter_provenance["description"]


@pytest.mark.parametrize("field,value", [("D_m", "NaN"), ("D_m", "0.03"), ("n_cells", 1.9), ("n_cells", True)])
def test_profile_numeric_types_cannot_bypass_validation(field, value):
    raw = manifest()
    profile = raw["parameter_pack"]["profiles"]["pipe-a"]
    profile["params"][field] = value
    profile["units"][field] = "1" if field == "n_cells" else "m"
    with pytest.raises(ValidationError, match="(number|integer)"):
        LiquidrunConfig.model_validate(raw)


@pytest.mark.parametrize("field,value,message", [
    ("diameter_typo", 0.03, "unsupported parameter"),
    ("D_m", "NaN", "number"),
    ("n_cells", 1.9, "integer"),
])
def test_override_uses_same_field_and_type_validation(field, value, message):
    raw = manifest()
    raw["edges"][0]["params"][field] = value
    with pytest.raises(ValidationError, match=message):
        LiquidrunConfig.model_validate(raw)


def test_characteristic_typo_does_not_silently_become_linear():
    raw = manifest()
    raw["parameter_pack"]["profiles"]["bad-valve"] = {
        "type": "valve", "params": {"characteristic": "equl_percentage"},
        "units": {"characteristic": "enum"},
    }
    with pytest.raises(ValidationError, match="characteristic"):
        LiquidrunConfig.model_validate(raw)


def test_fluid_profile_and_edge_named_fluid_have_separate_binding_namespaces():
    from backend.liquidcooling import parameters
    raw = manifest()
    raw["parameter_pack"] = json.loads((Path(parameters.__file__).parent / "data" / "mock-demo-v1.json").read_text())
    raw["fluid_ref"] = "water"
    raw["edges"][0]["id"] = "fluid"
    raw["edges"][0]["parameter_ref"] = "pipe-25mm"
    cfg = LiquidrunConfig.model_validate(raw)
    assert cfg.parameter_provenance["bindings"]["fluid"]["profile"] == "water"
    assert cfg.parameter_provenance["bindings"]["edges"]["fluid"]["profile"] == "pipe-25mm"


def test_effective_parameter_hash_ignores_visual_metadata():
    raw = manifest()
    a = LiquidrunConfig.model_validate(raw)
    raw["edges"][0]["display"] = {"selected": True, "opacity": 0.5}
    b = LiquidrunConfig.model_validate(raw)
    assert a.parameter_provenance["resolved_sha256"] == b.parameter_provenance["resolved_sha256"]
