"""agent.dcdesign.l0: the hall's zonal network, its convergence over the design space, and the plant objective.

The liquid side is stubbed with the DAE result of the base hall (G-D0 of the ISPO plan:
21 kW into the CDU primary at 20 degC inlet and 1 kg/s, primary return 298.17 K); the full
path runs the DAE through agent.scripted_api.liquid on a host with the headless exporter.
"""
import random

import pytest

torch = pytest.importorskip("torch")

from agent.dcdesign import compiler as C  # noqa: E402
from agent.dcdesign import l0  # noqa: E402
from agent.dcdesign import spec as S  # noqa: E402

LIQUID = {"metrics": {"primaryReturnK": {"m_30_hx": 298.17}, "maxChipTemperatureK": 338.89, "heatInputW": 21000.0},
          "objectives": {"pump_hydraulic_power_W": 150.0}}


def _design(family, **kw):
    return S.Design(**{**C.reference_design(family).to_json(), **kw})


@pytest.mark.parametrize("family", list(S.FAMILIES))
def test_reference_designs_close_heat_and_mass(family):
    p = C.compile_design(S.Task(), C.reference_design(family))
    r = l0.evaluate(p, LIQUID)
    assert abs(float(r["air_heat_closure_kw"])) < 1e-6          # the coils remove exactly the servers' air heat
    assert r["declared_liquid_kw"] == pytest.approx(21.0)
    assert float(r["plant"]["liquid_heat_kw"]) == pytest.approx(21.0)
    assert float(r["plant"]["it_kw"]) == pytest.approx(84.0)
    assert 1.0 < float(r["pue"]) < 1.6
    inlet = r["server_inlet_c"]
    assert inlet.shape == (56,) and float(inlet.min()) >= min(C.reference_design(family).acu_supply_c) - 1e-6


def test_random_designs_converge_in_every_family():
    rng = random.Random(1)
    for family in S.FAMILIES:
        for _ in range(8):
            kw = {"acu_supply_c": tuple(rng.uniform(16, 27) for _ in range(4)),
                  "acu_flow_fraction": tuple(rng.uniform(0.22, 1.0) for _ in range(4))}
            if S.FAMILIES[family]["units"] == "perimeter":
                kw["tile_count"] = rng.choice(S.TILE_COUNTS)
            if rng.random() < 0.25:
                kw["closed_units"] = (rng.randrange(4),)
            hn = l0.build_network(C.compile_design(S.Task(), _design(family, **kw)))
            state, info = hn.network.solve(hn.nominal_controls, hn.areas)
            assert info["residual_inf"] < 1e-10


def test_pressurized_plenum_converges_by_newton():
    """ISPO data-hall task validity (2026-10-21): with cold-aisle containment and few tiles, the perimeter
    plenum must reach about 140 Pa; from the 1 Pa linear network Newton hit its iteration limit and the
    pseudo-transient fallback crawled (33 of 1440 grid designs failed, all PERIM_CAC). The Picard
    initial state (isothermal orifice network) starts Newton next to the solution."""
    for tiles in S.TILE_COUNTS:
        for scale in (0.6, 1.0, 1.278, 1.6):
            for frac in (0.829, 1.0):
                for t in (18.0, 22.0):
                    d = _design("PERIM_CAC", tile_count=tiles, acu_supply_c=(t,) * 4, acu_flow_fraction=(frac,) * 4)
                    hn = l0.build_network(C.compile_design(S.Task(it_scale=scale), d))
                    _, info = hn.network.solve(hn.nominal_controls, hn.areas)
                    assert info["method"] == "newton" and info["residual_inf"] < 1e-10, (tiles, scale, frac, t)


@pytest.mark.parametrize("units", ["INROW", "PERIM"])
def test_containment_lowers_the_hottest_inlet_when_supply_is_short(units):
    def hottest(family):
        p = C.compile_design(S.Task(), _design(family, acu_flow_fraction=(0.4,) * 4))
        return float(l0.evaluate(p, LIQUID)["server_inlet_c"].max())

    assert hottest(f"{units}_CAC") < hottest(f"{units}_OPEN")


