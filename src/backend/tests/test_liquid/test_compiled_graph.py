"""Tests for CompiledGraph dataclass construction, serialization, and incidence."""
import json
import math
import tempfile
from pathlib import Path

import pytest
import torch

from backend.liquidcooling.graphcompiling.compiled_graph import (
    ColdPlateBatchParams,
    ColdPlateIndices,
    CompiledGraph,
    PipeBatchParams,
    PipeIndices,
    PumpBatchParams,
    PumpIndices,
    RackMeta,
    ValveBatchParams,
    ValveIndices,
)


# ---------------------------------------------------------------------------
# Helper: build a minimal CompiledGraph (2 nodes, 1 edge, 5 cells)
# ---------------------------------------------------------------------------

def _minimal_graph() -> CompiledGraph:
    """
    Network: node 0 --[edge 0]--> node 1
    Edge type 0 = pipe
    5 cells all belonging to edge 0
    """
    N_nodes = 2
    N_edges = 1
    N_cells = 5
    N_cp = 0
    N_t = 3

    node_ids = ["n0", "n1"]
    node_position = torch.zeros(N_nodes, 3)
    node_position[1, 0] = 1.0  # node 1 is 1 m away in x

    bc_mask = torch.tensor([True, False])
    bc_P = torch.tensor([101325.0, 0.0])
    bc_T = torch.tensor([293.15, 0.0])
    junction_mask = torch.tensor([False, False])

    edge_from = torch.tensor([0], dtype=torch.long)
    edge_to = torch.tensor([1], dtype=torch.long)
    edge_type = torch.tensor([0], dtype=torch.long)  # 0 = pipe

    # Incidence: +1 at departure node, -1 at arrival node
    # Shape [N_nodes, N_edges] = [2, 1]
    incidence = torch.tensor([[1.0], [-1.0]])

    # --- pipe ---
    pipe_indices = PipeIndices(
        edge_ids=torch.tensor([0], dtype=torch.long),
        node_from=torch.tensor([0], dtype=torch.long),
        node_to=torch.tensor([1], dtype=torch.long),
        cell_start=torch.tensor([0], dtype=torch.long),
        cell_end=torch.tensor([N_cells], dtype=torch.long),
    )
    pipe_params = PipeBatchParams(
        D=torch.tensor([0.02]),
        L=torch.tensor([1.0]),
        dh=torch.tensor([0.02]),
        K_minor=torch.tensor([0.5]),
        A=torch.tensor([math.pi * 0.01**2]),
        roughness=torch.tensor([1.5e-6]),
    )

    # Cell arrays
    ds = 1.0 / N_cells
    cell_edge_id = torch.zeros(N_cells, dtype=torch.long)
    cell_ds = torch.full((N_cells,), ds)
    r = 0.01
    cell_volume = torch.full((N_cells,), math.pi * r**2 * ds)
    cell_dh = torch.full((N_cells,), 0.02)
    cell_position = torch.zeros(N_cells, 3)
    for k in range(N_cells):
        cell_position[k, 0] = (k + 0.5) * ds

    node_in_edges: list[list[int]] = [[], [0]]
    node_out_edges: list[list[int]] = [[0], []]

    return CompiledGraph(
        N_nodes=N_nodes,
        N_edges=N_edges,
        N_cells=N_cells,
        N_cp=N_cp,
        N_t=N_t,
        node_ids=node_ids,
        node_position=node_position,
        bc_mask=bc_mask,
        bc_P=bc_P,
        bc_T=bc_T,
        bc_T_mask=torch.tensor([True, False]),
        junction_mask=junction_mask,
        N_junctions=0,
        max_junction_depth=0,
        junction_indices=torch.zeros(0, dtype=torch.long),
        node_to_junction=torch.tensor([-1, -1], dtype=torch.long),
        edge_str_ids=["pipe_0"],
        edge_from=edge_from,
        edge_to=edge_to,
        edge_type=edge_type,
        incidence=incidence,
        pipe_indices=pipe_indices,
        cp_indices=None,
        valve_indices=None,
        pump_indices=None,
        hx_indices=None,
        pipe_params=pipe_params,
        cp_params=None,
        valve_params=None,
        pump_params=None,
        hx_params=None,
        N_hx=0,
        cell_edge_id=cell_edge_id,
        cell_ds=cell_ds,
        cell_volume=cell_volume,
        cell_dh=cell_dh,
        cell_position=cell_position,
        node_in_edges=node_in_edges,
        node_out_edges=node_out_edges,
        dt=0.1,
        bdf_order=2,
        T_fluid_init=293.15,
        T_solid_init=300.0,
        P_init=101325.0,
        solver_config={"max_iter": 200, "tol": 1e-6},
        rack_meta=[],
        fluid_rho=998.2,
        fluid_cp=4182.0,
        fluid_mu=1e-3,
        g_mag=9.81,
    )


