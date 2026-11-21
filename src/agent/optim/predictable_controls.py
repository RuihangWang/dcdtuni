"""Independent multi-component ISPO controls fixed before current proposals.

I1/I2 values predict the unchanged HF unknown return. Independent path controls
must match their own LF function and derivative, rather than an I2 correction
whose derivative is unavailable. Missing requested I3 aborts the transaction;
it does not induce a design-dependent zero-mean-control coefficient.

Moved unchanged in meaning from the frozen ``methods/predictable_controls.py``
(``VERSION`` is kept, so snapshot identities hash as before); torch is
imported only by :func:`multi_control_policy_loss`.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
import hashlib,json,math
import numpy as np
from agent.optim.ispo_controller import rloo_joint_weights

VERSION="predictable-multicomponent-controls/v1"
class PathControlUnavailable(ValueError):pass

@dataclass(frozen=True)
class ValueControl:
    name:str
    coefficient:float

@dataclass(frozen=True)
class PathControl:
    name:str
    coefficient:float
    function_identity:str
    derivative_of:str
    def validate(self):
        if not self.function_identity or self.derivative_of!=self.function_identity:
            raise ValueError("path derivative must match the exact same LF function")
        if not math.isfinite(self.coefficient):raise ValueError("finite predictable path coefficient required")

@dataclass(frozen=True)
class ControlSnapshot:
    group_index:int
    policy_sha256:str
    values:tuple
    paths:tuple
    past_group_ids:tuple
    provenance:str
    bundle:str
    def __post_init__(self):
        if self.group_index<0 or not self.policy_sha256 or not self.provenance:raise ValueError("complete predictable identity required")
        names=[row.name for row in self.values]
        if len(set(names))!=len(names) or any(not math.isfinite(r.coefficient) for r in self.values):raise ValueError("unique finite value controls")
        names=[row.name for row in self.paths]
        if len(set(names))!=len(names):raise ValueError("unique path controls")
        for row in self.paths:row.validate()
    def identity(self):
        result={"version":VERSION,**asdict(self),"fixed_before_current_proposals":True}
        result["sha256"]=hashlib.sha256(json.dumps(result,sort_keys=True,allow_nan=False).encode()).hexdigest()
        return result

class PredictableLedger:
    """Completed group identities only; no partial/current outcome fitting."""
    def __init__(self,past_group_ids=()):
        self.past_group_ids=tuple(past_group_ids);self.next_group=0;self.active=None;self.receipts=[]
    def begin(self,*,policy_sha256,values,paths,bundle,provenance):
        if self.active is not None:raise RuntimeError("previous group not terminal")
        self.active=ControlSnapshot(self.next_group,policy_sha256,tuple(values),tuple(paths),self.past_group_ids,provenance,bundle)
        return self.active
    def complete(self,snapshot,*,group_id,costs,status):
        if snapshot is not self.active:raise ValueError("only the active frozen transaction can terminate")
        if status not in ("completed","failed"):raise ValueError("terminal group status required")
        if group_id in self.past_group_ids:raise ValueError("no group reuse")
        if any(not math.isfinite(float(costs[key])) or costs[key]<0 for key in ("wall_seconds","CPU_seconds")):raise ValueError("all attempted group costs retained")
        self.receipts.append({"group_id":group_id,"status":status,"snapshot":snapshot.identity(),"costs":dict(costs)})
        self.past_group_ids+= (group_id,);self.next_group+=1;self.active=None


def _finite_vector(value,n,name,mask=None):
    result=np.asarray(value,dtype=np.float64)
    if result.shape!=(n,) or not np.isfinite(result if mask is None else result[mask]).all():raise ValueError(name+" requires aligned finite used values")
    return result


def multi_control_gradient(joint_scores,discrete_scores,known_paths,*,snapshot,
                           hf_observed,known_rewards,value_features,path_values,
                           path_derivatives,selected,q):
    """Exact native parameter-vector identity for a complete IID RLOO group."""
    S=np.asarray(joint_scores,dtype=np.float64);D=np.asarray(discrete_scores,dtype=np.float64)
    if S.shape!=D.shape or not np.isfinite(D).all():raise ValueError("joint/discrete score alignment")
    n,p=S.shape;t=rloo_joint_weights(S);td=rloo_joint_weights(D);tx=rloo_joint_weights(S-D)
    mask=np.asarray(selected,dtype=bool)
    if mask.shape!=(n,):raise ValueError("one independent Bernoulli draw per proposal")
    q=_finite_vector(q,n,"true acquisition probabilities")
    if (q<=0).any() or (q>1).any():raise ValueError("strictly positive true q")
    H=_finite_vector(hf_observed,n,"purchased HF including failure",mask)
    known=_finite_vector(known_rewards,n,"known reward")
    K=np.asarray(known_paths,dtype=np.float64)
    if K.shape!=(n,p) or not np.isfinite(K).all():raise ValueError("known full-parameter derivative")
    proxy=np.zeros(n)
    for row in snapshot.values:proxy+=row.coefficient*_finite_vector(value_features[row.name],n,row.name)
    signal=proxy.copy();signal[mask]+=(H[mask]-known[mask]-proxy[mask])/q[mask]
    gradient=signal@t+known@td+K.mean(0)
    for row in snapshot.paths:
        if row.coefficient==0:continue
        L=_finite_vector(path_values[row.name],n,row.name)
        P=path_derivatives.get(row.name)
        if P is None:raise PathControlUnavailable("requested "+row.name+" I3 absent; current-outcome fallback cannot certify zero mean")
        P=np.asarray(P,dtype=np.float64)
        if P.shape!=(n,p) or not np.isfinite(P).all():raise PathControlUnavailable("nonfinite/unmatched "+row.name+" I3")
        gradient+=row.coefficient*(P.mean(0)-L@tx)
    return gradient,{"version":VERSION,"snapshot":snapshot.identity(),"HF_purchased":int(mask.sum()),"global_target":"unchanged whole HF reward","known_once_outside_purchase":True,"no_I2_derivative_imputation":True}


def multi_control_policy_loss(sample,*,snapshot,hf_observed,known_rewards,
                              known_design_gradients,value_features,path_values,
                              path_design_gradients,selected,q):
    """Autograd surrogate for the exact group identity above, before KL/Adam."""
    import torch
    n=len(sample.actions);device=sample.values.device
    if n<2:raise ValueError("complete IID group needed")
    mask=np.asarray(selected,dtype=bool);q=_finite_vector(q,n,"true q")
    if mask.shape!=(n,) or (q<=0).any() or (q>1).any():raise ValueError("positive recorded Bernoulli propensities")
    H=_finite_vector(hf_observed,n,"HF outcomes including finite failures",mask)
    k=_finite_vector(known_rewards,n,"known reward")
    def tensor(v):return torch.as_tensor(v,dtype=sample.values.dtype,device=device)
    def path(grad,name):
        if grad is None:raise PathControlUnavailable("requested "+name+" I3 absent; no endogenous coefficient fallback")
        grad=tensor(grad)
        if grad.shape!=sample.values.shape or not torch.isfinite(grad).all():raise PathControlUnavailable("finite aligned derivative needed for "+name)
        return (sample.values*grad.detach()).sum(-1)
    proxy=np.zeros(n)
    for row in snapshot.values:proxy+=row.coefficient*_finite_vector(value_features[row.name],n,row.name)
    signal=proxy.copy();signal[mask]+=(H[mask]-k[mask]-proxy[mask])/q[mask]
    def advantage(v):
        v=tensor(v).detach();return v-(v.sum()-v)/(n-1)
    loss=-(advantage(signal)*sample.log_prob).mean()
    loss-= (advantage(k)*sample.discrete_log_prob).mean()+path(known_design_gradients,"known").mean()
    for row in snapshot.paths:
        if row.coefficient==0:continue
        L=_finite_vector(path_values[row.name],n,row.name)
        zero_mean=path(path_design_gradients.get(row.name),row.name).mean()-(advantage(L)*sample.continuous_log_prob).mean()
        loss-=row.coefficient*zero_mean
    return loss,{"version":VERSION,"snapshot":snapshot.identity(),"HF_purchased":int(mask.sum()),"global_target":"unchanged whole HF reward","known_once_outside_purchase":True,"I2_value_only_if_no_matched_I3":True}
