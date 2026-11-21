"""Differentiable losses for joint categorical/continuous design policies.

Selection is independent Bernoulli conditional on each recorded proposal. HF
failures retain finite target rewards; unpurchased outcomes stay NaN. Coefficients
are fixed before current HF purchases. Loss gradients precede clipping/Adam.

:func:`joint_policy_loss` and :func:`information_policy_loss` are the frozen
``training/joint_losses.py`` and ``training/information_loss.py``, unchanged in
meaning (their version strings are kept).
"""
from __future__ import annotations
import torch
from agent.optim.estimators import rloo_advantages

LOSS_VERSION = "joint-design-loss/v2-discrepancy-acquisition-peer-baselines"

METHODS = ("hf-rloo", "lf-rloo", "scalar-aipw", "continuous-lax", "hf-path")

def _vector(value, n, device, name, *, selected=None):
    result = torch.as_tensor(value, dtype=torch.float64, device=device)
    if result.shape != (n,):
        raise ValueError(f"{name} must have one value per proposal")
    used = result if selected is None else result[selected]
    if not torch.isfinite(used).all():
        raise ValueError(f"{name} requires finite purchased outcomes, including failures")
    return result

def _path(sample, gradient, name):
    if gradient is None:
        raise ValueError(f"{name} is unavailable; preserve forward outcomes and stop this gradient method")
    gradient = torch.as_tensor(gradient, dtype=sample.values.dtype, device=sample.values.device)
    if gradient.shape != sample.values.shape or not torch.isfinite(gradient).all():
        raise ValueError(f"{name} must be a finite physical derivative per numerical value")
    return (sample.values * gradient.detach()).sum(-1)

def joint_policy_loss(sample, *, method, hf_rewards=None, lf_rewards=None,
                      selected=None, probabilities=None, coefficient=1.0,
                      proxy_gradients=None, hf_gradients=None,
                      known_rewards=None, known_gradients=None):
    """One IID group for one prompt; no group-standardization or missing imputation.

    LAX proxy is LF minus the optional known smooth reward, with the exact same
    derivative subtraction. The categorical target score is retained. A common
    known-reward path correction, if provided, applies to every score baseline.
    HF-path is a different, fully differentiable oracle baseline and needs all HF.
    Missing optional gradients stop that method; they never change target I0.
    """
    if method not in METHODS:
        raise ValueError("unknown estimator")
    n = len(sample.actions)
    if n < 2 or sample.values.ndim != 2:
        raise ValueError("at least two IID proposals for one prompt required")
    device = sample.values.device
    if not torch.isfinite(torch.as_tensor(coefficient)).all():
        raise ValueError("coefficient must be finite and frozen before current outcomes")
    beta = float(coefficient)
    known = torch.zeros(n, dtype=torch.float64, device=device)
    known_path = torch.zeros(n, dtype=torch.float64, device=device)
    if (known_rewards is None) != (known_gradients is None):
        raise ValueError("known reward and its exact derivative must be provided together")
    if known_rewards is not None:
        known = _vector(known_rewards, n, device, "known reward").detach()
        known_path = _path(sample, known_gradients, "known derivative")
    if method in ("hf-rloo", "hf-path"):
        hf = _vector(hf_rewards, n, device, "HF reward").detach()-known
        baseline = (hf.sum() - hf) / (n-1)
        if method == "hf-rloo":
            advantage = rloo_advantages(hf).to(device)
            loss = -(advantage.detach()*sample.log_prob).mean()
        else:
            loss = -((hf-baseline).detach()*sample.discrete_log_prob).mean()
            loss = loss - (_path(sample, hf_gradients, "HF derivative")-known_path).mean()
    elif method == "lf-rloo":
        lf = _vector(lf_rewards, n, device, "LF reward").detach()-known
        advantage = rloo_advantages(lf).to(device)
        baseline = (lf.sum() - lf) / (n-1)
        loss = -(advantage.detach()*sample.log_prob).mean()
    else:
        mask = torch.as_tensor(selected, dtype=torch.bool, device=device)
        if mask.shape != (n,):
            raise ValueError("selection must contain one independent Bernoulli draw per proposal")
        q = _vector(probabilities, n, device, "acquisition probability").detach()
        if (q <= 0).any() or (q > 1).any():
            raise ValueError("probabilities must lie in (0,1]")
        hf = _vector(hf_rewards, n, device, "HF reward", selected=mask).detach()-known
        lf = _vector(lf_rewards, n, device, "LF reward").detach()-known
        discrepancy = torch.zeros_like(q)
        discrepancy[mask]=(hf[mask]-beta*lf[mask])/q[mask]
        signal=beta*lf+discrepancy
        signal_baseline=(signal.sum()-signal)/(n-1)
        discrepancy_baseline=(discrepancy.sum()-discrepancy)/(n-1)
        baseline=signal_baseline
        if method == "scalar-aipw":
            loss=-((signal-signal_baseline).detach()*sample.log_prob).mean()
        else:
            proxy_path=_path(sample,proxy_gradients,"LF derivative")-known_path
            loss=-((signal-signal_baseline).detach()*sample.discrete_log_prob).mean()
            loss-=((discrepancy-discrepancy_baseline).detach()*sample.continuous_log_prob).mean()
            loss-=beta*proxy_path.mean()
    # The same complete known term is supplied to every method, outside sparse
    # acquisition. Keep its categorical derivative because x is conditional on s.
    if known_rewards is not None:
        known_baseline=(known.sum()-known)/(n-1)
        loss -= ((known-known_baseline)*sample.discrete_log_prob).mean()+known_path.mean()
    return loss, {"loss_version":LOSS_VERSION,"target": "LF" if method == "lf-rloo" else "HF",
                  "HF_purchased": n if method in ("hf-rloo","hf-path") else
                      (0 if method == "lf-rloo" else int(mask.sum())),
                  "coefficient": beta, "baseline": baseline.detach().cpu().tolist(),
                  "common_known_path": known_rewards is not None}


