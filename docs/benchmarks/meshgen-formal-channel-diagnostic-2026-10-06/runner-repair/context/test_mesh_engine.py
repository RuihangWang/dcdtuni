"""meshEngine: which executable builds the mesh (snappy | dcCart | meshgen).

Orthogonal to meshMode. Default snappy = the pipeline as it was; dcCart runs
the repo's cut-cell mesher in place of blockMesh + snappyHexMesh.
"""
import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig, MeshConfig
from backend.aircooling.runner.derived_config import derive_mesh_config
from backend.aircooling.runner.stlgen_runner import STLGenRunner, _resolve_mesh_engine


def test_resolve_engine_default_and_explicit():
    assert _resolve_mesh_engine({}) == "snappy"
    assert _resolve_mesh_engine({"meshEngine": "dcCart"}) == "dcCart"
    assert _resolve_mesh_engine({"meshEngine": "meshgen"}) == "meshgen"
    assert _resolve_mesh_engine({"meshEngine": "bogus"}) == "snappy"
    assert _resolve_mesh_engine({"meshEngine": None}) == "snappy"


def test_schema_defaults_and_v2_declares_it():
    assert CFDExportMeshConfig().meshEngine == "snappy"
    assert CFDExportMeshConfig(meshEngine="dcCart").meshEngine == "dcCart"
    # V2 MeshConfig is extra="forbid": the key must be declared or every
    # manifest carrying it would be rejected.
    assert MeshConfig(meshEngine="dcCart").meshEngine == "dcCart"


def test_derived_config_carries_engine():
    out = derive_mesh_config({"meshEngine": "dcCart"}, {}, block_mesh_bounds=None)
    assert out["meshEngine"] == "dcCart"
    # Absent in the project -> absent in the derived config (parity with old blobs).
    assert "meshEngine" not in derive_mesh_config({}, {}, block_mesh_bounds=None)


def test_dcCart_mesh_dict_from_cached_planes(tmp_path):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r._data = {"meshConfig": {"meshTimeLimitSeconds": 120}}
    r._bg_grid_planes = {"x": [0.0, 0.5, 1.0], "y": [0.0, 1.0], "z": [0.0, 0.25, 0.5]}
    (tmp_path / "system").mkdir()
    r._write_dcCart_mesh_dict(tmp_path / "system")
    text = (tmp_path / "system" / "dcCartMeshDict").read_text()
    assert "x (0.0 0.5 1.0);" in text
    assert "z (0.0 0.25 0.5);" in text
    assert "timeLimitSeconds    120;" in text
    assert "geometry" not in text  # read from snappyHexMeshDict


def test_repo_built_mesher_is_preferred():
    path = STLGenRunner._resolve_mesher_binary("dcCartMesh")
    # Either the repo build (absolute path under third_parties/dcCartMesh) or,
    # on a host without the build, the bare name.
    assert path == "dcCartMesh" or path.endswith("/bin/dcCartMesh")


@pytest.mark.parametrize("config", [{}, {"meshTimeLimitSeconds": 0}, {"meshTimeLimitSeconds": None}, {"meshTimeLimitSeconds": "invalid"}])
def test_dcCart_dict_enforces_default_or_legacy_disabled_budget(tmp_path, config):
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "p.cfdrun")
    r._data = {"meshConfig": {"meshEngine": "dcCart", **config}}
    r._bg_grid_planes = {axis: [0.0, 1.0] for axis in ("x", "y", "z")}
    r._write_dcCart_mesh_dict(tmp_path)
    assert "timeLimitSeconds    300;" in (tmp_path / "dcCartMeshDict").read_text()
    r._start_mesh_budget()
    assert 0 < r._mesh_time_remaining() <= 300


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


def test_dcCart_parallel_failure_cleans_up_and_reports(tmp_path):
    (tmp_path / "system").mkdir()
    r = _FakeRunner(tmp_path, fail=True)
    assert STLGenRunner._run_dcCart_parallel(r, "dcCartMesh", "", 2, tmp_path) is False
    assert not list(tmp_path.glob("processor*"))
    # a rank that wrote nothing is a failure too
    r = _FakeRunner(tmp_path, ranks_writing={0})
    assert STLGenRunner._run_dcCart_parallel(r, "dcCartMesh", "", 2, tmp_path) is False
    assert not list(tmp_path.glob("processor*"))


