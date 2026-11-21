"""Exercise the shared browser loader and its React/legacy completion gates."""

import json
import subprocess
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from server import _BROWSER_READY_PROBE, _browser_load_project_script


_NODE_RUNNER = """
const vm = require('node:vm');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {window: {calls: []}, state: {schemaVersion: 4, uid: 'old'}, renderMeshes: [1]};
(async () => {
  vm.runInNewContext(input.setup, context);
  const ready = vm.runInNewContext(input.readyProbe, context);
  const results = [];
  for (let i = 0; i < input.scripts.length; i++) {
    if (i && input.between) vm.runInNewContext(input.between, context);
    results.push(await vm.runInNewContext(input.scripts[i], context));
  }
  process.stdout.write(JSON.stringify({results, ready, calls: context.window.calls,
    workspace: context.window.workspace, sceneUid: context.window.sceneUid}));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def _run(setup, projects=(), between=""):
    scripts = [_browser_load_project_script(json.dumps(p)) for p in projects]
    payload = {"setup": setup, "scripts": scripts, "between": between,
               "readyProbe": _BROWSER_READY_PROBE}
    run = subprocess.run(
        ["node", "-e", _NODE_RUNNER], input=json.dumps(payload), text=True,
        capture_output=True, check=True,
    )
    return json.loads(run.stdout)


def _project(uid):
    return {"schemaVersion": 4, "uid": uid, "constructions": {},
            "models": [{"uid": f"{uid}-model"}]}


_REACT_SETUP = """
    window.project = {uid: 'old', schemaVersion: 4, models: [{uid: 'old-model'}]};
    window.rev = 0;
    window.workspace = {selection: ['old-model'], layers: ['old-result']};
    window.__dcdtuni = {
        getProject() { return window.project; },
        allModels() { return window.project.models; },
        revision() { return window.rev; },
        async buildGeometryData() {
            window.calls.push(['build', window.project.uid]);
            window.sceneUid = window.project.uid;
            return window.project.models.length * 2;
        },
        geometryStatus() { return {renderMeshesCount: window.project.models.length * 2}; },
    };
    window._applyProjectData = (raw, source, options) => {
        window.calls.push(['app-load', raw.uid, source, options.clearRemotePath]);
        window.workspace = {selection: [], layers: []};
        window.project = raw;
        window.rev++;
        return true;
    };
"""


def test_ready_probe_waits_for_app_loader_effect():
    early = _run("""
        window.__dcdtuni = {getProject() {}, allModels() { return []; }};
    """)
    assert early["ready"] is False
    mounted = _run(_REACT_SETUP)
    assert mounted["ready"] is True
    legacy = _run("""
        window._applyProjectData = () => true;
        window.allModels = () => [];
    """)
    assert legacy["ready"] is True


def test_react_uses_app_loader_and_builds_new_scene():
    result = _run(_REACT_SETUP, [_project("A")])
    loaded = result["results"][0]
    assert loaded["success"] is True
    assert loaded["sceneReady"] is True
    assert loaded["modelCount"] == 1
    assert loaded["renderMeshes"] == 2
    assert result["calls"] == [["app-load", "A", "headless-load", True], ["build", "A"]]
    assert result["workspace"] == {"selection": [], "layers": []}


def test_react_a_to_b_discards_old_workspace_and_rebuilds_b():
    result = _run(_REACT_SETUP, [_project("A"), _project("B")],
                  "window.workspace = {selection: ['A-model'], layers: ['A-result']};")
    assert [r["success"] for r in result["results"]] == [True, True]
    assert result["calls"] == [
        ["app-load", "A", "headless-load", True], ["build", "A"],
        ["app-load", "B", "headless-load", True], ["build", "B"],
    ]
    assert result["workspace"] == {"selection": [], "layers": []}
    assert result["sceneUid"] == "B"


def test_react_same_uid_noop_and_rejected_load_cannot_pass():
    noop = _run(_REACT_SETUP + """
        window.project = {uid: 'A', schemaVersion: 4, models: [{uid: 'old-model'}]};
        window._applyProjectData = () => true;
    """, [_project("A")])
    assert noop["results"][0]["success"] is False
    assert [c for c in noop["calls"] if c[0] == "build"] == []

    refused = _run(_REACT_SETUP + "window._applyProjectData = () => false;", [_project("B")])
    assert refused["results"][0]["success"] is False


def test_react_loader_migrates_to_v5_and_nothing_newer_passes():
    # The React loader migrates every opened file to cfdproj v5 in memory.
    stamp = """
        const load = window._applyProjectData;
        window._applyProjectData = (raw, source, options) =>
            load(Object.assign({}, raw, {schemaVersion: window.stamp}), source, options);
    """
    migrated = _run(_REACT_SETUP + stamp + "window.stamp = 5;", [_project("A")])
    assert migrated["results"][0]["success"] is True
    assert migrated["results"][0]["schemaVersion"] == 5

    future = _run(_REACT_SETUP + stamp + "window.stamp = 6;", [_project("B")])
    assert future["results"][0]["success"] is False


def test_legacy_requires_explicit_success_even_after_state_assignment():
    setup = """
        window.allModels = () => state.models || [];
        window._applyProjectData = raw => {
            window.calls.push(['legacy-load', raw.uid]);
            state = raw;
            renderMeshes = [1, 2];
            return window.verdict;
        };
    """
    passed = _run(setup + "window.verdict = true;", [_project("A")])
    assert passed["results"][0]["success"] is True
    assert passed["calls"] == [["legacy-load", "A"]]

    partial_failure = _run(setup + "window.verdict = false;", [_project("A")])
    assert partial_failure["results"][0]["success"] is False
    undefined_failure = _run(setup + "window.verdict = undefined;", [_project("A")])
    assert undefined_failure["results"][0]["success"] is False


def test_software_browser_requires_explicit_opt_in(monkeypatch):
    import server

    monkeypatch.delenv("DCDTUNI_BROWSER_SOFTWARE", raising=False)
    assert server._browser_launch_args() is server._BROWSER_GPU_ARGS
    monkeypatch.setenv("DCDTUNI_BROWSER_SOFTWARE", "1")
    assert server._browser_launch_args() is server._BROWSER_SOFTWARE_ARGS
    assert "--use-angle=swiftshader" in server._browser_launch_args()
