"""OV display dataset preparation: the server picks a run from ITS case/log,
converts it with foamToEnsight inside a derived mirror (never writing into the
run's own time directories), and signs a display grant for Kit.

The OpenFOAM call is injected, so these tests run on CPU without OpenFOAM."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ov_result_dataset as ovd  # noqa: E402

KEY = "g" * 64


def make_run(root: Path, name="2026-09-27-03-31-00_Box", times=("0", "1", "2"), uniform=False):
    run = root / "log" / name
    (run / "constant" / "polyMesh").mkdir(parents=True)
    (run / "system").mkdir()
    for f in ("points", "faces", "owner", "neighbour", "boundary"):
        (run / "constant" / "polyMesh" / f).write_text(f)
    for t in times:
        (run / t).mkdir()
        (run / t / "T").write_text(f"T at {t}")
        (run / t / "U").write_text(f"U at {t}")
    if uniform:
        (run / times[-1] / "uniform").mkdir()
        (run / times[-1] / "uniform" / "time").write_text("value 2;")
    return run


def fake_foam(calls):
    """Stand-in foamToEnsight: records the mirror it was pointed at and writes
    the EnSight layout the real tool writes (<case>/EnSight/<name>.case + data)."""
    def run(mirror: Path):
        calls.append(mirror)
        out = mirror / "EnSight"
        (out / "data" / "00000002").mkdir(parents=True)
        (out / "data" / "00000002" / "T").write_text("t")
        (out / f"{mirror.name}.case").write_text("FORMAT\ntype: ensight gold\n")
        return 0, "End"
    return run


def run_id(run: Path) -> str:
    return hashlib.sha256(str(run.resolve()).encode("utf-8")).hexdigest()


def test_find_run_matches_pp_runs_identity_and_rejects_unknown(tmp_path):
    run = make_run(tmp_path)
    assert ovd.find_run(tmp_path, run_id(run)) == run.resolve()
    with pytest.raises(ovd.DatasetError) as e:
        ovd.find_run(tmp_path, "0" * 64)
    assert e.value.status == 404
    with pytest.raises(ovd.DatasetError) as e:
        ovd.find_run(tmp_path, "../../etc")
    assert e.value.status == 400


def test_latest_field_time_is_numeric_not_lexical(tmp_path):
    run = make_run(tmp_path, times=("0", "2", "10"))
    assert ovd.latest_field_time(run)[0] == "10"


def make_decomposed_run(root: Path, n=3, times=("0", "5", "10"), name="par"):
    """A partitioned dcCart run: results only under processor*/ (never reconstructed)."""
    run = root / "log" / name
    (run / "system").mkdir(parents=True)
    (run / "system" / "decomposeParDict").write_text(f"numberOfSubdomains {n};")
    (run / "constant").mkdir()
    for i in range(n):
        proc = run / f"processor{i}"
        (proc / "constant" / "polyMesh").mkdir(parents=True)
        (proc / "constant" / "polyMesh" / "points").write_text(f"p{i}")
        for t in times:
            (proc / t).mkdir()
            (proc / t / "T").write_text(f"T {i} {t}")
    return run


def test_decomposed_only_run_is_still_a_conflict_for_the_serial_reader(tmp_path):
    run = make_decomposed_run(tmp_path, n=1, times=("5",))
    with pytest.raises(ovd.DatasetError) as e:
        ovd.latest_field_time(run)
    assert e.value.status == 409 and e.value.code == "run_decomposed"


def test_a_decomposed_run_converts_in_parallel_through_a_per_processor_mirror(tmp_path):
    run = make_decomposed_run(tmp_path, n=3)
    before = sorted(str(p.relative_to(run)) for p in run.rglob("*"))
    calls, ranks = [], []

    def convert(mirror, nprocs=None):
        ranks.append(nprocs)
        return fake_foam(calls)(mirror)
    out = ovd.prepare_ensight(run, convert)
    assert ranks == [3] and out["time"] == "10" and out["fields"] == ["T"]
    mirror = calls[0]
    for i in range(3):
        assert os.path.realpath(mirror / f"processor{i}" / "10" / "T") == str((run / f"processor{i}" / "10" / "T").resolve())
        assert os.path.realpath(mirror / f"processor{i}" / "constant") == str((run / f"processor{i}" / "constant").resolve())
        assert (mirror / f"processor{i}" / "10" / "uniform" / "time").is_file()
    assert os.path.realpath(mirror / "system") == str((run / "system").resolve())
    after = sorted(str(p.relative_to(run)) for p in run.rglob("*") if ovd.MIRROR_DIRNAME not in p.parts)
    assert after == before
    # cached until a processor's field changes
    assert ovd.prepare_ensight(run, convert)["cached"] is True
    (run / "processor2" / "10" / "T").write_text("re-solved")
    assert ovd.prepare_ensight(run, convert)["cached"] is False


def test_the_latest_time_must_exist_on_every_processor(tmp_path):
    run = make_decomposed_run(tmp_path, n=2)
    for f in (run / "processor1" / "10").iterdir():
        f.unlink()
    assert ovd.latest_decomposed_time(run)[0] == "5"
    for t in ("0", "5"):
        for f in (run / "processor1" / t).iterdir():
            f.unlink()
    with pytest.raises(ovd.DatasetError) as e:
        ovd.latest_decomposed_time(run)
    assert e.value.status == 409 and e.value.code == "run_decomposed_incomplete"


def test_the_production_converter_launches_mpi_only_for_a_decomposed_mirror(tmp_path, monkeypatch):
    import subprocess
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append(argv[-1]) or subprocess.CompletedProcess(argv, 0, "", ""))
    convert = ovd.foam_to_ensight("PREFIX && ")
    convert(tmp_path / "m")
    convert(tmp_path / "m", nprocs=8)
    assert seen[0].startswith("PREFIX && foamToEnsight ") and "-parallel" not in seen[0]
    assert seen[1].startswith("PREFIX && mpirun -np 8 foamToEnsight ") and seen[1].endswith("-no-boundary -parallel")


def test_prepare_builds_a_mirror_and_never_touches_the_run(tmp_path):
    run = make_run(tmp_path)
    before = sorted(str(p.relative_to(run)) for p in run.rglob("*"))
    calls = []
    out = ovd.prepare_ensight(run, fake_foam(calls))
    assert out["time"] == "2" and out["cached"] is False
    mirror = calls[0]
    assert mirror.parent == run.resolve() / ovd.MIRROR_DIRNAME
    # The fixture had no uniform/time (foamToEnsight silently skips such a
    # time); the mirror supplies one — the source time dir stays untouched.
    assert (mirror / "2" / "uniform" / "time").is_file()
    assert not (run / "2" / "uniform").exists()
    assert os.path.realpath(mirror / "2" / "T") == str((run / "2" / "T").resolve())
    assert os.path.realpath(mirror / "constant") == str((run / "constant").resolve())
    assert Path(out["path"]).is_file() and out["path"].endswith(".case")
    after = sorted(str(p.relative_to(run)) for p in run.rglob("*")
                   if ovd.MIRROR_DIRNAME not in p.parts)
    assert after == before


def test_existing_uniform_time_is_linked_not_rewritten(tmp_path):
    run = make_run(tmp_path, uniform=True)
    calls = []
    ovd.prepare_ensight(run, fake_foam(calls))
    link = calls[0] / "2" / "uniform"
    assert os.path.realpath(link) == str((run / "2" / "uniform").resolve())


def test_prepare_is_cached_until_a_source_file_changes(tmp_path):
    run = make_run(tmp_path)
    calls = []
    first = ovd.prepare_ensight(run, fake_foam(calls))
    second = ovd.prepare_ensight(run, fake_foam(calls))
    assert second["cached"] is True and second["path"] == first["path"] and len(calls) == 1
    (run / "2" / "T").write_text("T at 2, re-solved and longer")
    third = ovd.prepare_ensight(run, fake_foam(calls))
    assert third["cached"] is False and third["path"] != first["path"] and len(calls) == 2


def test_empty_conversion_is_an_error_not_a_blank_dataset(tmp_path):
    run = make_run(tmp_path)

    def empty(mirror):
        (mirror / "EnSight" / "data").mkdir(parents=True)
        (mirror / "EnSight" / f"{mirror.name}.case").write_text("FORMAT\n")
        return 0, "skip ... missing file"
    with pytest.raises(ovd.DatasetError) as e:
        ovd.prepare_ensight(run, empty)
    assert e.value.code == "ensight_conversion_empty"
    with pytest.raises(ovd.DatasetError) as e:
        ovd.prepare_ensight(run, lambda m: (3, "FOAM FATAL ERROR"))
    assert e.value.code == "ensight_conversion_failed"


def test_issue_grant_is_verifiable_by_the_kit_side(tmp_path):
    run = make_run(tmp_path)
    prepared = ovd.prepare_ensight(run, fake_foam([]))
    grant = ovd.issue_grant(prepared, run, run_id(run), KEY, now=1000.0)
    verify = ovd._grant_module().verify
    payload = verify(grant, KEY, now=1001.0, roots=[str(tmp_path)])
    assert payload["run"] == run.name and payload["time"] == "2"
    assert payload["kind"] == "dataset" and payload["runId"] == run_id(run)
    assert payload["expiresAt"] - payload["issuedAt"] <= ovd._grant_module().MAX_TTL_SECONDS
    with pytest.raises(ovd.DatasetError) as e:
        ovd.issue_grant(prepared, run, run_id(run), "", now=1000.0)
    assert e.value.status == 503
