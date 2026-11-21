"""Counterexamples for unit-aware hard feasibility and unchanged I0 targets."""
import math
import numpy as np
import pytest
import torch
from agent.scenarios.air import objective as old
from agent.scenarios.air import tolerances as new


def feedback(backend="numpy", **changes):
    make = lambda x: np.array(x, dtype=float) if backend == "numpy" else torch.tensor(x, dtype=torch.float64)
    result = {k: make([0.0]) for k in old.BASE_KEYS}
    result.update({k: make(v) for k, v in changes.items()})
    return result


def all_decisions(v, *, policy=new.STRICT_POLICY):
    feasible = new.is_feasible(v, policy=policy)
    score = new.score(1.2, [40.], v, policy=policy)
    rank = new.rank_key(1.2, [40.], v, policy=policy)
    assert rank[1] == int(feasible)
    assert (score >= -1) == feasible
    return feasible, score, rank


@pytest.mark.parametrize("backend", ["numpy", "torch"])
def test_temperature_tolerance_never_authorizes_capacity_or_closure(backend):
    policy = new.temperature_tolerance_policy(temperature_tolerance_k=.2)
    warm = feedback(backend, inlet_hi_k=[.15])
    assert old.is_feasible(warm, tol=.2) and all_decisions(warm, policy=policy)[0]
    capacity = feedback(backend, inlet_hi_k=[.15], acu_capacity_frac=[.15])
    assert old.is_feasible(capacity, tol=.2)  # Actual regression trigger.
    assert not all_decisions(capacity, policy=policy)[0]
    closure = feedback(backend, inlet_hi_k=[.15], energy_closure_frac=[.15])
    assert not all_decisions(closure, policy=policy)[0]
    assert new.assess(capacity, policy=policy).penalty == pytest.approx(.15 + .15/.1)


@pytest.mark.parametrize("backend", ["numpy", "torch"])
def test_explicit_fraction_tolerance_is_independent_and_boundary_is_inclusive(backend):
    policy = new.temperature_tolerance_policy(temperature_tolerance_k=.2,
        capacity_tolerance_fraction=.01, closure_tolerance_fraction=.002)
    assert all_decisions(feedback(backend, inlet_hi_k=[.2], acu_capacity_frac=[.01],
                                 energy_closure_frac=[.002]), policy=policy)[0]
    assert not all_decisions(feedback(backend, inlet_hi_k=[np.nextafter(.2, 1.)]), policy=policy)[0]
    assert not all_decisions(feedback(backend, acu_capacity_frac=[.010001]), policy=policy)[0]


@pytest.mark.parametrize("api", [new.is_feasible, new.score, new.rank_key])
@pytest.mark.parametrize("kind", ["empty", "nan", "inf", "negative", "empty_array",
                                 "none", "unknown", "missing", "bool", "complex", "string"])
def test_all_public_decisions_reject_invalid_or_undeclared_feedback(api, kind):
    v = feedback()
    if kind == "empty": v = {}
    elif kind == "unknown": v["undeclared"] = np.array([0.])
    elif kind == "missing": del v["return_hi_k"]
    else:
        v["inlet_hi_k"] = {"nan":[math.nan], "inf":[math.inf], "negative":[-.1],
            "empty_array":[], "none":None, "bool":[True], "complex":[1+2j], "string":["0.1"]}[kind]
    args = (v,) if api is new.is_feasible else (1.2, [40.], v)
    with pytest.raises(ValueError): api(*args)


@pytest.mark.parametrize("bad", [-.1, math.nan, math.inf, True, "0.2"])
def test_invalid_tolerances_are_rejected(bad):
    with pytest.raises(ValueError):
        new.ConstraintRule("inlet_hi_k", "K", 1., bad)
    with pytest.raises(ValueError):
        new.temperature_tolerance_policy(temperature_tolerance_k=bad)


def test_unknown_constraint_needs_explicit_unit_scale_and_required_status():
    extra = new.ConstraintRule("pressure_excess_pa", "Pa", 10., .1)
    policy = new.temperature_tolerance_policy(extra_rules=(extra,),
                                              extra_required_keys=("pressure_excess_pa",))
    with pytest.raises(ValueError): new.is_feasible(feedback(), policy=policy)
    ok = feedback(pressure_excess_pa=[.1])
    assert all_decisions(ok, policy=policy)[0]
    assert not all_decisions(feedback(pressure_excess_pa=[.2]), policy=policy)[0]
    with pytest.raises(ValueError): new.ConstraintRule("acu_capacity_frac", "K", .1, .2)
    with pytest.raises(ValueError): new.ConstraintRule("new", "", 1., 0.)
    with pytest.raises(ValueError): new.ConstraintRule("new", "Pa", 0., 0.)
    with pytest.raises(ValueError): new.ConstraintPolicy(new.STRICT_POLICY.rules, ("inlet_hi_k",))
    with pytest.raises(ValueError): new.ConstraintPolicy(new.STRICT_POLICY.rules * 2)


