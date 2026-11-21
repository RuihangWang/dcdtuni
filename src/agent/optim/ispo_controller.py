"""Predictable information/controller mechanics with an unchanged global HF return.

Value controls may use I1 components and goal-directed I2 auxiliary values;
path controls must use the exact LF function with reliable I3. Information
choices and all propensities precede the current HF purchases. Calibration
reads only completed past groups. Finite failures and all costs are retained.

Moved unchanged in meaning from the frozen ``methods/ispo_controller.py``;
torch is needed only by :func:`acquisition_decision` (through
:func:`agent.optim.estimators.allocation_probabilities`).
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
import math
import numpy as np

@dataclass(frozen=True)
class ReturnContract:
    weights:dict
    known_names:tuple=("control_cost",)
    def verify(self,global_reward,components):
        missing=set(self.weights)-set(components)
        if missing:raise ValueError("missing global return components")
        value=-sum(float(weight)*float(components[name]) for name,weight in self.weights.items())
        if not math.isclose(float(global_reward),value,rel_tol=1e-10,abs_tol=1e-10):raise ValueError("I1 decomposition changed the global target return")
        return value
    def known_reward(self,components):return -sum(float(self.weights[name])*float(components[name]) for name in self.known_names)
    def unknown_component_values(self,components):
        return [-float(weight)*float(components[name]) for name,weight in self.weights.items() if name not in self.known_names]

@dataclass(frozen=True)
class FrozenControls:
    feature_names:tuple
    value_coefficients:tuple
    path_coefficient:float
    residual_variance:float
    past_group_ids:tuple
    source_identity:dict
    fit_costs:dict
    def predict(self,features):
        X=np.asarray(features,dtype=np.float64)
        if X.ndim!=2 or X.shape[1]!=len(self.feature_names) or not np.isfinite(X).all():raise ValueError("finite feature bundle needed")
        return X@np.asarray(self.value_coefficients)
    def identity(self):return asdict(self)

def fit_past_controls(past_rows,feature_names,*,source_identity,ridge=1e-8):
    import time
    started,cpu=time.perf_counter(),time.process_time()
    if not past_rows or any(row.get("phase")!="calibration" or not row.get("terminal",False) for row in past_rows):raise ValueError("only complete disjoint past calibration groups")
    X=np.concatenate([row["features"] for row in past_rows]);h=np.concatenate([row["unknown_HF_reward"] for row in past_rows])
    norm=np.concatenate([row["centered_joint_score_norm2"] for row in past_rows])
    if X.ndim!=2 or X.shape[1]!=len(feature_names) or h.shape!=(len(X),) or norm.shape!=h.shape:raise ValueError("past feature/target dimensions")
    if not all(np.isfinite(a).all() for a in (X,h,norm)) or (norm<0).any():raise ValueError("all failures need finite targets, not dropped")
    gram=X.T@(norm[:,None]*X);scale=max(float(np.trace(gram))/len(feature_names),1e-24)
    beta=np.linalg.solve(gram+ridge*scale*np.eye(len(feature_names)),X.T@(norm*h))
    beta=np.clip(beta,-2.,2.)
    alpha=0.;varianceC=cross=0.;dof=0
    for context in {row["context"] for row in past_rows}:
        rows=[row for row in past_rows if row["context"]==context]
        if len(rows)<2:continue
        H=np.stack([r["HF_gradient"] for r in rows]);C=np.stack([r["raw_LF_path_control"] for r in rows])
        H=H-H.mean(0);C=C-C.mean(0);cross+=float(np.sum(H*C));varianceC+=float(np.sum(C*C));dof+=len(rows)-1
    if varianceC>1e-24:alpha=float(np.clip(-cross/varianceC,-2.,2.))
    noise=float(np.mean((h-X@beta)**2))
    return FrozenControls(tuple(feature_names),tuple(beta.tolist()),alpha,max(noise,1e-12),tuple(row["group_id"] for row in past_rows),source_identity,{"wall_seconds":time.perf_counter()-started,"own_CPU_seconds":time.process_time()-cpu,"calibration_physics_costs_external_not_free":True})

def rloo_joint_weights(scores):
    S=np.asarray(scores,dtype=np.float64)
    if S.ndim!=2 or len(S)<2 or not np.isfinite(S).all():raise ValueError("complete IID group joint parameter scores")
    return (S-S.mean(0))/(len(S)-1)

def acquisition_decision(controls,features,goal_value_guess,joint_scores,HF_prices,*,expected_HF_cost_budget,q_min=.1):
    prediction=controls.predict(features)
    scores=rloo_joint_weights(joint_scores);norm2=np.sum(scores*scores,axis=1)
    goal=np.asarray(goal_value_guess,dtype=np.float64)
    prices=np.asarray(HF_prices,dtype=np.float64)
    if goal.shape!=prediction.shape or prices.shape!=prediction.shape or not np.isfinite(goal).all():raise ValueError("per-proposal goal and price alignment")
    # Real I2 goal estimate helps forecast error, but it is not a certified bound.
    predicted_error2=(goal-prediction)**2+controls.residual_variance
    from agent.optim.estimators import allocation_probabilities
    q=allocation_probabilities(predicted_error2*norm2,prices,expected_hf_cost_budget=expected_HF_cost_budget,q_min=q_min).numpy()
    return {"q":q,"value_prediction":prediction,"centered_joint_score_norm2":norm2,
            "predicted_error2":predicted_error2,"HF_prices":prices,
            "expected_HF_charge":float(q@prices),"positive_floor":q_min,
            "HF_current_outcomes_read":False,"predictor_bound_certified":False,"controls":controls.identity()}

def information_choice(past_profiles,*,available_cost_budget):
    """Choose from measured past information contracts, including no extra info."""
    if not past_profiles:raise ValueError("past measured profile required")
    candidates=[]
    for row in past_profiles:
        if row.get("phase")!="calibration" or not row.get("terminal"):raise ValueError("no current-outcome information selection")
        if row["information_cost"]<0 or row["HF_target_variance"]<0:raise ValueError("nonnegative measured cost/variance")
        if row["information_cost"]<=available_cost_budget:
            # A decision-directed scalar is an estimated gradient-variance/cost
            # product under the stated past policy, not an unconditional theorem.
            candidates.append((row["HF_target_variance"]*max(row["total_charge"],1e-12),row))
    if not candidates:raise ValueError("no measured feasible information bundle")
    score,row=min(candidates,key=lambda value:value[0])
    return {"bundle":row["bundle"],"past_estimated_variance_cost":score,"profile_source":row["source"],
            "current_HF_read":False,"policy_shift_may_invalidate_variance_forecast":True,"profile_and_calibration_costs_charged":True}

def information_gradient(sample,joint_scores,discrete_scores,raw_LF_paths,known_paths,*,HF_observed,LF_unknown,known_rewards,features,selected,q,controls):
    """Entire global target with peer dependence; no local reward replacement."""
    S=np.asarray(joint_scores,dtype=np.float64);D=np.asarray(discrete_scores,dtype=np.float64)
    t=rloo_joint_weights(S);td=rloo_joint_weights(D);tx=rloo_joint_weights(S-D)
    mask=np.asarray(selected,dtype=bool);prob=np.asarray(q,dtype=np.float64)
    observed=np.asarray(HF_observed,dtype=np.float64);known=np.asarray(known_rewards,dtype=np.float64)
    if not np.isfinite(observed[mask]).all() or (prob<=0).any() or (prob>1).any():raise ValueError("finite purchased failure rewards and positive true propensity")
    value=controls.predict(features);correction=np.zeros(len(value))
    correction[mask]=(observed[mask]-known[mask]-value[mask])/prob[mask]
    signal=value+correction
    C=np.asarray(raw_LF_paths).mean(0)-np.asarray(LF_unknown)@tx
    common=known@td+np.asarray(known_paths).mean(0)
    gradient=signal@t+controls.path_coefficient*C+common
    return gradient,{"HF_purchased":int(mask.sum()),"global_return_preserved":True,"known_once_outside_acquisition":True,"value_and_raw_LF_path_controls_distinct":True,"purchased_failures_not_dropped":True,"conditional_mean_target":"HF score plus predictable raw-LF zero-mean path control"}
