"""Score-function, nested control-variable and randomized HF estimators (the HF acquisition maths).

Moved unchanged in meaning from the frozen ``methods/estimators.py``
(float64 torch tensors in and out). ``allocation_probabilities`` is the
Bernoulli purchase rule that :class:`agent.optim.acquisition.Acquisition`
registers before any outcome is read.

All signals refer to the same frozen proposal policy. Coefficients/predictors
are fitted on disjoint past/calibration data. No clipping, group standardization
or missing-HF imputation is hidden in these reference estimators.
"""
from __future__ import annotations
from dataclasses import dataclass
import torch


def _matrix(value, name):
    value = torch.as_tensor(value, dtype=torch.float64)
    if value.ndim != 2 or not torch.isfinite(value).all():
        raise ValueError(f"{name} must be a finite two-dimensional tensor")
    return value


def _vector(value, n, name):
    value = torch.as_tensor(value, dtype=torch.float64)
    if value.shape != (n,) or not torch.isfinite(value).all():
        raise ValueError(f"{name} must be a finite length-{n} vector")
    return value


def rloo_advantages(rewards):
    rewards = torch.as_tensor(rewards, dtype=torch.float64)
    if rewards.ndim != 1 or len(rewards) < 2 or not torch.isfinite(rewards).all():
        raise ValueError("RLOO requires at least two finite IID rewards per prompt")
    # Float64 and the exact-tie guard prevent the old zero-spread phantom update.
    if torch.equal(rewards, rewards[0].expand_as(rewards)):
        return torch.zeros_like(rewards)
    return rewards - (rewards.sum() - rewards) / (len(rewards) - 1)


def ht_peer_baseline(hf_rewards, selected, inclusion_probabilities):
    """A leave-one-out HT baseline, defined even if zero/one HF is bought.

    Unselected hf_rewards may be NaN and are never interpreted as outcomes.
    Selection draws must be conditionally independent Bernoulli draws with
    the recorded probabilities. It is not a selected-HF sample mean.
    """
    probabilities = torch.as_tensor(inclusion_probabilities, dtype=torch.float64)
    selected = torch.as_tensor(selected, dtype=torch.bool)
    rewards = torch.as_tensor(hf_rewards, dtype=torch.float64)
    if probabilities.ndim != 1 or selected.shape != probabilities.shape or rewards.shape != probabilities.shape:
        raise ValueError("HF rewards, selection and probability shapes differ")
    if not torch.isfinite(probabilities).all() or (probabilities <= 0).any() or (probabilities > 1).any():
        raise ValueError("all inclusion probabilities must be in (0,1]")
    if not torch.isfinite(rewards[selected]).all():
        raise ValueError("every purchased HF evaluation requires a finite outcome including failures")
    contributions = torch.zeros_like(probabilities)
    contributions[selected] = rewards[selected] / probabilities[selected]
    if len(probabilities) == 1:
        return torch.zeros_like(probabilities)
    return (contributions.sum() - contributions) / (len(probabilities) - 1)


def score_updates(rewards, scores, baseline=0.0):
    scores = _matrix(scores, "scores")
    rewards = _vector(rewards, len(scores), "rewards")
    baseline = torch.as_tensor(baseline, dtype=torch.float64)
    if baseline.ndim == 0:
        baseline = baseline.expand_as(rewards)
    baseline = _vector(baseline, len(scores), "baseline")
    return (rewards - baseline)[:, None] * scores


@dataclass(frozen=True)
class Calibration:
    coefficients: torch.Tensor
    samples: int
    components: int
    ridge: float
    provenance: str


def fit_control_coefficients(target_updates, component_updates, *, ridge=1e-8, provenance):
    """Trace-covariance fit on separate calibration data; returns positive beta.

    component_updates has (calibration_samples, components, parameters).
    The mean-gradient inner products are subtracted, unlike raw E[u*v*||s||²].
    This is not a promise of variance/cost improvement on a shifted policy.
    """
    target = _matrix(target_updates, "target_updates")
    components = torch.as_tensor(component_updates, dtype=torch.float64)
    if components.ndim != 3 or components.shape[0] != len(target) or components.shape[2] != target.shape[1]:
        raise ValueError("component_updates must have shape (samples, components, parameters)")
    if len(target) < 2 or not components.shape[1] or not torch.isfinite(components).all():
        raise ValueError("calibration requires finite nonempty components and at least two samples")
    if not provenance or ridge < 0:
        raise ValueError("declare disjoint calibration provenance and nonnegative ridge")
    centered_target = target - target.mean(0)
    centered_components = components - components.mean(0, keepdim=True)
    covariance = torch.einsum("nkp,njp->kj", centered_components, centered_components) / (len(target) - 1)
    cross = torch.einsum("nkp,np->k", centered_components, centered_target) / (len(target) - 1)
    scale = covariance.diagonal().mean().clamp_min(torch.finfo(torch.float64).eps)
    stabilized = covariance + ridge * scale * torch.eye(components.shape[1], dtype=torch.float64)
    coefficients = torch.linalg.lstsq(stabilized, cross[:, None]).solution[:, 0]
    return Calibration(coefficients.detach(), len(target), components.shape[1], ridge, provenance)


