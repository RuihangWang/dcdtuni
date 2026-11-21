"""meshEngine: which executable builds the mesh (dcCart | snappy).

Orthogonal to meshMode. Default snappy = the pipeline as it was; dcCart runs
the repo's cut-cell mesher in place of blockMesh + snappyHexMesh.
"""
import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig, MeshConfig
from backend.aircooling.runner.derived_config import derive_mesh_config
from backend.aircooling.runner.stlgen_runner import STLGenRunner, _resolve_mesh_engine


def test_resolve_engine_default_and_explicit():
    assert _resolve_mesh_engine({}) == "dcCart"
    assert _resolve_mesh_engine({"meshEngine": "snappy"}) == "snappy"
    assert _resolve_mesh_engine({"meshEngine": "dcCart"}) == "dcCart"
    assert _resolve_mesh_engine({"meshEngine": "bogus"}) == "dcCart"
    assert _resolve_mesh_engine({"meshEngine": None}) == "dcCart"


def test_schema_defaults_and_v2_declares_it():
    assert CFDExportMeshConfig().meshEngine == "dcCart"
    assert CFDExportMeshConfig().baseSize == 0.25
    assert CFDExportMeshConfig().meshTimeLimitSeconds == 300
    assert CFDExportMeshConfig(meshEngine="dcCart").meshEngine == "dcCart"
    # V2 MeshConfig is extra="forbid": the key must be declared or every
    # manifest carrying it would be rejected.
    assert MeshConfig(meshEngine="dcCart").meshEngine == "dcCart"


def test_derived_config_carries_engine():
    out = derive_mesh_config({"meshEngine": "dcCart"}, {}, block_mesh_bounds=None)
    assert out["meshEngine"] == "dcCart"
    # Missing fields follow the same product default as the frontend exporter.
    missing = derive_mesh_config({}, {}, block_mesh_bounds=None)
    assert missing["meshEngine"] == "dcCart"
    assert missing["baseSize"] == 0.25
    for explicit in ("snappy", "dcCart"):
        assert derive_mesh_config({"meshEngine": explicit}, {}, block_mesh_bounds=None)["meshEngine"] == explicit


def test_dcCart_mesh_dict_from_cached_planes(tmp_path):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r._data = {"meshConfig": {"meshTimeLimitSeconds": 120}}
    r._bg_grid_planes = {"x": [0.0, 0.5, 1.0], "y": [0.0, 1.0], "z": [0.0, 0.25, 0.5]}
    (tmp_path / "system").mkdir()
    r._write_dcCart_mesh_dict(tmp_path / "system")
    text = (tmp_path / "system" / "dcCartMeshDict").read_text()
    assert "x (0.0 0.5 1.0);" in text
    assert "z (0.0 0.25 0.5);" in text
    # soft target: dcCart is never given a time limit, whatever the project asks
    assert "timeLimitSeconds    0;" in text
    assert "geometry" not in text  # read from snappyHexMeshDict


def test_repo_built_mesher_is_preferred():
    path = STLGenRunner._resolve_mesher_binary("dcCartMesh")
    # Either the repo build (absolute path under third_parties/dcCartMesh) or,
    # on a host without the build, the bare name.
    assert path == "dcCartMesh" or path.endswith("/bin/dcCartMesh")


@pytest.mark.parametrize("config", [{}, {"meshTimeLimitSeconds": 0}, {"meshTimeLimitSeconds": None}, {"meshTimeLimitSeconds": "invalid"}])
def test_dcCart_dict_never_carries_a_time_limit(tmp_path, config):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "p.cfdrun")
    r._data = {"meshConfig": {"meshEngine": "dcCart", **config}}
    r._bg_grid_planes = {axis: [0.0, 1.0] for axis in ("x", "y", "z")}
    r._write_dcCart_mesh_dict(tmp_path)
    assert "timeLimitSeconds    0;" in (tmp_path / "dcCartMeshDict").read_text()
    r._start_mesh_budget()
    assert r._mesh_time_remaining() is None


def test_case_path_with_space_is_refused_up_front(tmp_path):
    import pytest
    bad = tmp_path / "DAML-Testbed copy"
    bad.mkdir()
    r = STLGenRunner(case_dir=bad, cfdrun_path=bad / "model" / "p.cfdrun")
    with pytest.raises(RuntimeError, match="OpenFOAM"):
        r._preflight_case_path()
    ok = STLGenRunner(case_dir=tmp_path / "DAML-Testbed_copy", cfdrun_path=tmp_path / "p.cfdrun")
    ok._preflight_case_path()  # no raise


