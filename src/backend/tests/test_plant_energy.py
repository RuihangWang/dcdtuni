"""agent.plant.energy: facility power and design PUE of air-only and hybrid operating points."""
import math

import numpy as np
import pytest

from agent.plant import energy as E

RHO, CP = 1.18, 1006.0
FANS = [E.FanParams(rated_flow_m3s=2.8, rated_power_kw=2.4) for _ in range(4)]


def _air_point(it_kw=100.0):
    """Four ACUs whose measured room heat equals the declared air heat exactly."""
    flow = np.array([2.0, 1.8, 2.2, 1.6])
    supply = np.array([18.0, 18.5, 19.0, 18.2])
    share = flow / flow.sum() * it_kw
    ret = supply + share * 1000.0 / (RHO * CP * flow)
    return flow, supply, ret


def test_air_only_accounting_and_reset():
    flow, supply, ret = _air_point()
    r = E.facility_power(100.0, 100.0, flow, supply, ret, FANS)
    coil, plant = E.CoilParams(), E.PlantParams()
    ts = supply.min() - coil.approach_k
    tw = ts + min(ret.min() - coil.pinch_k - ts, plant.dt_w_max_k)
    fan = 2.4 * (flow / 2.8) ** 2.8
    assert r["t_chws_c"] == pytest.approx(ts)
    assert r["t_chwr_c"] == pytest.approx(tw)          # no liquid branch: one air-coil return
    assert r["plant_load_kw"] == pytest.approx(100.0 + fan.sum())
    p = E.primary_power_kw(100.0 + fan.sum(), ts, tw, plant, 100.0 + 4 * 2.4)
    assert r["total_kw"] == pytest.approx(100.0 + fan.sum() + p["primary_kw"])
    assert r["pue"] == pytest.approx(r["total_kw"] / 100.0)
    assert abs(float(r["air_heat_closure_kw"])) < 1e-9
    assert 1.0 < float(r["pue"]) < 1.5


def test_warmer_supply_lowers_chiller_power():
    flow, supply, ret = _air_point()
    cold = E.facility_power(100.0, 100.0, flow, supply, ret, FANS)
    warm = E.facility_power(100.0, 100.0, flow, supply + 1.0, ret + 1.0, FANS)
    assert warm["chiller_kw"] < cold["chiller_kw"]
    assert warm["pue"] < cold["pue"]


def test_torch_matches_numpy_and_gradient_matches_finite_differences():
    torch = pytest.importorskip("torch")
    flow, supply, ret = _air_point()
    ref = E.facility_power(100.0, 100.0, flow, supply, ret, FANS, tau_k=0.05)
    f = torch.tensor(flow, dtype=torch.float64, requires_grad=True)
    s = torch.tensor(supply, dtype=torch.float64, requires_grad=True)
    t = torch.tensor(ret, dtype=torch.float64)
    out = E.facility_power(torch.tensor(100.0, dtype=torch.float64), 100.0, f, s, t, FANS, tau_k=0.05)
    assert float(out["pue"].detach()) == pytest.approx(float(ref["pue"]), rel=1e-12)
    gf, gs = torch.autograd.grad(out["pue"], (f, s))
    h = 1e-6
    for i in range(4):
        for grad, base, which in ((gf, flow, "flow"), (gs, supply, "supply")):
            up, dn = base.copy(), base.copy()
            up[i] += h
            dn[i] -= h
            args_up = (up, supply) if which == "flow" else (flow, up)
            args_dn = (dn, supply) if which == "flow" else (flow, dn)
            fd = (E.facility_power(100.0, 100.0, *args_up, ret, FANS, tau_k=0.05)["pue"]
                  - E.facility_power(100.0, 100.0, *args_dn, ret, FANS, tau_k=0.05)["pue"]) / (2 * h)
            assert float(grad[i]) == pytest.approx(float(fd), rel=1e-5, abs=1e-9)


def test_smooth_reset_is_conservative():
    flow, supply, ret = _air_point()
    hard = E.facility_power(100.0, 100.0, flow, supply, ret, FANS)
    soft = E.facility_power(100.0, 100.0, flow, supply, ret, FANS, tau_k=0.1)
    assert soft["t_chws_c"] <= hard["t_chws_c"]
    assert soft["t_chws_c"] >= hard["t_chws_c"] - 0.1 * math.log(4) - 1e-12


