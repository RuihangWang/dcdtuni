"""Control derivatives (I3) of the hybrid LF oracle, each against central finite differences.

Tolerances registered on 2026-10-26 before any comparison was run, and kept:

- air tangents (server inlet/exhaust, ACU return temperatures): central differences
  with steps 1e-3 K (supply temperature) and 1e-4 m^3/s (flow), each entry
  ``|adjoint - FD| <= 1e-6 + 1e-4 |FD|`` (K per control unit); a flow at the catalog's
  upper end takes the second-order one-sided difference from below;
- the PUE gradient, air controls: the same steps, re-solving the network and the
  energy, ``|gradient - FD| <= 1e-9 + 1e-4 |FD|``;
- the liquid speed gradients (opt-in, platform solves): the legacy's
  preregistration (``pump_dae_implicit_i3_v1``): ``rtol 1e-4`` with ``atol 1e-5 W``
  per unit speed for the pump hydraulic power and ``5e-5 K`` for chip temperatures;
- the PUE gradient, pump speed (opt-in): steps ±1e-3 through two platform solves,
  ``1e-9 + 1e-4 |FD|``.
"""
from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path

import numpy as np
import pytest
import torch

from agent.scenarios.hybrid import task as T
from agent.scenarios.hybrid.air_network import RATED_FLOW_M3S
from agent.scenarios.hybrid.derivatives import (air_sensitivity, classify_active_min, control_order,
                                                design_head_gradient, oracle_derivatives, primary_circuit,
                                                pue_sensitivity, single_return_alias)
from agent.scenarios.hybrid.oracle import plant_energy, scenario, solve_air

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hybrid_lf"
SMOKE = os.environ.get("DCDTUNI_AGENT_SMOKE") == "1"
AIR_STEPS = (1e-3, 1e-4)          # K, m^3/s
AIR_ATOL, AIR_RTOL = 1e-6, 1e-4
PUE_ATOL, PUE_RTOL = 1e-9, 1e-4
LIQUID_RTOL, LIQUID_ATOL = 1e-4, {"pump_hydraulic_power_W": 1e-5, "max_chip_temperature_K": 5e-5,
                                  "mean_chip_temperature_K": 5e-5}
PUMP_STEP = 1e-3
# The export's synthetic primary circuit (1 kg/s water at 4180 J/(kg K), backend liquid export default).
PRIMARY = {"flow_kg_s": 1, "cp_J_kgK": 4180}


def read_fixture(name):
    return json.loads(gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes()))


def with_speed_gradient(fixture, slopes):
    """The frozen liquid inputs with the primary circuit and given pump-speed gradients (for the algebra tests)."""
    liquidrun = copy.deepcopy(fixture["inputs"]["liquidrun"])
    steady = copy.deepcopy(fixture["inputs"]["steady"])
    (hx, _), = steady["metrics"]["primaryReturnK"].items()
    liquidrun["primary_circuits"] = [{"edge_id": hx, **PRIMARY}]
    pump = next(e["id"] for e in liquidrun["edges"] if e["type"] == "pump")
    steady["gradientParameters"] = ["pump_rpm_ratio"]
    steady["gradients"] = {name: {"pump_rpm_ratio": {pump: value}} for name, value in slopes.items()}
    steady["objectives"] = {**steady["objectives"], **{k: steady["objectives"].get(k, 0.) for k in slopes}}
    return liquidrun, steady


@pytest.mark.parametrize("name", ("open-normal", "cac-normal", "hac-normal", "open-single-off-m_14"))
def test_the_air_tangent_matches_central_differences(name):
    fixture = read_fixture(name)
    model, fields = solve_air(fixture["contract"], fixture["solids"])
    air = air_sensitivity(model, fields["state"], model.nominal_controls)
    assert air["summary"]["linear_residual_inf"] <= 1e-9 and air["summary"]["extra_air_solves"] == 0
    n = len(model.active_uids)
    # Every control on the reference OPEN design; the first and last temperature and flow elsewhere (time).
    indices = range(2 * n) if name == "open-normal" else (0, n - 1, n, 2 * n - 1)
    for index in indices:
        step = AIR_STEPS[0] if index < n else AIR_STEPS[1]

        def at(shift):
            controls = model.nominal_controls.clone()
            controls[index] += shift
            return model.fields(controls)

        # A flow at the catalog's upper end (a fault context runs at rated flow) cannot step up: the
        # second-order one-sided difference from below, as the platform's own check does there.
        upper = index >= n and float(model.nominal_controls[index]) + step > RATED_FLOW_M3S
        points = (at(0.), at(-step), at(-2 * step)) if upper else (at(step), at(-step))
        for key, matrix in air["gradients"].items():
            if upper:
                base, low, lower = (p[key].numpy() for p in points)
                difference = (3 * base - 4 * low + lower) / (2 * step)
            else:
                difference = (points[0][key] - points[1][key]).numpy() / (2 * step)
            error = np.abs(matrix[:, index].numpy() - difference)
            assert (error <= AIR_ATOL + AIR_RTOL * np.abs(difference)).all(), (key, index, error.max())


