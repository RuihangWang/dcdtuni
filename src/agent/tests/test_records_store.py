"""RunStore: one injected root, no traversal, no symlinks, no overwrite, validated candidates.

Ported from the frozen ``tests/test_artifacts.py`` (CaseArtifacts); the store
root is a parameter instead of the repository, and the project validator is
injected (the default goes through ``agent.tools.project.validate_project``,
i.e. ``agent.scripted_api``), so these tests run on CPU without Node.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.records import store as records
from agent.records import RunStore

REPO = Path(__file__).resolve().parents[3]
SINGLE_RACK = REPO / "case/SingleRack/SingleRack.cfdproj"
RUN = "2026-10-22_120001_SGT__contract-test"


def accept(project):
    if not isinstance(project, dict) or not project.get("constructions"):
        raise ValueError("invalid .cfdproj: stub validator wants constructions")
    return {"validator": "stub", "ok": True, "errors": []}


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "agent-root"
    path.mkdir()
    return path


@pytest.fixture
def store(root):
    return RunStore(root, validator=accept)


@pytest.fixture
def case(root):
    return RunStore.create_case("save-contract", SINGLE_RACK, base=root, validator=accept)


def save(case, run_id=RUN, agent_id="agent-1"):
    return case.save_candidate(run_id, agent_id, case.read_project("project.cfdproj"),
                               structured_output={"operation": "identity export"},
                               raw_output="contract smoke: no model called",
                               identity={"purpose": "artifact-only", "model": "not used"})


def test_writes_are_exclusive_and_inside_the_root(store, root):
    path = store.write_json("a/b.json", {"x": 1}) if store.make_directory("a") else None
    assert path == root / "a/b.json"
    assert json.loads(path.read_text()) == {"x": 1}
    with pytest.raises(FileExistsError):
        store.write_json("a/b.json", {"x": 2})
    assert json.loads(path.read_text()) == {"x": 1}
    store.write_bytes("a/log.txt", b"")
    store.append_bytes("a/log.txt", b"one\n")
    store.append_bytes("a/log.txt", b"two\n")
    assert store.read_bytes("a/log.txt") == b"one\ntwo\n"
    assert store.read_json("a/b.json") == {"x": 1}
    assert RunStore.sha256(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_relative_rejects_traversal_absolute_escape_and_empty(store, root):
    with pytest.raises(ValueError, match="traversal"):
        store.relative("x/../y.json")
    with pytest.raises(ValueError, match="inside"):
        store.write_bytes("/etc/forbidden-contract.json", b"never")
    with pytest.raises(ValueError):
        store.relative("")
    assert store.relative(root / "a" / "b") == Path("a/b")


def test_symlinks_are_refused_even_inside_the_root(store, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        store.write_bytes("escape/x.txt", b"never")
    assert not (outside / "x.txt").exists()
    store.write_bytes("real.txt", b"data")
    (root / "linked.txt").symlink_to(root / "real.txt")
    with pytest.raises(ValueError, match="symlink"):
        store.read_bytes("linked.txt")
    with pytest.raises(ValueError, match="symlink"):
        store.append_bytes("linked.txt", b"must not append")


def test_nonfinite_json_is_refused_before_any_file(store, root):
    with pytest.raises(ValueError):
        store.write_json("bad.json", {"x": float("nan")})
    assert not (root / "bad.json").exists()


def test_root_must_exist_unless_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        RunStore(tmp_path / "missing")
    made = RunStore(tmp_path / "made" / "deep", create=True)
    assert made.root.is_dir()


def test_default_case_root_is_case_agent_under_the_repository():
    assert records.default_case_root("x-case") == REPO / "case" / "agent" / "x-case"
    with pytest.raises(ValueError):
        records.default_case_root("../escape")


@pytest.mark.parametrize("unsafe", ["../escape", "/abs/escape", "..", "a/b", ""])
def test_unsafe_ids_rejected(root, unsafe):
    with pytest.raises(ValueError):
        RunStore.create_case(unsafe, SINGLE_RACK, base=root, validator=accept)


def test_case_reference_and_candidate_roundtrip(case):
    original = SINGLE_RACK.read_bytes()
    assert case.read_bytes("reference/original.cfdproj") == original
    identity = case.read_json("reference/source_identity.json")
    assert identity["sha256"] == RunStore.sha256(original)
    adopted = case.read_project("project.cfdproj")
    assert adopted["constructions"] == json.loads(original)["constructions"]
    assert adopted["meta"]["exportFileName"] == "project"
    output = save(case)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["format"] == "agent-project-artifacts/v1"
    assert manifest["project"]["sha256"] == RunStore.sha256((output / "candidate.cfdproj").read_bytes())
    assert manifest["project"]["validator"]["ok"] is True
    candidate = json.loads((output / "candidate.cfdproj").read_text())
    assert candidate["constructions"] == adopted["constructions"]
    final = case.select_candidate(RUN, "agent-1", reason="artifact saving acceptance only")
    assert final == case.root / "final_candidate.cfdproj"
    selection = case.read_json("final_candidate.manifest.json")
    assert selection["source_candidate_sha256"] == manifest["project"]["sha256"]
    assert SINGLE_RACK.read_bytes() == original


def test_invalid_project_or_identity_writes_nothing(case):
    with pytest.raises(ValueError, match="invalid .cfdproj"):
        case.save_candidate(RUN, "invalid", {}, structured_output={}, raw_output="",
                            identity={"purpose": "test"})
    with pytest.raises(ValueError):
        case.save_candidate(RUN, "invalid", case.read_project("project.cfdproj"),
                            structured_output={}, raw_output="", identity={"score": float("nan")})
    with pytest.raises(ValueError):
        case.save_candidate(RUN, "invalid", case.read_project("project.cfdproj"),
                            structured_output={}, raw_output="", identity={})
    assert not (case.root / "agent_outputs").exists()


def test_exclusive_case_candidate_and_final(case, root):
    with pytest.raises(FileExistsError):
        RunStore.create_case(case.root.name, SINGLE_RACK, base=root, validator=accept)
    output = save(case)
    before = (output / "candidate.cfdproj").read_bytes()
    with pytest.raises(FileExistsError):
        save(case)
    assert (output / "candidate.cfdproj").read_bytes() == before
    second = save(case, agent_id="agent-2")
    assert second != output and second.is_dir()
    case.select_candidate(RUN, "agent-1", reason="contract only")
    with pytest.raises(FileExistsError):
        case.select_candidate(RUN, "agent-1", reason="must not overwrite")


def test_tampered_candidate_cannot_be_selected(case):
    output = save(case)
    (output / "candidate.cfdproj").write_text("{}")
    with pytest.raises(ValueError, match="manifest"):
        case.select_candidate(RUN, "agent-1", reason="should fail")
    assert not (case.root / "final_candidate.cfdproj").exists()


def test_checked_run_id_and_sgt_timestamp():
    assert records.checked_run_id(RUN) == RUN
    for bad in ("2026-10-22_120001__x", "2026-13-40_250000_SGT__x", "x"):
        with pytest.raises(ValueError):
            records.checked_run_id(bad)
    assert records.timestamp_sgt().endswith("+08:00")
