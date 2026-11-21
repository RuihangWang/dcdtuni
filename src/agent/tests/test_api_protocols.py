"""The protocols of agent.api (Task, Design, Scenario, Codec, Evaluator), the Edit base and canonical_identity."""
from __future__ import annotations

import pytest

import agent.api as api


def test_protocols_are_structural():
    class Task:
        def identity(self):
            return "t"

    class Scenario:
        name, version = "air-v1", "1"

        def identity(self):
            return "s"

        def validate(self, task, design):
            return []

        def compile(self, task, design):
            return [{"verb": "newProject", "args": []}]

    class Codec:
        schema_version = "1"

        def encode(self, discrete, continuous):
            return "{}"

        def decode(self, text):
            return 0, ()

        def domain(self):
            return {}
    assert isinstance(Task(), api.Task) and isinstance(Task(), api.Design)
    assert isinstance(Scenario(), api.Scenario) and not isinstance(Task(), api.Scenario)
    assert isinstance(Codec(), api.Codec) and not isinstance(object(), api.Codec)
    assert isinstance(api.evaluate, api.Evaluator)


def test_canonical_identity_is_order_free_and_refuses_nonfinite():
    assert api.canonical_identity({"a": 1, "b": [1, 2]}) == api.canonical_identity({"b": [1, 2], "a": 1})
    assert len(api.canonical_identity({"a": 1})) == 64
    with pytest.raises(ValueError):
        api.canonical_identity({"a": float("nan")})


def test_every_semantic_edit_is_an_Edit_with_a_plain_dict_form():
    edits = [api.MoveModel("u", 0., 0., 0.), api.RotateModel("u", yaw_deg=10.), api.RemoveModels(["u"]),
             api.SetServerLoad("r", "s", power_w=1.), api.SetRackPower("r", 1.), api.SetAcuSupply("a", temperature_c=1.),
             api.AddProbe("p", 0., 0., 0.), api.SetCduSupply("c", temperature_c=30.), api.SetPumpSpeed("c", 1.),
             api.SetValveOpening("p", "v", .5), api.SetPipeDiameter("p", .02)]
    for edit in edits:
        assert isinstance(edit, api.Edit)
        assert edit.to_dict()["op"] == type(edit).__name__
    assert issubclass(api.ContractVerbMissing, NotImplementedError)