def pue_at(fixture, model, liquidrun, steady, controls):
    """The oracle's hard PUE with the air network re-solved at ``controls`` and the liquid answer held."""
    context = scenario(fixture["action"], fault_id=fixture["fault_id"])
    fields = model.fields(controls)
    fields = {**fields, "original_full_residual_inf": 0.}
    return float(plant_energy(context, fixture["contract"], fields, liquidrun, steady)[0]["total_pue_design"])


# An interior point with distinct set-points (the legacy oracle v2 point was 19.6/20.0/20.4/20.8 C, 2.3-2.6 m^3/s).
INTERIOR = [19.6, 20.0, 20.4, 20.8, 21.2, 21.6, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7]
SLOPES = {"pump_hydraulic_power_W": 15., "max_chip_temperature_K": -11.5, "mean_primary_return_temperature_K": 0.}


def interior_point():
    fixture = read_fixture("open-normal")
    liquidrun, steady = with_speed_gradient(fixture, SLOPES)
    context = scenario(fixture["action"])
    model, _ = solve_air(fixture["contract"], fixture["solids"])
    controls = torch.tensor(INTERIOR, dtype=torch.float64)
    fields = model.fields(controls)
    energy, pumps = plant_energy(context, fixture["contract"], fields, liquidrun, steady)
    result = oracle_derivatives(context, fixture["contract"], model, fields, liquidrun, steady, pumps, controls)
    return fixture, model, liquidrun, steady, energy, result


def test_the_pue_gradient_matches_differences_and_its_decomposition_sums():
    fixture, model, liquidrun, steady, energy, result = interior_point()
    pue = result["PUE"]
    assert pue["value"] == pytest.approx(float(energy["total_pue_design"]), rel=1e-14)
    assert pue["control_order"] == control_order(model.active_uids, ["m_30_pump"]) and len(pue["control_order"]) == 13
    assert pue["full_gradient_supported"], pue["unavailable_reasons"]
    assert {b["classification"] for b in pue["branches"].values()} == {"unique_exact_minimum"}
    gradient = np.array(pue["full_gradient"])
    parts = pue["decomposition"]
    assert np.abs(np.sum([parts[k] for k in parts], axis=0) - gradient).max() <= 1e-15
    n = len(model.active_uids)
    for index in range(2 * n):
        step = AIR_STEPS[0] if index < n else AIR_STEPS[1]
        shifted = [torch.tensor(INTERIOR, dtype=torch.float64) for _ in range(2)]
        shifted[0][index] += step
        shifted[1][index] -= step
        difference = (pue_at(fixture, model, liquidrun, steady, shifted[0])
                      - pue_at(fixture, model, liquidrun, steady, shifted[1])) / (2 * step)
        assert abs(gradient[index] - difference) <= PUE_ATOL + PUE_RTOL * abs(difference), (index, difference)
    # The speed partial is the secondary pump's electricity: d(P_hyd)/dN / eta / IT.
    assert gradient[-1] == pytest.approx(15. / .7 / 1000 / float(energy["it_kw"]), rel=1e-12)
    assert parts["air_state"][-1] == 0. and all(v == 0. for v in parts["liquid_state"][:-1])
    # The liquid tangent carries no ACU control; the air tangent no speed.
    assert all(row[-1] == 0. for row in result["air"]["gradients"]["unit_return_c"])
    assert result["liquid"]["gradients"]["max_chip_temperature_K"] == [0.] * 12 + [-11.5]


