"""
LiquidResult dataclass — post-processed output of the liquid cooling ODIL solver.

Stores converged state fields (pressure, flow, temperatures) plus per-rack
summary statistics.  Supports JSON round-trip for downstream consumption.
"""

import json
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ServerSummary:
    server_id: str
    flow_kg_s: np.ndarray          # [N_t]
    max_chip_temp_K: np.ndarray    # [N_t]
    heat_removed_W: np.ndarray     # [N_t]
    cp_edge_ids: list[str] = field(default_factory=list)
    # Branch flow with its sign (+ from->to of the first listed plate);
    # flow_kg_s keeps the magnitude for existing consumers.
    signed_flow_kg_s: np.ndarray | None = None


@dataclass
class CpDetail:
    edge_id: str
    chip_temp_K: np.ndarray            # [N_t]
    water_inlet_temp_K: np.ndarray     # [N_t]
    water_outlet_temp_K: np.ndarray    # [N_t]
    inlet_pressure_Pa: np.ndarray      # [N_t]
    outlet_pressure_Pa: np.ndarray     # [N_t]


@dataclass
class RackSummary:
    rack_id: str
    total_flow_kg_s: np.ndarray    # [N_t]
    supply_temp_K: np.ndarray      # [N_t]
    return_temp_K: np.ndarray      # [N_t]
    servers: list[ServerSummary] = field(default_factory=list)


