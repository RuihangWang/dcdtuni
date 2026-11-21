"""Independent thermodynamic counterexamples and directional sensitivity checks."""
import numpy as np
import pytest
from agent.scenarios.air.energy import FanParams,AirProps,PlantParams,CoilParams,design_power
from agent.scenarios.air.energy_coilwise import design_power_coilwise

FANS=[FanParams(2.,.1),FanParams(2.,.1)]
AIR=AirProps(rho=1.19,cp=1005.)
IT=AIR.rho*AIR.cp*20./1000.
def evaluate(returns,**kwargs):
    return design_power_coilwise(IT,np.ones(2),np.full(2,22.),returns,FANS,air=AIR,**kwargs)

def test_closed_room_mixed_return_can_hide_a_single_coil_pinch_violation():
    old=design_power(IT,np.ones(2),np.full(2,22.),np.array([26.,38.]),FANS,air=AIR)
    assert abs(old["energy_closure_kw"])<1e-12
    assert 26.-old["t_chwr_c"]<0
    result=evaluate(np.array([26.,38.]))
    assert abs(result["energy_closure_kw"])<1e-12
    assert np.all(np.array([26.,38.])-result["t_chwr_c"]>=2.-1e-12)
    assert result["t_chwr_c"]==pytest.approx(24.)
    assert result["chiller_kw"]==pytest.approx(old["chiller_kw"])
    assert result["chw_pump_kw"]>old["chw_pump_kw"]

def test_uniform_return_recovers_same_common_return_setpoint():
    result=evaluate(np.array([32.,32.]))
    old=design_power(IT,np.ones(2),np.full(2,22.),np.array([32.,32.]),FANS,air=AIR)
    assert result["total_kw"]==pytest.approx(old["total_kw"])

def test_fixed_design_water_delta_rejects_local_pinch_even_with_feasible_average():
    with pytest.raises(ValueError,match="pinch"):
        evaluate(np.array([24.,40.]),plant=PlantParams(dt_w_mode="fixed"))

def test_torch_physical_redistribution_direction_agrees_with_two_fd_sizes():
    import torch
    returns=torch.tensor([26.,38.],dtype=torch.float64,requires_grad=True)
    result=design_power_coilwise(IT,torch.ones(2,dtype=torch.float64),
        torch.full((2,),22.,dtype=torch.float64),returns,FANS,air=AIR)
    direction=torch.tensor([1.,-1.],dtype=torch.float64)
    gradient=torch.autograd.grad(result["total_kw"],returns)[0]@direction
    assert float(gradient)<0.
    for step in [1e-3,5e-4]:
        plus=evaluate(np.array([26.+step,38.-step]))["total_kw"]
        minus=evaluate(np.array([26.-step,38.+step]))["total_kw"]
        assert float(gradient)==pytest.approx(float((plus-minus)/(2*step)),rel=1e-6)

def test_explicit_smooth_proxy_retains_every_coil_pinch():
    result=evaluate(np.array([26.,38.]),tau_k=.1)
    assert result["reset_mode"]=="smooth_proxy"
    assert min(result["per_coil_hot_end_gap_k"])>=2.
