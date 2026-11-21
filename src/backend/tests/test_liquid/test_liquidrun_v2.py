"""The versioned manifest rejects ambiguity before any physics allocation."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from backend.tests.test_liquid.test_runtime import controller, runtime_config


def contract():
    name = "backend.liquidcooling.liquidrun_schema"
    assert importlib.util.find_spec(name), "liquidrun requires its own versioned schema"
    return __import__(name, fromlist=["LiquidrunV2"])


def manifest():
    raw = runtime_config(samples=4).model_dump(by_alias=True, exclude_none=True)
    raw.update(
        format="liquidrun", schemaVersion=2, version="2.0",
        source_project={"format": "cfdproj", "schema_version": 4,
                        "project_uid": "project-stable-uid", "project_name": "Example"},
        units={"length": "m", "time": "s", "temperature": "K",
               "pressure": "Pa", "mass_flow": "kg/s", "power": "W"},
        execution={"backend": "pytorch", "device": "cpu", "dtype": "float64", "max_threads": 1},
        controllers=[controller()],
        object_bindings={"cdu-uid": {"label": "CDU", "kind": "cdu",
                                   "node_ids": ["supply", "return"], "edge_ids": ["pump"]}},
    )
    raw.pop("parameter_provenance", None)
    return raw


def test_v2_is_self_contained_and_preserves_source_binding_and_controllers():
    api = contract()
    raw = manifest()
    before = copy.deepcopy(raw)
    parsed = api.LiquidrunV2.model_validate(raw)
    cfg, controls = api.resolve_run_input(parsed.model_dump(by_alias=True, mode="json", exclude_none=True))
    assert cfg.version == "2.0"
    assert controls[0]["actuator"] == {"edge_id": "pump", "field": "rpm_ratio"}
    assert cfg.model_dump()["source_project"]["project_uid"] == "project-stable-uid"
    assert cfg.model_dump()["controllers"] == controls
    assert cfg.model_dump()["object_bindings"] == raw["object_bindings"]
    assert raw == before


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.update(typo=True), "typo"),
    (lambda d: d["time"].update(dt_s=True), "dt_s"),
    (lambda d: d["time"].update(N_t=4.5), "N_t"),
    (lambda d: d["time"].update(dt_s=float("nan")), "dt_s"),
    (lambda d: d["time"].update(bdf_order=2), "bdf_order"),
    (lambda d: d["nodes"][1].update(position_m=[1, 2]), "position_m"),
    (lambda d: d["edges"][0].update(to="absent"), "node"),
    (lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0])), "unique"),
    (lambda d: d["edges"][0]["params"].update(UA_W_per_K=-1), "UA_W_per_K"),
    (lambda d: d["edges"][0]["params"].update(UA_typo=100), "UA_typo"),
    (lambda d: d["edges"][-1]["params"].update(rpm_ratio=[1, .5]), "N_t"),
    (lambda d: d["events"][0].update(time_s=2.5), "grid"),
    (lambda d: d["events"][0].update(target="absent"), "target"),
    (lambda d: d["events"][0].update(value=-1), "value"),
    (lambda d: d["controllers"][0]["measurement"].update(target_id="absent"), "measurement"),
    (lambda d: d["controllers"][0].update(sample_period_s=.5), "grid"),
    (lambda d: d["controllers"][0]["pid"].update(typo=1), "typo"),
    (lambda d: d["controllers"].append({**copy.deepcopy(d["controllers"][0]), "id": "duplicate"}), "actuator"),
    (lambda d: d["events"].append({"time_s": 3, "target": "pump", "field": "rpm_ratio", "value": .8}), "conflict"),
    (lambda d: d["object_bindings"]["cdu-uid"]["edge_ids"].append("absent"), "binding"),
    (lambda d: d["object_bindings"]["cdu-uid"].update(parent_uid="absent"), "parent_uid"),
    (lambda d: d["units"].update(temperature="C"), "temperature"),
    (lambda d: d["source_project"].update(project_uid=""), "project_uid"),
    (lambda d: d["execution"].update(device="cuda"), "device"),
    (lambda d: d["execution"].update(max_threads=64), "max_threads"),
])
def test_invalid_v2_fails_during_schema_validation(mutate, message):
    api = contract()
    raw = manifest()
    mutate(raw)
    with pytest.raises(ValueError, match=message):
        api.parse_liquidrun(raw)


def test_parameter_pack_is_embedded_and_verified_before_runtime_conversion():
    api = contract()
    raw = manifest()
    pack_path = Path(__file__).parents[2] / "liquidcooling/data/mock-demo-v1.json"
    raw["parameter_pack"] = json.loads(pack_path.read_text())
    raw["edges"][-1].update(parameter_ref="pump-demo", params={"rpm_ratio": 1.0})
    config = api.parse_liquidrun(raw)
    assert config.edges[-1].params["H0_m"] == 6.0
    assert config.parameter_provenance["source"] == "mock"
    assert config.parameter_provenance["bindings"]["edges"]["pump"]["profile"] == "pump-demo"
    broken = copy.deepcopy(raw)
    broken["parameter_pack"]["profiles"]["pump-demo"]["units"]["H0_m"] = "ft"
    with pytest.raises(ValueError, match="units"):
        api.parse_liquidrun(broken)


def test_duplicate_transport_controllers_must_agree_without_silent_override():
    api = contract()
    raw = manifest()
    _, controls = api.resolve_run_input(raw, [controller()])
    assert len(controls) == 1
    with pytest.raises(ValueError, match="conflict"):
        api.resolve_run_input(raw, [])
    with pytest.raises(ValueError, match="conflict"):
        api.resolve_run_input(raw, [controller(setpoint=31000)])


def test_legacy_files_remain_loadable_but_unknown_versions_do_not_downgrade():
    api = contract()
    raw = runtime_config(samples=4).model_dump(by_alias=True)
    raw["legacy_extension"] = "kept"
    config, controls = api.resolve_run_input(raw, [controller()])
    assert config.model_dump()["legacy_extension"] == "kept"
    assert controls == [controller()]
    raw["version"] = "99.0"
    with pytest.raises(ValueError, match="version"):
        api.parse_liquidrun(raw)


def test_v2_manager_persists_a_replayable_manifest_and_embedded_controller(tmp_path):
    api = contract()
    from backend.liquidcooling.run_manager import LiquidRunManager, RunInputError

    manager = LiquidRunManager()
    created = manager.create(manifest(), case_dir=tmp_path)
    run_dir = tmp_path / "Liquid/log" / f"run-{created['run_id']}"
    saved = json.loads((run_dir / "input.liquidrun").read_text())
    assert saved["controllers"][0]["id"] == "dp"
    assert saved["source_project"]["project_uid"] == "project-stable-uid"
    # The distributable file itself must satisfy v2, not just the legacy adapter.
    api.LiquidrunV2.model_validate(saved)
    api.parse_liquidrun(saved)
    manager.operate(created["run_id"], "step")
    assert "dp" in manager.get(created["run_id"])["frame"]["controls"]
    with pytest.raises(RunInputError, match="conflict"):
        manager.create(saved, controllers=[], case_dir=tmp_path)


def test_distributed_json_schema_is_generated_from_the_versioned_model():
    api = contract()
    schema_path = Path(__file__).parents[2] / "liquidcooling/liquidrun.schema.json"
    assert json.loads(schema_path.read_text()) == api.liquidrun_json_schema()
    schema = api.liquidrun_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schemaVersion"]["const"] == 2
    assert "controllers" in schema["required"]


def test_unsorted_events_cannot_silently_overwrite_a_later_load_step():
    """The compiler applies events in order; reject reversed time before compilation."""
    raw = manifest()
    raw["events"].append({"time_s": 1.0, "target": "cold_plate_1", "field": "Q_W", "value": 600.0})
    with pytest.raises(ValueError, match="chronological"):
        contract().parse_liquidrun(raw)


def test_literal_bdf_order_does_not_accept_a_boolean_alias_for_one():
    raw = manifest()
    raw["time"]["bdf_order"] = True
    with pytest.raises(ValueError, match="bdf_order"):
        contract().parse_liquidrun(raw)


def test_resolved_parameter_provenance_cannot_be_reused_after_parameter_mutation():
    raw = manifest()
    pack_path = Path(__file__).parents[2] / "liquidcooling/data/mock-demo-v1.json"
    raw["parameter_pack"] = json.loads(pack_path.read_text())
    # JSON.stringify in the browser emits whole-number floats without '.0'.
    raw["parameter_pack"]["profiles"]["pump-demo"]["params"].update(H0_m=6, a_coef=20000000)
    raw["edges"][-1].update(parameter_ref="pump-demo", params={"rpm_ratio": 1.0})
    resolved = contract().parse_liquidrun(raw).model_dump(by_alias=True, mode="json", exclude_none=True)
    contract().parse_liquidrun(resolved)
    resolved["edges"][-1]["params"]["H0_m"] += 1.0
    with pytest.raises(ValueError, match="resolved_sha256"):
        contract().parse_liquidrun(resolved)