def test_equal_set_points_are_a_kink_not_a_gradient():
    """The reference design runs every ACU at 20 C: the plant reset is a tie of different functions."""
    fixture = read_fixture("open-normal")
    liquidrun, steady = with_speed_gradient(fixture, SLOPES)
    context = scenario(fixture["action"])
    model, fields = solve_air(fixture["contract"], fixture["solids"])
    _, pumps = plant_energy(context, fixture["contract"], fields, liquidrun, steady)
    pue = oracle_derivatives(context, fixture["contract"], model, fields, liquidrun, steady, pumps)["PUE"]
    assert pue["full_gradient"] is None
    assert pue["branches"]["plant_supply_reset"]["classification"] == "gradient_disagreeing_active_tie"
    assert pue["pump_speed_partial"]["m_30_pump"] > 0


def test_availability_follows_the_hard_minima_and_the_catalog():
    order = ["a", "b"]
    assert classify_active_min([1., 2.], [[1., 0.], [0., 1.]], order)["classification"] == "unique_exact_minimum"
    tie = classify_active_min([1., 1.], [[1., 0.], [0., 1.]], order)
    assert tie["ordinary_first_order_derivative"] is False
    same = classify_active_min([1., 1.], [[1., 2.], [1., 2.]], order)
    assert same["ordinary_first_order_derivative"] is True and same["ordinary_gradient"] == [1., 2.]
    close = classify_active_min([1., 1.], [[1., 2.], [1., 2. + 1e-12]], order)
    assert close["ordinary_first_order_derivative"] is None
    proven = classify_active_min([1., 1.], [[1., 2.], [1., 2. + 1e-12]], order, alias={"verified": True})
    assert proven["classification"] == "source_proven_same_function_alias"
    # In a fault context every active unit runs at its rated flow, the catalog's upper end: no full gradient.
    fixture = read_fixture("open-single-off-m_14")
    liquidrun, steady = with_speed_gradient(read_fixture("open-normal"), {"pump_hydraulic_power_W": 15.})
    context = scenario(fixture["action"], fault_id=fixture["fault_id"])
    model, fields = solve_air(fixture["contract"], fixture["solids"])
    _, pumps = plant_energy(context, fixture["contract"], fields, liquidrun, steady)
    result = oracle_derivatives(context, fixture["contract"], model, fields, liquidrun, steady, pumps)
    pue = result["PUE"]
    assert len(pue["control_order"]) == 11 and pue["full_gradient"] is None
    assert any("catalog endpoint" in r for r in pue["unavailable_reasons"])
    assert pue["pump_speed_partial"]["m_30_pump"] > 0
    assert single_return_alias(model)["verified"] is False


def test_the_design_head_gradient():
    fixture, model, liquidrun, steady, energy, derivatives = interior_point()
    task = T.REGISTERED.load()
    action = copy.deepcopy(fixture["action"])
    for i, uid in enumerate(model.active_uids):
        action["setpoints"][uid] = {"supply_C": INTERIOR[i], "flow_fraction": INTERIOR[6 + i] / T.RATED_FLOW_M3S}
    rows = {r["name"]: r for r in design_head_gradient(action, task, "normal", derivatives)}
    gradient, order = derivatives["PUE"]["full_gradient"], derivatives["PUE"]["control_order"]
    assert rows["m_14:supply_C"]["dPUE"] == gradient[order.index("m_14:supply_temperature_C")]
    assert rows["m_14:flow_fraction"]["dPUE"] == pytest.approx(
        T.RATED_FLOW_M3S * gradient[order.index("m_14:volume_flow_m3s")], rel=1e-15)
    assert rows["m_30:pump_N"]["dPUE"] == gradient[-1]
    assert rows["row:A:center_m"]["dPUE"] is None and rows["row:A:center_m"]["status"].startswith("geometry")
    fault = {r["name"]: r for r in design_head_gradient(action, task, "single-off/m_14", derivatives)}
    assert fault["m_18:flow_fraction"]["dPUE"] == 0. and "rated flow" in fault["m_18:flow_fraction"]["status"]


def test_the_primary_circuit_must_close():
    fixture = read_fixture("open-normal")
    liquidrun, steady = with_speed_gradient(fixture, {"pump_hydraulic_power_W": 15.})
    circuit = primary_circuit(liquidrun, steady)
    assert circuit["capacity_W_per_K"] == 4180.
    liquidrun["primary_circuits"][0]["flow_kg_s"] = 1.1
    with pytest.raises(ValueError, match="not m cp"):
        primary_circuit(liquidrun, steady)