@pytest.mark.parametrize("backend", ["numpy", "torch"])
def test_zero_policy_exactly_preserves_existing_target_and_selection(backend):
    for pue in [1., 1.2, 1e12]:
        for ex in [[30.], [40., 44.]]:
            for v in [feedback(backend), feedback(backend, inlet_hi_k=[.15]),
                      feedback(backend, acu_capacity_frac=[.15], energy_closure_frac=[.01])]:
                assert new.is_feasible(v, tol=0.) == old.is_feasible(v, tol=0.)
                assert new.score(pue, ex, v, tol=0.) == old.score(pue, ex, v, tol=0.)
                assert new.rank_key(pue, ex, v, tol=0.) == old.rank_key(pue, ex, v, tol=0.)
    assert new.score(None, None, None, evaluation_valid=False) == old.score(None, None, None, evaluation_valid=False)
    assert new.rank_key(None, None, None, evaluation_valid=False) == old.rank_key(None, None, None, evaluation_valid=False)


def test_all_finite_feasible_I0_outrank_infeasible_and_exact_rank_is_lexicographic():
    policy = new.temperature_tolerance_policy(temperature_tolerance_k=.2)
    ok = feedback(inlet_hi_k=[.15]); bad = feedback(acu_capacity_frac=[.15])
    assert new.score(1e12, [44.], ok, policy=policy) > new.score(1.01, [20.], bad, policy=policy)
    assert new.rank_key(1e12, [44.], ok, policy=policy) > new.rank_key(1.01, [20.], bad, policy=policy)
    assert new.rank_key(1.2, [44.], ok, policy=policy) > new.rank_key(1.200001, [30.], ok, policy=policy)
    assert new.score(1.2, [44.], ok, policy=policy) < new.score(1.200001, [30.], ok, policy=policy)
    assert new.rank_key(10., [50.], bad, policy=policy) > new.rank_key(None,None,None,evaluation_valid=False)


@pytest.mark.parametrize("api", [new.is_feasible, new.score, new.rank_key])
def test_old_nonzero_scalar_tolerance_is_rejected_instead_of_reinterpreted(api):
    args = (feedback(),) if api is new.is_feasible else (1.2, [40.], feedback())
    for tol in [.2, -.1, math.nan]:
        with pytest.raises(ValueError): api(*args, tol=tol)


def test_policy_units_serialization_and_scale_conflicts_are_explicit():
    policy = new.temperature_tolerance_policy(temperature_tolerance_k=.2)
    serialized = policy.as_dict()
    assert {r["unit"] for r in serialized["rules"]} == {"K","1"}
    assert serialized["required_keys"] == list(old.BASE_KEYS)
    scales = dict(old.ScoreWeights().scales); scales["acu_capacity_frac"] = .2
    w = old.ScoreWeights(scales=scales)
    for api in [new.score, new.rank_key]:
        with pytest.raises(ValueError): api(1.2, [40.], feedback(), w=w)


def test_mixed_numpy_torch_is_supported_but_non_cpu_and_nonfloating_tensors_refused():
    v = feedback();v["inlet_hi_k"] = torch.tensor([.15],dtype=torch.float64)
    assert all_decisions(v, policy=new.temperature_tolerance_policy(temperature_tolerance_k=.2))[0]
    for bad in [torch.tensor([True]), torch.tensor([1]), torch.empty(1, device="meta")]:
        vv = feedback();vv["inlet_hi_k"] = bad
        with pytest.raises(ValueError): new.is_feasible(vv)


def test_existing_air_limits_are_used_without_adding_chip_temperature_assumptions():
    assert old.ASHRAE_CLASSES["A1"].t_in_max_c == 27.
    assert old.ASHRAE_CLASSES["H1"].t_in_max_c == 22.
    assert old.ThermalLimits().t_ex_max_c == old.ThermalLimits().t_ra_max_c == 45.
    assert not any("chip" in rule.name for rule in new.STRICT_POLICY.rules)