def test_lower_flow_saves_fan_power_and_raises_inlets():
    full = l0.evaluate(C.compile_design(S.Task(), _design("INROW_OPEN", acu_flow_fraction=(0.9,) * 4)), LIQUID)
    half = l0.evaluate(C.compile_design(S.Task(), _design("INROW_OPEN", acu_flow_fraction=(0.45,) * 4)), LIQUID)
    assert float(half["plant"]["fan_total_kw"]) < float(full["plant"]["fan_total_kw"])
    assert float(half["server_inlet_c"].max()) > float(full["server_inlet_c"].max())


def test_pue_gradient_matches_finite_differences():
    p = C.compile_design(S.Task(), _design("PERIM_CAC", acu_flow_fraction=(0.6, 0.7, 0.65, 0.75)))
    hn = l0.build_network(p)
    c0 = hn.nominal_controls

    def pue(c, diff=False):
        return l0.evaluate(p, LIQUID, controls=c, differentiable=diff)["pue"]

    c = c0.clone().requires_grad_(True)
    g, = torch.autograd.grad(pue(c, True), c)
    h = 1e-5
    for i in range(len(c0)):
        e = torch.zeros_like(c0)
        e[i] = h
        fd = (float(pue(c0 + e)) - float(pue(c0 - e))) / (2 * h)
        assert float(g[i]) == pytest.approx(fd, rel=1e-4, abs=1e-8)


def test_closed_unit_is_out_of_the_network_and_in_the_ledger():
    p = C.compile_design(S.Task(), _design("PERIM_HAC", acu_flow_fraction=(0.8,) * 4, closed_units=(1,)))
    r = l0.evaluate(p, LIQUID)
    assert r["units"] == ("crah_01", "crah_03", "crah_04")
    assert r["plant"]["acu_active"] == (True, False, True, True)
    assert len(r["plant"]["fan_kw"]) == 3


def test_unit_set_above_its_return_is_infeasible_not_an_error():
    """ISPO data-hall P0-L0 (2026-10-21): at partial load one unit's setpoint 0.2-0.7 K above the shared return
    zone made the plant raise "an active ACU has positive flow and return >= delivered supply". The plant now sees
    that unit with its coil off and the constraint unit_heating reports the excess. Where every return is above
    its setpoint the clamp max(return, setpoint) is the identity, so those designs are unchanged."""
    task = S.Task(it_scale=0.61)
    hot = _design("PERIM_CAC", tile_count=16, acu_supply_c=(17.8, 21.95, 18.3, 19.45),
                  acu_flow_fraction=(0.71, 0.83, 0.82, 0.79))
    r = l0.evaluate(C.compile_design(task, hot), LIQUID)
    assert 0.0 < float(r["constraints"]["unit_heating"]) < 2.0 and 1.0 < float(r["pue"]) < 1.6
    assert float(r["unit_return_c"].min()) < max(hot.acu_supply_c)
    normal = _design("PERIM_CAC", tile_count=16, acu_supply_c=(18.0,) * 4, acu_flow_fraction=(0.8,) * 4)
    r = l0.evaluate(C.compile_design(task, normal), LIQUID)
    assert float(r["constraints"]["unit_heating"]) < 0.0
    assert bool((r["unit_return_c"] > 18.0).all())


def test_plant_parameters_reach_the_chiller_and_the_default_is_unchanged():
    """``plant`` replaces the shared plant's parameters for this L0 call only (ISPO M2's L0 variant)."""
    from agent.plant.energy import PlantParams

    p = C.compile_design(S.Task(), _design("INROW_OPEN"))
    base = l0.evaluate(p, LIQUID)
    same = l0.evaluate(p, LIQUID, plant=PlantParams())
    assert float(same["pue"]) == float(base["pue"])
    ts0 = float(base["plant"]["t_chws_c"])
    assert ts0 > PlantParams().t_ref_c
    # no temperature sensitivity: the chiller runs at COPref, dearer than the default above Tref
    flat = l0.evaluate(p, LIQUID, plant=PlantParams(k_per_k=0.0))
    assert float(flat["plant"]["chiller_kw"]) > float(base["plant"]["chiller_kw"])
    # half the sensitivity pivoted at this design's chilled-water supply: same chiller power here
    rho, ref = 0.5, PlantParams()
    pivot = PlantParams(k_per_k=rho * ref.k_per_k, t_ref_c=ts0 - (ts0 - ref.t_ref_c) / rho)
    piv = l0.evaluate(p, LIQUID, plant=pivot)
    assert float(piv["plant"]["chiller_kw"]) == pytest.approx(float(base["plant"]["chiller_kw"]), rel=1e-9)
