"""Tests for the .liquidrun Pydantic schema."""
import json
import pathlib
import pytest

from backend.liquidcooling.schema import LiquidrunConfig

FIXTURE_PATH = pathlib.Path(__file__).parent / "minimal.liquidrun"


def _load() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


class TestLiquidrunSchema:
    def test_load_and_validate(self):
        """minimal.liquidrun must parse without validation errors."""
        data = _load()
        cfg = LiquidrunConfig(**data)
        assert cfg.version == "1.0"
        assert cfg.projectName == "MinimalTest"

    def test_gravity_vector(self):
        """gravity must be a three-element list with correct values."""
        cfg = LiquidrunConfig(**_load())
        assert len(cfg.gravity) == 3
        assert cfg.gravity[1] == pytest.approx(-9.81)

    def test_node_types_include_pressure_bc(self):
        """At least one node must have type 'pressure_bc'."""
        cfg = LiquidrunConfig(**_load())
        types = {n.type for n in cfg.nodes}
        assert "pressure_bc" in types

    def test_edge_from_to_alias(self):
        """EdgeConfig must resolve the 'from'/'to' JSON keys via alias."""
        cfg = LiquidrunConfig(**_load())
        assert len(cfg.edges) == 1
        edge = cfg.edges[0]
        assert edge.from_node == "supply"
        assert edge.to_node == "return"

    def test_missing_version_fails(self):
        """Omitting 'version' must raise a ValidationError."""
        from pydantic import ValidationError

        data = _load()
        del data["version"]
        with pytest.raises(ValidationError):
            LiquidrunConfig(**data)

    def test_empty_events_ok(self):
        """An empty events list must be accepted without error."""
        cfg = LiquidrunConfig(**_load())
        assert cfg.events == []

    def test_pipe_waypoints_present(self):
        """The pipe edge params must carry a non-empty waypoints_m list."""
        cfg = LiquidrunConfig(**_load())
        pipe = cfg.edges[0]
        assert pipe.type == "pipe"
        waypoints = pipe.params["waypoints_m"]
        assert isinstance(waypoints, list)
        assert len(waypoints) == 3

    def test_fluid_properties(self):
        """FluidConfig fields must round-trip through validation."""
        cfg = LiquidrunConfig(**_load())
        assert cfg.fluid.rho_kg_m3 == pytest.approx(997.0)
        assert cfg.fluid.cp_J_kgK == pytest.approx(4182.0)
        assert cfg.fluid.mu_Pa_s == pytest.approx(0.00089)
        assert cfg.fluid.k_W_mK == pytest.approx(0.606)

    def test_time_config(self):
        """TimeConfig fields must be correct."""
        cfg = LiquidrunConfig(**_load())
        assert cfg.time.dt_s == pytest.approx(1.0)
        assert cfg.time.N_t == 10
        assert cfg.time.bdf_order == 1

    def test_node_bc_optional_T(self):
        """Return node BC must have no T_K (optional field absent)."""
        cfg = LiquidrunConfig(**_load())
        return_node = next(n for n in cfg.nodes if n.id == "return")
        assert return_node.bc is not None
        assert return_node.bc.T_K is None

    def test_supply_node_bc_T(self):
        """Supply node BC must carry T_K = 308.15."""
        cfg = LiquidrunConfig(**_load())
        supply = next(n for n in cfg.nodes if n.id == "supply")
        assert supply.bc is not None
        assert supply.bc.T_K == pytest.approx(308.15)

    def test_initial_conditions_defaults(self):
        """ICConfig values must match the fixture."""
        cfg = LiquidrunConfig(**_load())
        ic = cfg.initial_conditions
        assert ic.T_fluid_K == pytest.approx(308.15)
        assert ic.T_solid_K == pytest.approx(340.0)
        assert ic.P_uniform_Pa == pytest.approx(150000.0)

    def test_solver_config(self):
        """SolverConfig must parse optimizer and lr correctly."""
        cfg = LiquidrunConfig(**_load())
        assert cfg.solver.optimizer == "adam"
        assert cfg.solver.lr == pytest.approx(0.001)
        assert cfg.solver.max_epochs == 1000
        assert cfg.solver.weights.bc == pytest.approx(100.0)
        assert cfg.solver.weights.momentum == pytest.approx(1e-5)

    def test_racks_empty(self):
        """Empty racks list must be accepted and returned as []."""
        cfg = LiquidrunConfig(**_load())
        assert cfg.racks == []

    def test_extra_fields_allowed(self):
        """Extra fields in the JSON must be silently preserved (forward-compat)."""
        data = _load()
        data["future_field"] = "some_value"
        cfg = LiquidrunConfig(**data)
        # No ValidationError means extra="allow" is working
        assert cfg.future_field == "some_value"  # type: ignore[attr-defined]
