"""Acquisition: q and the predictions are on disk before the draw, and the draw before any outcome.

The "register, then buy" ledger the research line asked for: a purchase is a
recorded independent Bernoulli draw with the registered probabilities, an
outcome is accepted only for a purchased proposal and only after the draw is
saved, and a purchased failure keeps a finite target value instead of being
dropped.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from agent.optim.acquisition import Acquisition, AcquisitionError
from agent.records import RunStore


@pytest.fixture
def ledger(tmp_path):
    return Acquisition(RunStore(tmp_path / "root", create=True), "hf-purchases",
                       policy_identity={"policy_sha256": "fixed-before-proposals"})


def test_register_draw_outcomes_close_in_that_order(ledger):
    group = ledger.register("group-0", ["p0", "p1", "p2", "p3"],
                            predictions={"lf_reward": [-.2, -.3, -.25, -.15]},
                            discrepancy=[0., 1., 4., 25.], hf_cost=[1., 2., 2., 3.],
                            expected_hf_cost_budget=1.5, q_min=.05)
    registration = ledger.store.read_json(group.path / "registration.json")
    assert registration["q"] == pytest.approx(group.q.tolist())
    assert registration["predictions"]["lf_reward"] == [-.2, -.3, -.25, -.15]
    assert registration["outcomes_read"] is False
    assert sum(q * c for q, c in zip(registration["q"], [1., 2., 2., 3.])) == pytest.approx(1.5)
    with pytest.raises(AcquisitionError, match="draw"):
        ledger.record_outcome(group, "p0", outcome=-.2, costs={"wall_seconds": 1.}, status="succeeded")
    selected = ledger.draw(group, seed=11)
    draw = ledger.store.read_json(group.path / "draw.json")
    assert draw["registration_sha256"] == RunStore.sha256(ledger.store.read_bytes(group.path / "registration.json"))
    assert draw["selected"] == [bool(s) for s in selected]
    expected = np.random.default_rng(11).random(4) < np.asarray(registration["q"])
    assert list(selected) == list(expected)
    chosen = [pid for pid, s in zip(group.proposals, selected) if s]
    skipped = [pid for pid, s in zip(group.proposals, selected) if not s]
    if skipped:
        with pytest.raises(AcquisitionError, match="not purchased"):
            ledger.record_outcome(group, skipped[0], outcome=0., costs={"wall_seconds": 1.}, status="succeeded")
    for pid in chosen:
        ledger.record_outcome(group, pid, outcome=-1e6, costs={"wall_seconds": 2.}, status="failed")
    if chosen:
        with pytest.raises(AcquisitionError, match="already"):
            ledger.record_outcome(group, chosen[0], outcome=0., costs={"wall_seconds": 1.}, status="succeeded")
    summary = ledger.close(group)
    assert summary["purchased"] == len(chosen) and summary["failed"] == len(chosen)
    assert summary["charged_wall_seconds"] == pytest.approx(2. * len(chosen))
    with pytest.raises(FileExistsError):
        ledger.close(group)


def test_outcomes_must_be_finite_and_costs_measured(ledger):
    group = ledger.register("g", ["a", "b"], q=[1., 1.], predictions={"lf": [0., 1.]})
    ledger.draw(group, seed=0)
    with pytest.raises(AcquisitionError, match="finite"):
        ledger.record_outcome(group, "a", outcome=float("nan"), costs={"wall_seconds": 1.}, status="failed")
    with pytest.raises(AcquisitionError, match="cost"):
        ledger.record_outcome(group, "a", outcome=0., costs={}, status="succeeded")
    ledger.record_outcome(group, "a", outcome=0., costs={"wall_seconds": 1.}, status="succeeded")
    with pytest.raises(AcquisitionError, match="missing"):
        ledger.close(group)


def test_registration_is_exclusive_and_validates_inputs(ledger):
    ledger.register("g", ["a"], q=[.5], predictions={})
    with pytest.raises(FileExistsError):
        ledger.register("g", ["a"], q=[.5], predictions={})
    for kwargs in ({"q": [0.]}, {"q": [1.5]}, {"q": [.5, .5]}, {"q": [.5], "predictions": {"x": [1., 2.]}}):
        with pytest.raises(ValueError):
            ledger.register("h", ["a"], **{"predictions": {}, **kwargs})
    with pytest.raises(ValueError):
        ledger.register("h", ["a", "a"], q=[.5, .5], predictions={})


def test_reopen_reads_the_saved_group(ledger):
    group = ledger.register("g", ["a", "b"], q=[1., .5], predictions={"lf": [1., 2.]})
    ledger.draw(group, seed=3)
    again = ledger.group("g")
    assert again.proposals == ("a", "b") and list(again.q) == [1., .5]
    assert again.selected is not None and bool(again.selected[0]) is True
    rows = json.loads(ledger.store.read_bytes(group.path / "registration.json"))
    assert rows["format"] == "agent-acquisition-registration/v1"


def test_reopen_refuses_a_draw_or_outcome_that_does_not_match_its_registration(ledger):
    group = ledger.register("h", ["a", "b"], q=[1., 1.], predictions={"lf": [1., 2.]})
    ledger.draw(group, seed=3)
    draw = ledger.store.root / group.path / "draw.json"
    record = json.loads(draw.read_text())
    draw.write_text(json.dumps({**record, "registration_sha256": "0" * 64}))
    with pytest.raises(AcquisitionError, match="registration"):
        ledger.group("h")
    draw.write_text(json.dumps({**record, "selected": [True]}))
    with pytest.raises(AcquisitionError, match="one draw per proposal"):
        ledger.group("h")
    draw.write_text(json.dumps(record))
    ledger.record_outcome(group, "a", outcome=-1., costs={"wall_seconds": 1.}, status="succeeded")
    (ledger.store.root / group.path / "outcomes.jsonl").write_text(
        json.dumps({"proposal": "a", "outcome": -1.}) + "\n" + json.dumps({"proposal": "a", "outcome": -2.}) + "\n")
    with pytest.raises(AcquisitionError, match="twice"):
        ledger.group("h")
