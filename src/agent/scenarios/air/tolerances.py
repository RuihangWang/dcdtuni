"""Unit-aware hard air-task decisions; frozen objective.py remains unchanged.

Temperature violations are differences in K; capacity and heat-closure
violations are dimensionless fractions. Tolerances affect feasibility only.
Infeasible penalties still use the original nonnegative violations, not a
tolerance-subtracted surrogate. No physical temperature limit, chip limit,
or new energy reward is introduced here.

The I0 target and exact rank_key match objective.py at zero tolerances.
I0 remains a bounded weighted scalar among feasible designs; rank_key is
the separate exact PUE-first ordering. Invalid I0 retains the old sentinel.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from collections.abc import Mapping
import math
import numbers
import numpy as np

from . import objective as legacy

VERSION = "air-objective-unit-tolerances/v2"
KNOWN_UNITS = {
    "inlet_hi_k": "K", "inlet_lo_k": "K",
    "exhaust_hi_k": "K", "return_hi_k": "K",
    "acu_capacity_frac": "1", "energy_closure_frac": "1",
}
DEFAULT_SCALES = {
    "inlet_hi_k": 1.0, "inlet_lo_k": 1.0,
    "exhaust_hi_k": 1.0, "return_hi_k": 1.0,
    "acu_capacity_frac": 0.1, "energy_closure_frac": 0.01,
}


def _number(value, name, *, positive=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real):
        raise ValueError(name + " must be a real scalar, not bool")
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(name + (" must be finite and positive" if positive
                                else " must be finite and nonnegative"))


@dataclass(frozen=True)
class ConstraintRule:
    name: str
    unit: str
    scale: float
    tolerance: float

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("constraint name required")
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise ValueError("explicit constraint unit required")
        if self.name in KNOWN_UNITS and self.unit != KNOWN_UNITS[self.name]:
            raise ValueError("known constraint unit mismatch: " + self.name)
        _number(self.scale, self.name + " scale", positive=True)
        _number(self.tolerance, self.name + " tolerance")


@dataclass(frozen=True)
class ConstraintPolicy:
    rules: tuple[ConstraintRule, ...]
    required_keys: tuple[str, ...] = legacy.BASE_KEYS

    def __post_init__(self):
        object.__setattr__(self, "rules", tuple(self.rules))
        object.__setattr__(self, "required_keys", tuple(self.required_keys))
        if not self.rules or any(not isinstance(r, ConstraintRule) for r in self.rules):
            raise ValueError("explicit ConstraintRule objects required")
        names = [r.name for r in self.rules]
        if len(set(names)) != len(names):
            raise ValueError("duplicate constraint rule")
        required = self.required_keys
        if any(not isinstance(k, str) or not k for k in required):
            raise ValueError("required constraint names must be nonempty strings")
        if len(set(required)) != len(required):
            raise ValueError("duplicate required constraint")
        if not set(legacy.BASE_KEYS) <= set(required):
            raise ValueError("all five base air constraints must remain required")
        if not set(required) <= set(names):
            raise ValueError("every required constraint needs an explicit rule")

    def as_dict(self):
        return {"version": VERSION, "rules": [asdict(r) for r in self.rules],
                "required_keys": list(self.required_keys),
                "tolerance_semantics": "feasibility only; raw violations retained in penalty"}


def temperature_tolerance_policy(*, temperature_tolerance_k=0.0,
                                 capacity_tolerance_fraction=0.0,
                                 closure_tolerance_fraction=0.0,
                                 extra_rules=(), extra_required_keys=()):
    """Explicit unit-bearing tolerance factory; no hardware limits are chosen."""
    _number(temperature_tolerance_k, "temperature_tolerance_k")
    _number(capacity_tolerance_fraction, "capacity_tolerance_fraction")
    _number(closure_tolerance_fraction, "closure_tolerance_fraction")
    rules = []
    for name, unit in KNOWN_UNITS.items():
        tol = (temperature_tolerance_k if unit == "K" else
               capacity_tolerance_fraction if name == "acu_capacity_frac" else
               closure_tolerance_fraction)
        rules.append(ConstraintRule(name, unit, DEFAULT_SCALES[name], tol))
    return ConstraintPolicy(tuple(rules) + tuple(extra_rules),
                            legacy.BASE_KEYS + tuple(extra_required_keys))


STRICT_POLICY = temperature_tolerance_policy()


def _legacy_zero_tol(tol):
    _number(tol, "legacy scalar tol")
    if tol != 0:
        raise ValueError("nonzero scalar tol is ambiguous; pass an explicit unit-aware policy")


def _array(value, name):
    # Hard decisions are CPU-only; never silently move GPU tensors or infer units.
    if type(value).__module__.split(".")[0] == "torch":
        import torch
        if value.device.type != "cpu" or not value.is_floating_point():
            raise ValueError(name + " requires real floating CPU tensor")
        a = value.detach().to(dtype=torch.float64).numpy()
    else:
        try:
            a = np.asarray(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(name + " must be a real numeric array") from exc
        if a.dtype.kind not in "fiu":
            raise ValueError(name + " must be real numeric, not bool/string/complex")
        a = a.astype(np.float64, copy=False)
    if a.size == 0 or not np.isfinite(a).all() or np.any(a < 0):
        raise ValueError(name + " must contain finite nonnegative violations")
    return a


@dataclass(frozen=True)
class Assessment:
    feasible: bool
    penalty: float
    max_violation: dict[str, float]
    tolerance: dict[str, float]


def assess(viol, *, policy=STRICT_POLICY):
    """One validation/feasibility implementation used by all three public APIs."""
    if not isinstance(policy, ConstraintPolicy):
        raise ValueError("explicit ConstraintPolicy required")
    if not isinstance(viol, Mapping) or not viol:
        raise ValueError("nonempty constraint mapping required")
    if any(not isinstance(k, str) for k in viol):
        raise ValueError("constraint keys must be strings")
    rules = {r.name: r for r in policy.rules}
    if not set(policy.required_keys) <= set(viol):
        raise ValueError("missing required constraints")
    if not set(viol) <= set(rules):
        raise ValueError("unknown constraint must declare unit, scale and tolerance")
    maxima, tolerances, penalty = {}, {}, 0.0
    for name, value in viol.items():
        a = _array(value, name)
        rule = rules[name]
        maxima[name] = float(a.max())
        tolerances[name] = float(rule.tolerance)
        # Scalarization preserves old raw-violation normalization.
        penalty += float(a.sum()) / rule.scale
    if not math.isfinite(penalty):
        raise ValueError("nonfinite normalized violation penalty")
    return Assessment(all(maxima[k] <= tolerances[k] for k in maxima),
                      penalty, maxima, tolerances)


def _weights_match_policy(w, policy, viol):
    # ScoreWeights still carries old normalization scales. Do not silently
    # override caller settings when a new explicit policy uses other scales.
    scales = dict(w.scales)
    rules = {r.name: r for r in policy.rules}
    for name in viol:
        if name in scales and scales[name] != rules[name].scale:
            raise ValueError("weight/policy normalization scale mismatch: " + name)


def _metric_cpu(value, name):
    if type(value).__module__.split(".")[0] == "torch" and value.device.type != "cpu":
        raise ValueError(name + " requires CPU input")


def is_feasible(viol, tol=0.0, *, policy=STRICT_POLICY):
    _legacy_zero_tol(tol)
    return assess(viol, policy=policy).feasible


def score(pue, t_ex, viol, w=legacy.ScoreWeights(), exhaust_mode="mean",
          tol=0.0, *, policy=STRICT_POLICY, evaluation_valid=True):
    """Same bounded I0 as frozen objective.py with a unit-aware feasible tier."""
    _legacy_zero_tol(tol)
    if not evaluation_valid:
        return -w.invalid_offset
    a = assess(viol, policy=policy)
    _weights_match_policy(w, policy, viol)
    _metric_cpu(pue, "PUE"); _metric_cpu(t_ex, "exhaust temperature")
    loss = float(legacy.scalarized_loss(pue, t_ex, w, exhaust_mode))
    if a.feasible:
        return -2.0 / math.pi * math.atan(loss)
    return -w.infeasible_offset - a.penalty


def rank_key(pue, t_ex, viol, w=legacy.ScoreWeights(), exhaust_mode="mean",
             tol=0.0, *, policy=STRICT_POLICY, evaluation_valid=True):
    """Same exact validity/feasibility/PUE/exhaust ordering as the frozen target."""
    _legacy_zero_tol(tol)
    if not evaluation_valid:
        return (0, 0, -math.inf, -math.inf, -math.inf)
    a = assess(viol, policy=policy)
    _weights_match_policy(w, policy, viol)
    _metric_cpu(pue, "PUE"); _metric_cpu(t_ex, "exhaust temperature")
    legacy.scalarized_loss(pue, t_ex, w, exhaust_mode)
    return (1, int(a.feasible), 0.0 if a.feasible else -a.penalty,
            -float(pue), -float(legacy.exhaust_stat(t_ex, exhaust_mode)))