@dataclass
class LiquidResult:
    converged: bool
    final_loss: float
    epochs: int
    node_pressure_Pa: np.ndarray       # [N_nodes, N_t]
    node_ids: list[str]
    edge_flow_kg_s: np.ndarray         # [N_edges, N_t]
    edge_ids: list[str]
    cell_temp_K: np.ndarray            # [N_cells, N_t]
    cell_positions: np.ndarray         # [N_cells, 3]
    chip_temp_K: np.ndarray            # [N_cp, N_t]
    water_outlet_temp_K: np.ndarray    # [N_cp, N_t]
    rack_summaries: list[RackSummary] = field(default_factory=list)
    cp_details: list[CpDetail] = field(default_factory=list)
    time_s: np.ndarray = field(default_factory=lambda: np.array([]))
    hx_edge_ids: list[str] = field(default_factory=list)
    primary_return_K: np.ndarray = field(default_factory=lambda: np.array([]))
    secondary_supply_K: np.ndarray = field(default_factory=lambda: np.array([]))

    def save_json(self, path: Path) -> None:
        def _to_list(a):
            if isinstance(a, np.ndarray):
                return a.tolist()
            return a
        data = {
            "converged": self.converged,
            "final_loss": self.final_loss,
            "epochs": self.epochs,
            "node_ids": self.node_ids,
            "node_pressure_Pa": _to_list(self.node_pressure_Pa),
            "edge_ids": self.edge_ids,
            "edge_flow_kg_s": _to_list(self.edge_flow_kg_s),
            "cell_temp_K": _to_list(self.cell_temp_K),
            "chip_temp_K": _to_list(self.chip_temp_K),
            "water_outlet_temp_K": _to_list(self.water_outlet_temp_K),
            "time_s": _to_list(self.time_s),
            "cell_positions": _to_list(self.cell_positions),
        }
        if self.hx_edge_ids:
            data.update(hx_edge_ids=self.hx_edge_ids,
                        primary_return_K=_to_list(self.primary_return_K),
                        secondary_supply_K=_to_list(self.secondary_supply_K))
        if self.rack_summaries:
            data["rack_summaries"] = [
                {
                    "rack_id": rs.rack_id,
                    "total_flow_kg_s": _to_list(rs.total_flow_kg_s),
                    "supply_temp_K": _to_list(rs.supply_temp_K),
                    "return_temp_K": _to_list(rs.return_temp_K),
                    "servers": [
                        {
                            "server_id": ss.server_id,
                            "flow_kg_s": _to_list(ss.flow_kg_s),
                            "max_chip_temp_K": _to_list(ss.max_chip_temp_K),
                            "heat_removed_W": _to_list(ss.heat_removed_W),
                            "cp_edge_ids": ss.cp_edge_ids,
                            **({} if ss.signed_flow_kg_s is None
                               else {"signed_flow_kg_s": _to_list(ss.signed_flow_kg_s)}),
                        }
                        for ss in rs.servers
                    ],
                }
                for rs in self.rack_summaries
            ]
        if self.cp_details:
            data["cp_details"] = [
                {
                    "edge_id": cd.edge_id,
                    "chip_temp_K": _to_list(cd.chip_temp_K),
                    "water_inlet_temp_K": _to_list(cd.water_inlet_temp_K),
                    "water_outlet_temp_K": _to_list(cd.water_outlet_temp_K),
                    "inlet_pressure_Pa": _to_list(cd.inlet_pressure_Pa),
                    "outlet_pressure_Pa": _to_list(cd.outlet_pressure_Pa),
                }
                for cd in self.cp_details
            ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    @staticmethod
    def load_json(path: Path) -> 'LiquidResult':
        data = json.loads(path.read_text())

        # Reconstruct rack summaries if present
        rack_summaries: list[RackSummary] = []
        for rs_data in data.get("rack_summaries", []):
            servers = [
                ServerSummary(
                    server_id=ss["server_id"],
                    flow_kg_s=np.array(ss["flow_kg_s"]),
                    max_chip_temp_K=np.array(ss["max_chip_temp_K"]),
                    heat_removed_W=np.array(ss["heat_removed_W"]),
                    cp_edge_ids=list(ss.get("cp_edge_ids", [])),
                    signed_flow_kg_s=(np.array(ss["signed_flow_kg_s"]) if "signed_flow_kg_s" in ss else None),
                )
                for ss in rs_data.get("servers", [])
            ]
            rack_summaries.append(RackSummary(
                rack_id=rs_data["rack_id"],
                total_flow_kg_s=np.array(rs_data["total_flow_kg_s"]),
                supply_temp_K=np.array(rs_data["supply_temp_K"]),
                return_temp_K=np.array(rs_data["return_temp_K"]),
                servers=servers,
            ))

        cp_details: list[CpDetail] = []
        for cd_data in data.get("cp_details", []):
            cp_details.append(CpDetail(
                edge_id=cd_data["edge_id"],
                chip_temp_K=np.array(cd_data["chip_temp_K"]),
                water_inlet_temp_K=np.array(cd_data["water_inlet_temp_K"]),
                water_outlet_temp_K=np.array(cd_data["water_outlet_temp_K"]),
                inlet_pressure_Pa=np.array(cd_data["inlet_pressure_Pa"]),
                outlet_pressure_Pa=np.array(cd_data["outlet_pressure_Pa"]),
            ))

        return LiquidResult(
            converged=data["converged"],
            final_loss=data["final_loss"],
            epochs=data["epochs"],
            node_pressure_Pa=np.array(data["node_pressure_Pa"]),
            node_ids=data["node_ids"],
            edge_flow_kg_s=np.array(data["edge_flow_kg_s"]),
            edge_ids=data.get("edge_ids", []),
            cell_temp_K=np.array(data.get("cell_temp_K", [])),
            cell_positions=np.array(data.get("cell_positions", [])),
            chip_temp_K=np.array(data.get("chip_temp_K", [])),
            water_outlet_temp_K=np.array(data.get("water_outlet_temp_K", [])),
            time_s=np.array(data.get("time_s", [])),
            hx_edge_ids=data.get('hx_edge_ids', []),
            primary_return_K=np.array(data.get('primary_return_K', [])),
            secondary_supply_K=np.array(data.get('secondary_supply_K', [])),
            rack_summaries=rack_summaries,
            cp_details=cp_details,
        )
