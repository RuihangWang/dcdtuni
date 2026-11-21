import math
from dataclasses import replace
import numpy as np
import pytest
import torch
from agent.scenarios.air.energy import (FanParams,CoilParams,PlantParams,AirProps,
    chiller_eir,design_power,primary_power_kw,return_water_c)

FANS=(FanParams(2.0,1.5),FanParams(2.0,1.5))
AIR=AirProps()

def inputs(tsa=(20.,20.),flow=(1.6,1.6),it=40.):
    v=np.array(flow,dtype=float);s=np.array(tsa,dtype=float)
    r=s+(it/len(v))*1000/(AIR.rho*AIR.cp*v)
    return v,s,r

def run(tsa=(20.,20.),flow=(1.6,1.6),it=40.,**kw):
    return design_power(it,*inputs(tsa,flow,it),FANS,**kw)

def test_noninteger_fan_law_and_room_fan_heat_accounting():
    o=run(flow=(2.,1.))
    assert o['fan_kw'][1]==pytest.approx(1.5*0.5**2.8)
    assert np.sum(o['acu_room_heat_kw'])==pytest.approx(40.)
    assert o['cooling_load_kw']==pytest.approx(40.+o['fan_total_kw'])
    assert np.sum(o['acu_coil_kw'])==pytest.approx(o['cooling_load_kw'])
    assert o['total_kw']==pytest.approx(40.+o['fan_total_kw']+o['primary_kw'])
    assert o['energy_closure']==pytest.approx(0.,abs=1e-14)

def test_reset_uses_coldest_delivered_supply_without_soft_size_offset():
    a=run(tsa=(20.,23.));b=run()
    assert a['t_chws_c']==pytest.approx(16.)
    assert b['t_chws_c']==pytest.approx(16.)
    duplicated=design_power(80.,*[np.tile(x,2) for x in inputs()],FANS*2)
    assert duplicated['t_chws_c']==pytest.approx(b['t_chws_c'])
    assert duplicated['pue']==pytest.approx(b['pue'])
    assert run(tau_k=.25)['t_chws_c']==pytest.approx(16.-.25*math.log(2))
    assert run(tau_k=.25)['reset_mode']=='smooth_proxy'

def test_postfan_temperature_location_and_no_thermal_crossover():
    o=run()
    heat=1.18*1006*np.array([1.6,1.6])*o['fan_air_rise_k']/1000
    assert heat==pytest.approx(o['fan_kw'])
    assert o['coil_leaving_air_c']==pytest.approx(np.array([20.,20.])-o['fan_air_rise_k'])
    assert np.all(o['coil_cold_end_approach_k']>0)
    with pytest.raises(ValueError,match='crossover'):
        design_power(40.,*inputs(),(FanParams(2.,50.),)*2)

def test_warmer_supply_and_return_have_different_primary_side_effects():
    fixed=PlantParams(dt_w_mode='fixed')
    a=run(tsa=(18.,18.),plant=fixed);b=run(tsa=(20.,20.),plant=fixed)
    assert b['chiller_kw']/a['chiller_kw']==pytest.approx(math.exp(-.032*2))
    cold=primary_power_kw(40.,16.,22.,PlantParams(),43.)
    warm=primary_power_kw(40.,16.,26.,PlantParams(),43.)
    assert warm['chw_pump_kw']/cold['chw_pump_kw']==pytest.approx((6/10)**2)
    assert warm['chiller_kw']==pytest.approx(cold['chiller_kw'])

def test_coil_limited_rule_rejects_pinch_conflict_instead_of_clamping_it_away():
    p=PlantParams();c=CoilParams()
    assert return_water_c(16.,24.,p,c)==pytest.approx(22.)
    assert return_water_c(16.,40.,p,c)==pytest.approx(26.)
    with pytest.raises(ValueError,match='minimum water dT'):
        return_water_c(16.,18.5,p,c)
    with pytest.raises(ValueError,match='fixed return-water'):
        return_water_c(16.,20.,replace(p,dt_w_mode='fixed'),c)

def test_total_design_pue_and_partial_cooling_pue_do_not_conflate_other_loads():
    a=run();b=run(other_kw_fraction=.05)
    assert b['pue']-a['pue']==pytest.approx(.05)
    assert b['ppue_cool']==pytest.approx(a['ppue_cool'])
    assert b['total_pue_design']==pytest.approx(b['pue'])
    v,s,r=inputs(it=42.)
    o=design_power(40.,v,s,r,FANS,other_kw_fraction=.05,other_air_heat_kw=2.)
    assert o['cooling_load_kw']==pytest.approx(42.+o['fan_total_kw'])
    assert o['energy_closure']==pytest.approx(0.,abs=1e-14)

