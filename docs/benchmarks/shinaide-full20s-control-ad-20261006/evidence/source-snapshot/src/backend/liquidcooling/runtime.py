"""Deterministic closed-loop state machine for dcdtuni liquid simulations.

The runtime owns cloned, resolved input data.  It applies simulated controls only
to a one-sample graph and never writes to BMS or plant equipment.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import uuid
from typing import Any

import torch
import numpy as np
from backend.liquidcooling.postprocess.summaries import build_rack_summaries, summaries_json

from backend.liquidcooling.control import (
    PIDConfig,
    PIDState,
    initial_pid_state,
    pid_step,
)
from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.graphcompiling.time_slice import time_slice
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.solver.newton_hydraulic import gga_solve
from backend.liquidcooling.solver.thermal_step import thermal_step


def _environment_limit(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


MAX_SAMPLES = _environment_limit("DCDTUNI_LIQUID_MAX_SAMPLES", 10_000)
MAX_NODES = _environment_limit("DCDTUNI_LIQUID_MAX_NODES", 5_000)
MAX_EDGES = _environment_limit("DCDTUNI_LIQUID_MAX_EDGES", 20_000)
MAX_THERMAL_CELLS = _environment_limit("DCDTUNI_LIQUID_MAX_THERMAL_CELLS", 200_000)
MAX_EXPANDED_VALUES = _environment_limit("DCDTUNI_LIQUID_MAX_EXPANDED_VALUES", 5_000_000)
_MEASUREMENTS = {
    "node_temperature", "solid_temperature", "pressure_difference", "mass_flow"
}
_ACTUATORS = {"rpm_ratio": "pump", "opening": "valve"}


class LiquidRuntimeError(RuntimeError):
    """A numerical step failed before it could be committed."""


def _canonical_hash(liquidrun: dict, controllers: list[dict]) -> str:
    payload = {"liquidrun": liquidrun, "controllers": controllers}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, torch.Tensor):
        value = value.detach().cpu().tolist()
    elif isinstance(value, dict):
        value = {str(k): _json_value(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [_json_value(v) for v in value]
    elif isinstance(value, float) and not math.isfinite(value):
        raise LiquidRuntimeError("simulation produced a non-finite JSON value")
    return value


def _state_to_json(state: PIDState) -> dict:
    def scalar(value):
        return None if value is None else float(value.detach().cpu())
    return {
        "integral": scalar(state.integral),
        "derivative": scalar(state.derivative),
        "last_output": scalar(state.last_output),
        "last_measurement": scalar(state.last_measurement),
        "tracking_setpoint": scalar(state.tracking_setpoint),
    }


def _state_from_json(data: dict) -> PIDState:
    def tensor(value):
        return None if value is None else torch.tensor(value, dtype=torch.float64)
    return PIDState(
        integral=tensor(data["integral"]),
        derivative=tensor(data["derivative"]),
        last_output=tensor(data["last_output"]),
        last_measurement=tensor(data.get("last_measurement")),
        tracking_setpoint=tensor(data.get("tracking_setpoint")),
    )


class LiquidSimulation:
    """One immutable-input liquid simulation advanced one configured sample at a time."""

    def __init__(
        self,
        config: LiquidrunConfig,
        controllers: list[dict] | None = None,
        run_id: str | None = None,
    ):
        if not isinstance(config, LiquidrunConfig):
            raise TypeError("config must be a LiquidrunConfig")
        from backend.liquidcooling.liquidrun_schema import resolve_run_input
        config, controllers = resolve_run_input(config, controllers)
        if config.version == '2.0':
            torch.set_num_threads(1)
        resolved = config.model_dump(by_alias=True, mode="json")
        self._config = LiquidrunConfig.model_validate(copy.deepcopy(resolved))
        self._liquidrun = self._config.model_dump(by_alias=True, mode="json")
        self._controllers = copy.deepcopy(controllers or [])
        self._validate_inputs()
        self._public_controllers = [
            {key: copy.deepcopy(value) for key, value in definition.items()
             if not key.startswith("_")}
            for definition in self._controllers
        ]
        self._config_hash = _canonical_hash(self._liquidrun, self._public_controllers)
        self._run_id = run_id or uuid.uuid4().hex
        if not isinstance(self._run_id, str) or not self._run_id:
            raise ValueError("run_id must be a non-empty string")

        self._graph = compile_graph(self._config, device="cpu")
        self._node_index = {name: i for i, name in enumerate(self._graph.node_ids)}
        self._edge_index = {name: i for i, name in enumerate(self._graph.edge_str_ids)}
        self._cp_local = {}
        if self._graph.cp_indices is not None:
            self._cp_local = {
                self._graph.edge_str_ids[int(edge)]: i
                for i, edge in enumerate(self._graph.cp_indices.edge_ids)
            }

        self._seq = 0
        self._event_index = sum(
            1 for event in self._config.events if event.time_s <= 0.0
        )
        self._T_node = torch.full(
            (self._graph.N_nodes,), self._graph.T_fluid_init, dtype=torch.float64
        )
        if self._graph.bc_T_mask.any():
            self._T_node = torch.where(
                self._graph.bc_T_mask, self._graph.bc_T, self._T_node
            )
        if self._graph.cell_T_init is None:
            self._T_w = torch.full(
                (self._graph.N_cells,), self._graph.T_fluid_init, dtype=torch.float64
            )
        else:
            self._T_w = self._graph.cell_T_init.clone().to(dtype=torch.float64)
        self._T_s = torch.full(
            (self._graph.N_cp,), self._graph.T_solid_init, dtype=torch.float64
        )

        graph0 = self._step_graph(0, {})
        self._P, self._m, hydraulic = self._solve_hydraulic(graph0, None)
        self._edge_outlet = self._T_node[
            torch.where(self._m >= 0, graph0.edge_from, graph0.edge_to)
        ].clone()
        self._controller_configs: dict[str, PIDConfig] = {}
        self._controller_states: dict[str, PIDState] = {}
        initial_controls = {}
        for definition in self._controllers:
            loop_id = definition["id"]
            pid_config = PIDConfig(**definition["pid"])
            measurement = self._measure(definition, self._P, self._m, self._T_node, self._T_s)
            output = self._actuator_value(graph0, definition)
            self._controller_configs[loop_id] = pid_config
            self._controller_states[loop_id] = initial_pid_state(
                pid_config, measurement=measurement, output=output
            )
            initial_controls[loop_id] = {
                "measurement": measurement,
                "setpoint": pid_config.setpoint,
                "output": output,
                "saturated": False,
                "rate_limited": False,
            }

        frame0 = self._frame(
            hydraulic=hydraulic,
            thermal={},
            controls=initial_controls,
        )
        self._frames = [frame0]
        self._cell_history = [_json_value(self._T_w)]
        self._controller_history = [copy.deepcopy(initial_controls)]

    @property
    def config_hash(self) -> str:
        return self._config_hash

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def done(self) -> bool:
        return self._seq >= self._config.time.N_t - 1

    @property
    def latest_frame(self) -> dict:
        return copy.deepcopy(self._frames[-1])

    @property
    def metadata(self) -> dict:
        return {
            "object_bindings": copy.deepcopy(self._liquidrun.get("object_bindings", {})),
            "node_ids": list(self._graph.node_ids),
            "edge_ids": list(self._graph.edge_str_ids),
            "cp_edge_ids": list(self._cp_local),
            "total_sample_count": self._config.time.N_t,
            "dt_s": self._config.time.dt_s,
            "config_hash": self.config_hash,
            "parameter_provenance": copy.deepcopy(self._config.parameter_provenance),
        }

    @property
    def frames(self) -> list[dict]:
        return copy.deepcopy(self._frames)

    def _validate_inputs(self) -> None:
        dt = _finite_number(self._config.time.dt_s, "time.dt_s")
        if dt <= 0:
            raise ValueError("time.dt_s must be positive")
        if self._config.time.N_t < 1:
            raise ValueError("time.N_t must be at least one")
        node_ids = [node.id for node in self._config.nodes]
        edge_ids = [edge.id for edge in self._config.edges]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("node IDs must be unique")
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("edge IDs must be unique")
        if self._config.time.N_t > MAX_SAMPLES:
            raise ValueError(
                f"time.N_t exceeds the session limit of {MAX_SAMPLES} samples"
            )
        horizon = dt * (self._config.time.N_t - 1)
        if not math.isfinite(horizon):
            raise ValueError("derived simulation horizon must be finite")
        if len(self._config.nodes) > MAX_NODES:
            raise ValueError(f"node count exceeds the session limit of {MAX_NODES}")
        if len(self._config.edges) > MAX_EDGES:
            raise ValueError(f"edge count exceeds the session limit of {MAX_EDGES}")
        thermal_cells = 0
        scheduled_components = 0
        for edge in self._config.edges:
            if edge.type == "pipe":
                raw_cells = edge.params.get("n_cells")
                if isinstance(raw_cells, bool) or not isinstance(raw_cells, int) or raw_cells <= 0:
                    raise ValueError(f"pipe {edge.id} n_cells must be a positive integer")
                thermal_cells += raw_cells
            if edge.type in {"pump", "valve", "cold_plate"}:
                scheduled_components += 1
        if thermal_cells > MAX_THERMAL_CELLS:
            raise ValueError(
                f"thermal cell count exceeds the session limit of {MAX_THERMAL_CELLS}"
            )
        expanded = self._config.time.N_t * (thermal_cells + scheduled_components)
        if expanded > MAX_EXPANDED_VALUES:
            raise ValueError(
                "time-expanded parameter count exceeds the session limit of "
                f"{MAX_EXPANDED_VALUES}"
            )
        nodes = set(node_ids)
        edges = {edge.id: edge for edge in self._config.edges}
        cp_ids = {edge.id for edge in self._config.edges if edge.type == "cold_plate"}

        event_actuators = set()
        horizon = (self._config.time.N_t - 1) * dt
        for event in self._config.events:
            when = _finite_number(event.time_s, f"event {event.target} time")
            quotient = when / dt
            if when < 0 or when > horizon:
                raise ValueError(f"event {event.target} time is outside the configured horizon")
            if not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(f"event {event.target} time must lie on the dt grid")
            if event.target not in edges:
                raise ValueError(f"event target {event.target!r} is unknown")
            expected = {"rpm_ratio": "pump", "opening": "valve", "Q_W": "cold_plate"}
            if event.field not in expected or edges[event.target].type != expected[event.field]:
                raise ValueError(
                    f"event field {event.field!r} is invalid for target {event.target!r}"
                )
            _finite_number(event.value, f"event {event.target}.{event.field}")
            if event.field in _ACTUATORS:
                event_actuators.add((event.target, event.field))

        loop_ids = set()
        actuator_bindings = set()
        normalized = []
        for original in self._controllers:
            if not isinstance(original, dict):
                raise ValueError("controller definitions must be objects")
            definition = copy.deepcopy(original)
            loop_id = definition.get("id")
            if not isinstance(loop_id, str) or not loop_id or loop_id in loop_ids:
                raise ValueError("controller id must be non-empty and unique")
            loop_ids.add(loop_id)
            measurement = definition.get("measurement")
            actuator = definition.get("actuator")
            if not isinstance(measurement, dict) or not isinstance(actuator, dict):
                raise ValueError(f"controller {loop_id} requires measurement and actuator objects")
            kind = measurement.get("kind")
            target = measurement.get("target_id")
            if kind not in _MEASUREMENTS:
                raise ValueError(f"controller {loop_id} measurement kind is invalid")
            if kind in {"node_temperature", "pressure_difference"}:
                if target not in nodes:
                    raise ValueError(f"controller {loop_id} measurement target is unknown")
            elif kind == "solid_temperature":
                if target not in cp_ids:
                    raise ValueError(f"controller {loop_id} measurement target is not a cold plate")
            elif target not in edges:
                raise ValueError(f"controller {loop_id} measurement target is unknown")
            if kind == "pressure_difference" and measurement.get("reference_id") not in nodes:
                raise ValueError(f"controller {loop_id} measurement reference is unknown")
            edge_id, field = actuator.get("edge_id"), actuator.get("field")
            if field not in _ACTUATORS or edge_id not in edges:
                raise ValueError(f"controller {loop_id} actuator binding is invalid")
            if edges[edge_id].type != _ACTUATORS[field]:
                raise ValueError(f"controller {loop_id} actuator field does not match edge type")
            binding = (edge_id, field)
            if binding in actuator_bindings:
                raise ValueError(f"duplicate actuator binding for {edge_id}.{field}")
            if binding in event_actuators:
                raise ValueError(f"controller actuator {edge_id}.{field} conflicts with an event")
            actuator_bindings.add(binding)
            period = _finite_number(
                definition.get("sample_period_s"), f"controller {loop_id} sample_period_s"
            )
            steps = period / dt
            if period <= 0 or not math.isclose(steps, round(steps), rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(f"controller {loop_id} sample period must be on the dt grid")
            try:
                PIDConfig(**definition.get("pid", {}))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"controller {loop_id} PID is invalid: {exc}") from exc
            definition["_sample_steps"] = int(round(steps))
            normalized.append(definition)
        self._controllers = normalized

    def _step_graph(self, time_index: int, commands: dict[tuple[str, str], float]):
        graph = time_slice(self._graph, time_index)

        for (edge_id, field), output in commands.items():
            global_index = graph.edge_str_ids.index(edge_id)
            if field == "rpm_ratio":
                local = int((graph.pump_indices.edge_ids == global_index).nonzero()[0])
                graph.pump_params.rpm_ratio[local, 0] = output
            else:
                local = int((graph.valve_indices.edge_ids == global_index).nonzero()[0])
                graph.valve_params.opening[local, 0] = output
        return graph

    def _solve_hydraulic(self, graph, warm_start):
        pressure, flow, info = gga_solve(graph, m_init=warm_start)
        step_info = info["per_step"][0]
        if not info["converged"] or not step_info["converged"]:
            raise LiquidRuntimeError(
                "hydraulic solve failed: " + str(step_info.get("reason", "nonconvergence"))
            )
        P, m = pressure[:, 0].clone(), flow[:, 0].clone()
        if not bool(torch.isfinite(P).all() and torch.isfinite(m).all()):
            raise LiquidRuntimeError("hydraulic solve produced non-finite state")
        return P, m, _json_value(step_info)

    def _actuator_value(self, graph, definition) -> float:
        edge_id = definition["actuator"]["edge_id"]
        field = definition["actuator"]["field"]
        global_index = graph.edge_str_ids.index(edge_id)
        if field == "rpm_ratio":
            local = int((graph.pump_indices.edge_ids == global_index).nonzero()[0])
            return float(graph.pump_params.rpm_ratio[local, 0])
        local = int((graph.valve_indices.edge_ids == global_index).nonzero()[0])
        return float(graph.valve_params.opening[local, 0])

    def _measure(self, definition, P, m, T_node, T_s) -> float:
        measurement = definition["measurement"]
        kind, target = measurement["kind"], measurement["target_id"]
        if kind == "node_temperature":
            return float(T_node[self._node_index[target]])
        if kind == "solid_temperature":
            return float(T_s[self._cp_local[target]])
        if kind == "mass_flow":
            return float(m[self._edge_index[target]])
        reference = measurement["reference_id"]
        return float(P[self._node_index[target]] - P[self._node_index[reference]])

    def _frame(self, *, hydraulic: dict, thermal: dict, controls: dict) -> dict:
        chip = _json_value(self._T_s)
        frame = {
            "kind": "frame",
            "run_id": self.run_id,
            "seq": self._seq,
            "time_s": self._seq * self._config.time.dt_s,
            "node_pressure_Pa": _json_value(self._P),
            "node_temperature_K": _json_value(self._T_node),
            "edge_flow_kg_s": _json_value(self._m),
            "chip_temp_K": chip,
            "cp_edge_ids": list(self._cp_local),
            "cell_positions": self._graph.cell_position.tolist(),
            "edge_outlet_temp_K": _json_value(self._edge_outlet),
            "controls": _json_value(controls),
            "diagnostics": {
                "hydraulic": _json_value(hydraulic),
                "thermal": _json_value(thermal),
            },
        }
        json.dumps(frame, allow_nan=False)
        return frame

    def step(self) -> dict:
        if self.done:
            raise RuntimeError("simulation is already complete")
        next_seq = self._seq + 1
        next_states = dict(self._controller_states)
        controls = {}
        commands = {}
        for definition in self._controllers:
            loop_id = definition["id"]
            config = self._controller_configs[loop_id]
            measurement = self._measure(
                definition, self._P, self._m, self._T_node, self._T_s
            )
            if next_seq % definition["_sample_steps"] == 0:
                result = pid_step(
                    config, self._controller_states[loop_id], measurement,
                    definition["sample_period_s"],
                )
                next_states[loop_id] = result.state
                output = float(result.output)
                saturated, rate_limited = result.saturated, result.rate_limited
            else:
                output = float(self._controller_states[loop_id].last_output)
                saturated = rate_limited = False
            actuator = definition["actuator"]
            commands[(actuator["edge_id"], actuator["field"])] = output
            controls[loop_id] = {
                "measurement": measurement,
                "setpoint": config.setpoint,
                "output": output,
                "saturated": saturated,
                "rate_limited": rate_limited,
            }

        graph = self._step_graph(next_seq, commands)
        P, m, hydraulic = self._solve_hydraulic(graph, self._m)
        with torch.no_grad():
            try:
                thermal = thermal_step(
                    graph, m, self._T_node, self._T_w, self._T_s,
                    dt=self._config.time.dt_s, time_index=0,
                )
            except Exception as exc:
                raise LiquidRuntimeError(f"thermal step failed: {exc}") from exc
        if not all(bool(torch.isfinite(value).all()) for value in (
            thermal.T_node, thermal.T_w, thermal.T_s, thermal.edge_outlet
        )):
            raise LiquidRuntimeError("thermal step produced non-finite state")

        self._seq = next_seq
        self._P, self._m = P, m
        self._T_node = thermal.T_node.clone()
        self._T_w = thermal.T_w.clone()
        self._T_s = thermal.T_s.clone()
        self._edge_outlet = thermal.edge_outlet.clone()
        self._controller_states = next_states
        self._event_index = sum(
            1 for event in self._config.events
            if event.time_s <= self._seq * self._config.time.dt_s
        )
        frame = self._frame(
            hydraulic=hydraulic,
            thermal=thermal.diagnostics,
            controls=controls,
        )
        self._frames.append(frame)
        self._cell_history.append(_json_value(self._T_w))
        self._controller_history.append(copy.deepcopy(controls))
        return copy.deepcopy(frame)

    def checkpoint(self) -> dict:
        snapshot = {
            "format": "dcdtuni-liquid-checkpoint-v1",
            "run_id": self.run_id,
            "config_hash": self.config_hash,
            "liquidrun": copy.deepcopy(self._liquidrun),
            "controllers": copy.deepcopy(self._public_controllers),
            "seq": self._seq,
            "event_index": self._event_index,
            "state": {
                "P": _json_value(self._P),
                "m": _json_value(self._m),
                "T_node": _json_value(self._T_node),
                "T_w": _json_value(self._T_w),
                "T_s": _json_value(self._T_s),
                "edge_outlet": _json_value(self._edge_outlet),
            },
            "controller_states": {
                loop_id: _state_to_json(state)
                for loop_id, state in self._controller_states.items()
            },
            "frames": copy.deepcopy(self._frames),
            "cell_history": copy.deepcopy(self._cell_history),
            "controller_history": copy.deepcopy(self._controller_history),
        }
        json.dumps(snapshot, allow_nan=False)
        return snapshot

    @classmethod
    def from_checkpoint(cls, checkpoint: dict):
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be an object")
        try:
            json.dumps(checkpoint, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"malformed checkpoint: {exc}") from exc
        try:
            config = LiquidrunConfig.model_validate(checkpoint["liquidrun"])
            simulation = cls(
                config, checkpoint.get("controllers", []), run_id=checkpoint["run_id"]
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed checkpoint: {exc}") from exc
        if simulation.config_hash != checkpoint.get("config_hash"):
            raise ValueError("checkpoint input hash does not match resolved input")
        seq = checkpoint.get("seq")
        if not isinstance(seq, int) or not 0 <= seq < simulation._config.time.N_t:
            raise ValueError("checkpoint seq is invalid")
        state = checkpoint.get("state", {})
        try:
            simulation._P = torch.tensor(state["P"], dtype=torch.float64)
            simulation._m = torch.tensor(state["m"], dtype=torch.float64)
            simulation._T_node = torch.tensor(state["T_node"], dtype=torch.float64)
            simulation._T_w = torch.tensor(state["T_w"], dtype=torch.float64)
            simulation._T_s = torch.tensor(state["T_s"], dtype=torch.float64)
            simulation._edge_outlet = torch.tensor(state["edge_outlet"], dtype=torch.float64)
            simulation._controller_states = {
                loop_id: _state_from_json(checkpoint["controller_states"][loop_id])
                for loop_id in simulation._controller_states
            }
            restored_tensors = (
                ("P", simulation._P, simulation._graph.N_nodes),
                ("m", simulation._m, simulation._graph.N_edges),
                ("T_node", simulation._T_node, simulation._graph.N_nodes),
                ("T_w", simulation._T_w, simulation._graph.N_cells),
                ("T_s", simulation._T_s, simulation._graph.N_cp),
                ("edge_outlet", simulation._edge_outlet, simulation._graph.N_edges),
            )
            for name, tensor, size in restored_tensors:
                if tensor.shape != (size,):
                    raise ValueError(
                        f"{name} has shape {tuple(tensor.shape)}, expected {(size,)}"
                    )
                if not bool(torch.isfinite(tensor).all()):
                    raise ValueError(f"{name} contains non-finite values")
            for state_value in simulation._controller_states.values():
                values = [
                    state_value.integral, state_value.derivative, state_value.last_output,
                    state_value.last_measurement, state_value.tracking_setpoint,
                ]
                if any(
                    value is not None
                    and (value.ndim != 0 or not bool(torch.isfinite(value)))
                    for value in values
                ):
                    raise ValueError("controller state must contain finite scalars")
            simulation._frames = copy.deepcopy(checkpoint["frames"])
            simulation._cell_history = copy.deepcopy(checkpoint["cell_history"])
            simulation._controller_history = copy.deepcopy(checkpoint["controller_history"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed checkpoint state: {exc}") from exc
        if (
            len(simulation._frames) != seq + 1
            or any(
                frame.get("seq") != index or frame.get("run_id") != simulation.run_id
                for index, frame in enumerate(simulation._frames)
            )
        ):
            raise ValueError("checkpoint frame history is inconsistent with seq")
        if (
            len(simulation._cell_history) != seq + 1
            or any(len(sample) != simulation._graph.N_cells for sample in simulation._cell_history)
            or len(simulation._controller_history) != seq + 1
        ):
            raise ValueError("checkpoint retained history is inconsistent with seq")
        expected_events = sum(
            1 for event in simulation._config.events
            if event.time_s <= seq * simulation._config.time.dt_s
        )
        if checkpoint.get("event_index") != expected_events:
            raise ValueError("checkpoint event index is inconsistent with seq")
        simulation._seq = seq
        simulation._event_index = expected_events
        return simulation

    def result(self) -> dict:
        def transpose(key):
            return [
                [frame[key][index] for frame in self._frames]
                for index in range(len(self._frames[0][key]))
            ]

        node_pressure = transpose("node_pressure_Pa")
        node_temperature = transpose("node_temperature_K")
        edge_flow = transpose("edge_flow_kg_s")
        chip = transpose("chip_temp_K")
        edge_outlet = transpose("edge_outlet_temp_K")
        cp_details = []
        for cp_id, local in self._cp_local.items():
            edge = self._edge_index[cp_id]
            from_node = int(self._graph.edge_from[edge])
            to_node = int(self._graph.edge_to[edge])
            inlet_T, outlet_T, inlet_P, outlet_P = [], [], [], []
            for frame in self._frames:
                forward = frame["edge_flow_kg_s"][edge] >= 0
                upstream, downstream = (
                    (from_node, to_node) if forward else (to_node, from_node)
                )
                inlet_T.append(frame["node_temperature_K"][upstream])
                outlet_T.append(frame["edge_outlet_temp_K"][edge])
                inlet_P.append(frame["node_pressure_Pa"][upstream])
                outlet_P.append(frame["node_pressure_Pa"][downstream])
            cp_details.append({
                "edge_id": cp_id,
                "chip_temp_K": chip[local],
                "water_inlet_temp_K": inlet_T,
                "water_outlet_temp_K": outlet_T,
                "inlet_pressure_Pa": inlet_P,
                "outlet_pressure_Pa": outlet_P,
            })

        hydraulic_steps = [
            frame["diagnostics"]["hydraulic"] for frame in self._frames
        ]
        result = {
            "run_id": self.run_id,
            "config_hash": self.config_hash,
            "status": "completed" if self.done else "partial",
            "completed": self.done,
            "converged": all(step.get("converged", False) for step in hydraulic_steps),
            "final_loss": max((abs(step.get("max_dm", 0.0)) for step in hydraulic_steps), default=0.0),
            "epochs": sum(int(step.get("iterations", 0)) for step in hydraulic_steps),
            "node_ids": list(self._graph.node_ids),
            "node_pressure_Pa": node_pressure,
            "node_temperature_K": node_temperature,
            "edge_ids": list(self._graph.edge_str_ids),
            "edge_flow_kg_s": edge_flow,
            "cell_temp_K": [
                [sample[index] for sample in self._cell_history]
                for index in range(self._graph.N_cells)
            ],
            "chip_temp_K": chip,
            "water_outlet_temp_K": [
                edge_outlet[self._edge_index[cp_id]] for cp_id in self._cp_local
            ],
            "edge_outlet_temp_K": edge_outlet,
            "time_s": [frame["time_s"] for frame in self._frames],
            "cp_details": cp_details,
            "controls": copy.deepcopy(self._controller_history),
            "diagnostics": [copy.deepcopy(frame["diagnostics"]) for frame in self._frames],
            "parameter_provenance": copy.deepcopy(self._config.parameter_provenance),
            "object_bindings": copy.deepcopy(self._liquidrun.get("object_bindings", {})),
            "fluid": copy.deepcopy(self._liquidrun["fluid"]),
            "cell_edge_ids": [self._graph.edge_str_ids[int(self._graph.pipe_indices.edge_ids[i])]
                              for i in self._graph.cell_edge_id.tolist()],
            "rack_summaries": summaries_json(build_rack_summaries(
                self._graph, {"m": np.asarray(edge_flow)},
                np.asarray([edge_outlet[self._edge_index[cp_id]] for cp_id in self._cp_local]),
                np.asarray(chip), np.asarray(node_temperature))),
        }
        json.dumps(result, allow_nan=False)
        return result