# -- the platform's liquid gradients and the whole oracle (opt-in) ------------------------------------

@pytest.mark.skipif(not SMOKE, reason="solves the liquid network on the platform; set DCDTUNI_AGENT_SMOKE=1")
def test_the_liquid_speed_gradients_and_the_pue_speed_derivative_match_platform_differences():
    import agent.scripted_api as api

    fixture = read_fixture("open-normal")
    liquidrun = api.export_liquidrun(T.REGISTERED.project)["liquidrun"]
    steady = api.solve_liquid_steady(liquidrun, objectives=(*LIQUID_ATOL, "mean_primary_return_temperature_K"),
                                     gradient_parameters=("pump_rpm_ratio",), threads=2, fd_check=1)
    checked = {row["objective"] for row in steady["finiteDifferenceCheck"]}
    assert set(LIQUID_ATOL) <= checked
    for row in steady["finiteDifferenceCheck"]:
        assert "error" not in row, row
        if row["objective"] in LIQUID_ATOL:
            assert row["absoluteError"] <= LIQUID_ATOL[row["objective"]] + LIQUID_RTOL * abs(row["finiteDifference"]), row
    context = scenario(fixture["action"])
    model, fields = solve_air(fixture["contract"], fixture["solids"])
    _, pumps = plant_energy(context, fixture["contract"], fields, liquidrun, steady)
    pue = oracle_derivatives(context, fixture["contract"], model, fields, liquidrun, steady, pumps)["PUE"]
    values = []
    for shift in (PUMP_STEP, -PUMP_STEP):
        run = copy.deepcopy(liquidrun)
        next(e for e in run["edges"] if e["type"] == "pump")["params"]["rpm_ratio"] = 1. + shift
        solved = api.solve_liquid_steady(run, gradient_parameters=(), threads=2)
        rows = {r["rack_id"] + "/" + s["server_id"]: s["heat_removed_W"][0]
                for r in solved["metrics"]["racks"] for s in r["servers"]}
        base = {r["rack_id"] + "/" + s["server_id"]: s["heat_removed_W"][0]
                for r in steady["metrics"]["racks"] for s in r["servers"]}
        # The server air heat does not move with the pump speed (the structural zero the composition uses).
        assert max(abs(rows[k] - base[k]) for k in base) <= 1e-6
        values.append(float(plant_energy(context, fixture["contract"], fields, run, solved)[0]["total_pue_design"]))
    difference = (values[0] - values[1]) / (2 * PUMP_STEP)
    # The reference design's equal set-points leave the full gradient undefined; the speed partial is defined.
    partial = pue["pump_speed_partial"]["m_30_pump"]
    assert abs(partial - difference) <= PUE_ATOL + PUE_RTOL * abs(difference), (partial, difference)
    print("liquid FD rows", [(r["objective"], r["adjoint"], r["finiteDifference"]) for r in steady["finiteDifferenceCheck"]])
    print("PUE speed partial", partial, "FD", difference)


@pytest.mark.skipif(not SMOKE, reason="compiles and solves on the platform; set DCDTUNI_AGENT_SMOKE=1")
def test_the_oracle_reports_its_derivatives(tmp_path):
    from agent.scenarios.hybrid.oracle import lf_design
    from agent.tools.fidelity import Fidelity, evaluate

    action = T.reference_action(T.REGISTERED.load())
    result = lf_design(action, out_dir=tmp_path / "lf", timestamp="2026-10-25T00:00:00.000Z", threads=2,
                       derivatives=True)
    assert result["status"] == "succeeded", result.get("failure")
    assert result["I3_requested"] and len(result["derivatives"]) == 13
    # Six ACUs at one 20 C set-point: the plant reset is a kink; the speed partial stays available.
    assert result["I3"]["PUE_gradient"] is None and result["I3"]["pump_speed_partial"]["m_30_pump"] > 0
    saved = json.loads(Path(result["artifacts"]["derivatives"]).read_text())
    assert saved["air"]["summary"]["linear_residual_inf"] <= 1e-9
    assert saved["liquid"]["gradients"]["pump_hydraulic_power_W"][-1] > 0
    record = evaluate(T.REGISTERED.project, Fidelity.LF, lf=lambda *a, **k: result)
    assert record.derivatives == tuple(result["derivatives"])