def test_carnot_kelvin_calibration_and_unphysical_lift_refusal():
    p=PlantParams(chiller_model='carnot')
    assert 1/chiller_eir(7.,p)==pytest.approx(p.cop_ref)
    assert 1/chiller_eir(18.,p)>p.cop_ref
    with pytest.raises(ValueError,match='Kelvin'):
        chiller_eir(40.,p)

def test_doe2_temperature_factor_requires_provenance_and_domain():
    with pytest.raises(ValueError,match='source-identified'):
        chiller_eir(12.,PlantParams(chiller_model='doe2'))
    p=PlantParams(chiller_model='doe2',eirft=(1.,0.,0.,0.,0.,0.),
                  curve_provenance='manufactured flat test curve; not equipment data',
                  curve_t_chws_range_c=(5.,20.),curve_t_cwe_range_c=(20.,35.))
    assert chiller_eir(12.,p)==pytest.approx(1/6)
    with pytest.raises(ValueError,match='outside'):
        chiller_eir(25.,p)
    with pytest.raises(ValueError,match='positive'):
        chiller_eir(12.,replace(p,eirft=(-1.,0.,0.,0.,0.,0.)))

@pytest.mark.parametrize('field,value',[('flow',[0.,1.6]),('flow',[-1.,1.6]),
    ('flow',[2.1,1.6]),('tsa',[math.nan,20.]),('tra',[math.inf,31.])])
def test_invalid_inputs_fail_closed(field,value):
    v,s,r=inputs();items={'flow':v,'tsa':s,'tra':r};items[field]=np.array(value)
    with pytest.raises(ValueError):design_power(40.,items['flow'],items['tsa'],items['tra'],FANS)

def test_shapes_and_immutable_task_parameter_bounds():
    with pytest.raises(ValueError):design_power(0.,*inputs(),FANS)
    with pytest.raises(ValueError):design_power(40.,[1.6],[20.],[30.],FANS)
    with pytest.raises(ValueError):PlantParams(dt_w_min_k=7.,dt_w_design_k=6.)
    with pytest.raises(ValueError):AirProps(rho=0.)
    with pytest.raises(ValueError):FanParams(0.,1.)

def test_numpy_torch_agree_and_parameter_derivatives_match_independent_fd():
    plant=PlantParams(dt_w_mode='fixed')
    v,s,r=inputs(tsa=(20.,21.))
    vt=torch.tensor(v,dtype=torch.float64,requires_grad=True)
    st=torch.tensor(s,dtype=torch.float64,requires_grad=True)
    rt=st+20e3/(AIR.rho*AIR.cp*vt)
    o=design_power(40.,vt,st,rt,FANS,plant=plant)
    reference=run(tsa=(20.,21.),plant=plant)
    assert o['pue'].item()==pytest.approx(reference['pue'],rel=1e-13)
    o['pue'].backward()
    assert st.grad[0]<0 and st.grad[1]==pytest.approx(0.)
    assert torch.all(vt.grad>0)
    h=1e-5
    plus=run(tsa=(20.+h,21.),plant=plant)['pue'];minus=run(tsa=(20.-h,21.),plant=plant)['pue']
    assert st.grad[0].item()==pytest.approx((plus-minus)/(2*h),rel=1e-7)
    plus=run(tsa=(20.,21.),flow=(1.6+h,1.6),plant=plant)['pue']
    minus=run(tsa=(20.,21.),flow=(1.6-h,1.6),plant=plant)['pue']
    assert vt.grad[0].item()==pytest.approx((plus-minus)/(2*h),rel=1e-7)


def test_wrong_kelvin_as_celsius_and_nonfinite_fan_domain_are_refused():
    with pytest.raises(ValueError,match='degC'):
        chiller_eir(300.,PlantParams())
    with pytest.raises(ValueError):FanParams(2.,1.5,max_fraction=math.inf)


def test_curve_reference_normalization_and_freezing_are_enforced():
    coeff=[2.,0.,0.,0.,0.,0.]
    p=PlantParams(chiller_model='doe2',eirft=coeff,curve_provenance='manufactured test',
                  curve_t_chws_range_c=(5.,20.),curve_t_cwe_range_c=(20.,35.))
    coeff[0]=1.
    assert p.eirft[0]==2.
    with pytest.raises(ValueError,match='normalized'):chiller_eir(12.,p)
