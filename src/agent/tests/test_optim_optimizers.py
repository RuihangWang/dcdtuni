"""Generic optimizers: heuristic, rank-mu CMA-ES, multi-fidelity BO and projected adjoint ascent.

Extracted from the frozen ``training/e5_native_optimizers_v2.py`` behind one
interface ``optimize(oracle, space, budget, seed, logger, method=...)``. On the
frozen module's own analytic fixture (2 continuous variables, 2 discrete
choices, seed 2301) the generic code proposes exactly the designs the frozen
code proposed, in the same order and fidelity (``LEGACY_GOLDEN``); the other
tests port the fixture's own acceptance checks and exercise other dimensions.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from agent.optim import optimizers as opt
from agent.optim.optimizers import METHODS, Space, optimize

# Recorded by running the frozen agent.training.e5_native_optimizers_v2.optimize (tag
# agent-pre-slim-2026-10-22) with ``fixture_oracle`` below on BOUNDS/choices (0, 1), seed 2301,
# caps heuristic 2 / others 16. Each row: (choice, fidelity, gradient requested, physical design, I0).
LEGACY_GOLDEN = {
    'heuristic': {'attempts': 2, 'incumbent_I0': -0.09489999999999997, 'history': [
        (0, 'HF', False, (0.14, 0.5), -0.09489999999999997),
        (1, 'HF', False, (0.14, 0.5), -0.1649),
    ]},
    'cma-es': {'attempts': 16, 'incumbent_I0': -0.013826890948397294, 'history': [
        (0, 'HF', False, (0.12979164029264706, 0.5490721755853709), -0.1834824212003919),
        (1, 'HF', False, (0.13528254645808827, 0.5366660751096639), -0.2209875593777963),
        (0, 'HF', False, (0.08287313536076604, 0.48563300794828923), -0.340699959725875),
        (1, 'HF', False, (0.1172410277056519, 0.550116864480632), -0.2951914739137864),
        (0, 'HF', False, (0.13924984775765725, 0.45604356030692217), -0.06311034816063736),
        (1, 'HF', False, (0.12547181511877956, 0.3709542996280497), -0.1840964440776067),
        (0, 'HF', False, (0.15301247242567922, 0.5631082518934196), -0.16667268580442862),
        (1, 'HF', False, (0.12724844755455128, 0.3276310353509509), -0.2140078017058451),
        (0, 'HF', False, (0.16244758274618368, 0.4601387530919778), -0.027661784755349618),
        (1, 'HF', False, (0.16935033812869493, 0.5748770602343377), -0.2579272649183457),
        (0, 'HF', False, (0.15990144052249178, 0.5435074868617171), -0.1277968144426799),
        (1, 'HF', False, (0.15178965152903418, 0.5713243705634687), -0.25341389326998676),
        (0, 'HF', False, (0.17353142224291856, 0.48911632903021734), -0.05164196261459931),
        (1, 'HF', False, (0.16090966458936698, 0.3809830936644443), -0.08243431441514043),
        (0, 'HF', False, (0.16488744877985995, 0.4388752800855604), -0.013826890948397294),
        (1, 'HF', False, (0.13614889379578593, 0.5071437211736867), -0.18151626271144583),
    ]},
    'mfbo': {'attempts': 16, 'incumbent_I0': -0.09489999999999997, 'history': [
        (0, 'HF', False, (0.14, 0.5), -0.09489999999999997),
        (1, 'HF', False, (0.14, 0.5), -0.1649),
        (0, 'LF', False, (0.14, 0.5), -0.003899999999999993),
        (1, 'LF', False, (0.14, 0.5), -0.0739),
        (0, 'LF', False, (0.2174874271452427, 0.7258176314644516), -0.6579289449940763),
        (0, 'LF', False, (0.06735777005553245, 0.2781596458517015), -0.6001975717912773),
        (0, 'LF', False, (0.06677828952670097, 0.7310575167648494), -0.508427498704062),
        (0, 'HF', False, (0.2168140085041523, 0.2728997780941427), -0.12145882364216365),
        (1, 'LF', False, (0.2174874271452427, 0.7258176314644516), -0.7279289449940762),
        (1, 'LF', False, (0.06735777005553245, 0.2781596458517015), -0.6701975717912774),
        (1, 'LF', False, (0.2168140085041523, 0.2728997780941427), -0.4232470871595956),
        (1, 'HF', False, (0.06677828952670097, 0.7310575167648494), -0.989365627662518),
        (0, 'LF', False, (0.15370659589767455, 0.26423012698069215), -0.2729162114754047),
        (0, 'LF', False, (0.2131238190829754, 0.47300260746851563), -0.16311410538486132),
        (1, 'LF', False, (0.14, 0.25), -0.40140000000000003),
        (0, 'HF', False, (0.14, 0.75), -0.7273999999999999),
    ]},
    'adjoint-descent': {'attempts': 16, 'incumbent_I0': -1.864314344484162e-05, 'history': [
        (0, 'HF', True, (0.14, 0.5), -0.09489999999999997),
        (1, 'HF', True, (0.14, 0.5), -0.1649),
        (0, 'HF', True, (0.1542, 0.435625), -0.024115624999999995),
        (1, 'HF', True, (0.1542, 0.435625), -0.09411562500000001),
        (0, 'HF', True, (0.163875, 0.41398437499999996), -0.0071544677734375),
        (1, 'HF', True, (0.163875, 0.41398437499999996), -0.07715446777343751),
        (0, 'HF', True, (0.169578125, 0.404794921875), -0.0021708557128906274),
        (1, 'HF', True, (0.169578125, 0.404794921875), -0.07217085571289063),
        (0, 'HF', True, (0.17279726562499997, 0.400269775390625), -0.0006606435358524414),
        (1, 'HF', True, (0.17279726562499997, 0.400269775390625), -0.07066064353585245),
        (0, 'HF', True, (0.174587841796875, 0.3978810119628906), -0.00020112650599330591),
        (1, 'HF', True, (0.174587841796875, 0.3978810119628906), -0.07020112650599332),
        (0, 'HF', True, (0.17557868041992186, 0.39658437728881835), -6.123403007368369e-05),
        (1, 'HF', True, (0.17557868041992186, 0.39658437728881835), -0.0700612340300737),
        (0, 'HF', True, (0.17612596511840822, 0.39587317228317265), -1.864314344484162e-05),
        (1, 'HF', True, (0.17612596511840822, 0.39587317228317265), -0.07001864314344484),
    ]},
}


BOUNDS = ((.06, .22), (.25, .75))
SPACE = Space(BOUNDS, names=("lid_speed_m_s", "hot_center_fraction"), choices=(0, 1))
HF_OPT, LF_OPT = np.asarray([.73, .29]), np.asarray([.55, .46])
H = np.asarray([[1., .25], [.25, 1.5]])


def fixture_oracle(point, fidelity, with_gradient):
    """The frozen fixture: independently known HF optimum, biased LF optimum, choice penalty."""
    x = SPACE.to_unit(point.values)
    target = HF_OPT if fidelity == "HF" else LF_OPT
    reward = -float((x - target) @ H @ (x - target)) - .07 * point.choice
    gradient = -2 * H @ (x - target) / SPACE.width
    return {"I0": reward, "status": "success", "I3": gradient.tolist() if with_gradient else None,
            "costs": {"cpu_seconds": 1. if fidelity == "HF" else .1}}


@pytest.mark.parametrize("method", METHODS)
def test_generic_optimizers_reproduce_the_frozen_proposals(method):
    golden = LEGACY_GOLDEN[method]
    result = optimize(fixture_oracle, SPACE, golden["attempts"], 2301, None, method=method)
    assert result["attempts"] == golden["attempts"]
    assert len(result["history"]) == len(golden["history"])
    for row, (choice, fidelity, gradient, physical, reward) in zip(result["history"], golden["history"]):
        assert row["design"]["choice"] == choice and row["fidelity"] == fidelity
        assert row["gradient_requested"] is gradient
        assert list(row["design"]["values"].values()) == pytest.approx(physical, rel=1e-12, abs=1e-15)
        assert row["I0"] == pytest.approx(reward, rel=1e-10, abs=1e-14)
    assert result["incumbent_HF"]["I0"] == pytest.approx(golden["incumbent_I0"], rel=1e-10, abs=1e-14)


def test_the_frozen_fixture_acceptance_checks_hold_at_64_attempts():
    summaries = {m: optimize(fixture_oracle, SPACE, 64, 2301, None, method=m) for m in METHODS}
    for row in summaries.values():
        for item in row["history"]:
            for (low, high), value in zip(BOUNDS, item["design"]["values"].values()):
                assert low <= value <= high
    assert summaries["cma-es"]["incumbent_HF"]["I0"] >= -.03
    assert summaries["adjoint-descent"]["incumbent_HF"]["I0"] >= -.01
    mf = summaries["mfbo"]
    assert mf["LF_attempts"] > 0 and mf["HF_attempts"] > 0
    assert any("acquisition_score" in r["optimizer_metadata"] for r in mf["history"])


def test_co_kriging_covariance_conditioning_and_lf_information_transfer():
    gp = opt.AutoregressiveGP(dim=2)
    X = np.asarray([[.5, .5, 0, 0], [.5, .5, 0, 1], [.7, .2, 1, 0]])
    K = gp.kernel(X, X)
    assert np.linalg.eigvalsh(K).min() > 0
    pred, _ = gp.fit(X, [-.1, -.12, -.3]).predict(X)
    assert np.max(abs(pred - np.asarray([-.1, -.12, -.3]))) < 1e-6
    q = np.asarray([[.5, .5, 0, 1]])
    before = opt.AutoregressiveGP(dim=2).kernel(q, q)[0, 0]
    after = opt.AutoregressiveGP(dim=2).fit(X[:1], [-.1]).predict(q)[1][0]
    assert after < before


@pytest.mark.parametrize("method", METHODS)
def test_three_dimensions_three_choices_stay_in_bounds_and_log(method, tmp_path):
    from agent.records import RunLogger, RunStore
    space = Space(((0., 1.), (-2., 2.), (10., 20.)), choices=("a", "b", "c"))
    target = np.asarray([.2, 1., 17.])

    def oracle(point, fidelity, with_gradient):
        values = np.asarray(point.values)
        scale = space.width
        reward = -float(np.sum(((values - target) / scale) ** 2)) - (.05 if fidelity == "LF" else 0.)
        gradient = (-2 * (values - target) / scale ** 2).tolist() if with_gradient else None
        return {"I0": reward, "status": "success", "I3": gradient, "costs": {"cpu_seconds": .5}}
    store = RunStore(tmp_path / "root", create=True)
    logger = RunLogger(store, "optim", config={}, identity={"model": "none", "data": "none", "seed": 7,
                                                            "purpose": "unit"}, source_identity={"git": "unit"})
    result = optimize(oracle, space, 12, 7, logger, method=method)
    assert 1 <= result["attempts"] <= 12
    assert {row["design"]["choice"] for row in result["history"]} <= {"a", "b", "c"}
    for row in result["history"]:
        for (low, high), value in zip(space.bounds, row["design"]["values"].values()):
            assert low <= value <= high
    lines = (logger.path / "metrics.jsonl").read_text().splitlines()
    assert len(lines) == result["attempts"]
    assert json.loads(lines[0])["values"]["attempt"] == 1


def test_callback_retains_paid_partial_history_on_a_later_failure():
    paid, calls = [], 0

    def bounded(point, fidelity, gradient):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise TimeoutError("analytic deadline injection")
        return fixture_oracle(point, fidelity, gradient)
    with pytest.raises(TimeoutError):
        optimize(bounded, SPACE, 8, 1301, None, method="cma-es", on_reply=lambda record, best: paid.append((record, best)))
    assert [x[0]["attempt"] for x in paid] == [1, 2, 3]
    assert all(x[1] is not None for x in paid)


def test_nonfinite_rewards_unknown_methods_and_bad_spaces_are_refused():
    with pytest.raises(ValueError, match="finite"):
        optimize(lambda p, f, g: {"I0": float("nan"), "status": "failed"}, SPACE, 2, 1, None, method="heuristic")
    with pytest.raises(ValueError, match="method"):
        optimize(fixture_oracle, SPACE, 2, 1, None, method="random-search")
    with pytest.raises(ValueError):
        optimize(fixture_oracle, SPACE, 0, 1, None, method="cma-es")
    for bounds in (((1., 0.),), ((0., float("inf")),), ()):
        with pytest.raises(ValueError):
            Space(bounds)
    with pytest.raises(ValueError):
        Space(((0., 1.),), choices=())


def test_heuristic_evaluates_the_midpoint_of_every_choice():
    result = optimize(fixture_oracle, SPACE, 2, 1301, None, method="heuristic")
    assert result["attempts"] == 2 and result["HF_attempts"] == 2
    assert [x["design"]["choice"] for x in result["history"]] == [0, 1]
    assert list(result["history"][0]["design"]["values"].values()) == pytest.approx([.14, .5])


def test_adjoint_failure_retains_the_reply_and_invents_no_step():
    replies = []

    def oracle(point, fidelity, with_gradient):
        replies.append(point.values)
        return {"I0": -1., "status": "failed", "I3": None}
    result = optimize(oracle, SPACE, 4, 1, None, method="adjoint-descent")
    assert result["attempts"] == 4 and all(row["status"] == "failed" for row in result["history"])
    assert all(values == replies[0] for values in replies)
