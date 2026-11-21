"""
Pydantic v2 validation models for the .liquidrun manifest (JSON).

This schema validates the JSON file exported by frontend
for liquid cooling network simulation via the ODIL-based solver.
"""
from typing import Any, Dict, List, Literal, Optional
from pydantic import ConfigDict, Field, model_validator

from backend.aircooling.runner.cfdrun_schema import CFDBaseModel
from backend.liquidcooling.parameters import resolve_parameter_pack


class FluidConfig(CFDBaseModel):
    """Physical properties of the coolant fluid."""

    rho_kg_m3: float
    cp_J_kgK: float
    mu_Pa_s: float
    k_W_mK: float


class TimeConfig(CFDBaseModel):
    """Temporal discretisation parameters."""

    dt_s: float
    N_t: int
    bdf_order: int = 1


class NodeBCConfig(CFDBaseModel):
    """Boundary condition values applied at a network node."""

    P_Pa: float
    T_K: Optional[float] = None


class NodeConfig(CFDBaseModel):
    """A node in the pipe-network graph (junction or pressure BC)."""

    id: str
    type: Literal["pressure_bc", "junction"]
    position_m: List[float]
    bc: Optional[NodeBCConfig] = None


class EdgeConfig(CFDBaseModel):
    """An edge (pipe / cold-plate / valve / pump) in the network graph."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    type: Literal["pipe", "cold_plate", "valve", "pump", "heat_exchanger"]
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    params: Dict[str, Any]


# ── Rack sub-models ──────────────────────────────────────────────────────────

class ManifoldConfig(CFDBaseModel):
    """Supply / return manifold geometry inside a rack."""

    node_id: str
    D_m: float = 0.019
    n_cells: int = 3


class CpCuPairConfig(CFDBaseModel):
    """One cold-plate + copper spreader pair for a single server slot."""

    server_id: str
    cold_plate_edge_id: str
    power_W: float = 0.0
    T_inlet_K: Optional[float] = None


class ServerConfig(CFDBaseModel):
    """A server unit inside a rack with its liquid-cooling attachment."""

    id: str
    slot_u: int = 0
    span_u: int = 1
    cp_cu: Optional[CpCuPairConfig] = None

    @model_validator(mode="before")
    @classmethod
    def _populate_cp_cu_from_pairs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("cp_cu") is not None:
            return data
        pairs = data.get("cp_cu_pairs")
        if pairs and len(pairs) > 0:
            p = pairs[0]
            data["cp_cu"] = CpCuPairConfig(
                server_id=data.get("id", p.get("id", "")),
                cold_plate_edge_id=p.get("edge_id", ""),
                power_W=p.get("Q_W", 0.0),
            )
        return data


class RackConfig(CFDBaseModel):
    """A server rack with supply/return manifolds and server list."""

    id: str
    supply_manifold: Optional[ManifoldConfig] = None
    return_manifold: Optional[ManifoldConfig] = None
    servers: List[ServerConfig] = Field(default_factory=list)


# ── Event / IC / Solver ──────────────────────────────────────────────────────

class EventConfig(CFDBaseModel):
    """A time-scheduled override of a single field on a target object."""

    time_s: float
    target: str
    field: str
    value: Any


class ICConfig(CFDBaseModel):
    """Uniform initial conditions for the whole network."""

    T_fluid_K: float = 308.15
    T_solid_K: float = 340.0
    P_uniform_Pa: float = 150000.0


class SolverWeightsConfig(CFDBaseModel):
    """Loss-function weights for the ODIL physics residuals."""

    mass: float = 1.0
    momentum: float = 1e-5
    energy: float = 1.0
    bc: float = 100.0
    ic: float = 10.0


class SolverConfig(CFDBaseModel):
    """Hyper-parameters for the ODIL optimiser."""

    optimizer: str = "adam"
    lr: float = 1e-3
    max_epochs: int = 5000
    convergence_tol: float = 1e-6
    weights: SolverWeightsConfig = Field(default_factory=SolverWeightsConfig)


# ── Top-level manifest ───────────────────────────────────────────────────────

class LiquidrunConfig(CFDBaseModel):
    """Root model for a .liquidrun JSON manifest."""

    parameter_provenance: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _resolve_parameter_pack(cls, data: Any) -> Any:
        return resolve_parameter_pack(data)

    version: str
    source: str
    projectName: str
    gravity: List[float]
    fluid: FluidConfig
    time: TimeConfig
    nodes: List[NodeConfig]
    edges: List[EdgeConfig]
    racks: List[RackConfig] = Field(default_factory=list)
    events: List[EventConfig] = Field(default_factory=list)
    initial_conditions: ICConfig = Field(default_factory=ICConfig)
    solver: SolverConfig = Field(default_factory=SolverConfig)