class _FakePipeline(_FakeRunner):
    """The rest of what _run_dcCart_mesh_pipeline touches, as no-ops."""

    def __init__(self, case_dir, parallel_ok):
        super().__init__(case_dir)
        self.mesh_dir = case_dir
        self.parallel_ok = parallel_ok

    def _start_mesh_budget(self): pass
    def _ensure_mesh_system_files(self): pass
    def _clean_stale_polymesh_zones(self, case_dir): pass
    def _write_dcCart_mesh_dict(self, system_dir): pass
    def _mesh_time_remaining(self): return None
    def _diagnose_background_patch(self, case_dir): pass
    def _warn_open_room_enclosure(self, case_dir): pass
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
# meshgen: the vendored MeshGenerator engine (runner wiring, dc17 full-flow)
# ---------------------------------------------------------------------------

_OWNER_ASCII = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       labelList;
    object      owner;
}

3
(
0
1
0
)
"""

_NEIGHBOUR_ASCII = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       labelList;
    object      neighbour;
}

1
(
1
)
"""

_BOUNDARY_ASCII = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       polyBoundaryMesh;
    object      boundary;
}

1
(
Room_Wall_1
{
    type            wall;
    nFaces          0;
    startFace       3;
}
)
"""


class _FakeMeshgen(STLGenRunner):
    """STLGenRunner with the meshgen pipeline's heavy parts stubbed."""

    def __init__(self, case_dir, run_cmd_exc=None):
        super().__init__(case_dir=case_dir, cfdrun_path=case_dir / "model" / "p.cfdrun")
        self.cmds = []
        self.cleanup = []
        self.assembled = []
        self._run_cmd_exc = run_cmd_exc

    def _start_mesh_budget(self): pass
    def _ensure_mesh_system_files(self): pass
    def _warn_open_room_enclosure(self, case_dir): pass
    def _resolve_meshgen_binary(self): return "/bin/meshgen-cli"
    def _remove_disconnected_cells(self, case_dir, *, snap_off=None):
        self.cleanup.append(snap_off); return 0

    def _run_cmd(self, cmd, workdir):
        self.cmds.append((cmd, str(workdir)))
        if self._run_cmd_exc is not None:
            raise self._run_cmd_exc


def _seed_case(tmp_path):
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    tri.mkdir(parents=True)
    (tri / "room_wall.stl").write_text("solid Wall_1\nendsolid Wall_1\n")
    return tri


def test_meshgen_pipeline_command_shape_and_skips(tmp_path, monkeypatch):
    monkeypatch.setenv("DCDTUNI_MESHGEN_THREADS", "4")
    monkeypatch.setenv("DCDTUNI_MESHGEN_TASKSET", "0")
    _seed_case(tmp_path)
    r = _FakeMeshgen(tmp_path)
    r._assemble_meshgen_polymesh = lambda *a, **k: r.assembled.append(a)
    r._run_meshgen_pipeline()
    assert len(r.cmds) == 1
    cmd, workdir = r.cmds[0]
    # sanctioned dc17 production state: threads + ON+S9D knobs in one env clause
    environment, executable = cmd.split("/bin/meshgen-cli cfdrun ", 1)
    assert environment.startswith("env MESHGEN_THREADS=4 ")
    for flag in ("CART_CARVED_MERGE1=1", "CART_S9D_RESCUE=1", "CART_DUAL_SNAP=1", "CART_S9D_EXH_BNDSKEW=1"):
        assert flag in environment.split()
    assert str(r.cfdrun_path) in cmd
    assert f"--surface-dir {r.tri_surface_dir}" in cmd
    assert "--dc-mode --dc-raw --dc-seeds pre" in cmd
    assert "--export-foam" in cmd and str(tmp_path / "Mesh" / ".meshgen_scratch") in cmd
    assert "--report" in cmd and cmd.endswith("dc_report.json")
    # ONE binary step: no blockMesh / snappy / createBaffles / decomposePar
    assert not any(name in cmd for name in
                   ("blockMesh", "snappy", "createBaffles", "decomposePar", "mpirun"))
    # cleanup follows the dcCart choice (snap-off, no -allGeometry)
    assert r.cleanup == [True]
    assert len(r.assembled) == 1
    # scratch (under Mesh/) is removed on success
    assert not (tmp_path / "Mesh" / ".meshgen_scratch").exists()