def nested_control_variate(target_paired, proxy_all, paired_indices, coefficients):
    """mean(HF) + beta*(mean(all-LF)-mean(paired-LF)).

    The HF indices must be an independently uniform subset of the same IID
    current-policy proposals; residual-ranked top-k is not this estimator.
    Proxy updates can have any mean but must have the same marginal form.
    """
    target = _matrix(target_paired, "target_paired")
    proxy = torch.as_tensor(proxy_all, dtype=torch.float64)
    if proxy.ndim == 2:
        proxy = proxy[:, None, :]
    indices = torch.as_tensor(paired_indices, dtype=torch.long)
    beta = torch.as_tensor(coefficients, dtype=torch.float64).reshape(-1)
    if proxy.ndim != 3 or proxy.shape[2] != target.shape[1] or beta.shape != (proxy.shape[1],):
        raise ValueError("proxy/coefficient/target dimensions differ")
    if indices.shape != (len(target),) or not len(target) or len(indices.unique()) != len(indices):
        raise ValueError("paired indices must be a nonempty unique index per HF result")
    if (indices < 0).any() or (indices >= len(proxy)).any() or not torch.isfinite(proxy).all() or not torch.isfinite(beta).all():
        raise ValueError("invalid paired indices or nonfinite proxy/coefficient")
    correction = torch.einsum("k,kp->p", beta, proxy.mean(0) - proxy[indices].mean(0))
    return target.mean(0) + correction


def aipw_updates(target_observed, auxiliary, selected, probabilities):
    """Per-proposal auxiliary + Z/q*(HF-auxiliary).

    HF results exist exactly where purchased. No arbitrary NaN/failed-solve
    dropping: purchased failures need a declared finite target reward.
    auxiliary/probability/calibration state must be fixed before buying HF.
    """
    auxiliary = _matrix(auxiliary, "auxiliary")
    target = torch.as_tensor(target_observed, dtype=torch.float64)
    selected = torch.as_tensor(selected, dtype=torch.bool)
    probabilities = _vector(probabilities, len(auxiliary), "probabilities")
    if target.shape != auxiliary.shape or selected.shape != (len(auxiliary),):
        raise ValueError("target, auxiliary and selection dimensions differ")
    if (probabilities <= 0).any() or (probabilities > 1).any():
        raise ValueError("all probabilities must be positive and at most one")
    if not torch.isfinite(target[selected]).all():
        raise ValueError("purchased HF outcomes cannot be missing/nonfinite")
    result = auxiliary.clone()
    result[selected] += (target[selected] - auxiliary[selected]) / probabilities[selected, None]
    return result


def allocation_probabilities(predicted_squared_discrepancy, hf_cost, *,
                             expected_hf_count=None, expected_hf_cost_budget=None,
                             q_min=0.05):
    """Optimal positive Bernoulli acquisition for a conditional cost budget.

    Uses q proportional to sqrt(a/c), with probabilities in [q_min,1]. Count
    budgets are supported only for equal attempt costs. The inequality budget
    may be under-used when all informative samples saturate at probability one.
    Calibration/cheap costs are external fixed charges, never success-amortized.
    """
    a = torch.as_tensor(predicted_squared_discrepancy, dtype=torch.float64)
    if a.ndim != 1 or not len(a) or (a < 0).any() or not torch.isfinite(a).all():
        raise ValueError("discrepancy must be a finite nonnegative vector")
    cost = torch.as_tensor(hf_cost, dtype=torch.float64)
    if cost.ndim == 0:
        cost = cost.expand_as(a)
    cost = _vector(cost, len(a), "hf_cost")
    if (cost <= 0).any() or not 0 < q_min <= 1:
        raise ValueError("positive costs and q_min in (0,1] are required")
    if (expected_hf_count is None) == (expected_hf_cost_budget is None):
        raise ValueError("declare exactly one count or cost budget")
    if expected_hf_count is not None:
        if not torch.equal(cost, cost[0].expand_as(cost)):
            raise ValueError("a variable-cost allocation requires a cost budget, not a count")
        budget = float(expected_hf_count)*cost[0].item()
    else:
        budget = float(expected_hf_cost_budget)
    total = cost.sum().item()
    if not q_min*total <= budget <= total:
        raise ValueError("budget is incompatible with positive exploration floor")
    if budget == total:
        return torch.ones_like(a)
    magnitude = torch.sqrt(a/cost)
    if magnitude.max() == 0:
        return torch.full_like(a, budget/total)
    saturated = torch.where(a > 0, torch.ones_like(a), torch.full_like(a, q_min))
    if (saturated*cost).sum() <= budget:
        return saturated
    low, high = 0.0, 1.0
    while (torch.clamp(high*magnitude, q_min, 1.0)*cost).sum() < budget:
        high *= 2
    for _ in range(80):
        middle = (low+high)/2
        if (torch.clamp(middle*magnitude, q_min, 1.0)*cost).sum() < budget:
            low = middle
        else:
            high = middle
    return torch.clamp(high*magnitude, q_min, 1.0)


def lax_continuous_update(pathwise_proxy_all, hf_continuous_scores, hf_rewards, proxy_rewards_paired, *, coefficient=1.0):
    """c*mean(path_L) + mean_H[(R_H-c*R_L)*score_x].

    The path term must differentiate exactly the same fixed smooth proxy
    function used in proxy_rewards_paired. x must not feed later token choices.
    The separate discrete target score is not included in this return value.
    """
    path = _matrix(pathwise_proxy_all, "pathwise_proxy_all")
    scores = _matrix(hf_continuous_scores, "hf_continuous_scores")
    hf = _vector(hf_rewards, len(scores), "hf_rewards")
    lf = _vector(proxy_rewards_paired, len(scores), "proxy_rewards_paired")
    if path.shape[1] != scores.shape[1]:
        raise ValueError("pathwise and score parameter dimensions differ")
    return coefficient*path.mean(0) + ((hf-coefficient*lf)[:,None]*scores).mean(0)