def test_hybrid_shared_plant():
    flow, supply, ret = _air_point(it_kw=40.0)
    ql = np.array([35.0, 25.0])
    tls = np.array([25.0, 27.0])          # warmer than the plant supply: ideal recirculation
    tlr = np.array([33.0, 35.0])
    sec = np.array([0.8, 0.6])
    r = E.facility_power(100.0, 40.0, flow, supply, ret, FANS, liquid_heat_kw=ql,
                         liquid_t_supply_c=tls, liquid_t_return_c=tlr, secondary_pump_kw=sec)
    fan = 2.4 * (flow / 2.8) ** 2.8
    ts = supply.min() - 4.0
    assert r["t_chws_c"] == pytest.approx(ts)
    assert r["plant_load_kw"] == pytest.approx(40.0 + fan.sum() + ql.sum())
    mix = (ql / (tlr - ts)) / (ql / (tlr - tls))
    assert np.allclose(r["liquid_fresh_flow_fraction"], mix) and (mix < 1).all()
    cap = (40.0 + fan.sum()) / (r["t_chwr_air_c"] - ts) + (ql / (tlr - ts)).sum()
    assert r["t_chwr_c"] == pytest.approx(ts + r["plant_load_kw"] / cap)
    assert r["total_kw"] == pytest.approx(100.0 + fan.sum() + sec.sum() + r["primary_kw"])
    # a liquid loop colder than every coil sets the plant supply
    cold = E.facility_power(100.0, 40.0, flow, supply, ret, FANS, liquid_heat_kw=ql,
                            liquid_t_supply_c=np.array([12.0, 27.0]), liquid_t_return_c=tlr,
                            secondary_pump_kw=sec)
    assert cold["t_chws_c"] == pytest.approx(12.0)
    with pytest.raises(ValueError, match="heat partition"):
        E.facility_power(100.0, 45.0, flow, supply, ret, FANS, liquid_heat_kw=ql,
                         liquid_t_supply_c=tls, liquid_t_return_c=tlr, secondary_pump_kw=sec)


def test_closed_acu_keeps_installed_design_load():
    flow, supply, ret = _air_point()
    flow = flow.copy()
    flow[[0, 1, 2]] *= flow.sum() / flow[[0, 1, 2]].sum()   # three units carry the room
    flow[3] = 0.0
    share = flow[:3] / flow[:3].sum() * 100.0
    ret = ret.copy()
    ret[:3] = supply[:3] + share * 1000.0 / (RHO * CP * flow[:3])
    ret[3] = np.nan                                       # no return state for a closed unit
    active = [True, True, True, False]
    r = E.facility_power(100.0, 100.0, flow, supply, ret, FANS, acu_active=active)
    assert r["acu_active"] == (True, True, True, False)
    assert r["plant_design_load_kw"] == pytest.approx(100.0 + 4 * 2.4)
    assert len(r["fan_kw"]) == 3
    bad = flow.copy()
    bad[3] = 0.5
    with pytest.raises(ValueError, match="closed ACU"):
        E.facility_power(100.0, 100.0, bad, supply, ret, FANS, acu_active=active)


def test_infeasible_points_are_reported_not_clipped():
    flow, supply, ret = _air_point()
    with pytest.raises(ValueError, match="fan domain"):
        E.facility_power(100.0, 100.0, flow * 1.5, supply, ret, FANS)
    with pytest.raises(ValueError, match="return >= delivered supply"):
        E.facility_power(100.0, 100.0, flow, supply, supply - 1.0, FANS)
    with pytest.raises(ValueError, match="one supply and one return"):
        E.facility_power(100.0, 100.0, flow, supply[:3], ret, FANS)


def test_doe2_curve_must_be_normalized_at_reference():
    good = E.PlantParams(chiller_model="doe2", eirft=(1.0, 0, 0, 0, 0, 0), curve_provenance="unit test",
                         curve_t_chws_range_c=(4.0, 20.0), curve_t_cwe_range_c=(20.0, 40.0))
    assert float(E.chiller_eir(10.0, good)) == pytest.approx(1 / 6.0)
    bad = E.PlantParams(chiller_model="doe2", eirft=(1.2, 0, 0, 0, 0, 0), curve_provenance="unit test",
                        curve_t_chws_range_c=(4.0, 20.0), curve_t_cwe_range_c=(20.0, 40.0))
    with pytest.raises(ValueError, match="normalized"):
        E.chiller_eir(10.0, bad)


def test_pump_electric_power():
    r = E.pump_electric_power_kw([2.0], [150e3], [0.6], rho_kg_m3=1000.0,
                                 head_convention="total head gain", efficiency_provenance="unit test")
    assert float(r["hydraulic_kw"][0]) == pytest.approx(0.3)
    assert float(r["electrical_kw"][0]) == pytest.approx(0.5)
