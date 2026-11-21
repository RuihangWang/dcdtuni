"""agent.api: the stable surface, its version, its protocols and its layering (CPU contract tests).

Every name in ``agent.api.__all__`` must also be exercised by name in some
other trunk test file (checked below), so a public symbol cannot ship without
its own contract test.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import agent.api as api

TRUNK = Path(__file__).resolve().parents[1]
SRC = TRUNK.parent
TESTS = TRUNK / "tests"
EXPECTED = {
    "AGENT_API_VERSION",
    "Task", "Design", "Scenario", "Codec", "Evaluator", "canonical_identity",
    "Fidelity", "Budget", "BudgetExhausted", "EvaluationRecord", "evaluate",
    "CostLedger", "RunStore", "RunLogger", "new_run_id",
    "Acquisition", "allocation_probabilities", "Space", "optimize",
    "edit_project", "query_project", "project_identity", "validate_project",
    "admit_air", "mesh_air", "solve_air", "evaluate_air", "evaluate_liquid", "liquid_gradient", "evaluate_hybrid",
    "evaluate_dcrans_l0", "evaluate_dcrans_l1", "solve_dcrans", "invert_dcrans", "verify_dcrans_patches",
    "evaluate_many", "read_result", "identity",
    "Edit", "MoveModel", "RotateModel", "RemoveModels", "SetServerLoad", "SetRackPower", "SetAcuSupply", "AddProbe",
    "SetCduSupply", "SetPumpSpeed", "SetValveOpening", "SetPipeDiameter", "SetPipeRoute", "ContractVerbMissing",
}
LEGACY_NAMES = ("agent.harness", "agent.training", "agent.methods", "agent.analysis", "agent.scenario_air",
                "agent.scenario_hybrid", "agent.scenario_airfoil", "agent._runtime")


def test_surface_and_version():
    assert api.AGENT_API_VERSION == "1.0"
    assert set(api.__all__) == EXPECTED and len(api.__all__) == len(EXPECTED)
    for name in api.__all__:
        assert getattr(api, name) is not None
    import agent
    assert agent.AGENT_API_VERSION == api.AGENT_API_VERSION
    assert api.evaluate.__module__ == "agent.tools.fidelity"


def test_every_public_symbol_has_a_contract_test_by_name():
    texts = {path.name: path.read_text(encoding="utf-8") for path in TESTS.glob("test_*.py")
             if path.name != Path(__file__).name}
    missing = [name for name in api.__all__ if name != "AGENT_API_VERSION"
               and not any(re.search(rf"\b{re.escape(name)}\b", text) for text in texts.values())]
    assert missing == []


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ()
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module, tuple(alias.name for alias in node.names)


def trunk_sources():
    return [p for p in TRUNK.rglob("*.py") if "tests" not in p.relative_to(TRUNK).parts
            and "__pycache__" not in p.parts]


PLATFORM_BRIDGE_PKGS = {"scripted_api", "dcdesign", "plant", "dcrans"}


def test_trunk_imports_no_frozen_name_and_only_the_public_platform_api():
    offenders = []
    for path in trunk_sources():
        top_pkg = path.relative_to(TRUNK).parts[0]
        is_bridge = top_pkg in PLATFORM_BRIDGE_PKGS
        for module, names in _imports(path):
            if module.startswith(LEGACY_NAMES) or module in ("agent.tests",):
                offenders.append(f"{path.relative_to(SRC)}: {module}")
            if is_bridge:
                continue    # the platform bridge (scripted API, design modules, dcRANS service) imports backend
            if module == "backend" or module.startswith("backend."):
                offenders.append(f"{path.relative_to(SRC)}: {module}")    # the trunk reaches the platform only
            if module.startswith(("agent.scripted_api", "agent.dcdesign", "agent.plant")):   # through the bridge
                private = [n for n in names if n.startswith("_")]
                if private or any(part.startswith("_") for part in module.split(".")):
                    offenders.append(f"{path.relative_to(SRC)}: private {module} {private}")
    assert offenders == []


def test_importing_the_api_sets_no_environment_and_loads_no_heavy_or_frozen_module():
    code = ("import json,os,sys;before=dict(os.environ);import agent.api;"
            "print(json.dumps({'env':dict(os.environ)==before,'mods':sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('torch','scipy','transformers') or m.startswith(('agent.harness','agent.training',"
            "'agent._runtime','agent.methods','agent.scenario')))}))")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-c", code], cwd=SRC, capture_output=True, text=True, env=env,
                          timeout=120, check=True)
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen == {"env": True, "mods": []}


def test_lazy_torch_names_resolve_on_demand():
    torch = pytest.importorskip("torch")
    q = api.allocation_probabilities([0., 1.], 1., expected_hf_count=1.)
    assert isinstance(q, torch.Tensor) and float(q.sum()) == pytest.approx(1.)
