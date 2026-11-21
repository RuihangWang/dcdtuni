"""Restoring a saved project must never invent its absent working directory."""
import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def server(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "server/server.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("restore_case_server", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.CASE_DIR = tmp_path
    module.responses = []
    monkeypatch.setattr(module, "_send_json", lambda _, code, body: module.responses.append((code, body)))
    return module


def call(server, monkeypatch, path, **options):
    monkeypatch.setattr(server, "_read_body", lambda _: {"caseDir": str(path), **options})
    server.handle_set_case_dir(None)
    return server.responses[-1]


def test_restore_existing_directory_resolves_and_applies(server, monkeypatch, tmp_path):
    working = tmp_path / "已有工程"
    working.mkdir()
    status, body = call(server, monkeypatch, working / ".." / working.name, createIfMissing=False)
    assert status == 200 and body["ok"]
    assert server.CASE_DIR == working
    assert body["caseDir"] == str(working)


def test_restore_missing_directory_does_not_create_or_switch(server, monkeypatch, tmp_path):
    missing = tmp_path / "different-computer" / "case"
    status, body = call(server, monkeypatch, missing, createIfMissing=False)
    assert status == 404 and body["code"] == "case_dir_missing"
    assert not missing.exists()
    assert server.CASE_DIR == tmp_path


@pytest.mark.parametrize("child", [False, True])
def test_restore_file_is_not_a_case_directory(server, monkeypatch, tmp_path, child):
    file = tmp_path / "case.cfdproj"
    file.write_text("{}")
    status, body = call(server, monkeypatch, file / "child" if child else file, createIfMissing=False)
    assert status == 400 and body["code"] == "case_dir_not_directory"
    assert server.CASE_DIR == tmp_path


def test_manual_directory_selection_keeps_create_behavior(server, monkeypatch, tmp_path):
    new = tmp_path / "new-case"
    status, body = call(server, monkeypatch, new)
    assert status == 200 and body["ok"] and new.is_dir()
    assert server.CASE_DIR == new


def test_permission_error_is_distinct_from_missing(server, monkeypatch, tmp_path):
    blocked = tmp_path / "blocked"
    original = Path.stat
    def stat(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("permission denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    status, body = call(server, monkeypatch, blocked, createIfMissing=False)
    assert status == 403 and body["code"] == "case_dir_permission_denied"
    assert server.CASE_DIR == tmp_path


# A project carries its case directory relative to the repository, so a
# teammate's clone at another path opens the same case. The server resolves a
# relative path against the repository root, never its own working directory
# (it chdirs to the web root), and reports the portable form back.

@pytest.fixture
def repo(server, monkeypatch, tmp_path):
    root = tmp_path / "clone"
    (root / "case" / "Tri_angle").mkdir(parents=True)
    monkeypatch.setattr(server, "REPO_ROOT", root)
    elsewhere = tmp_path / "web-root"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return root


def test_relative_case_path_resolves_against_the_repository(server, monkeypatch, repo):
    status, body = call(server, monkeypatch, "case/Tri_angle", createIfMissing=False)
    assert status == 200 and body["ok"]
    assert server.CASE_DIR == repo / "case" / "Tri_angle"
    assert body["caseDir"] == str(repo / "case" / "Tri_angle")
    assert body["portablePath"] == "case/Tri_angle"


def test_absolute_case_path_inside_the_repository_reports_its_relative_form(server, monkeypatch, repo):
    status, body = call(server, monkeypatch, repo / "case" / "Tri_angle", createIfMissing=False)
    assert status == 200 and body["portablePath"] == "case/Tri_angle"


def test_case_path_outside_the_repository_stays_absolute(server, monkeypatch, repo, tmp_path):
    outside = tmp_path / "scratch-case"
    outside.mkdir()
    status, body = call(server, monkeypatch, outside, createIfMissing=False)
    assert status == 200 and body["portablePath"] == str(outside)


def test_missing_relative_case_path_is_reported_and_never_created(server, monkeypatch, repo):
    status, body = call(server, monkeypatch, "case/FromAnotherComputer", createIfMissing=False)
    assert status == 404 and body["code"] == "case_dir_missing"
    assert not (repo / "case" / "FromAnotherComputer").exists()
    assert not (Path.cwd() / "case").exists()


def test_request_case_dir_accepts_a_repository_relative_path(server, repo):
    assert server._request_case_dir(None, {"caseDir": "case/Tri_angle"}) == repo / "case" / "Tri_angle"
    with pytest.raises((OSError, ValueError)):  # callers report both as case_dir_invalid
        server._request_case_dir(None, {"caseDir": "case/Missing"})


def test_server_info_reports_the_repository_root(server, monkeypatch, repo):
    server.handle_server_info(None)
    assert server.responses[-1][1]["repoRoot"] == str(repo)