def test_room_enclosure_keeps_browser_exported_sheets(tmp_path):
    """The ceiling with return grilles must survive _ensure_watertight_room_enclosure."""
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    tri.mkdir(parents=True)
    r.tri_surface_dir = tri
    fc = "solid Floor\nendsolid Floor\nsolid Ceiling_patch_1\nendsolid Ceiling_patch_1\nsolid Ceiling_part1\nendsolid Ceiling_part1\n"
    (tri / "merged_floor_ceiling.stl").write_text(fc)
    (tri / "room_wall.stl").write_text("solid Wall_1\nendsolid Wall_1\n")
    r._stl_solids = {"merged_floor_ceiling.stl": ["Floor", "Ceiling_patch_1", "Ceiling_part1"], "room_wall.stl": ["Wall_1"]}
    r._data = {"room": {"plane": [{"x": 0, "y": 0}, {"x": 6, "y": 0}, {"x": 6, "y": 3}, {"x": 0, "y": 3}], "height": 2.7}}
    r._ensure_watertight_room_enclosure()
    assert (tri / "merged_floor_ceiling.stl").read_text() == fc
    assert r._stl_solids["merged_floor_ceiling.stl"] == ["Floor", "Ceiling_patch_1", "Ceiling_part1"]


def test_room_enclosure_is_synthesised_only_when_missing(tmp_path):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    tri.mkdir(parents=True)
    r.tri_surface_dir = tri
    r._stl_solids = {}
    r._data = {"room": {"plane": [{"x": 0, "y": 0}, {"x": 6, "y": 0}, {"x": 6, "y": 3}, {"x": 0, "y": 3}], "height": 2.7}}
    r._ensure_watertight_room_enclosure()
    assert (tri / "room_wall.stl").exists() and (tri / "merged_floor_ceiling.stl").exists()
    assert r._stl_solids["merged_floor_ceiling.stl"] == ["Ground_floor", "Room_top"]


class _FakeRunner:
    _clear_dcCart_previous_outputs = staticmethod(STLGenRunner._clear_dcCart_previous_outputs)
    """Just enough of STLGenRunner for the parallel dcCart step."""

    def __init__(self, case_dir, fail=False, ranks_writing=None):
        self.case_dir = case_dir
        self.fail = fail
        self.ranks_writing = ranks_writing
        self.cmds = []
        self.cleaned = 0

    def _run_cmd(self, cmd, workdir):
        self.cmds.append(cmd)
        if self.fail:
            raise RuntimeError("mpirun exited 1")
        n = int(cmd.split("-np ")[1].split()[0])
        for i in range(n):
            if self.ranks_writing is None or i in self.ranks_writing:
                d = workdir / f"processor{i}" / "constant" / "polyMesh"
                d.mkdir(parents=True)
                (d / "faces").write_text("")

    def _clean_processor_dirs(self, case_dir):
        self.cleaned += 1
        import shutil
        for p in case_dir.glob("processor*"):
            shutil.rmtree(p)


def test_dcCart_parallel_runs_under_mpirun_and_keeps_processor_meshes(tmp_path):
    (tmp_path / "system").mkdir()
    r = _FakeRunner(tmp_path)
    ok = STLGenRunner._run_dcCart_parallel(r, "/bin/dcCartMesh", " -timeLimit 0", 4, tmp_path)
    assert ok
    assert r.cmds == ["mpirun -np 4 /bin/dcCartMesh -overwrite -timeLimit 0 -parallel"]
    # dcCartMesh partitions itself; it only reads numberOfSubdomains
    assert "numberOfSubdomains 4;" in (tmp_path / "system" / "decomposeParDict").read_text()
    # the processor meshes stay for reconstructParMesh
    assert all((tmp_path / f"processor{i}" / "constant" / "polyMesh" / "faces").exists() for i in range(4))


def test_dcCart_parallel_failure_preserves_partial_output_and_reports(tmp_path):
    (tmp_path / "system").mkdir()
    r = _FakeRunner(tmp_path, fail=True)
    assert STLGenRunner._run_dcCart_parallel(r, "dcCartMesh", "", 2, tmp_path) is False
    assert all((tmp_path / f"processor{i}").is_dir() for i in range(2))
    # a rank that wrote nothing is a failure too
    r = _FakeRunner(tmp_path, ranks_writing={0})
    assert STLGenRunner._run_dcCart_parallel(r, "dcCartMesh", "", 2, tmp_path) is False
    assert (tmp_path / "processor0" / "constant" / "polyMesh" / "faces").exists()


