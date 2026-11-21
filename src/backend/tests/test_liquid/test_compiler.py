"""Tests for the .liquidrun compiler (config → CompiledGraph)."""

import json
import math
import pathlib
import tempfile

import pytest
import torch

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph, _interp_polyline, _compute_cumlen

FIXTURE_PATH = pathlib.Path(__file__).parent / "minimal.liquidrun"


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------

def _load_config() -> LiquidrunConfig:
    data = json.loads(FIXTURE_PATH.read_text())
    return LiquidrunConfig(**data)


@pytest.fixture
def config():
    return _load_config()


@pytest.fixture
def graph(config):
    return compile_graph(config)


# ---------------------------------------------------------------------------
# Basic compilation
# ---------------------------------------------------------------------------

class TestCompileBasic:
    def test_compiles_without_error(self, config):
        """compile_graph must not raise."""
        g = compile_graph(config)
        assert g is not None

    def test_node_ids_preserved(self, graph):
        """Node IDs must appear in the same order as the config."""
        assert graph.node_ids == ["supply", "return"]

    def test_node_count(self, graph):
        assert graph.N_nodes == 2

    def test_edge_count(self, graph):
        assert graph.N_edges == 1


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------

class TestBoundaryConditions:
    def test_bc_mask_all_true(self, graph):
        """Both nodes are pressure_bc, so bc_mask must be all True."""
        assert graph.bc_mask.dtype == torch.bool
        assert graph.bc_mask.all().item()

    def test_bc_P_values(self, graph):
        """Supply=200kPa, Return=100kPa."""
        assert graph.bc_P[0].item() == pytest.approx(200000.0)
        assert graph.bc_P[1].item() == pytest.approx(100000.0)

    def test_bc_T_supply(self, graph):
        """Supply node has T_K=308.15."""
        assert graph.bc_T[0].item() == pytest.approx(308.15)

    def test_bc_T_return_uses_ic(self, graph):
        """Return node has no T_K → falls back to initial_conditions.T_fluid_K."""
        assert graph.bc_T[1].item() == pytest.approx(308.15)

    def test_junction_mask_none(self, graph):
        """No junction nodes in minimal fixture → all False."""
        assert not graph.junction_mask.any().item()


# ---------------------------------------------------------------------------
# Incidence matrix
# ---------------------------------------------------------------------------

class TestIncidenceMatrix:
    def test_shape(self, graph):
        inc = graph.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        assert inc.shape == (2, 1)

    def test_departure_positive(self, graph):
        """Node 0 (supply) is edge.from → +1."""
        inc = graph.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        assert inc[0, 0].item() == pytest.approx(1.0)

    def test_arrival_negative(self, graph):
        """Node 1 (return) is edge.to → -1."""
        inc = graph.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        assert inc[1, 0].item() == pytest.approx(-1.0)

    def test_column_sum_zero(self, graph):
        """Each column should sum to 0."""
        inc = graph.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        assert inc.sum(dim=0).abs().max().item() < 1e-9


# ---------------------------------------------------------------------------
# Pipe indices and params
# ---------------------------------------------------------------------------

class TestPipeCompilation:
    def test_pipe_indices_exist(self, graph):
        assert graph.pipe_indices is not None
        assert graph.pipe_params is not None

    def test_pipe_D(self, graph):
        """D_m=0.025 in the fixture."""
        assert graph.pipe_params.D[0].item() == pytest.approx(0.025)

    def test_pipe_L(self, graph):
        """Straight pipe along Z from z=0 to z=1 → L=1.0."""
        assert graph.pipe_params.L[0].item() == pytest.approx(1.0)

    def test_pipe_A(self, graph):
        """Cross-section area = pi/4 * D^2."""
        D = 0.025
        expected_A = math.pi / 4.0 * D ** 2
        assert graph.pipe_params.A[0].item() == pytest.approx(expected_A)

    def test_pipe_roughness(self, graph):
        assert graph.pipe_params.roughness[0].item() == pytest.approx(1.5e-6)


# ---------------------------------------------------------------------------
# Pipe cells
# ---------------------------------------------------------------------------

