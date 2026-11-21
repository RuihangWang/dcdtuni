import numpy as np
import pytest
from agent.scenarios.air.energy import FanParams,AirProps
from agent.scenarios.hybrid.energy import hybrid_design_power,pump_electric_power_kw
AIR=AirProps(rho=1.19,cp=1005.)
FANS=[FanParams(3.,2.4,provenance="explicit mock Shinaide reference, not vendor rating")]*4
RETURN=19.+63000./(AIR.rho*AIR.cp*12.)
def case(liquid_return=25.,**updates):
    args=dict(it_kw=84.,air_heat_kw=63.,acu_flow_m3s=np.full(4,3.),
        acu_t_supply_c=np.full(4,19.),acu_t_return_c=np.full(4,RETURN),fans=FANS,
        liquid_load_kw=np.array([21.]),liquid_t_supply_c=np.array([20.]),
        liquid_t_return_c=np.array([liquid_return]),secondary_liquid_pump_kw=np.array([.01]),air=AIR)
    args.update(updates);return hybrid_design_power(**args)

def test_one_shared_plant_and_one_IT_denominator_conserve_branch_heat():
    r=case()
    assert r["plant_cooling_load_kw"]==pytest.approx(84.+9.6)
    assert abs(r["air_heat_closure_kw"])<1e-10
    assert r["t_chws_c"]==pytest.approx(15.)
    assert r["liquid_fresh_flow_fraction"][0]==pytest.approx(.5)
    assert r["liquid_fresh_capacity_flow_kw_per_K"][0]*(25.-15.)==pytest.approx(21.)
    assert r["total_primary_capacity_flow_kw_per_K"]*(r["t_chwr_c"]-15.)==pytest.approx(93.6)
    direct=84.+9.6+.01+r["chiller_kw"]+r["chw_pump_kw"]+r["rejection_kw"]
    assert r["total_kw"]==pytest.approx(direct)
    assert r["total_pue_design"]==pytest.approx(direct/84.)

def test_model_head_and_efficiency_are_required_and_watts_not_flow_alone():
    r=pump_electric_power_kw(np.array([.5]),np.array([10000.]),np.array([.5]),
        rho_kg_m3=1000.,head_convention="declared lumped DAE pressure rise",
        efficiency_provenance="mock test assumption")
    assert r["hydraulic_kw"][0]==pytest.approx(.005)
    assert r["electrical_kw"][0]==pytest.approx(.01)
    with pytest.raises(ValueError,match="provenance"):
        pump_electric_power_kw([.5],[10000.],[.5],rho_kg_m3=1000.,head_convention="",efficiency_provenance="")

def test_impossible_heat_partition_is_not_hidden_in_PUE():
    with pytest.raises(ValueError,match="partition"):case(liquid_load_kw=np.array([22.]))

def test_unavailable_airflow_rating_is_not_silently_replaced():
    with pytest.raises(ValueError,match="catalog"):case(fans=[FanParams(2.8,2.4)]*4)

def test_pure_liquid_limit_has_no_artificial_air_heat():
    r=hybrid_design_power(21.,0.,[],[],[],[],[21.],[20.],[25.],[.01])
    assert r["air_fan_total_kw"]==0
    assert r["plant_cooling_load_kw"]==21.
    assert r["t_chws_c"]==20.
    assert r["t_chwr_c"]==25.

def test_torch_input_gradient_matches_two_difference_steps():
    import torch
    t=torch.tensor([25.],dtype=torch.float64,requires_grad=True)
    r=case(liquid_t_return_c=t)
    derivative=torch.autograd.grad(r["total_kw"],t)[0].item()
    assert derivative<0
    for h in [1e-3,5e-4]:
        fd=(case(25.+h)["total_kw"]-case(25.-h)["total_kw"])/(2*h)
        assert derivative==pytest.approx(float(fd),rel=2e-6)