class _FakePipeline(_FakeRunner):
    """The rest of what _run_dcCart_mesh_pipeline touches, as no-ops."""

    def __init__(self, case_dir, parallel_ok):
        super().__init__(case_dir)
        self.mesh_dir = case_dir
        self.parallel_ok = parallel_ok

    def _start_mesh_budget(self): pass
    def _check_mesh_budget(self, stage): pass
    def _ensure_mesh_system_files(self): pass
    def _clean_stale_polymesh_zones(self, case_dir): pass
    def _write_dcCart_mesh_dict(self, system_dir): pass
    def _mesh_time_remaining(self): return None
    def _diagnose_background_patch(self, case_dir): pass
    def _warn_open_room_enclosure(self, case_dir): pass
    def _check_server_fan_faces(self, case_dir): pass
    def _remove_disconnected_cells(self, case_dir, *, n_procs=1):
        self.checked_ranks = n_procs

    @staticmethod
    def _resolve_mesher_binary(name): return "/bin/" + name

    def _run_dcCart_parallel(self, mesher, budget, n_procs, case_dir):
        self.parallel_budget = budget
        return self.parallel_ok

    def _run_cmd(self, cmd, workdir):
        self.cmds.append(cmd)


def test_dcCart_failed_parallel_run_is_an_error_not_a_serial_rerun(tmp_path):
    """One algorithm for any rank count: a failed MPI run must surface, not be
    papered over by a one-rank rerun."""
    (tmp_path / "system").mkdir()
    r = _FakePipeline(tmp_path, parallel_ok=False)
    with pytest.raises(RuntimeError, match="MPI ranks"):
        STLGenRunner._run_dcCart_mesh_pipeline(r, 4)
    assert not any(c.startswith("/bin/dcCartMesh") for c in r.cmds)


def test_dcCart_one_rank_and_many_ranks_run_the_same_mesher(tmp_path):
    (tmp_path / "system").mkdir()
    one = _FakePipeline(tmp_path, parallel_ok=True)
    STLGenRunner._run_dcCart_mesh_pipeline(one, 1)
    assert one.cmds[0] == "/bin/dcCartMesh -overwrite"
    many = _FakePipeline(tmp_path, parallel_ok=True)
    STLGenRunner._run_dcCart_mesh_pipeline(many, 4)
    assert many.cmds == []  # MPI mesher is stubbed; no serial reconstruction.
    assert many.parallel_budget == ""
    assert many.checked_ranks == 4


# ---------------------------------------------------------------------------
# meshgen (MeshGenerator) was retired on 2026-10-26: refused, never re-routed
# ---------------------------------------------------------------------------

RETIRED = "the meshgen engine was retired on 2026-10-26; choose dcCart or snappy"


def test_resolve_engine_refuses_retired_meshgen():
    with pytest.raises(ValueError, match=RETIRED):
        _resolve_mesh_engine({"meshEngine": "meshgen"})


@pytest.mark.parametrize("model", [CFDExportMeshConfig, MeshConfig])
def test_schema_refuses_retired_meshgen_with_the_clear_message(model):
    with pytest.raises(ValueError, match=RETIRED):
        model(meshEngine="meshgen")


@pytest.mark.parametrize("entry", ["run_mesh_only", "run_simulation"])
def test_runner_refuses_meshgen_before_any_mesh_step(tmp_path, monkeypatch, entry):
    """A real manifest that names meshgen stops at load: no geometry check, no mesher."""
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parent / "fixtures/cfdrun_v2_full/Base_leakage.cfdrun").read_text())
    for holder in (raw, raw.get("meshGeometry") or {}):
        if isinstance(holder.get("meshConfig"), dict):
            holder["meshConfig"]["meshEngine"] = "meshgen"
    cfdrun = tmp_path / "model" / "p.cfdrun"
    cfdrun.parent.mkdir(parents=True)
    cfdrun.write_text(json.dumps(raw))
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=cfdrun)
    steps = []
    monkeypatch.setattr(r, "_verify_hybrid_source_stls", lambda: None)
    monkeypatch.setattr(r, "_preflight_floor_openings", lambda: None)
    for name in ("validate_geometry", "_generate_mesh_dicts", "_run_dcCart_mesh_pipeline",
                 "_run_serial_mesh_pipeline", "_run_parallel_mesh_pipeline"):
        monkeypatch.setattr(r, name, lambda *a, _n=name, **k: steps.append(_n))
    with pytest.raises(ValueError, match=RETIRED):
        getattr(r, entry)()
    assert steps == []
