"""Predictable multi-component controls (ported from the frozen tests/test_predictable_controls.py)."""
import itertools
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from agent.optim.predictable_controls import (ControlSnapshot,ValueControl,PathControl,
    PredictableLedger,PathControlUnavailable,multi_control_gradient,multi_control_policy_loss)
from agent.policies.numeric import NumericalDesignHead


def _snapshot():
    return ControlSnapshot(0,"fixed-test-policy",(ValueControl("thermal",1.1),ValueControl("goal",.4)),
        (PathControl("thermal",.7,"thermal-v1","thermal-v1"),PathControl("second",-.2,"second-v1","second-v1")),
        ("past0",),"completed past calibration","I1/I2/I3")


def _actual_policy_data():
    torch.manual_seed(513)
    hidden=torch.nn.Parameter(torch.randn(1,3));logits=torch.nn.Parameter(torch.tensor([[.3,-.2]]))
    head=NumericalDesignHead(3,((.06,.22),(.25,.75)))
    sample=head.sample(hidden,logits,samples=4,generator=torch.Generator().manual_seed(8))
    params=[hidden,logits,*head.parameters()]
    def gradient(value):
        out=torch.autograd.grad(value,params,retain_graph=True,allow_unused=True)
        return torch.cat([g.reshape(-1) if g is not None else torch.zeros_like(p).reshape(-1) for p,g in zip(params,out)]).detach().numpy().astype(float)
    x=sample.values.detach().numpy();known=-.02*x[:,0]**2
    kg=np.c_[-.04*x[:,0],np.zeros(4)]
    values={"thermal":-x[:,0]**2-x[:,1]**2,"second":np.sin(x[:,1]),"goal":-x[:,0]**2-x[:,1]**2+.01}
    grads={"thermal":-2*x,"second":np.c_[np.zeros(4),np.cos(x[:,1])]}
    D=np.stack([gradient(v) for v in sample.discrete_log_prob]);X=np.stack([gradient(v) for v in sample.continuous_log_prob])
    K=np.stack([gradient(v) for v in (sample.values*torch.tensor(kg)).sum(-1)])
    P={name:np.stack([gradient(v) for v in (sample.values*torch.tensor(g)).sum(-1)]) for name,g in grads.items()}
    HF=values["thermal"]+known+np.array([.005,.002,-99.,.004])
    return sample,gradient,D,X,K,values,grads,P,kg,known,HF


def test_actual_multicomponent_autograd_equals_native_group_assembly():
    sample,grad,D,X,K,values,grads,P,kg,known,HF=_actual_policy_data();q=np.array([.1,.3,.7,.9]);mask=np.array([0,1,1,0],bool)
    observed=np.where(mask,HF,np.nan)
    loss,meta=multi_control_policy_loss(sample,snapshot=_snapshot(),hf_observed=observed,known_rewards=known,
        known_design_gradients=kg,value_features=values,path_values=values,path_design_gradients=grads,selected=mask,q=q)
    vector,_=multi_control_gradient(D+X,D,K,snapshot=_snapshot(),hf_observed=observed,known_rewards=known,
        value_features=values,path_values=values,path_derivatives=P,selected=mask,q=q)
    assert np.allclose(-grad(loss),vector,rtol=1e-6,atol=2e-7)
    assert meta["HF_purchased"]==2 and meta["known_once_outside_purchase"]


def test_bernoulli_exact_mean_with_RLOO_dependencies_two_path_controls_and_failed_HF():
    sample,grad,D,X,K,values,grads,P,kg,known,HF=_actual_policy_data();q=np.array([.1,.3,.7,.9]);average=np.zeros(D.shape[1])
    for bits in itertools.product((0,1),repeat=4):
        mask=np.array(bits,bool);prob=np.prod(np.where(mask,q,1-q))
        g,_=multi_control_gradient(D+X,D,K,snapshot=_snapshot(),hf_observed=np.where(mask,HF,np.nan),known_rewards=known,
            value_features=values,path_values=values,path_derivatives=P,selected=mask,q=q)
        average+=prob*g
    direct,_=multi_control_gradient(D+X,D,K,snapshot=_snapshot(),hf_observed=HF,known_rewards=known,
        value_features=values,path_values=values,path_derivatives=P,selected=np.ones(4,bool),q=np.ones(4))
    assert np.allclose(average,direct,atol=1e-12)


def test_missing_current_I3_aborts_and_mismatched_derivative_is_rejected():
    sample,grad,D,X,K,values,grads,P,kg,known,HF=_actual_policy_data();P["thermal"]=None
    with pytest.raises(PathControlUnavailable):
        multi_control_gradient(D+X,D,K,snapshot=_snapshot(),hf_observed=HF,known_rewards=known,
            value_features=values,path_values=values,path_derivatives=P,selected=np.ones(4,bool),q=np.ones(4))
    with pytest.raises(ValueError):
        ControlSnapshot(0,"policy",(),(PathControl("goal",1.,"corrected-value","raw-LF-value"),),(),"past","I2")


def test_group_snapshot_requires_terminal_cost_and_future_groups_are_excluded():
    ledger=PredictableLedger(("past0",));snapshot=ledger.begin(policy_sha256="before-proposal",values=(),paths=(),bundle="HF-only",provenance="past profiles")
    assert snapshot.past_group_ids==("past0",)
    with pytest.raises(RuntimeError):ledger.begin(policy_sha256="current",values=(),paths=(),bundle="HF-only",provenance="partial outcome")
    ledger.complete(snapshot,group_id="new0",costs={"wall_seconds":2.,"CPU_seconds":3.},status="failed")
    next_snapshot=ledger.begin(policy_sha256="after-update",values=(),paths=(),bundle="HF-only",provenance="complete past")
    assert next_snapshot.past_group_ids==("past0","new0") and snapshot.past_group_ids==("past0",)
    assert ledger.receipts[0]["status"]=="failed"


def test_design_dependent_path_disabling_is_not_a_zero_mean_control():
    nodes,weights=np.polynomial.hermite.hermgauss(200);x=np.sqrt(2)*nodes;weights=weights/np.sqrt(np.pi)
    # Gaussian location score=x, f(x)=x, df/dmu=1: C=1-x^2 has zero
    # mean. Turning it off after a design-dependent failure breaks that identity.
    C=1-x*x
    assert abs(weights@C)<1e-12
    assert weights@np.where(np.abs(x)<1,C,0.)>.4
