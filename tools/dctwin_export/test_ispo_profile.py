"""The ispo profile ships no server; the dctwin profile and this repository's own scripted API are unchanged.

    python -m pytest tools/dctwin_export/test_ispo_profile.py
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

import ported_server

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# the exporter before the server-less ispo profile: what the dctwin profile is compared with
BASELINE = "84e58f62bd25e0209a8e0ab3e9cf9e7bdc5b9b21"
DCTWIN_TEMPLATES = {"README.md": "README.md", "setup_env.sh": "setup_env.sh", "pyproject.toml": "pyproject.toml",
                    "gitignore": ".gitignore", "start_server.sh": "src/start_server.sh"}


def exporter(profile=None, path=HERE / "export.py"):
    """A fresh copy of the exporter module (``configure`` changes its lists in place)."""
    spec = importlib.util.spec_from_file_location(f"export_{profile or 'default'}_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if profile:
        module.configure(profile)
    return module


def test_ispo_profile_ships_the_session_and_front_door_but_no_server():
    ex = exporter("ispo")
    files = ex.tracked_files(REPO)
    placed = set(files) | set(ex.TEMPLATE_TARGETS.values())
    assert {"src/backend/api/direct_session.py", "src/dceval/__init__.py", "src/dceval/__main__.py",
            "src/backend/tests/test_direct_session.py", "src/backend/api/air.py", "README.md",
            "pyproject.toml", "setup_env.sh", ".gitignore"} <= placed
    assert not [name for name in placed if name.startswith("src/server/") or name.endswith("start_server.sh")
                or name.endswith("server_session.py")]
    assert any(name.startswith(ex.GPU_SOLVER_DIR) for name in files)
    assert "src/backend/aircooling/runner/gpu_newton.py" in files
    assert "src/backend/aircooling/runner/run_cfd.py" in files and "src/backend/liquidcooling/run_manager.py" in files
    # every template exists, none lands on a file the export copies or on a path the destination owns
    for name, target in ex.TEMPLATE_TARGETS.items():
        assert (ex.TEMPLATES / name).is_file(), name
        assert target not in files and not ex.dctwin_own(target)
    # a file left over from the server would be caught by the leak scan
    paths = [__import__("re").compile(p) for p in ex.FORBIDDEN_PATHS]
    for name in ("src/server/server.py", "src/start_server.sh", "src/backend/api/server_session.py"):
        assert any(p.search(name) for p in paths), name
    for name in placed:
        assert not any(p.search(name) for p in paths), name


def test_ispo_templates_need_no_server_dependency_and_pass_the_leak_scan(tmp_path):
    ex = exporter("ispo")
    pyproject = (ex.TEMPLATES / "pyproject.toml").read_text()
    assert "websockets" not in pyproject and '{include = "dceval", from = "src"}' in pyproject
    for name, target in ex.TEMPLATE_TARGETS.items():
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ex.rename_tool(ex.anonymise((ex.TEMPLATES / name).read_text())))
    assert ex.leak_scan(tmp_path, sorted(ex.TEMPLATE_TARGETS.values())) == []
    readme = (tmp_path / "README.md").read_text()
    assert "start_server" not in readme and "from dceval import evaluate" in readme
    assert "python -m dceval evaluate" in readme


def test_ispo_export_points_the_air_run_at_the_in_process_session():
    ex = exporter("ispo")
    source = ex.exported_modules((REPO / "src/agent/scripted_api/air.py").read_text())
    shipped = ex.transform_air_session(source)
    assert 'SESSION_MODULE = "backend.api.direct_session"\n' in shipped
    assert 'SESSION_MODULE = "backend.api.server_session"' not in shipped
    assert len(shipped) == len(source) and shipped.count("\n") == source.count("\n")
    # the module the shipped air.py names is the template, and it has the class air.py looks up
    template = (ex.TEMPLATES / "direct_session.py").read_text()
    assert "\nclass DirectSession:" in template and "\nclass ServerError(RuntimeError):" in template
    # nothing shipped imports the server's client or the server's modules
    for name in ex.tracked_files(REPO):
        if name.endswith(".py"):
            text = ex.exported_modules((REPO / ex.checkout_name(name)).read_text())
            if name == "src/backend/api/air.py":
                text = shipped
            assert "import server_session" not in text and "backend.api.server_session import" not in text, name
            assert "import liquid_runtime_api" not in text and "from server" not in text, name


def test_this_repository_keeps_the_server_path():
    """The direct module is not importable from this repository's own ``src``; the air run names the server's client."""
    assert not list((REPO / "src").rglob("direct_session.py")) and not (REPO / "src" / "dceval").exists()
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "src"], capture_output=True, text=True,
                             check=True).stdout
    assert "direct_session" not in tracked and "dceval" not in tracked
    probe = ("import importlib.util, agent.scripted_api.air as air\n"
             "from agent.scripted_api import server_session\n"
             "assert importlib.util.find_spec('agent.scripted_api.direct_session') is None\n"
             "assert air.SESSION_MODULE == 'agent.scripted_api.server_session'\n"
             "assert air._session_api() == (server_session.ServerSession, server_session.ServerError)\n")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr


def test_dctwin_profile_is_unchanged(tmp_path):
    now = exporter()
    files = now.tracked_files(REPO)
    assert now.TEMPLATE_TARGETS == DCTWIN_TEMPLATES and now.TEMPLATES == HERE / "templates"
    assert {"src/server/server.py", "src/server/liquid_runtime_api.py", "src/backend/api/server_session.py",
            "src/backend/api/air.py"} <= set(files)
    assert not [name for name in files if "direct_session" in name or "dceval" in name or "gpu_newton" in name]
    old_source = subprocess.run(["git", "-C", str(REPO), "show", f"{BASELINE}:tools/dctwin_export/export.py"],
                                capture_output=True, text=True)
    if old_source.returncode:
        pytest.skip(f"baseline revision {BASELINE[:9]} is not in this clone")
    (tmp_path / "baseline").mkdir()
    (tmp_path / "baseline" / "export.py").write_text(old_source.stdout)
    old = exporter(path=tmp_path / "baseline" / "export.py")
    # the modules moved to src/agent (2026-11-16) keep their former paths in the export; the baseline exporter,
    # reading this checkout, no longer finds them there
    moved = tuple(old_p for old_p, _ in now.MOVED)
    assert [name for name in files if not name.startswith(moved)] == old.tracked_files(REPO)
    assert all(any(name.startswith(prefix) for name in files) for prefix in moved)
    for name in ("INCLUDE", "EXCLUDE", "TESTS", "PROJECTS", "DCTWIN_OWN"):
        assert getattr(now, name) == getattr(old, name), name
    # the leak scan may only grow stricter (2026-11-16: the SCANO folder's new path, the dcRANS / JFGNK names)
    for name in ("FORBIDDEN_PATHS", "FORBIDDEN_TOKENS"):
        assert set(getattr(old, name)) <= set(getattr(now, name)), name
    # the templates the old exporter copied, by the names it hard-coded
    assert 'for name in ("README.md", "setup_env.sh", "pyproject.toml", "gitignore", "start_server.sh"):' \
        in old_source.stdout
    for name in DCTWIN_TEMPLATES:
        assert (now.TEMPLATES / name).is_file()


def test_ported_server_functions_have_not_changed():
    """direct_session.py repeats these functions. One of them changed: read the change, bring the port in
    templates_ispo/direct_session.py up to date (or decide it does not apply), then record the new hashes with
    ``python tools/dctwin_export/ported_server.py --record``."""
    changed = ported_server.changed()
    assert not changed, ("server functions ported into templates_ispo/direct_session.py changed: "
                         f"{changed}; re-port them, then run tools/dctwin_export/ported_server.py --record")


def test_drift_guard_sees_a_changed_function(tmp_path):
    for name in list(ported_server.PORTED) + list(ported_server.SHELL_FUNCTIONS):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO / name).read_text())
    assert ported_server.changed(tmp_path) == []
    server = tmp_path / "src/server/server.py"
    server.write_text(server.read_text().replace('artifacts["caseDir"] = str(case_dir)',
                                                 'artifacts["caseDir"] = str(case_dir)  # edited', 1))
    launcher = tmp_path / "src/start_server.sh"
    launcher.write_text(launcher.read_text().replace("export FOAM_MPI=sys-mpich", "export FOAM_MPI=sys-openmpi", 1))
    assert ported_server.changed(tmp_path) == ["src/server/server.py:_prepare_cfdrun_payload",
                                               "src/start_server.sh:sanitize_foam_env"]


def test_every_ported_function_has_its_port():
    template = (HERE / "templates_ispo" / "direct_session.py").read_text()
    renamed = {"handle_run_mesh": "run_mesh", "handle_abort_job": "abort_job", "handle_job_status": "job_status",
               "handle_log_stream": "job_log", "_read_cfdrun_body_key": "run_mesh", "_request_case_dir": "run_mesh",
               "_error": "_liquid_error", "handle_get": "liquid_request", "handle_post": "liquid_request"}
    for functions in ported_server.PORTED.values():
        for function in functions:
            assert f"def {renamed.get(function, function)}(" in template, function


def test_ispo_profile_exports_the_design_modules_from_this_repository():
    """Owner 2026-11-12: the design language, plant and zonal network are developed here and reach ISPO through
    this profile only; ISPO keeps only its renderer."""
    ex = exporter("ispo")
    files = set(ex.tracked_files(REPO))
    for name in ("src/backend/dcdesign/spec.py", "src/backend/dcdesign/compiler.py", "src/backend/dcdesign/hf.py",
                 "src/backend/plant/energy.py", "src/backend/aircooling/zonal.py",
                 "src/backend/tests/test_dcdesign.py", "src/backend/tests/test_dcdesign_hf.py"):
        assert name in files and not ex.dctwin_own(name), name
    assert ex.dctwin_own("src/backend/render/scene.py") and ex.dctwin_own("OWNED.md")


def test_dceval_check_reports_a_hand_edit_and_a_missing_file(tmp_path):
    import hashlib
    import json

    spec = importlib.util.spec_from_file_location("dceval_check", HERE / "templates_ispo" / "dceval" / "__init__.py")
    source = (HERE / "templates_ispo" / "dceval" / "__init__.py").read_text()
    check_src = source[source.index("def check("):]
    namespace = {}
    exec("from pathlib import Path\nfrom typing import Any, Dict, Union\nFOLDER = None\n" + check_src, namespace)
    check = namespace["check"]
    files = {"a.py": b"x = 1\n", "sub/b.py": b"y = 2\n"}
    for name, data in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    (tmp_path / "EXPORT.json").write_text(json.dumps({"source_commit": "c0ffee", "sha256": {
        name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}))
    assert check(tmp_path)["ok"] is True
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "sub/b.py").unlink()
    report = check(tmp_path)
    assert report == {"ok": False, "source_commit": "c0ffee", "gpu_solver": None, "files": 2,
                      "changed": ["a.py"], "missing": ["sub/b.py"]}


def test_ispo_foam_scripts_use_the_folder_environment():
    """setup_env.sh creates "dceval"; the OpenFOAM build scripts of the ispo export look for the same name."""
    ex = exporter("ispo")
    setup = (ex.TEMPLATES / "setup_env.sh").read_text()
    assert 'ENV_NAME="${DCEVAL_ENV:-dceval}"' in setup and "DCTWIN" not in setup
    source = (HERE / "export.py").read_text()
    assert 'text.replace("DCTWIN_ENV", "DCEVAL_ENV").replace("dctwin", "dceval")' in source


# the solver's imports that are optional (guarded or not vendored) or come with another package
SOLVER_OPTIONAL = {"NNmodels", "Parallel", "pyamgx", "tomllib", "triton", "typing_extensions", "vtk"}
PIP_NAMES = {"torch_geometric": "torch-geometric", "warp": "warp-lang"}


def test_ispo_setup_installs_every_package_the_gpu_solver_imports():
    """A clean environment from setup_env.sh must run the vendored solver: every third-party module it imports
    is installed there (2026-11-12: tomlkit was missing and the first solve in a fresh environment failed)."""
    import ast

    ex = exporter("ispo")
    solver = REPO / ex.GPU_SOLVER_DIR
    local = {p.stem for p in solver.rglob("*.py")} | {p.name for p in solver.iterdir()}
    imported = set()
    for path in solver.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    third_party = {m for m in imported if m not in local and m not in sys.stdlib_module_names} - SOLVER_OPTIONAL
    installs = (ex.TEMPLATES / "setup_env.sh").read_text() + (ex.TEMPLATES / "pyproject.toml").read_text()
    missing = sorted(m for m in third_party if f'"{PIP_NAMES.get(m, m)}' not in installs
                     and f"\n{PIP_NAMES.get(m, m)} =" not in installs)
    assert not missing, f"imported by the GPU solver but not installed by setup_env.sh / pyproject.toml: {missing}"
