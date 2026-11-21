"""Stage 2 light contract: the MMS product fixture is loadable, on-target and
coarsely solvable through the PRODUCT chain (compiled .liquidrun, not a test
adapter). The full analytic acceptance (closed-form comparison, dt-refinement
order, scenario gates) lives in tools/mms_product_acceptance.py with its
receipt under docs/benchmarks/ — this file only pins the fixture contract so
a regenerated/corrupted fixture fails fast in the unit suite.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input

REPO = pathlib.Path(__file__).resolve().parents[4]
FIXTURE = REPO / "docs/benchmarks/fixtures/mms-product-v3.liquidrun"


def _raw():
    return json.loads(FIXTURE.read_text())


def test_mms_fixture_loads_with_target_parameters_and_expected_states():
    d = load_dae_input(_raw())
    eq = d.equation_graph
    assert eq.size == 90
    layout = eq.state_layout
    width = lambda key: layout[key].stop - layout[key].start
    assert width("solid_temperature") == 1
    assert width("hx_secondary_temperature") == 1
    assert width("primary_return_temperature") == 1
    assert width("water_temperature") == 46  # 8 pipes x cells + cold-plate cells
    theta = d.parameters(0)
    pl = eq.parameter_layout
    assert theta[pl["cp_UA"]].tolist() == [100.0]
    assert theta[pl["cp_Csolid"]].tolist() == [1000.0]
    assert theta[pl["hx_UA"]].tolist() == [500.0]
    assert theta[pl["heat_W"]].tolist() == [100.0]
    assert theta[pl["primary_inlet_K"]].tolist() == [290.0]  # fixed supply boundary
    assert theta[pl["primary_flow_kg_s"]].tolist() == [0.1]
    assert d.dt == 1 and d.sample_count == 61


def test_mms_fixture_coarse_smoke_converges():
    sim = DAESimulation(_raw())
    for _ in range(3):
        frame = sim.step()
        assert frame["newton"]["scaled_residual_inf"] < 1e-8
        assert abs(frame["diagnostics"]["energy_balance_W"]) < 1e-6


def test_mms_fixture_provenance_is_fully_mock_labelled():
    project = json.loads((REPO / "case/MMS_Product/MMS_product.cfdproj").read_text())
    prov = project["meta"]["provenance"]
    assert prov["fixture_revision"] == "mms-product-1"
    for block in ("topology", "geometry", "thermal"):
        assert prov[block]["source"] == "mock", block
    circuit = project["runDefaults"]["liquid"]["experiment"]["dae"]["primary_circuits"][0]
    assert circuit["source"] == "mock"
    assert "290" in circuit["source_reference"]  # fixed supply boundary documented


def _acceptance_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("mms_product_acceptance", REPO / "tools/mms_product_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("hx_model", [None, "mixed-volumes", "counterflow-ntu"])
def test_acceptance_closed_form_matches_the_product_operator_for_the_fixture_hx_model(hx_model):
    """The acceptance tool reads the HX law from the fixture. Regenerating the
    fixture through the product compiler must not break acceptance because of
    the law it writes (stream-A verification, 2026-10-21): the hand-derived
    linear system has to agree with the production residual for both laws."""
    tool = _acceptance_tool()
    raw = _raw()
    if hx_model is not None:
        next(e for e in raw["edges"] if e["type"] == "heat_exchanger")["params"]["hxModel"] = hx_model
    definition = load_dae_input(raw)
    A, B1, B2, c, meta = tool.assemble_linear_system(definition)
    assert meta["hx_model"] == (hx_model or "mixed-volumes")
    x0 = tool.extract_dynamic(meta, definition)
    assert float(abs(A @ x0 + B1 * 100.0 + B2 * 290.0 + c).max()) <= 1e-9  # steady-init gate
    assert tool.jvp_audit(A, meta, definition) <= 1e-8  # assembly gate
