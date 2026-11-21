"""Versioned, self-contained liquid execution manifest.

The editor owns .cfdproj. This module validates its derived .liquidrun v2
snapshot before adapting to the existing numerical compiler's models. The
legacy ``schema.LiquidrunConfig`` remains deliberately permissive for v1 files;
new execution entry points must call ``resolve_run_input`` here.

JSON Schema checks shape, types, units and scalar ranges. Python additionally
checks graph references, profile resolution, sample grids, series dimensions
and mutually exclusive actuator ownership. No tensors are allocated here.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.liquidcooling.parameters import ParameterPack, _digest, resolve_parameter_pack
from backend.liquidcooling.schema import LiquidrunConfig


Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Positive = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Ratio = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Identifier = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
Vector3 = Annotated[list[Number], Field(min_length=3, max_length=3)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                              populate_by_name=True)


class SourceProject(StrictModel):
    format: Literal["cfdproj"]
    schema_version: Literal[4, 5]  # 5: cfdproj v5 (transient keyframes)
    project_uid: Identifier
    project_name: Identifier
    revision: NonnegativeInt | None = None
    sha256: Hash | None = None


class Units(StrictModel):
    length: Literal["m"]
    time: Literal["s"]
    temperature: Literal["K"]
    pressure: Literal["Pa"]
    mass_flow: Literal["kg/s"]
    power: Literal["W"]


class Execution(StrictModel):
    backend: Literal["pytorch"]
    device: Literal["cpu"]
    dtype: Literal["float64"]
    max_threads: Annotated[int, Field(strict=True, ge=1, le=1)]


class Fluid(StrictModel):
    rho_kg_m3: Positive
    cp_J_kgK: Positive
    mu_Pa_s: Positive
    k_W_mK: Positive


class Time(StrictModel):
    dt_s: Positive
    N_t: PositiveInt
    # Current runtime is first-order thermal marching. Do not claim BDF2.
    bdf_order: Literal[1]

    @model_validator(mode="before")
    @classmethod
    def literal_is_an_integer(cls, raw):
        if isinstance(raw, dict) and type(raw.get("bdf_order")) is not int:
            raise ValueError("bdf_order must be the integer 1")
        return raw


class NodeBC(StrictModel):
    P_Pa: Number
    T_K: Positive | None = None


class Node(StrictModel):
    id: Identifier
    type: Literal["pressure_bc", "junction"]
    position_m: Vector3
    bc: NodeBC | None = None

    @model_validator(mode="after")
    def boundary_kind(self):
        if (self.type == "pressure_bc") != (self.bc is not None):
            raise ValueError("pressure_bc nodes require bc; junction nodes must not have bc")
        return self


class PartialParams(StrictModel):
    """Omitted fields may come from an embedded profile; explicit null is invalid."""

    @model_validator(mode="before")
    @classmethod
    def no_null_values(cls, raw):
        if isinstance(raw, dict) and any(value is None for value in raw.values()):
            raise ValueError("physical parameters cannot be null")
        return raw


class PipeParams(PartialParams):
    D_m: Positive | None = None
    roughness_m: Nonnegative | None = None
    n_cells: PositiveInt | None = None
    waypoints_m: Annotated[list[Vector3], Field(min_length=2)] | None = None
    K_minor_per_bend: Nonnegative | None = None
    # Absent stays absent in legacy snapshots; the compiler applies effective zero.
    K_minor_additional: Nonnegative | None = None
    heat_source_W_per_m: Number | list[Number | list[Number]] | None = None


class PumpParams(PartialParams):
    H0_m: Positive | None = None
    a_coef: Nonnegative | None = None
    rpm_ratio: Nonnegative | list[Nonnegative] | None = None


class ValveParams(PartialParams):
    D_m: Positive | None = None
    Kv_max: Positive | None = None
    opening: Ratio | list[Ratio] | None = None
    characteristic: Literal["linear", "equal_percentage", "quick_opening"] | None = None


class ColdPlateParams(PartialParams):
    dp_a: Nonnegative | None = None
    dp_b: Nonnegative | None = None
    UA_W_per_K: Positive | None = None
    C_solid_J_per_K: Positive | None = None
    V_fluid_m3: Positive | None = None
    Q_W: Nonnegative | list[Nonnegative] | None = None


class HeatExchangerParams(PartialParams):
    dp_a: Nonnegative | None = None
    dp_b: Nonnegative | None = None
    UA_W_per_K: Positive | None = None
    T_secondary_K: Positive | None = None


class EdgeBase(StrictModel):
    id: Identifier
    from_node: Identifier = Field(alias="from")
    to_node: Identifier = Field(alias="to")
    parameter_ref: Identifier | None = None


class PipeEdge(EdgeBase):
    type: Literal["pipe"]
    params: PipeParams


class PumpEdge(EdgeBase):
    type: Literal["pump"]
    params: PumpParams


class ValveEdge(EdgeBase):
    type: Literal["valve"]
    params: ValveParams


class ColdPlateEdge(EdgeBase):
    type: Literal["cold_plate"]
    params: ColdPlateParams


class HeatExchangerEdge(EdgeBase):
    type: Literal["heat_exchanger"]
    params: HeatExchangerParams


Edge = Annotated[PipeEdge | PumpEdge | ValveEdge | ColdPlateEdge | HeatExchangerEdge,
                 Field(discriminator="type")]
_PARAMS = {"pipe": PipeParams, "pump": PumpParams, "valve": ValveParams,
           "cold_plate": ColdPlateParams, "heat_exchanger": HeatExchangerParams}
_REQUIRED = {
    "pipe": {"D_m", "n_cells", "waypoints_m"},
    "pump": {"H0_m", "a_coef", "rpm_ratio"},
    "valve": {"D_m", "Kv_max", "opening", "characteristic"},
    "cold_plate": {"dp_a", "dp_b", "UA_W_per_K", "C_solid_J_per_K", "V_fluid_m3", "Q_W"},
    "heat_exchanger": {"dp_a", "dp_b", "UA_W_per_K", "T_secondary_K"},
}


class Event(StrictModel):
    time_s: Nonnegative
    target: Identifier
    field: Literal["rpm_ratio", "opening", "Q_W"]
    value: Nonnegative


class InitialConditions(StrictModel):
    T_fluid_K: Positive
    T_solid_K: Positive
    P_uniform_Pa: Number


class LegacyWeights(StrictModel):
    mass: Nonnegative
    momentum: Nonnegative
    energy: Nonnegative
    bc: Nonnegative
    ic: Nonnegative


class LegacySolverSettings(StrictModel):
    """Retained legacy ODIL settings; execution.backend selects the v2 forward path."""
    optimizer: Literal["adam"]
    lr: Positive
    max_epochs: PositiveInt
    convergence_tol: Positive
    weights: LegacyWeights


class Measurement(StrictModel):
    kind: Literal["node_temperature", "pressure_difference", "solid_temperature", "mass_flow"]
    target_id: Identifier
    reference_id: Identifier | None = None

    @model_validator(mode="after")
    def pressure_reference(self):
        if (self.kind == "pressure_difference") != (self.reference_id is not None):
            raise ValueError("only pressure_difference requires a measurement reference_id")
        return self


class Actuator(StrictModel):
    edge_id: Identifier
    field: Literal["rpm_ratio", "opening"]


class PID(StrictModel):
    kp: Nonnegative
    ki: Nonnegative
    kd: Nonnegative = 0.0
    setpoint: Number = 0.0
    bias: Number = 0.0
    output_min: Nonnegative = 0.0
    output_max: Positive = 1.0
    action: Literal["direct", "reverse"] = "reverse"
    rate_limit_per_s: Positive | None = None
    derivative_tau_s: Nonnegative = 0.0

    @model_validator(mode="after")
    def limits(self):
        if self.output_min >= self.output_max:
            raise ValueError("PID output_min must be below output_max")
        if not self.output_min <= self.bias <= self.output_max:
            raise ValueError("PID bias must lie within output limits")
        return self


class Controller(StrictModel):
    id: Identifier
    measurement: Measurement
    actuator: Actuator
    pid: PID
    sample_period_s: Positive


class ObjectBinding(StrictModel):
    label: str
    kind: Identifier
    node_ids: list[Identifier]
    edge_ids: list[Identifier]
    parent_uid: Identifier | None = None


class Pair(StrictModel):
    id: Identifier
    edge_id: Identifier
    Q_W: Nonnegative


class LegacyPair(StrictModel):
    server_id: Identifier
    cold_plate_edge_id: Identifier
    power_W: Nonnegative = 0.0
    T_inlet_K: Positive | None = None


class Server(StrictModel):
    id: Identifier
    slot_range: Annotated[list[Number], Field(min_length=2, max_length=2)] | None = None
    z_m: Number | None = None
    cp_cu_pairs: list[Pair] = Field(default_factory=list)
    inlet_node: Identifier | None = None
    outlet_node: Identifier | None = None
    # Explicit compatibility fields produced by the existing compiler model.
    slot_u: NonnegativeInt = 0
    span_u: PositiveInt = 1
    cp_cu: LegacyPair | None = None


class RackManifold(StrictModel):
    supply_node: Identifier
    return_node: Identifier
    layout: str
    D_m: Positive


class LegacyManifold(StrictModel):
    node_id: Identifier
    D_m: Positive = 0.019
    n_cells: PositiveInt = 3


class Rack(StrictModel):
    id: Identifier
    position_m: Vector3 | None = None
    n_slots: PositiveInt | None = None
    manifold: RackManifold | None = None
    servers: list[Server]
    supply_manifold: LegacyManifold | None = None
    return_manifold: LegacyManifold | None = None


class ProvenanceBinding(StrictModel):
    profile: Identifier
    # Audit-only copy; actual edge override values are checked via the typed edge.
    overrides: dict[str, Any]


class ProvenanceBindings(StrictModel):
    edges: dict[str, ProvenanceBinding]
    fluid: ProvenanceBinding | None = None


class ParameterProvenance(StrictModel):
    id: Identifier
    revision: Identifier
    source: Literal["mock", "vendor", "calibrated"]
    description: Identifier
    sha256: Hash
    resolved_sha256: Hash
    bindings: ProvenanceBindings


def _unique(values: list[str], location: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{location} IDs must be unique")


def _on_grid(value: float, dt: float, location: str) -> None:
    ratio = value / dt
    if not math.isfinite(ratio) or not math.isclose(ratio, round(ratio), rel_tol=0, abs_tol=1e-8):
        raise ValueError(f"{location} must lie on the dt grid")


class LiquidrunV2(StrictModel):
    format: Literal["liquidrun"]
    schemaVersion: Literal[2]
    version: Literal["2.0"]
    source: Identifier
    projectName: Identifier
    source_project: SourceProject
    units: Units
    execution: Execution
    gravity: Vector3
    fluid: Fluid
    time: Time
    nodes: Annotated[list[Node], Field(min_length=1)]
    edges: Annotated[list[Edge], Field(min_length=1)]
    racks: list[Rack]
    events: list[Event]
    initial_conditions: InitialConditions
    solver: LegacySolverSettings | None = None
    controllers: list[Controller]
    object_bindings: dict[Identifier, ObjectBinding]
    parameter_pack: ParameterPack | None = None
    parameter_provenance: ParameterProvenance | None = None

    @model_validator(mode="before")
    @classmethod
    def verify_incoming_provenance(cls, raw):
        # Verify before Pydantic normalizes integer JSON numbers into floats.
        # Otherwise a browser's `6` and Python's `6.0` change the digest even
        # though the input is unchanged. The loader rehashes the typed snapshot.
        if isinstance(raw, dict) and raw.get("parameter_provenance") and not raw.get("parameter_pack"):
            provenance = raw["parameter_provenance"]
            if isinstance(provenance, dict):
                effective = [{"id": edge.get("id"), "type": edge.get("type"), "params": edge.get("params", {})}
                             for edge in raw.get("edges", []) if isinstance(edge, dict)]
                effective.sort(key=lambda edge: str(edge["id"]))
                if _digest({"fluid": raw.get("fluid"), "edges": effective}) != provenance.get("resolved_sha256"):
                    raise ValueError("parameter_provenance resolved_sha256 does not match physical parameters")
        return raw

    @model_validator(mode="after")
    def validate_references_and_resolved_parameters(self):
        raw = self.model_dump(by_alias=True, mode="json", exclude_none=True)
        resolved = resolve_parameter_pack(raw)
        node_ids = [node.id for node in self.nodes]
        edge_ids = [edge.id for edge in self.edges]
        _unique(node_ids, "node")
        _unique(edge_ids, "edge")
        nodes, edges = set(node_ids), {edge.id: edge for edge in self.edges}
        if not any(node.type == "pressure_bc" for node in self.nodes):
            raise ValueError("at least one pressure_bc node is required")
        duration = self.time.dt_s * (self.time.N_t - 1)
        if not math.isfinite(duration):
            raise ValueError("derived time horizon must be finite")
        for edge in resolved["edges"]:
            eid, kind, params = edge["id"], edge["type"], edge["params"]
            if edge["from"] not in nodes or edge["to"] not in nodes:
                raise ValueError(f"edge {eid} refers to an unknown node")
            if edge["from"] == edge["to"]:
                raise ValueError(f"edge {eid} must connect distinct nodes")
            _PARAMS[kind].model_validate(params)
            missing = _REQUIRED[kind] - set(params)
            if missing:
                raise ValueError(f"edge {eid} is missing required parameters {sorted(missing)}")
            for key in ("rpm_ratio", "opening", "Q_W"):
                if isinstance(params.get(key), list) and len(params[key]) != self.time.N_t:
                    raise ValueError(f"edge {eid}.{key} series must contain exactly N_t samples")
            if kind == "pipe":
                points = params["waypoints_m"]
                if all(point == points[0] for point in points):
                    raise ValueError(f"pipe {eid} waypoints_m has zero route length")
                source = params.get("heat_source_W_per_m")
                if isinstance(source, list):
                    if len(source) != params["n_cells"]:
                        raise ValueError(f"pipe {eid} heat source must match n_cells")
                    if any(isinstance(row, list) and len(row) != self.time.N_t for row in source):
                        raise ValueError(f"pipe {eid} heat source rows must match N_t")

        expected_types = {"rpm_ratio": "pump", "opening": "valve", "Q_W": "cold_plate"}
        event_keys, event_actuators = set(), set()
        previous_time = -1.0
        for event in self.events:
            if event.time_s < previous_time:
                raise ValueError("events must be in chronological order")
            previous_time = event.time_s
            if event.time_s > duration:
                raise ValueError(f"event {event.target} is outside the time horizon")
            _on_grid(event.time_s, self.time.dt_s, "event time")
            if event.target not in edges or edges[event.target].type != expected_types[event.field]:
                raise ValueError(f"event target {event.target} does not support {event.field}")
            if event.field == "opening" and event.value > 1:
                raise ValueError("event opening value must lie between 0 and 1")
            key = (event.time_s, event.target, event.field)
            if key in event_keys:
                raise ValueError("duplicate event for the same time, target and field")
            event_keys.add(key)
            event_actuators.add((event.target, event.field))

        _unique([controller.id for controller in self.controllers], "controller")
        actuators = set()
        for control in self.controllers:
            measure, actuator = control.measurement, control.actuator
            if measure.kind in {"node_temperature", "pressure_difference"}:
                valid = measure.target_id in nodes
            else:
                valid = measure.target_id in edges
                if valid and measure.kind == "solid_temperature":
                    valid = edges[measure.target_id].type == "cold_plate"
            if not valid or (measure.reference_id is not None and measure.reference_id not in nodes):
                raise ValueError(f"controller {control.id} measurement reference is invalid")
            if actuator.edge_id not in edges or edges[actuator.edge_id].type != expected_types[actuator.field]:
                raise ValueError(f"controller {control.id} actuator target is invalid")
            binding = (actuator.edge_id, actuator.field)
            if binding in actuators:
                raise ValueError(f"duplicate actuator ownership for {binding}")
            if binding in event_actuators:
                raise ValueError(f"controller actuator {binding} conflicts with an event")
            actuators.add(binding)
            _on_grid(control.sample_period_s, self.time.dt_s, "controller sample period")
            if actuator.field == "opening" and control.pid.output_max > 1:
                raise ValueError("valve controller output_max cannot exceed 1")
            if measure.kind in {"node_temperature", "solid_temperature"} and control.pid.setpoint <= 0:
                raise ValueError("temperature controller setpoint must exceed absolute zero K")

        for uid, binding in self.object_bindings.items():
            _unique(binding.node_ids, f"object binding {uid} node")
            _unique(binding.edge_ids, f"object binding {uid} edge")
            if set(binding.node_ids) - nodes or set(binding.edge_ids) - set(edges):
                raise ValueError(f"object binding {uid} contains an unknown graph reference")
            if binding.parent_uid is not None and binding.parent_uid not in self.object_bindings:
                raise ValueError(f"object binding {uid} parent_uid is unknown")
            visited, current = {uid}, binding.parent_uid
            while current is not None:
                if current in visited:
                    raise ValueError(f"object binding {uid} parent_uid contains a cycle")
                visited.add(current)
                parent = self.object_bindings.get(current)
                current = parent.parent_uid if parent else None

        _unique([rack.id for rack in self.racks], "rack")
        for rack in self.racks:
            _unique([server.id for server in rack.servers], f"rack {rack.id} server")
            refs = []
            if rack.manifold:
                refs += [rack.manifold.supply_node, rack.manifold.return_node]
            for manifold in (rack.supply_manifold, rack.return_manifold):
                if manifold:
                    refs.append(manifold.node_id)
            for server in rack.servers:
                refs += [node for node in (server.inlet_node, server.outlet_node) if node is not None]
                pairs = [pair.edge_id for pair in server.cp_cu_pairs]
                if server.cp_cu:
                    pairs.append(server.cp_cu.cold_plate_edge_id)
                if any(pair not in edges or edges[pair].type != "cold_plate" for pair in pairs):
                    raise ValueError(f"rack {rack.id} server {server.id} cold plate reference is invalid")
            if set(refs) - nodes:
                raise ValueError(f"rack {rack.id} manifold/server node reference is invalid")

        if self.parameter_provenance is not None and self.parameter_pack is None:
            effective = [{"id": edge["id"], "type": edge["type"], "params": edge["params"]}
                         for edge in resolved["edges"]]
            effective.sort(key=lambda edge: edge["id"])
            self.parameter_provenance.resolved_sha256 = _digest({"fluid": resolved["fluid"], "edges": effective})
        return self


def _raw_dict(value: dict | LiquidrunConfig | LiquidrunV2) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, mode="json", exclude_none=True)
    if not isinstance(value, dict):
        raise ValueError("liquidrun must be an object")
    return deepcopy(value)


def parse_liquidrun(value: dict | LiquidrunConfig | LiquidrunV2) -> LiquidrunConfig:
    """Validate v2 strictly or load an explicitly versioned legacy v1 manifest.

    Returns the existing compiler-facing model. v2 controls, source UIDs and
    execution requirements survive adaptation as JSON fields. Unknown versions
    cannot fall through to the legacy permissive model.
    """
    raw = _raw_dict(value)
    if raw.get("version") == "2.0" or raw.get("schemaVersion") == 2:
        # The legacy runtime model serializes its absent provenance as {}.
        if raw.get("parameter_provenance") == {}:
            raw.pop("parameter_provenance")
        checked = LiquidrunV2.model_validate(raw)
        resolved = resolve_parameter_pack(checked.model_dump(by_alias=True, mode="json", exclude_none=True))
        for edge in resolved["edges"]:
            edge["params"] = _PARAMS[edge["type"]].model_validate(edge["params"]).model_dump(mode="json", exclude_none=True)
        if resolved.get("parameter_provenance"):
            effective = [{"id": edge["id"], "type": edge["type"], "params": edge["params"]} for edge in resolved["edges"]]
            effective.sort(key=lambda edge: edge["id"])
            resolved["parameter_provenance"]["resolved_sha256"] = _digest({"fluid": resolved["fluid"], "edges": effective})
        return LiquidrunConfig.model_validate(resolved)
    if raw.get("version") != "1.0" or raw.get("schemaVersion") not in (None, 1):
        raise ValueError(f"unsupported liquidrun version {raw.get('version')!r}")
    return LiquidrunConfig.model_validate(raw)


def resolve_run_input(value, controllers: list[dict] | None = None) -> tuple[LiquidrunConfig, list[dict]]:
    """Resolve embedded controls; a second transport copy must be identical.

    V1 callers may continue sending controllers separately. V2 always declares
    a controllers array, including [] for open-loop runs, and never allows the
    HTTP body or checkpoint to silently replace it.
    """
    config = parse_liquidrun(value)
    raw = config.model_dump(by_alias=True, mode="json", exclude_none=True)
    embedded = raw.get("controllers")
    if controllers is not None and not isinstance(controllers, list):
        raise ValueError("controllers must be an array")
    external = deepcopy(controllers)
    if config.version == "2.0" and external is not None:
        external = [Controller.model_validate(item).model_dump(mode="json", exclude_none=True)
                    for item in external]
    if embedded is not None and external is not None and embedded != external:
        raise ValueError("controllers conflict between liquidrun and transport body")
    controls = deepcopy(embedded if embedded is not None else external or [])
    return config, controls


def load_liquidrun(path: str | Path) -> LiquidrunConfig:
    """Read a standalone .liquidrun without implicit access to project or pack files."""
    with Path(path).open(encoding="utf-8") as stream:
        return parse_liquidrun(json.load(stream))


def liquidrun_json_schema() -> dict:
    schema = LiquidrunV2.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://dcdtuni.local/schemas/liquidrun-v2.schema.json"
    schema["description"] = (
        "Liquid runtime v2 input. Strict structural schema; call parse_liquidrun for "
        "profile completeness, references, time-grid/series dimensions and actuator ownership. "
        "Parameters omitted on an edge must be supplied by its embedded parameter_pack profile."
    )
    return schema


if __name__ == "__main__":
    target = Path(__file__).with_name("liquidrun.schema.json")
    target.write_text(json.dumps(liquidrun_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
