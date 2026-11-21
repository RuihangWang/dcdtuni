"""The trunk's Shinaide hybrid task grammar is the legacy v4 codec's, action by action.

The legacy codec's own outcomes for these actions were frozen into
``fixtures/hybrid_legacy/codec_v4_outcomes.json`` (2026-10-22, from the tag
``agent-pre-slim-2026-10-22``) before ``src/agent_legacy`` was removed.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.scenarios.hybrid import task as T

FROZEN = json.loads((Path(__file__).parent / "fixtures/hybrid_legacy/codec_v4_outcomes.json").read_text())


def roundtrip(value):
    """What the frozen JSON holds for ``value`` (tuples become lists)."""
    return json.loads(json.dumps(value))


@pytest.fixture(scope="module")
def task():
    return T.REGISTERED.load()


def mutations(task):
    """(name, action) pairs: the reference action and edits that each break or bend one rule."""
    base = T.reference_action(task)
    out = [("reference", base)]

    def variant(name, change):
        action = copy.deepcopy(base)
        change(action)
        out.append((name, action))

    variant("extra key", lambda a: a.update(extra=1))
    variant("format", lambda a: a.update(format="other"))
    variant("architecture", lambda a: a.update(architecture="NOPE"))
    variant("form conflict", lambda a: a.update(acu_form="perimeter"))
    variant("axis", lambda a: a["layout"].update(axis="z"))
    variant("duplicate uid", lambda a: a["layout"]["rows"][1]["slots"].__setitem__(0, "m_1"))
    variant("missing uid", lambda a: a["layout"]["rows"][1]["slots"].__setitem__(0, "GAP"))
    variant("leak", lambda a: a["containment"].update(leakage_open_area_fraction=1.0))
    variant("OPEN pairs", lambda a: a["containment"].update(pairs=[["A", "B"]]))
    variant("CAC no pairs", lambda a: a.update(architecture="SLAB_INROW_CAC"))
    variant("supply bound", lambda a: a["setpoints"]["m_14"].update(supply_C=31.0))
    variant("flow bool", lambda a: a["setpoints"]["m_14"].update(flow_fraction=True))
    variant("pump bound", lambda a: a["liquid"].update(pump_N=1.3))
    variant("wall", lambda a: a["layout"]["rows"][0].update(center_m=6.0))
    variant("aisle", lambda a: a["layout"]["rows"][1].update(center_m=3.5))
    variant("airflow", lambda a: [s.update(flow_fraction=.3) for s in a["setpoints"].values()])
    variant("shifted", lambda a: [a["layout"]["rows"][0].update(center_m=5.0), a["liquid"].update(pump_N=.9)])

    def cac(a):
        a["architecture"] = "SLAB_INROW_CAC"
        a["containment"]["pairs"] = [["A", "B"]]
    variant("CAC on hot aisle", cac)

    def hac(a):
        a["architecture"] = "SLAB_INROW_HAC"
        a["containment"]["pairs"] = [["A", "B"]]
        a["layout"]["rows"][0]["front"], a["layout"]["rows"][1]["front"] = "+", "-"
    variant("HAC legal", hac)
    variant("perimeter", lambda a: a.update(architecture="RF_PERIM_OPEN", acu_form="perimeter"))
    return out


def outcome(function, *args):
    try:
        return ["ok", function(*args)]
    except (ValueError, KeyError, TypeError) as exc:
        return [type(exc).__name__, str(exc)]


def test_the_frozen_outcomes_cover_these_actions(task):
    assert FROZEN["task"] == {"path": T.REGISTERED.path, "sha256": T.REGISTERED.sha256}
    assert {name: roundtrip(action) for name, action in mutations(task)} == \
        {name: case["action"] for name, case in FROZEN["cases"].items()}


def test_validate_matches_the_legacy_codec(task):
    accepted = 0
    for name, action in mutations(task):
        mine = roundtrip(outcome(T.validate, action, task))
        assert mine == FROZEN["cases"][name]["validate"], name
        accepted += mine[0] == "ok"
    assert accepted >= 3  # the reference, the shifted design and the legal HAC


def test_numeric_head_and_codec_match_the_legacy_codec(task):
    for name, action in mutations(task):
        assert roundtrip(outcome(T.continuous_fields, action, task)) == FROZEN["cases"][name]["continuous_fields"], name
        assert roundtrip(outcome(T.encode, action, task)) == FROZEN["cases"][name]["encode"], name
    base = T.reference_action(task)
    assert roundtrip(base) == FROZEN["reference_action"]
    frozen = FROZEN["with_continuous_values"]
    assert roundtrip(T.with_continuous_values(base, task, frozen["values"])) == frozen["result"]
    assert T.decode(T.encode(base, task), task) == base
    with pytest.raises(ValueError, match="duplicate JSON action key"):
        T.decode('{"format": 1, "format": 2}', task)


def test_contexts_come_from_the_task(task):
    for context in task["fault_policy"]["contexts"]:
        assert roundtrip(T.external_context(T.REGISTERED, context)) == FROZEN["external_context"][context]
    with pytest.raises(ValueError, match="unregistered"):
        T.external_context(T.REGISTERED, "single-off/m_1")
    with pytest.raises(ValueError, match="TaskSpec bytes changed"):
        T.TaskRef(T.REGISTERED.path, "0" * 64).load()