# ---------------------------------------------------------------------------
# Tests: construction
# ---------------------------------------------------------------------------

class TestMinimalConstruction:
    def test_node_count(self):
        g = _minimal_graph()
        assert g.N_nodes == 2

    def test_edge_count(self):
        g = _minimal_graph()
        assert g.N_edges == 1

    def test_cell_count(self):
        g = _minimal_graph()
        assert g.N_cells == 5

    def test_node_ids(self):
        g = _minimal_graph()
        assert g.node_ids == ["n0", "n1"]

    def test_node_position_shape(self):
        g = _minimal_graph()
        assert g.node_position.shape == (2, 3)

    def test_bc_mask_dtype(self):
        g = _minimal_graph()
        assert g.bc_mask.dtype == torch.bool

    def test_edge_from_to_values(self):
        g = _minimal_graph()
        assert g.edge_from[0].item() == 0
        assert g.edge_to[0].item() == 1

    def test_optional_none_fields(self):
        g = _minimal_graph()
        assert g.cp_indices is None
        assert g.valve_indices is None
        assert g.pump_indices is None
        assert g.cp_params is None
        assert g.valve_params is None
        assert g.pump_params is None

    def test_pipe_indices_present(self):
        g = _minimal_graph()
        assert g.pipe_indices is not None
        assert g.pipe_params is not None

    def test_adjacency_lists(self):
        g = _minimal_graph()
        # node 0 has 1 outgoing edge
        assert g.node_out_edges[0] == [0]
        assert g.node_in_edges[0] == []
        # node 1 has 1 incoming edge
        assert g.node_in_edges[1] == [0]
        assert g.node_out_edges[1] == []

    def test_scalar_fields(self):
        g = _minimal_graph()
        assert g.dt == pytest.approx(0.1)
        assert g.bdf_order == 2
        assert g.fluid_rho == pytest.approx(998.2)
        assert g.g_mag == pytest.approx(9.81)

    def test_cell_arrays_shape(self):
        g = _minimal_graph()
        assert g.cell_edge_id.shape == (5,)
        assert g.cell_ds.shape == (5,)
        assert g.cell_volume.shape == (5,)
        assert g.cell_dh.shape == (5,)
        assert g.cell_position.shape == (5, 3)


# ---------------------------------------------------------------------------
# Tests: to_json roundtrip
# ---------------------------------------------------------------------------

