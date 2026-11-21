"""The normal runner keeps one owned input through locks, BC and quality gates."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from backend.aircooling.runner import cfdrun_reader, stlgen_runner
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
from backend.aircooling.runner.stlgen_runner import STLGenRunner, _case_locked

FIXTURE = Path(__file__).parent / "fixtures/cfdrun_v2_full/Base_leakage.cfdrun"


def source_case(tmp_path, raw=None, artifact=True):
    case = tmp_path / "case"
    logical = case / "model/input.cfdrun"
    logical.parent.mkdir(parents=True)
    payload = FIXTURE.read_bytes() if raw is None else json.dumps(raw).encode()
    if artifact:
        logical.write_bytes(payload)
    return case, logical, payload


def ram_runner(tmp_path, raw=None, *, bind_batch=False):
    case, logical, payload = source_case(tmp_path, raw, artifact=False)
    batch = None
    if bind_batch:
        from backend.aircooling.runner.case_execution_lock import case_execution_lock
        from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
        with case_execution_lock(case):
            batch = surface_batch_sha256(case)
    snapshot = CfdrunSnapshot(payload, case, logical, stl_batch_sha256=batch)
    return STLGenRunner(case, input_snapshot=snapshot), snapshot


def prohibit_source_artifact(monkeypatch, source):
    for name in ("read_bytes", "read_text", "exists", "is_file"):
        original = getattr(Path, name)
        def guarded(path, *args, _name=name, _original=original, **kwargs):
            if path == source:
                pytest.fail("RAM source consulted disk artifact through " + _name)
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(Path, name, guarded)
    original_glob = Path.glob
    def glob(path, pattern):
        if path == source.parent:
            pytest.fail("RAM source discovered a disk artifact")
        return original_glob(path, pattern)
    monkeypatch.setattr(Path, "glob", glob)


def test_original_three_positional_file_entry_is_unchanged(tmp_path):
    case, logical, payload = source_case(tmp_path)
    digest = hashlib.sha256(payload).hexdigest()
    runner = STLGenRunner(case, logical, digest)
    runner._load_cfd_json_only()
    runner._prepare_runtime_metadata()
    assert runner._read_cfdrun_bytes() == payload
    assert len(list(runner._bridge_registry.items())) == 52


def test_explicit_snapshot_skips_discovery_and_all_source_artifact_reads(tmp_path, monkeypatch):
    control_case, control_path, _ = source_case(tmp_path / "file-control")
    file_control = STLGenRunner(control_case, control_path)
    file_control._load_cfd_json_only()
    case, logical, payload = source_case(tmp_path, artifact=False)
    snapshot = CfdrunSnapshot(payload, case, logical)
    prohibit_source_artifact(monkeypatch, logical)
    runner = STLGenRunner(case, input_snapshot=snapshot)
    runner._load_cfd_json_only()
    runner._prepare_runtime_metadata()
    control = cfdrun_reader.resolve_run(case, input_snapshot=snapshot, resolve_stl=False)
    # The transitional mesh blob and resolved configuration have distinct
    # existing defaults; compare each to its same-entry file-mode baseline.
    assert runner._data == file_control._data
    assert runner._resolved.mesh_config == control.mesh_config
    assert {key: spec.model_dump(mode="json") for key, spec in runner._bridge_registry.items()} == {
        key: spec.model_dump(mode="json") for key, spec in control.bridge_registry.items()}
    assert runner._read_cfdrun_bytes() == payload


@pytest.mark.parametrize("mode", ["run_mesh_only", "run_solve_only", "run_simulation"])
def test_snapshot_is_pinned_inside_full_case_lock_and_released_on_cancel(tmp_path, monkeypatch, mode):
    from backend.aircooling.runner.case_execution_lock import case_execution_lock, CaseExecutionBusyError
    runner, snapshot = ram_runner(tmp_path, bind_batch=True)
    prohibit_source_artifact(monkeypatch, snapshot.logical_path)
    class Cancelled(Exception):
        pass
    def preflight():
        assert runner._execution_cfdrun_sha256 == snapshot.sha256
        with pytest.raises(CaseExecutionBusyError):
            with case_execution_lock(runner.case_dir, blocking=False):
                pass
        raise Cancelled
    monkeypatch.setattr(runner, "_preflight_case_path", preflight)
    with pytest.raises(Cancelled):
        getattr(runner, mode)()
    assert runner._execution_cfdrun_sha256 is None
    with case_execution_lock(runner.case_dir, blocking=False):
        pass


def test_snapshot_expected_sha_refuses_before_pipeline(tmp_path, monkeypatch):
    case, logical, payload = source_case(tmp_path, artifact=False)
    snapshot = CfdrunSnapshot(payload, case, logical)
    runner = STLGenRunner(case, logical, "0" * 64, input_snapshot=snapshot)
    monkeypatch.setattr(runner, "_preflight_case_path", lambda: pytest.fail("stale input executed"))
    with pytest.raises((RuntimeError, ValueError), match="SHA|hash"):
        runner.run_mesh_only()


def test_snapshot_foreign_case_path_and_wrong_type_refuse(tmp_path):
    case, logical, payload = source_case(tmp_path, artifact=False)
    snapshot = CfdrunSnapshot(payload, case, logical)
    with pytest.raises(ValueError, match="case"):
        STLGenRunner(tmp_path / "other", input_snapshot=snapshot)
    with pytest.raises(ValueError, match="artifact"):
        STLGenRunner(case, case / "model/other.cfdrun", input_snapshot=snapshot)
    with pytest.raises(TypeError, match="Snapshot"):
        STLGenRunner(case, input_snapshot={"payload": payload})


def test_file_mode_mutation_during_lock_still_rejects(tmp_path):
    case, logical, payload = source_case(tmp_path)
    runner = STLGenRunner(case, logical, hashlib.sha256(payload).hexdigest())
    @_case_locked
    def mutate(self):
        logical.write_bytes(payload + b" ")
        self._read_cfdrun_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        mutate(runner)
    assert runner._execution_cfdrun_sha256 is None


def test_changed_replay_artifact_cannot_change_owned_source(tmp_path):
    runner, snapshot = ram_runner(tmp_path)
    snapshot.logical_path.write_bytes(b"not authoritative")
    runner._load_cfd_json_only()
    assert runner._read_cfdrun_bytes() == snapshot.payload
    assert runner._data["room"] is not None


def test_snapshot_preflight_uses_real_original_stl_validator(tmp_path):
    raw = json.loads(FIXTURE.read_bytes())
    stl = (b"solid sheet\nfacet normal 0 0 1\nouter loop\n"
           b"vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
           b"endloop\nendfacet\nendsolid sheet\n")
    raw["geometryExport"]["stlFiles"] = {
        "source": {"path": "sheet.stl", "contentHash": hashlib.sha256(stl).hexdigest()}}
    raw["geometryExport"]["sourceSurfaces"] = {"source:sheet": {
        "stlFileId": "source", "solidName": "sheet", "sourceObjectUid": "m_source",
        "sourceFaceUid": "project/m_source/front", "representation": "surfaceFace"}}
    runner, snapshot = ram_runner(tmp_path, raw)
    runner.tri_surface_dir.mkdir(parents=True)
    actual = runner.tri_surface_dir / "sheet.stl"
    actual.write_bytes(stl)
    runner._verify_hybrid_source_stls()
    assert runner._verified_source_cfdrun_sha256 == snapshot.sha256
    assert runner._point_repair_source_receipt["rawSha256"] == snapshot.sha256
    actual.write_bytes(stl + b"changed")
    with pytest.raises(ValueError, match="content hash"):
        runner._verify_hybrid_source_stls()
    assert runner._point_repair_source_receipt is None


def quality_mesh(runner):
    poly = runner.mesh_dir / "constant/polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text('FoamFile { note "nCells:1"; }\n')


def test_snapshot_full_scope_quality_receipt_reuse_never_reopens_input(tmp_path, monkeypatch):
    runner, snapshot = ram_runner(tmp_path)
    quality_mesh(runner)
    prohibit_source_artifact(monkeypatch, snapshot.logical_path)
    runner._prechecked_air_quality = {
        "command": ["checkMesh", "-allTopology", "-allGeometry"], "workdir": runner.mesh_dir,
        "mesh_sha256_before": mesh_identity_sha256(runner.mesh_dir),
        "cfdrun_sha256_before": snapshot.sha256, "returncode": 0, "stdout": "Mesh OK.\n", "stderr": ""}
    monkeypatch.setattr(runner, "_run_cmd_capture", lambda *args: pytest.fail("duplicate checker"))
    runner._record_air_mesh_quality()
    runner._require_air_mesh_quality()
    assert runner._accepted_air_cfdrun_sha == snapshot.sha256
    runner._prechecked_air_quality = None
    runner._require_air_mesh_quality()


def test_capture_pins_snapshot_sha_even_when_logical_file_absent(tmp_path, monkeypatch):
    runner, snapshot = ram_runner(tmp_path)
    quality_mesh(runner)
    prohibit_source_artifact(monkeypatch, snapshot.logical_path)
    monkeypatch.setattr(runner, "_check_mesh_budget", lambda *args: None)
    monkeypatch.setattr(runner, "_mesh_time_remaining", lambda: None)
    monkeypatch.setattr(runner, "_foam_env_prefix", lambda: "")
    def checker(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "Mesh OK.\n", "")
    monkeypatch.setattr(stlgen_runner.subprocess, "run", checker)
    runner._run_cmd_capture("checkMesh -allTopology -allGeometry", runner.mesh_dir)
    assert runner._prechecked_air_quality["cfdrun_sha256_before"] == snapshot.sha256


def test_memfd_is_parent_addressed_sealed_and_exact_without_spool(tmp_path):
    runner, snapshot = ram_runner(tmp_path)
    with runner._cfdrun_command_path() as path:
        assert str(path).startswith(f"/proc/{os.getpid()}/fd/")
        assert Path(path).read_bytes() == snapshot.payload
        fd = int(Path(path).name)
        expected = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & expected == expected
        with pytest.raises(OSError):
            os.write(fd, b"corruption")
    with pytest.raises(OSError):
        os.fstat(fd)
    assert not snapshot.logical_path.exists()


def test_meshgen_cancel_closes_owned_memfd_while_preserving_original_error(tmp_path, monkeypatch):
    runner, snapshot = ram_runner(tmp_path)
    runner.tri_surface_dir.mkdir(parents=True)
    (runner.tri_surface_dir / "surface.stl").write_text("owned fixture")
    monkeypatch.setattr(runner, "_start_mesh_budget", lambda: None)
    monkeypatch.setattr(runner, "_ensure_mesh_system_files", lambda: None)
    monkeypatch.setattr(runner, "_resolve_meshgen_binary", lambda: "/owned/meshgen-cli")
    monkeypatch.setattr(runner, "_meshgen_thread_prefix", lambda: "")
    held = []
    def cancelled(command, workdir):
        words = shlex.split(command)
        path = Path(words[words.index("cfdrun") + 1])
        held.append(int(path.name))
        assert path.read_bytes() == snapshot.payload
        assert words[words.index("--surface-dir") + 1] == str(runner.tri_surface_dir)
        raise RuntimeError("owned cancellation fixture")
    monkeypatch.setattr(runner, "_run_cmd", cancelled)
    with pytest.raises(RuntimeError, match="owned cancellation fixture"):
        runner._run_meshgen_pipeline()
    assert len(held) == 1
    with pytest.raises(OSError):
        os.fstat(held[0])
    assert not snapshot.logical_path.exists()


def test_memfd_parent_path_is_readable_by_a_distinct_normal_child(tmp_path):
    runner, snapshot = ram_runner(tmp_path)
    with runner._cfdrun_command_path() as path:
        result = subprocess.check_output([
            sys.executable, "-c",
            "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())",
            str(path),
        ], cwd=tmp_path, text=True)
        assert result.strip() == snapshot.sha256


def test_missing_sealed_memfd_never_falls_back_to_disk(tmp_path, monkeypatch):
    runner, snapshot = ram_runner(tmp_path)
    monkeypatch.delattr(os, "memfd_create")
    with pytest.raises(RuntimeError, match="sealed memfd"):
        with runner._cfdrun_command_path():
            pytest.fail("unsupported platform executed native child")
    assert not snapshot.logical_path.exists()


def test_file_native_carrier_keeps_original_path(tmp_path):
    case, logical, _ = source_case(tmp_path)
    runner = STLGenRunner(case, logical)
    with runner._cfdrun_command_path() as path:
        assert path == logical


def hybrid_ready_runner(tmp_path):
    raw = json.loads(FIXTURE.read_bytes())
    raw["runId"] = "hybrid_snapshot_unit"
    runner, snapshot = ram_runner(tmp_path, raw, bind_batch=True)
    runner._load_cfd_json_only()
    quality_mesh(runner)
    (runner.case_dir / "heat-ledger.json").write_text("{}")
    mesh_sha = mesh_identity_sha256(runner.mesh_dir)
    source = dict(format="hybrid-mesh-source/v2",
                  **runner._hybrid_source_fields(raw), mesh_sha256=mesh_sha)
    (runner.case_dir / "hybrid-mesh-source.json").write_text(json.dumps(source))
    return runner, snapshot, raw, mesh_sha


def test_actual_hybrid_gate_uses_parent_bound_adjusted_snapshot_not_replay_truth(tmp_path, monkeypatch):
    from copy import deepcopy
    from backend.aircooling.runner import hybrid_air_heat_handoff
    from backend.aircooling.runner.cfdrun_snapshot import HybridCfdrunSnapshot
    runner, snapshot, raw, mesh_sha = hybrid_ready_runner(tmp_path)
    adjusted = deepcopy(raw)
    key = next(iter(adjusted["boundaryBindings"]))
    adjusted["boundaryBindings"][key]["parameters"]["temperatureC"] = 26.0
    patch_name = adjusted["boundaryBindings"][key]["patchName"]
    calls = []
    def finalized(source, ledger, mesh, **kwargs):
        assert source == raw and ledger == {} and mesh == runner.mesh_dir
        calls.append(True)
        return deepcopy(adjusted), {"mesh_sha256": mesh_sha}
    monkeypatch.setattr(hybrid_air_heat_handoff, "finalize_hybrid_air_heat_for_mesh", finalized)
    replay = runner.case_dir / f"hybrid-mesh-{mesh_sha[:16]}.cfdrun"
    prohibit_source_artifact(monkeypatch, snapshot.logical_path)
    original_read_text = Path.read_text
    def no_replay_input(path, *args, **kwargs):
        if path == replay:
            pytest.fail("derived input reopened replay artifact")
        return original_read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", no_replay_input)
    runner._apply_hybrid_air_gate()
    assert calls == [True]
    child = runner._hybrid_input_snapshot
    assert isinstance(child, HybridCfdrunSnapshot)
    assert child.logical_path == replay
    assert child.parent_sha256 == snapshot.sha256
    assert child.mesh_sha256 == mesh_sha
    assert snapshot.stl_batch_sha256 is not None
    assert child.stl_batch_sha256 == snapshot.stl_batch_sha256
    assert runner._read_cfdrun_bytes() == snapshot.payload
    assert runner._resolved.bridge_registry.get(patch_name).temperature == 26.0
    replay.unlink()
    assert cfdrun_reader.resolve_run(runner.case_dir, cfdrun_path=replay,
                                    input_snapshot=child, resolve_stl=False).bridge_registry.get(patch_name).temperature == 26.0
    assert list(runner.model_dir.iterdir()) == []


def test_hybrid_source_mismatch_rejects_before_adjustment_or_child_authority(tmp_path, monkeypatch):
    from backend.aircooling.runner import hybrid_air_heat_handoff
    runner, snapshot, raw, mesh_sha = hybrid_ready_runner(tmp_path)
    path = runner.case_dir / "hybrid-mesh-source.json"
    receipt = json.loads(path.read_bytes())
    receipt["mesh_sha256"] = "0" * 64
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(hybrid_air_heat_handoff, "finalize_hybrid_air_heat_for_mesh",
                        lambda *a, **k: pytest.fail("mismatched mesh entered finalizer"))
    with pytest.raises(RuntimeError, match="source identity"):
        runner._apply_hybrid_air_gate()
    assert getattr(runner, "_hybrid_input_snapshot", None) is None


def test_derived_hybrid_snapshot_cannot_replace_original_runner_source(tmp_path):
    from backend.aircooling.runner.cfdrun_snapshot import HybridCfdrunSnapshot
    runner, parent = ram_runner(tmp_path)
    mesh_sha = "a" * 64
    child = HybridCfdrunSnapshot(parent.payload, runner.case_dir,
        runner.case_dir / f"hybrid-mesh-{mesh_sha[:16]}.cfdrun",
        parent_input=parent, mesh_sha256=mesh_sha)
    with pytest.raises(TypeError, match="original"):
        STLGenRunner(runner.case_dir, input_snapshot=child)


def hybrid_archive_fixture(tmp_path, monkeypatch, *, file_mode=False):
    """Reach the real solve archive, then stop before any solver setup/run."""
    from copy import deepcopy
    from backend.aircooling.runner import hybrid_air_heat_handoff

    if file_mode:
        raw = json.loads(FIXTURE.read_bytes())
        raw["runId"] = "hybrid_snapshot_unit"
        case, logical, payload = source_case(tmp_path, raw)
        runner = STLGenRunner(case, logical)
        runner._load_cfd_json_only()
        quality_mesh(runner)
        (case / "heat-ledger.json").write_text("{}")
        mesh_sha = mesh_identity_sha256(runner.mesh_dir)
        source = dict(format="hybrid-mesh-source/v2",
                      **runner._hybrid_source_fields(raw), mesh_sha256=mesh_sha)
        (case / "hybrid-mesh-source.json").write_text(json.dumps(source))
        original_sha = hashlib.sha256(payload).hexdigest()
    else:
        runner, snapshot, raw, mesh_sha = hybrid_ready_runner(tmp_path)
        original_sha = snapshot.sha256

    # Native checking/finalization is represented by the same controlled
    # fixture receipt as the existing hybrid unit. Provider, resolver, strict
    # receipt validation, mesh copy and execution-source preflight are real.
    runner._verified_source_cfdrun_sha256 = original_sha
    runner._prechecked_air_quality = {
        "command": ["checkMesh", "-allTopology", "-allGeometry"],
        "workdir": runner.mesh_dir, "mesh_sha256_before": mesh_sha,
        "cfdrun_sha256_before": original_sha, "returncode": 0,
        "stdout": "Mesh OK.\n", "stderr": "",
    }
    runner._record_air_mesh_quality()
    runner._require_air_mesh_quality()
    adjusted = deepcopy(raw)
    first = next(iter(adjusted["boundaryBindings"]))
    adjusted["boundaryBindings"][first]["parameters"]["temperatureC"] = 26.0
    monkeypatch.setattr(hybrid_air_heat_handoff, "finalize_hybrid_air_heat_for_mesh",
                        lambda *a, **k: (deepcopy(adjusted), {"mesh_sha256": mesh_sha}))
    runner._apply_hybrid_air_gate()
    return runner


@pytest.mark.parametrize("replay_state", ["missing", "tampered"])
def test_ram_hybrid_archive_uses_owned_child_bytes_after_replay_changes(
        tmp_path, monkeypatch, replay_state):
    runner = hybrid_archive_fixture(tmp_path, monkeypatch)
    child = runner._hybrid_input_snapshot
    replay = runner._hybrid_final_manifest
    expected_ledger = runner._hybrid_final_ledger.read_bytes()
    if replay_state == "missing":
        replay.unlink()
    else:
        replay.write_bytes(b"changed replay is not the accepted execution input\n")

    class ArchiveReached(Exception):
        pass

    reached = []
    def stop_after_archive(run_dir):
        archived = run_dir / "model" / replay.name
        assert archived.read_bytes() == child.read_bytes()
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == child.sha256
        assert (run_dir / runner._hybrid_final_ledger.name).read_bytes() == expected_ledger
        reached.append(run_dir)
        raise ArchiveReached

    monkeypatch.setattr(runner, "_generate_solver_dicts", stop_after_archive)
    monkeypatch.setattr(runner, "_run_cmd",
                        lambda *a, **k: pytest.fail("archive unit started a solver"))
    with pytest.raises(ArchiveReached):
        runner._run_solve_from_mesh()
    assert len(reached) == 1
    assert not replay.exists() if replay_state == "missing" else replay.read_bytes().startswith(b"changed replay")
    assert runner._accepted_air_cfdrun_sha == runner._input_snapshot.sha256


def test_file_hybrid_archive_keeps_existing_copy2_path(tmp_path, monkeypatch):
    import shutil

    runner = hybrid_archive_fixture(tmp_path, monkeypatch, file_mode=True)
    assert runner._hybrid_input_snapshot is None
    replay = runner._hybrid_final_manifest
    expected = replay.read_bytes()
    original_copy2 = shutil.copy2
    copied = []
    def copy2(source, destination, *args, **kwargs):
        copied.append((Path(source), Path(destination)))
        return original_copy2(source, destination, *args, **kwargs)

    class ArchiveReached(Exception):
        pass

    def stop_after_archive(run_dir):
        destination = run_dir / "model" / replay.name
        assert destination.read_bytes() == expected
        assert (replay, destination) in copied
        assert (runner._hybrid_final_ledger, run_dir / runner._hybrid_final_ledger.name) in copied
        raise ArchiveReached

    monkeypatch.setattr(shutil, "copy2", copy2)
    monkeypatch.setattr(runner, "_generate_solver_dicts", stop_after_archive)
    monkeypatch.setattr(runner, "_run_cmd",
                        lambda *a, **k: pytest.fail("archive unit started a solver"))
    with pytest.raises(ArchiveReached):
        runner._run_solve_from_mesh()


def legacy_published_snapshot(tmp_path, *, bound=True):
    from backend.aircooling.runner.case_execution_lock import case_execution_lock
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256

    case, logical, payload = source_case(tmp_path)
    raw = json.loads(payload)
    assert raw["schemaVersion"] == 2
    assert not raw.get("geometryExport", {}).get("sourceSurfaces")
    assert not stlgen_runner.requires_hybrid_air_gate(raw)
    stl = case / "Mesh/constant/triSurface/legacy.stl"
    stl.parent.mkdir(parents=True)
    stl.write_bytes(
        b"solid legacy\nfacet normal 0 0 1\nouter loop\n"
        b"vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
        b"endloop\nendfacet\nendsolid legacy\n")
    with case_execution_lock(case):
        batch_a = surface_batch_sha256(case)
        snapshot = CfdrunSnapshot(payload, case, logical,
                                  stl_batch_sha256=batch_a if bound else None)
    return case, logical, stl, snapshot, batch_a


def test_actual_legacy_ram_publication_race_rejects_changed_stl_under_case_lock(
        tmp_path, monkeypatch):
    from backend.aircooling.runner.case_execution_lock import case_execution_lock
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256

    case, logical, stl, snapshot, batch_a = legacy_published_snapshot(tmp_path)
    expected_raw_a = snapshot.sha256
    file_control = STLGenRunner(case, logical, expected_raw_a)
    ram = STLGenRunner(case, logical, expected_raw_a, input_snapshot=snapshot)
    raw_b = snapshot.parse()
    raw_b["runId"] = str(raw_b.get("runId", "legacy")) + "_publication_B"
    with case_execution_lock(case):
        logical.write_text(json.dumps(raw_b))
        stl.write_bytes(stl.read_bytes().replace(b"vertex 1 0 0", b"vertex 2 0 0"))
        batch_b = surface_batch_sha256(case)
    assert batch_a != batch_b
    assert hashlib.sha256(logical.read_bytes()).hexdigest() != expected_raw_a
    assert ram._source_cfdrun_bytes() == snapshot.payload
    reached = []
    def stale_pipeline():
        reached.append(True)
        assert ram._read_cfdrun_bytes() == snapshot.payload
        assert b"vertex 2 0 0" in stl.read_bytes()
        pytest.fail("old RAM entry reached preflight with A input and B STL")
    monkeypatch.setattr(file_control, "_preflight_case_path",
                        lambda: pytest.fail("file-mode stale hash reached pipeline"))
    monkeypatch.setattr(ram, "_preflight_case_path", stale_pipeline)
    with pytest.raises(RuntimeError, match="input hash changed"):
        file_control.run_mesh_only()
    with pytest.raises(RuntimeError, match="STL batch changed"):
        ram.run_mesh_only()
    assert reached == []
    assert getattr(ram, "_execution_cfdrun_sha256", None) is None
    with case_execution_lock(case, blocking=False):
        pass


def test_ram_entry_missing_physical_batch_refuses_before_preflight(tmp_path, monkeypatch):
    from backend.aircooling.runner.case_execution_lock import case_execution_lock

    case, logical, _, snapshot, _ = legacy_published_snapshot(tmp_path, bound=False)
    # Pure materialization remains valid without physical publication metadata.
    runner = STLGenRunner(case, logical, snapshot.sha256, input_snapshot=snapshot)
    runner._load_cfd_json_only()
    monkeypatch.setattr(runner, "_preflight_case_path",
                        lambda: pytest.fail("unbound RAM entry reached preflight"))
    with pytest.raises(RuntimeError, match="STL batch identity"):
        runner.run_mesh_only()
    assert getattr(runner, "_execution_cfdrun_sha256", None) is None
    with case_execution_lock(case, blocking=False):
        pass


def test_ram_entry_stl_namespace_failure_does_not_enter_pipeline(tmp_path, monkeypatch):
    case, logical, stl, snapshot, _ = legacy_published_snapshot(tmp_path)
    runner = STLGenRunner(case, logical, snapshot.sha256, input_snapshot=snapshot)
    alias = case / "owned-alias.stl"
    alias.write_bytes(stl.read_bytes())
    stl.unlink()
    stl.symlink_to(alias)
    monkeypatch.setattr(runner, "_preflight_case_path",
                        lambda: pytest.fail("invalid physical batch reached preflight"))
    with pytest.raises(ValueError, match="symlink or special file"):
        runner.run_mesh_only()


def test_legacy_bound_stl_batch_is_checked_inside_lock_then_cancel_releases_it(
        tmp_path, monkeypatch):
    from backend.aircooling.runner.case_execution_lock import case_execution_lock, CaseExecutionBusyError
    from backend.aircooling.runner import cfdrun_stl_identity

    case, logical, _, snapshot, batch = legacy_published_snapshot(tmp_path)
    runner = STLGenRunner(case, logical, snapshot.sha256, input_snapshot=snapshot)
    original_hash = cfdrun_stl_identity.surface_batch_sha256
    checks = []
    def locked_hash(path):
        assert path == case
        assert runner._data is None
        with pytest.raises(CaseExecutionBusyError):
            with case_execution_lock(case, blocking=False):
                pass
        checks.append(True)
        return original_hash(path)

    class ControlledCancellation(Exception):
        pass

    def preflight():
        assert checks == [True]
        assert runner._execution_cfdrun_sha256 == snapshot.sha256
        assert snapshot.stl_batch_sha256 == batch
        raise ControlledCancellation

    monkeypatch.setattr(cfdrun_stl_identity, "surface_batch_sha256", locked_hash)
    monkeypatch.setattr(runner, "_preflight_case_path", preflight)
    with pytest.raises(ControlledCancellation):
        runner.run_mesh_only()
    assert checks == [True]
    assert runner._execution_cfdrun_sha256 is None
    with case_execution_lock(case, blocking=False):
        pass


def ordinary_archive_fixture(tmp_path, *, file_mode=False):
    """Reach the ordinary solve archive with the real owned/strict provider."""
    case, logical, _stl, snapshot, _batch = legacy_published_snapshot(tmp_path)
    runner = (STLGenRunner(case, logical) if file_mode else
              STLGenRunner(case, input_snapshot=snapshot))
    runner._load_cfd_json_only()
    quality_mesh(runner)
    runner._prechecked_air_quality = {
        "command": ["checkMesh", "-allTopology", "-allGeometry"],
        "workdir": runner.mesh_dir,
        "mesh_sha256_before": mesh_identity_sha256(runner.mesh_dir),
        "cfdrun_sha256_before": snapshot.sha256, "returncode": 0,
        "stdout": "Mesh OK.\n", "stderr": "",
    }
    runner._record_air_mesh_quality()
    runner._require_air_mesh_quality()
    return runner, snapshot


@pytest.mark.parametrize("logical_state", ["missing", "tampered"])
def test_ram_ordinary_archive_keeps_exact_accepted_input_after_publication(
        tmp_path, monkeypatch, logical_state):
    runner, snapshot = ordinary_archive_fixture(tmp_path)
    logical = snapshot.logical_path
    if logical_state == "missing":
        logical.unlink()
    else:
        logical.write_bytes(b"later publication is not this accepted input\n")
    prohibit_source_artifact(monkeypatch, logical)

    class ArchiveReached(Exception):
        pass

    reached = []
    def stop_after_archive(run_dir):
        archived = run_dir / "model" / logical.name
        assert archived.read_bytes() == snapshot.read_bytes()
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == snapshot.sha256
        assert runner._accepted_air_cfdrun_sha == snapshot.sha256
        reached.append(run_dir)
        raise ArchiveReached

    monkeypatch.setattr(runner, "_generate_solver_dicts", stop_after_archive)
    monkeypatch.setattr(runner, "_run_cmd",
                        lambda *a, **k: pytest.fail("archive unit started a solver"))
    with pytest.raises(ArchiveReached):
        runner._run_solve_from_mesh()
    assert len(reached) == 1


@pytest.mark.parametrize("collision_kind", ["file", "symlink"])
def test_ram_ordinary_archive_refuses_existing_destination_without_overwriting(
        tmp_path, monkeypatch, collision_kind):
    import shutil

    runner, snapshot = ordinary_archive_fixture(tmp_path)
    retained = tmp_path / "other-owned-input.cfdrun"
    retained.write_bytes(b"already published other input\n")
    old_copytree = shutil.copytree
    copied = []
    def copytree(source, destination, *args, **kwargs):
        result = old_copytree(source, destination, *args, **kwargs)
        if Path(source) == runner.mesh_dir:
            model = Path(destination) / "model"
            model.mkdir()
            archive = model / snapshot.logical_path.name
            if collision_kind == "file":
                archive.write_bytes(retained.read_bytes())
            else:
                archive.symlink_to(retained)
            copied.append(archive)
        return result

    monkeypatch.setattr(shutil, "copytree", copytree)
    monkeypatch.setattr(runner, "_generate_solver_dicts",
                        lambda *a: pytest.fail("colliding archive reached solver setup"))
    monkeypatch.setattr(runner, "_run_cmd",
                        lambda *a, **k: pytest.fail("archive unit started a solver"))
    with pytest.raises(FileExistsError):
        runner._run_solve_from_mesh()
    assert len(copied) == 1
    assert retained.read_bytes() == b"already published other input\n"
    assert copied[0].read_bytes() == retained.read_bytes()


def test_file_ordinary_archive_behavior_stays_unchanged(tmp_path, monkeypatch):
    runner, snapshot = ordinary_archive_fixture(tmp_path, file_mode=True)

    class ArchiveReached(Exception):
        pass

    def stop_after_archive(run_dir):
        assert not (run_dir / "model").exists()
        assert snapshot.logical_path.read_bytes() == snapshot.read_bytes()
        raise ArchiveReached

    monkeypatch.setattr(runner, "_generate_solver_dicts", stop_after_archive)
    monkeypatch.setattr(runner, "_run_cmd",
                        lambda *a, **k: pytest.fail("archive unit started a solver"))
    with pytest.raises(ArchiveReached):
        runner._run_solve_from_mesh()
