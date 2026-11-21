import math
import numpy as np
import pytest
import torch
from agent.scenarios.air.objective import (ASHRAE_CLASSES,ThermalLimits,ScoreWeights,
    margins,violations,is_feasible,score,rank_key,smooth_objective,exhaust_stat,i1_vector)


def v(tin=(24.,),tex=(40.,),**kw):
    ns=len(tin);nacu=2
    return violations(margins(np.array(tin),np.array(tex),np.array([30.,32.]),
        np.array([20.,20.]),np.array([30.,30.]),**kw))


def test_primary_checked_temperature_envelopes_and_separate_server_acu_shapes():
    assert ASHRAE_CLASSES['H1'].t_in_allow_min_c==15.
    assert ASHRAE_CLASSES['H1'].t_in_allow_max_c==25.
    assert is_feasible(v(tin=(24.,25.,26.),tex=(40.,41.,42.)))
    high=v(tin=(24.,26.),tex=(40.,42.),lim=ASHRAE_CLASSES['H1'])
    assert high['inlet_hi_k']==pytest.approx([2.,4.])
    assert not is_feasible(high)


def test_every_finite_feasible_scalar_is_above_infeasible_even_at_huge_pue():
    ok=score(1e12,np.array([44.9]),v(tin=(26.9,),tex=(44.9,)))
    bad=score(1.01,np.array([30.]),v(tin=(27.001,),tex=(30.,)))
    assert -1<=ok<=1 and ok>bad
    assert score(None,None,None,evaluation_valid=False)<bad


def test_exact_lexicographic_selection_differs_from_weighted_scalarization():
    hot=(1.2,np.array([44.]),v(tex=(44.,)))
    cool=(1.200001,np.array([30.]),v(tex=(30.,)))
    assert rank_key(*hot)>rank_key(*cool)
    assert score(*hot)<score(*cool)  # documented tiny-primary/secondary trade-off
    assert rank_key(1.2,np.array([38.]),v(tex=(38.,)))>rank_key(*hot)
    assert rank_key(3.,np.array([44.]),v(tex=(44.,)))>rank_key(1.01,np.array([30.]),v(tin=(27.1,),tex=(30.,)))


def test_main_weight_dominates_large_pue_gap_without_claiming_exact_scalar_lex():
    assert score(1.2,np.array([44.]),v(tex=(44.,)))>score(1.25,np.array([30.]),v(tex=(30.,)))


def test_closure_is_optional_explicit_constraint_and_not_silently_accepted():
    close=v(energy_closure=.009);bad=v(energy_closure=.1)
    assert is_feasible(close) and not is_feasible(bad)
    assert bad['energy_closure_frac']==pytest.approx(.09)


@pytest.mark.parametrize('kind',['empty','nan','negative','incomplete'])
def test_invalid_feedback_is_not_a_feasible_design(kind):
    vv=v()
    if kind=='empty':vv={}
    if kind=='nan':vv['inlet_hi_k']=np.array([math.nan])
    if kind=='negative':vv['inlet_hi_k']=np.array([-1.])
    if kind=='incomplete':del vv['return_hi_k']
    with pytest.raises(ValueError):score(1.2,np.array([40.]),vv)


def test_invalid_pue_and_capacity_shape_are_refused():
    with pytest.raises(ValueError):score(.99,np.array([40.]),v())
    with pytest.raises(ValueError):margins([24.],[40.],[30.,31.],[20.],[30.,30.])
    with pytest.raises(ValueError):margins([24.],[40.],[30.],[20.],[0.])


def test_exhaust_aggregation_and_stable_smooth_violations():
    assert exhaust_stat([30.,41.,40.5],'mean')==pytest.approx(111.5/3)
    assert exhaust_stat([30.,41.,40.5],'softmax')>=41.
    sm=violations({'a':np.array([-1e6,0.,1e6])},smooth_beta=8.)['a']
    assert np.isfinite(sm).all() and sm[0]>=0
    assert sm[1]==pytest.approx(math.log(2)/8) and sm[2]==pytest.approx(1e6)
    with pytest.raises(ValueError):exhaust_stat([])


def test_i1_components_are_recorded_without_fake_token_attribution():
    energy=dict(chiller_kw=1.,chw_pump_kw=.1,rejection_kw=.2,fan_kw=np.array([.5,.5]),other_kw=0.)
    out=i1_vector(energy,v(),np.array([40.]))
    assert {'power.fan_kw','viol.inlet_hi_k','exhaust_c'}<=set(out)
    assert out['power.fan_kw'].shape==(2,)


def test_smooth_proxy_has_correct_forward_derivative_and_is_not_hard_i0():
    tin=torch.tensor([26.5,26.8],dtype=torch.float64,requires_grad=True)
    tex=tin+16.;z=torch.zeros(2,dtype=torch.float64)
    mm=margins(tin,tex,z+30,z+20,z+30)
    sm=violations(mm,smooth_beta=4.)
    obj=smooth_objective(torch.tensor(1.3,dtype=torch.float64),tex,sm)
    obj.backward();assert torch.all(tin.grad<0)
    def proxy(x):
        ti=np.array([x,26.8]);te=ti+16.
        m=margins(ti,te,[30.,30.],[20.,20.],[30.,30.])
        return smooth_objective(1.3,te,violations(m,smooth_beta=4.))
    h=1e-5
    assert tin.grad[0].item()==pytest.approx((proxy(26.5+h)-proxy(26.5-h))/(2*h),rel=1e-7)


def test_weight_scales_are_frozen_and_consistent():
    w=ScoreWeights(scales={'inlet_hi_k':1.,'inlet_lo_k':1.,'exhaust_hi_k':1.,'return_hi_k':1.,'acu_capacity_frac':.1})
    assert isinstance(w.scales,tuple)
    with pytest.raises(ValueError):ScoreWeights(pue_scale=0.)
    with pytest.raises(ValueError):ScoreWeights(infeasible_offset=.5)


def test_serialized_list_feedback_and_mixed_tensor_feedback_are_supported():
    plain={k:np.asarray(x).tolist() for k,x in v().items()}
    assert is_feasible(plain)
    assert math.isfinite(score(1.2,[40.],plain))
    mixed=dict(plain);mixed['inlet_hi_k']=torch.tensor([0.],dtype=torch.float64)
    assert is_feasible(mixed)
    assert math.isfinite(score(1.2,[40.],mixed))

@pytest.mark.parametrize('field',['infeasible_offset','invalid_offset'])
def test_nonfinite_reward_offsets_are_refused(field):
    with pytest.raises(ValueError):ScoreWeights(**{field:math.nan})


@pytest.mark.parametrize('bad',[math.nan,-1.])
def test_invalid_i1_power_is_not_reported_as_physical_feedback(bad):
    e=dict(chiller_kw=bad,chw_pump_kw=.1,rejection_kw=.2,fan_kw=np.array([.5,.5]),other_kw=0.)
    with pytest.raises(ValueError):i1_vector(e,v(),np.array([40.]))