class TestToJson:
    def test_writes_file(self, tmp_path):
        g = _minimal_graph()
        out = tmp_path / "graph.json"
        g.to_json(out)
        assert out.exists()

    def test_creates_parent_dirs(self, tmp_path):
        g = _minimal_graph()
        out = tmp_path / "subdir" / "nested" / "graph.json"
        g.to_json(out)
        assert out.exists()

    def test_roundtrip_scalars(self, tmp_path):
        g = _minimal_graph()
        out = tmp_path / "graph.json"
        g.to_json(out)
        with out.open() as fh:
            data = json.load(fh)

        assert data["N_nodes"] == 2
        assert data["N_edges"] == 1
        assert data["N_cells"] == 5
        assert data["N_cp"] == 0
        assert data["N_t"] == 3
        assert data["dt"] == pytest.approx(0.1)
        assert data["bdf_order"] == 2
        assert data["fluid_rho"] == pytest.approx(998.2)
        assert data["g_mag"] == pytest.approx(9.81)

    def test_roundtrip_node_ids(self, tmp_path):
        g = _minimal_graph()
        out = tmp_path / "graph.json"
        g.to_json(out)
        data = json.load(out.open())
        assert data["node_ids"] == ["n0", "n1"]

    def test_roundtrip_edge_from_to(self, tmp_path):
        g = _minimal_graph()
        out = tmp_path / "graph.json"
        g.to_json(out)
        data = json.load(out.open())
        assert data["edge_from"] == [0]
        assert data["edge_to"] == [1]
        assert data["edge_type"] == [0]

    def test_roundtrip_bc_mask(self, tmp_path):
        g = _minimal_graph()
        out = tmp_path / "graph.json"
        g.to_json(out)
        data = json.load(out.open())
        # bc_mask stored as list of booleans (or 0/1 depending on torch.tolist)
        bc = data["bc_mask"]
        assert len(bc) == 2
        assert bool(bc[0]) is True
        assert bool(bc[1]) is False

    def test_roundtrip_node_position_shape(self, tmp_path):
        g = _minimal_graph()
        out = tmp_path / "graph.json"
        g.to_json(out)
        data = json.load(out.open())
        pos = data["node_position"]
        assert len(pos) == 2        # N_nodes
        assert len(pos[0]) == 3     # 3-D coords


# ---------------------------------------------------------------------------
# Tests: incidence matrix shape and values
# ---------------------------------------------------------------------------

class TestIncidenceMatrix:
    def test_shape(self):
        g = _minimal_graph()
        inc = g.incidence
        assert inc.shape == (2, 1), f"Expected (2,1), got {inc.shape}"

    def test_departure_positive(self):
        """Row of the departure node (node 0) should be +1."""
        g = _minimal_graph()
        inc = g.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        assert inc[0, 0].item() == pytest.approx(1.0)

    def test_arrival_negative(self):
        """Row of the arrival node (node 1) should be -1."""
        g = _minimal_graph()
        inc = g.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        assert inc[1, 0].item() == pytest.approx(-1.0)

    def test_column_sum_zero(self):
        """Each column of the incidence matrix should sum to 0."""
        g = _minimal_graph()
        inc = g.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        col_sum = inc.sum(dim=0)
        assert col_sum.abs().max().item() < 1e-9

    def test_sparse_incidence_roundtrip(self, tmp_path):
        """to_json must handle a sparse incidence tensor without error."""
        g = _minimal_graph()
        # Replace incidence with a sparse version
        dense = g.incidence
        indices = dense.nonzero(as_tuple=False).T
        values = dense[indices[0], indices[1]]
        g.incidence = torch.sparse_coo_tensor(indices, values, dense.shape)
        out = tmp_path / "sparse_graph.json"
        g.to_json(out)  # should not raise
        assert out.exists()


# ---------------------------------------------------------------------------
# Tests: RackMeta
# ---------------------------------------------------------------------------

class TestRackMeta:
    def test_rack_meta_attached(self):
        g = _minimal_graph()
        meta = RackMeta(
            rack_id="rack-01",
            server_ids=["srv-A", "srv-B"],
            server_edge_ids=[["e0"], ["e1", "e2"]],
        )
        g.rack_meta = [meta]
        assert len(g.rack_meta) == 1
        assert g.rack_meta[0].rack_id == "rack-01"
        assert g.rack_meta[0].server_ids == ["srv-A", "srv-B"]