class TestPipeCells:
    def test_N_cells(self, graph):
        """n_cells=5 in the fixture."""
        assert graph.N_cells == 5

    def test_cell_ds(self, graph):
        """L=1.0, n_cells=5 → cell_ds=0.2 for each cell."""
        for i in range(5):
            assert graph.cell_ds[i].item() == pytest.approx(0.2)

    def test_cell_volume(self, graph):
        """cell_volume = A * cell_ds."""
        D = 0.025
        A = math.pi / 4.0 * D ** 2
        ds = 0.2
        expected_vol = A * ds
        for i in range(5):
            assert graph.cell_volume[i].item() == pytest.approx(expected_vol)

    def test_cell_edge_id(self, graph):
        """All cells belong to pipe index 0."""
        assert (graph.cell_edge_id == 0).all().item()

    def test_cell_position_shape(self, graph):
        assert graph.cell_position.shape == (5, 3)

    def test_cell_position_along_z(self, graph):
        """Pipe is along Z axis. Cell centres at z=0.1, 0.3, 0.5, 0.7, 0.9."""
        for i in range(5):
            expected_z = (i + 0.5) * 0.2
            assert graph.cell_position[i, 2].item() == pytest.approx(expected_z, abs=1e-9)
            assert graph.cell_position[i, 0].item() == pytest.approx(0.0, abs=1e-9)
            assert graph.cell_position[i, 1].item() == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Gravity
# ---------------------------------------------------------------------------

class TestGravity:
    def test_g_mag(self, graph):
        """Gravity magnitude should be 9.81."""
        assert graph.g_mag == pytest.approx(9.81)


# ---------------------------------------------------------------------------
# Elevation change and bend detection
# ---------------------------------------------------------------------------

class TestElevationAndBends:
    def test_straight_pipe_zero_bends(self, graph):
        """The minimal pipe has collinear waypoints → 0 bends → K_minor=0."""
        assert graph.pipe_params.K_minor[0].item() == pytest.approx(0.0)

    def test_dh_pipe_along_z_gravity_minus_y(self, graph):
        """Pipe along Z, gravity is [0,-9.81,0] (downward in Y).

        dh = -sum(segments @ g_unit). Segments are [0,0,0.5] and [0,0,0.5].
        g_unit = [0,-1,0]. dot products are 0. So dh ≈ 0.
        """
        assert graph.pipe_params.dh[0].item() == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# to_json
# ---------------------------------------------------------------------------

class TestToJson:
    def test_to_json_works(self, graph, tmp_path):
        out = tmp_path / "graph.json"
        graph.to_json(out)
        assert out.exists()

        data = json.loads(out.read_text())
        assert data["N_nodes"] == 2
        assert data["N_edges"] == 1
        assert data["N_cells"] == 5
        assert data["g_mag"] == pytest.approx(9.81)


# ---------------------------------------------------------------------------
# Adjacency lists
# ---------------------------------------------------------------------------

class TestAdjacencyLists:
    def test_node_in_edges(self, graph):
        """Node 0 (supply) has no incoming edges. Node 1 (return) has edge 0."""
        assert graph.node_in_edges[0] == []
        assert graph.node_in_edges[1] == [0]

    def test_node_out_edges(self, graph):
        """Node 0 (supply) has edge 0 outgoing. Node 1 (return) has none."""
        assert graph.node_out_edges[0] == [0]
        assert graph.node_out_edges[1] == []


# ---------------------------------------------------------------------------
# Edge type encoding
# ---------------------------------------------------------------------------

class TestEdgeType:
    def test_pipe_type_int(self, graph):
        """Pipe → edge_type 0."""
        assert graph.edge_type[0].item() == 0


# ---------------------------------------------------------------------------
# Scalar fields from config
# ---------------------------------------------------------------------------

class TestScalarFields:
    def test_dt(self, graph):
        assert graph.dt == pytest.approx(1.0)

    def test_bdf_order(self, graph):
        assert graph.bdf_order == 1

    def test_fluid_rho(self, graph):
        assert graph.fluid_rho == pytest.approx(997.0)

    def test_fluid_cp(self, graph):
        assert graph.fluid_cp == pytest.approx(4182.0)

    def test_fluid_mu(self, graph):
        assert graph.fluid_mu == pytest.approx(0.00089)

    def test_T_fluid_init(self, graph):
        assert graph.T_fluid_init == pytest.approx(308.15)

    def test_P_init(self, graph):
        assert graph.P_init == pytest.approx(150000.0)


# ---------------------------------------------------------------------------
# _interp_polyline unit tests
# ---------------------------------------------------------------------------