def test_meshgen_knobs_escape_hatch_restores_engine_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DCDTUNI_MESHGEN_THREADS", "4")
    monkeypatch.setenv("DCDTUNI_MESHGEN_TASKSET", "0")
    monkeypatch.setenv("DCDTUNI_MESHGEN_KNOBS", "0")
    _seed_case(tmp_path)
    r = _FakeMeshgen(tmp_path)
    r._assemble_meshgen_polymesh = lambda *a, **k: r.assembled.append(a)
    r._run_meshgen_pipeline()
    cmd, _ = r.cmds[0]
    assert cmd.startswith("env MESHGEN_THREADS=4 /bin/meshgen-cli cfdrun ")
    assert "CART_CARVED_MERGE1" not in cmd and "CART_S9D_RESCUE" not in cmd


def test_meshgen_failure_surfaces_report_and_ships_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("DCDTUNI_MESHGEN_TASKSET", "0")
    _seed_case(tmp_path)
    scratch_report = tmp_path / "Mesh" / ".meshgen_scratch" / "dc_report.json"

    def _refuse(cmd, workdir):
        scratch_report.parent.mkdir(parents=True, exist_ok=True)
        scratch_report.write_text('{"stage":"S7","mode":"dcMode-report-not-refusal"}')
        raise RuntimeError("'meshgen-cli' exited with code 1")

    r = _FakeMeshgen(tmp_path)
    r._run_cmd = _refuse
    assembled = []
    r._assemble_meshgen_polymesh = lambda *a, **k: assembled.append(a)
    with pytest.raises(RuntimeError, match="refused the mesh"):
        r._run_meshgen_pipeline()
    assert assembled == []
    assert not (tmp_path / "Mesh" / "constant" / "polyMesh").exists()


def test_meshgen_missing_stl_is_refused_before_the_binary(tmp_path):
    r = _FakeMeshgen(tmp_path)
    with pytest.raises(RuntimeError, match="Re-export"):
        r._run_meshgen_pipeline()
    assert r.cmds == []


def _write_mg_export(out_poly, *, owner=_OWNER_ASCII, boundary=_BOUNDARY_ASCII,
                     extras=("cellLevel", "pointLevel", "level0Edge", "refinementHistory")):
    out_poly.mkdir(parents=True, exist_ok=True)
    (out_poly / "points").write_text("/* pts */\n")
    (out_poly / "faces").write_text("/* faces */\n")
    (out_poly / "owner").write_text(owner)
    (out_poly / "neighbour").write_text(_NEIGHBOUR_ASCII)
    (out_poly / "boundary").write_text(boundary)
    (out_poly / "faceZones").write_text("/* zones */\n")
    for name in extras:
        (out_poly / name).write_text("/* AMR extra */\n")


def test_meshgen_assembly_keep_list_strips_amr_extras(tmp_path):
    _seed_case(tmp_path)
    r = _FakeMeshgen(tmp_path)
    scratch = tmp_path / ".meshgen_scratch"
    out_poly = scratch / "constant" / "polyMesh"
    # unparseable owner/neighbour body -> topology scan None -> floor skipped
    _write_mg_export(out_poly, owner="FoamFile\n{\n format ascii;\n}\n\nGARBAGE\n")
    (scratch / "constant" / "dynamicMeshDict").write_text("/* AMR */\n")
    dst = tmp_path / "Mesh" / "constant" / "polyMesh"
    dst.mkdir(parents=True)
    (dst / "cells").write_text("/* stale snappy file */\n")

    r._assemble_meshgen_polymesh(tmp_path / "Mesh", out_poly, scratch)

    names = sorted(p.name for p in dst.iterdir())
    assert names == ["boundary", "faceZones", "faces", "neighbour", "owner", "points"]
    assert not (dst / "cellLevel").exists()
    assert not (dst / "cells").exists()  # stale snappy polyMesh is REPLACED
    assert not (tmp_path / "Mesh" / "constant" / "dynamicMeshDict").exists()


