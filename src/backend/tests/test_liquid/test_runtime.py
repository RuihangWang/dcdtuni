"""Closed-loop liquid runtime contracts exercised through production kernels."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.liquidcooling.schema import LiquidrunConfig


FIXTURE = Path(__file__).with_name("cdu_loop.liquidrun")


def runtime_config(*, samples: int = 7) -> LiquidrunConfig:
    raw = json.loads(FIXTURE.read_text())
    raw["time"].update(dt_s=1.0, N_t=samples)
    raw["nodes"][0]["bc"].pop("T_K")
    raw["nodes"][-1] = {
        "id": "return", "type": "junction", "position_m": [2, 0, 0]
    }
    raw["edges"].append({
        "id": "pump", "type": "pump", "from": "return", "to": "supply",
        "params": {"H0_m": 10.0, "a_coef": 500000.0,
                   "rpm_ratio": [1.0, 1.0, 0.65, 0.65, 1.0, 1.0, 1.0][:samples]},
    })
    raw["events"] = [
        {"time_s": 3.0, "target": "cold_plate_1", "field": "Q_W", "value": 900.0}
    ] if samples > 3 else []
    return LiquidrunConfig.model_validate(raw)


def controller(setpoint: float = 30000.0) -> dict:
    return {
        "id": "dp",
        "measurement": {
            "kind": "pressure_difference", "target_id": "supply",
            "reference_id": "return",
        },
        "actuator": {"edge_id": "pump", "field": "rpm_ratio"},
        "pid": {
            "kp": 0.00001, "ki": 0.000001, "setpoint": setpoint,
            "bias": 0.8, "output_min": 0.2, "output_max": 1.0,
            "rate_limit_per_s": 0.1,
        },
        "sample_period_s": 1.0,
    }


def test_actual_kernels_apply_schedules_and_publish_coherent_frames():
    """Deleting per-step schedule selection must break flow and heat causality."""
    from backend.liquidcooling.runtime import LiquidSimulation

    sim = LiquidSimulation(runtime_config())
    without_event = runtime_config()
    without_event.events = []
    comparison = LiquidSimulation(without_event)
    frames = [sim.latest_frame]
    while not sim.done:
        frames.append(sim.step())
        comparison.step()

    assert [f["seq"] for f in frames] == list(range(7))
    assert [f["time_s"] for f in frames] == pytest.approx(range(7))
    assert frames[0]["node_temperature_K"][0] == pytest.approx(298.15)
    assert frames[2]["edge_flow_kg_s"][-1] < frames[1]["edge_flow_kg_s"][-1]
    assert frames[3]["diagnostics"]["thermal"]["heat_input_W"] == pytest.approx(900.0)
    assert frames[-1]["chip_temp_K"][0] > comparison.latest_frame["chip_temp_K"][0]
    assert sim.result()["converged"] is True


def test_pressure_pid_changes_pump_within_rate_and_output_limits():
    """Ignoring PID output or its slew limit must fail this comparison."""
    from backend.liquidcooling.runtime import LiquidSimulation

    cfg = runtime_config(samples=5)
    baseline = LiquidSimulation(cfg)
    measured = (baseline.latest_frame["node_pressure_Pa"][0]
                - baseline.latest_frame["node_pressure_Pa"][3])
    loop = controller(setpoint=measured * 0.5)
    loop["pid"]["ki"] = 0.001
    closed = LiquidSimulation(cfg, [loop])
    f1 = closed.step()
    baseline_f1 = baseline.step()
    command = f1["controls"]["dp"]["output"]

    assert 0.2 <= command <= 1.0
    assert command == pytest.approx(0.9)
    assert f1["edge_flow_kg_s"][-1] != pytest.approx(baseline_f1["edge_flow_kg_s"][-1])
    assert f1["controls"]["dp"]["rate_limited"] is True
    while not closed.done:
        closed.step()
    final = closed.latest_frame
    final_dp = final["node_pressure_Pa"][0] - final["node_pressure_Pa"][3]
    assert abs(loop["pid"]["setpoint"] - final_dp) < abs(
        loop["pid"]["setpoint"] - measured
    )


def test_checkpoint_restore_reproduces_history_and_future_controller_state():
    """Resetting thermal/PID state on restore must diverge from uninterrupted frames."""
    from backend.liquidcooling.runtime import LiquidSimulation

    sim = LiquidSimulation(runtime_config(), [controller()])
    sim.step()
    sim.step()
    restored = LiquidSimulation.from_checkpoint(sim.checkpoint())

    while not sim.done:
        sim.step()
        restored.step()

    assert restored.result() == sim.result()
    assert restored.checkpoint()["controller_states"] == sim.checkpoint()["controller_states"]

    damaged = copy.deepcopy(sim.checkpoint())
    damaged["liquidrun"]["fluid"]["rho_kg_m3"] += 1.0
    with pytest.raises(ValueError, match="hash"):
        LiquidSimulation.from_checkpoint(damaged)

    malformed_state = sim.checkpoint()
    malformed_state["state"]["m"].pop()
    with pytest.raises(ValueError, match="checkpoint state"):
        LiquidSimulation.from_checkpoint(malformed_state)



@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw, ctrls: ctrls[0]["measurement"].update(target_id="missing"), "target"),
        (lambda raw, ctrls: ctrls.append({
            **copy.deepcopy(ctrls[0]), "id": "duplicate-binding"
        }), "actuator"),
        (lambda raw, ctrls: raw["events"].append(
            {"time_s": 1.5, "target": "cold_plate_1", "field": "Q_W", "value": 10}), "grid"),
        (lambda raw, ctrls: raw["events"].append(
            {"time_s": 1.0, "target": "pump", "field": "rpm_ratio", "value": .5}), "actuator"),
    ],
)
def test_invalid_bindings_and_off_grid_events_fail_before_solve(mutate, message):
    """Removing eager runtime validation must expose ambiguous or floored inputs."""
    from backend.liquidcooling.runtime import LiquidSimulation

    raw = runtime_config().model_dump(by_alias=True)
    ctrls = [controller()]
    mutate(raw, ctrls)
    with pytest.raises(ValueError, match=message):
        LiquidSimulation(LiquidrunConfig.model_validate(raw), ctrls)


def test_hash_covers_controller_definition_and_copied_config():
    """Hashing only physical parameters or retaining caller aliases must fail."""
    from backend.liquidcooling.runtime import LiquidSimulation

    cfg = runtime_config(samples=4)
    a = LiquidSimulation(cfg, [controller(30000.0)])
    b = LiquidSimulation(cfg, [controller(31000.0)])
    before = a.config_hash
    cfg.edges[-1].params["rpm_ratio"] = 0.2

    assert a.config_hash == before
    assert a.config_hash != b.config_hash
    assert a.metadata["cp_edge_ids"] == ["cold_plate_1"]


def test_metadata_preserves_frontend_object_bindings():
    """Dropping UID-to-object linkage must fail renderer metadata consumption."""
    from backend.liquidcooling.runtime import LiquidSimulation

    raw = runtime_config(samples=3).model_dump(by_alias=True)
    raw["object_bindings"] = {
        "pump-uid": {
            "label": "CDU Pump A", "kind": "pump",
            "node_ids": ["return", "supply"], "edge_ids": ["pump"],
        }
    }
    simulation = LiquidSimulation(LiquidrunConfig.model_validate(raw))
    assert simulation.metadata["object_bindings"] == raw["object_bindings"]


def test_session_limits_reject_unbounded_horizon_before_graph_allocation():
    """Removing the pre-compile sample bound must permit an unsafe tensor allocation."""
    from backend.liquidcooling.runtime import LiquidSimulation, MAX_SAMPLES

    raw = runtime_config(samples=3).model_dump(by_alias=True)
    raw["time"]["N_t"] = MAX_SAMPLES + 1
    with pytest.raises(ValueError, match="session limit"):
        LiquidSimulation(LiquidrunConfig.model_validate(raw))


def test_nonfinite_derived_horizon_fails_before_graph_allocation():
    """An overflowing dt times sample count must never reach tensor compilation."""
    from backend.liquidcooling.runtime import LiquidSimulation

    raw = runtime_config(samples=3).model_dump(by_alias=True)
    raw["time"]["dt_s"] = 1e308
    with pytest.raises(ValueError, match="horizon"):
        LiquidSimulation(LiquidrunConfig.model_validate(raw))