class TestInterpPolyline:
    def test_at_start(self):
        pts = torch.tensor([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=torch.float64)
        cumlen = _compute_cumlen(pts)
        pos = _interp_polyline(pts, cumlen, 0.0)
        assert pos[0].item() == pytest.approx(0.0)

    def test_at_end(self):
        pts = torch.tensor([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=torch.float64)
        cumlen = _compute_cumlen(pts)
        pos = _interp_polyline(pts, cumlen, 1.0)
        assert pos[0].item() == pytest.approx(1.0)

    def test_at_midpoint(self):
        pts = torch.tensor([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=torch.float64)
        cumlen = _compute_cumlen(pts)
        pos = _interp_polyline(pts, cumlen, 1.0)
        assert pos[0].item() == pytest.approx(1.0)

    def test_multi_segment(self):
        pts = torch.tensor([
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
        ], dtype=torch.float64)
        cumlen = _compute_cumlen(pts)
        # At s=1.5 → halfway along second segment
        pos = _interp_polyline(pts, cumlen, 1.5)
        assert pos[0].item() == pytest.approx(1.0)
        assert pos[1].item() == pytest.approx(0.5)

    def test_clamp_beyond_end(self):
        pts = torch.tensor([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=torch.float64)
        cumlen = _compute_cumlen(pts)
        pos = _interp_polyline(pts, cumlen, 5.0)
        assert pos[0].item() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Optional components (None when absent)
# ---------------------------------------------------------------------------

class TestOptionalComponents:
    def test_no_pumps(self, graph):
        assert graph.pump_indices is None
        assert graph.pump_params is None

    def test_no_cold_plates(self, graph):
        assert graph.cp_indices is None
        assert graph.cp_params is None

    def test_no_valves(self, graph):
        assert graph.valve_indices is None
        assert graph.valve_params is None

    def test_no_racks(self, graph):
        assert graph.rack_meta == []


class TestExplicitPipeMinorLoss:
    def test_missing_and_zero_additional_preserve_old_compilation(self):
        base = _load_config()
        original = compile_graph(base)
        assert "K_minor_additional" not in base.edges[0].params
        base.edges[0].params["K_minor_additional"] = 0.0
        explicit = compile_graph(base)
        for name in ["D", "L", "dh", "A", "roughness", "K_minor"]:
            assert torch.equal(getattr(original.pipe_params,name),getattr(explicit.pipe_params,name))
        assert torch.equal(original.cell_volume,explicit.cell_volume)
        from backend.liquidcooling.liquidrun_schema import PipeParams
        assert "K_minor_additional" not in PipeParams().model_dump(exclude_none=True)

    def test_additional_is_applied_once_without_new_states_or_storage(self):
        cfg = _load_config()
        cfg.edges[0].params["waypoints_m"] = [[0.,0.,0.],[1.,0.,0.],[1.,1.,0.]]
        before = compile_graph(cfg)
        cfg.edges[0].params["K_minor_additional"] = .75
        after = compile_graph(cfg)
        assert after.pipe_params.K_minor.item() == before.pipe_params.K_minor.item()+.75
        assert after.N_nodes == before.N_nodes and after.N_edges == before.N_edges and after.N_cells == before.N_cells
        assert torch.equal(after.pipe_params.L,before.pipe_params.L)
        assert torch.equal(after.cell_volume,before.cell_volume)

    def test_strict_model_and_distributed_schema_accept_nonnegative_and_keep_default_absent(self):
        from backend.liquidcooling.liquidrun_schema import PipeParams
        from backend.liquidcooling.parameters import ParameterProfile
        assert PipeParams(K_minor_additional=.75).K_minor_additional == .75
        assert ParameterProfile(type="pipe",params={"K_minor_additional":.75},units={"K_minor_additional":"1"})
        root=pathlib.Path(__file__).resolve().parents[2]/"liquidcooling"
        expected=PipeParams.model_json_schema()["properties"]["K_minor_additional"]
        for name in ["liquidrun.schema.json","liquidrun_dae.schema.json","liquidrun_dae_v4.schema.json"]:
            assert json.loads((root/name).read_text())["$defs"]["PipeParams"]["properties"]["K_minor_additional"] == expected

    @pytest.mark.parametrize("value",[-.1,True,"0.5",float("nan"),float("inf")])
    def test_invalid_additional_is_rejected_by_strict_model_and_actual_compiler(self,value):
        from backend.liquidcooling.liquidrun_schema import PipeParams
        from pydantic import ValidationError
        with pytest.raises(ValidationError): PipeParams(K_minor_additional=value)
        cfg=_load_config();cfg.edges[0].params["K_minor_additional"]=value
        with pytest.raises(ValueError,match="finite non-negative"):compile_graph(cfg)

    def test_profile_rejects_negative_or_wrong_units(self):
        from backend.liquidcooling.parameters import ParameterProfile
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ParameterProfile(type="pipe",params={"K_minor_additional":-.1},units={"K_minor_additional":"1"})
        with pytest.raises(ValidationError):
            ParameterProfile(type="pipe",params={"K_minor_additional":.5},units={"K_minor_additional":"Pa"})