def test_meshgen_degenerate_export_is_refused(tmp_path):
    _seed_case(tmp_path)
    r = _FakeMeshgen(tmp_path)
    scratch = tmp_path / ".meshgen_scratch"
    out_poly = scratch / "constant" / "polyMesh"
    _write_mg_export(out_poly)  # valid tiny labelLists -> nCells = 2 < floor
    with pytest.raises(RuntimeError, match="DEGENERATE"):
        r._assemble_meshgen_polymesh(tmp_path / "Mesh", out_poly, scratch)
    assert not (tmp_path / "Mesh" / "constant" / "polyMesh").exists()


def test_meshgen_binary_boundary_is_refused(tmp_path):
    _seed_case(tmp_path)
    r = _FakeMeshgen(tmp_path)
    scratch = tmp_path / ".meshgen_scratch"
    out_poly = scratch / "constant" / "polyMesh"
    _write_mg_export(
        out_poly,
        owner="FoamFile\n{\n format ascii;\n}\n\nGARBAGE\n",
        boundary="FoamFile\n{\n    format      binary;\n}\n\n\x00\x01binary body",
    )
    with pytest.raises(RuntimeError, match="BINARY"):
        r._assemble_meshgen_polymesh(tmp_path / "Mesh", out_poly, scratch)


def test_meshgen_v2412_banner_boundary_is_accepted(tmp_path):
    # Regression: OpenFOAM v2412 puts 'FoamFile' at ~byte 560 in the standard
    # banner (golden snappy / dcCart / MG exports all share these offsets) —
    # the old 512-byte ASCII guard window refused every conformant boundary.
    _seed_case(tmp_path)
    r = _FakeMeshgen(tmp_path)
    scratch = tmp_path / ".meshgen_scratch"
    out_poly = scratch / "constant" / "polyMesh"
    banner = (
        "/*--------------------------------*- C++ -*-*\\\n"
        + "| this is a very long v2412 style comment banner |\n" * 10
        + "\\*-----------------------------------------------------------------*/\n"
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n}\n\n0()\n"
    )
    assert banner.index("FoamFile") > 512  # the bug condition, pinned
    _write_mg_export(out_poly, owner="FoamFile\n{\n format ascii;\n}\n\nGARBAGE\n",
                     boundary=banner)
    r._assemble_meshgen_polymesh(tmp_path / "Mesh", out_poly, scratch)
    assert (tmp_path / "Mesh" / "constant" / "polyMesh" / "boundary").is_file()


def test_meshgen_binary_env_pin_wins(tmp_path, monkeypatch):
    pin = tmp_path / "meshgen-cli"
    pin.write_text("#!/bin/sh\nexit 0\n")
    pin.chmod(0o755)
    monkeypatch.setenv("DCDTUNI_MESHGEN_BIN", str(pin))
    assert STLGenRunner._resolve_meshgen_binary() == str(pin)


def test_meshgen_binary_missing_is_a_loud_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("DCDTUNI_MESHGEN_BIN", raising=False)
    monkeypatch.setattr(STLGenRunner, "_MESHGEN_VENDOR_DIR", tmp_path / "no-vendor")
    monkeypatch.setattr(STLGenRunner, "_MESHGEN_DEV_BIN", tmp_path / "no-dev" / "meshgen-cli")
    with pytest.raises(RuntimeError, match="no meshgen-cli binary"):
        STLGenRunner._resolve_meshgen_binary()


def test_meshgen_binary_dev_fallback_is_found_on_this_host():
    # On the dc17 host the dev checkout build exists; where it does not, the
    # resolver must fail loudly rather than fall back to PATH.
    if STLGenRunner._MESHGEN_DEV_BIN.is_file():
        import os as _os
        if not _os.environ.get("DCDTUNI_MESHGEN_BIN") and \
                not STLGenRunner._MESHGEN_VENDOR_DIR.is_dir():
            assert STLGenRunner._resolve_meshgen_binary() == str(STLGenRunner._MESHGEN_DEV_BIN)
