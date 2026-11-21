"""Global return/predictability/component controls and exact acquisition algebra (ported from the frozen tests/test_ispo_controller.py)."""
import itertools
import numpy as np
import pytest
from agent.optim.ispo_controller import ReturnContract,FrozenControls,fit_past_controls,acquisition_decision,information_choice,information_gradient

def controls():
    return FrozenControls(("LFthermal","goal-increment"),(1.,1.),.7,.001,("cal0","cal1"),{"fixed_policy":"test"},{"wall_seconds":1.})

def test_I1_checks_the_complete_global_target_and_known_is_not_reassigned():
    contract=ReturnContract({"thermal":1.,"control_cost":1.})
    assert abs(contract.verify(-.2,{"thermal":.18,"control_cost":.02})+.2)<1e-12
    assert contract.known_reward({"thermal":.18,"control_cost":.02})==-.02
    with pytest.raises(ValueError):contract.verify(-.1,{"thermal":.18,"control_cost":.02})

def test_past_only_multiple_value_features_and_path_calibration():
    rows=[]
    for context in ("a","b"):
        for i in (0,1):
            X=np.array([[-.2,.01],[-.3,.02]])
            C=np.array([(-1.)**i,0.])
            rows.append({"phase":"calibration","terminal":True,"group_id":context+str(i),"context":context,"features":X,"unknown_HF_reward":X@np.array([1.03,.8]),"centered_joint_score_norm2":np.ones(2),"HF_gradient":-C,"raw_LF_path_control":C})
    fit=fit_past_controls(rows,("LF","DWR"),source_identity={"source":"test"})
    assert len(fit.value_coefficients)==2 and np.allclose(fit.value_coefficients,[1.03,.8],atol=.002)
    assert fit.path_coefficient==1. and fit.past_group_ids==("a0","a1","b0","b1")
    rows[0]["phase"]="evaluation"
    with pytest.raises(ValueError):fit_past_controls(rows,("LF","DWR"),source_identity={})

def test_real_goal_q_positive_floor_budget_and_no_current_HF_read():
    rng=np.random.default_rng(2);features=rng.normal(size=(4,2));score=rng.normal(size=(4,7))
    result=acquisition_decision(controls(),features,features.sum(1),score,[2.]*4,expected_HF_cost_budget=4.,q_min=.1)
    assert np.all(result["q"]>=.1) and np.all(result["q"]<=1.)
    assert abs(result["expected_HF_charge"]-4.)<1e-10
    assert result["HF_current_outcomes_read"] is False

def test_decision_directed_information_can_choose_no_extra_information():
    profile=[{"phase":"calibration","terminal":True,"bundle":"HF-only/no-extra","information_cost":0.,"HF_target_variance":1.,"total_charge":2.,"source":"past0"},
             {"phase":"calibration","terminal":True,"bundle":"I1/I2/I3","information_cost":5.,"HF_target_variance":.8,"total_charge":7.,"source":"past1"}]
    assert information_choice(profile,available_cost_budget=10)["bundle"]=="HF-only/no-extra"
    profile[1]["HF_target_variance"]=.1
    assert information_choice(profile,available_cost_budget=10)["bundle"]=="I1/I2/I3"

def test_conditional_target_exact_with_peer_dependence_and_missing_outcomes():
    rng=np.random.default_rng(2);D=rng.normal(size=(4,5));X=rng.normal(size=(4,5));P=rng.normal(size=(4,5));K=rng.normal(size=(4,5))
    HF=np.array([-.2,-.3,-.25,-.15]);LF=np.array([-.19,-.28,-.24,-.14]);known=np.full(4,-.02)
    features=np.c_[LF-known,np.array([-.01,-.02,-.01,-.01])];q=np.array([.1,.3,.7,.9])
    average=np.zeros(5)
    for bits in itertools.product((0,1),repeat=4):
        mask=np.asarray(bits,dtype=bool);prob=np.prod(np.where(mask,q,1-q));purchased=np.where(mask,HF,np.nan)
        g,_=information_gradient(None,D+X,D,P,K,HF_observed=purchased,LF_unknown=LF-known,known_rewards=known,features=features,selected=mask,q=q,controls=controls())
        average+=prob*g
    t=(D+X-(D+X).mean(0))/3;tx=(X-X.mean(0))/3;td=(D-D.mean(0))/3
    expected=(HF-known)@t+known@td+K.mean(0)+.7*(P.mean(0)-(LF-known)@tx)
    assert np.allclose(average,expected,atol=1e-12)
