"""A new project's steady solve runs long enough for the return-air verdict (2026-10-22).

The verdict needs at least ``convergence.minimum_run_iterations()`` (1000)
iterations: its last window is 20 % of the run and must span 200 (62eaacc23).
The editors' new-project default was 500, so a run started without
``--end-time`` (or from a fresh project in either editor) could only ever be
INCONCLUSIVE. The default is now 1500: long enough to judge, and a monitored
steady solve still stops by itself once its return air is steady.

One default for both editors: ``DEFAULT_SIM_CONFIG`` in the shared
``cfdproj_state_v4.js`` (React reads it through ``getInitialStateV4``); the
legacy editor's own ``getInitialState`` and its iteration box carry the same
number. A saved project without an ``endTime`` runs that default too
(``derived_config.derive_sim_config``, the backend mirror of the editors'
export), so the panel and the run agree.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from backend.aircooling.runner import convergence
from backend.aircooling.runner.derived_config import derive_sim_config

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "src" / "frontend" / "shared" / "js" / "cfdproj_state_v4.js"
LEGACY = REPO / "src" / "frontend" / "web" / "STL_generate.html"
NEW_PROJECT_END_TIME = 1500
# the example cases the docs run without --end-time
EXAMPLES = [REPO / "case" / "SingleRack" / "SingleRack.cfdproj",
            REPO / "case" / "LiquidSingle_Control" / "LiquidSingle_Control.cfdproj"]


def _node():
    from agent.scripted_api.headless import find_node
    try:
        return find_node()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


def _shared_defaults():
    script = ("const S = require(process.argv[1]);"
              "const sc = (doc) => doc.runDefaults.simulationConfig;"
              "process.stdout.write(JSON.stringify({initial: sc(S.getInitialStateV4()),"
              " created: sc(S.newDocumentV4())}));")
    out = subprocess.run([_node(), "-e", script, str(SHARED)], capture_output=True, text=True, timeout=120,
                         check=True)
    return json.loads(out.stdout)


def test_the_default_is_long_enough_for_the_return_air_verdict():
    assert NEW_PROJECT_END_TIME >= convergence.minimum_run_iterations()


def test_both_ways_to_a_new_shared_document_carry_the_default():
    defaults = _shared_defaults()
    assert defaults["initial"]["solverType"] == "steady"
    assert defaults["initial"]["endTime"] == NEW_PROJECT_END_TIME
    assert defaults["created"]["endTime"] == NEW_PROJECT_END_TIME


def test_the_legacy_editor_starts_on_the_same_default():
    html = LEGACY.read_text(encoding="utf-8")
    body = html[html.index("function getInitialState()"):]
    simulation = body[body.index("simulation: {"):]
    assert int(re.search(r"\bendTime:\s*(\d+)", simulation).group(1)) == NEW_PROJECT_END_TIME
    box = re.search(r'<input[^>]*id="slv-endtime"[^>]*>', html).group(0)
    assert int(re.search(r'value="(\d+)"', box).group(1)) == NEW_PROJECT_END_TIME


def test_a_saved_project_without_an_end_time_runs_the_default():
    derived = derive_sim_config({"solverType": "steady"}, room_temperature=22, monitoring={})
    assert derived["endTime"] == NEW_PROJECT_END_TIME
    # an explicit value is kept, however short
    assert derive_sim_config({"endTime": 20}, room_temperature=22, monitoring={})["endTime"] == 20


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_the_example_cases_solve_long_enough_to_judge(path):
    if not path.is_file():
        pytest.skip(f"{path} not checked out")
    sc = json.loads(path.read_text(encoding="utf-8"))["runDefaults"]["simulationConfig"]
    assert sc["solverType"] == "steady"
    assert sc["endTime"] == NEW_PROJECT_END_TIME