# --- target-corrected information loss (frozen training/information_loss.py) ---------------------
# Target-corrected information loss with distinct value and path proxies.
#
# A goal-corrected auxiliary value need not have an available derivative. Its
# AIPW score control is independent of the fixed smooth LF function used by LAX.
# Coefficients are predictable before proposals, purchases are independent
# Bernoulli with recorded positive probabilities, and every HF failure is finite.
INFORMATION_LOSS_VERSION="information-loss/v1-separate-value-and-path-functions"


def information_policy_loss(sample,*,hf_rewards,auxiliary_rewards,lf_rewards,
                            selected,probabilities,value_coefficient=1.,
                            path_coefficient=1.,lf_gradients=None,
                            known_rewards=None,known_gradients=None):
    n=len(sample.actions);device=sample.values.device
    if n<2:raise ValueError("one complete IID peer group with at least two proposals required")
    beta=float(value_coefficient);alpha=float(path_coefficient)
    if not torch.isfinite(torch.tensor([beta,alpha],dtype=torch.float64)).all():
        raise ValueError("finite predictable coefficients required")
    mask=torch.as_tensor(selected,dtype=torch.bool,device=device)
    if mask.shape!=(n,):raise ValueError("one independent acquisition draw per proposal")
    q=_vector(probabilities,n,device,"probability").detach()
    if (q<=0).any() or (q>1).any():raise ValueError("positive probabilities no greater than one")
    known=torch.zeros(n,dtype=torch.float64,device=device)
    known_path=torch.zeros(n,dtype=sample.values.dtype,device=device)
    if (known_rewards is None)!=(known_gradients is None):
        raise ValueError("known function and derivative must be supplied together")
    if known_rewards is not None:
        known=_vector(known_rewards,n,device,"known reward").detach()
        known_path=_path(sample,known_gradients,"known derivative")
    target=_vector(hf_rewards,n,device,"HF reward",selected=mask).detach()-known
    auxiliary=_vector(auxiliary_rewards,n,device,"auxiliary value").detach()-known
    low=_vector(lf_rewards,n,device,"LF path-function value").detach()-known
    correction=torch.zeros_like(q)
    correction[mask]=(target[mask]-beta*auxiliary[mask])/q[mask]
    signal=beta*auxiliary+correction
    def peer(value):return (value.sum()-value)/(n-1)
    discrete=signal-peer(signal)
    continuous=signal-alpha*low
    continuous=continuous-peer(continuous)
    loss=-(discrete.detach()*sample.discrete_log_prob).mean()
    loss-=(continuous.detach()*sample.continuous_log_prob).mean()
    if alpha:
        path=_path(sample,lf_gradients,"derivative of the same LF path function")-known_path
        loss-=alpha*path.mean()
    if known_rewards is not None:
        loss-=((known-peer(known))*sample.discrete_log_prob).mean()+known_path.mean()
    return loss,{"version":INFORMATION_LOSS_VERSION,"target":"HF","HF_purchased":int(mask.sum()),
                "value_coefficient":beta,"path_coefficient":alpha,
                "auxiliary_has_no_required_design_derivative":True,
                "LF_value_and_derivative_are_same_function":True,
                "common_known_component":known_rewards is not None}
