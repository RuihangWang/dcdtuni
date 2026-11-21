"""A wind result receipt binds the files that the native reader will open."""

import gzip
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.air_run_result_receipt import (
    build_air_run_result_receipt,
    verify_air_run_result_receipt,
)


def _field(name: str, *, dimensions: str | None = None, object_name: str | None = None) -> bytes:
    scalar = name == "T"
    dimensions = dimensions or ("[0 0 0 1 0 0 0]" if scalar else "[0 1 -1 0 0 0 0]")
    object_name = object_name or name
    value = "300" if scalar else "(0.1 0 0)"
    return (
        "FoamFile\n{\n"
        f"    format ascii; class {'volScalarField' if scalar else 'volVectorField'}; object {object_name};\n"
        "}\n"
        f"dimensions {dimensions};\n"
        f"internalField uniform {value};\n"
        "boundaryField {}\n"
    ).encode()


def _mesh(root: Path, tag: str) -> Path:
    poly = root / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_bytes(f"{tag}/{name}\n".encode())
    return poly


def _case(tmp_path):
    case = tmp_path / "Shinaide"
    model = case / "model"
    model.mkdir(parents=True)
    cfdrun = model / "executed.cfdrun"
    cfdrun.write_text(json.dumps({
        "runId": "air-2026-09-27",
        "sourceProject": {"contentHash": "a" * 64},
    }))
    run = case / "log" / "2026-09-27-12-00-00_Shinaide"
    run.mkdir(parents=True)
    return case, cfdrun, run


def _write_time(root: Path, name: str, *, compressed=False):
    time_dir = root / name
    time_dir.mkdir(parents=True)
    for field in ("T", "U"):
        path = time_dir / (field + (".gz" if compressed else ""))
        data = _field(field)
        path.write_bytes(gzip.compress(data) if compressed else data)
    return time_dir


def _build(case, cfdrun, run, time=100):
    return build_air_run_result_receipt(
        case_dir=case, cfdrun_path=cfdrun, run_dir=run, time_value=time,
    )


def _verify(receipt, case, cfdrun, run):
    return verify_air_run_result_receipt(
        receipt, case_dir=case, cfdrun_path=cfdrun, run_dir=run,
    )


def test_serial_receipt_binds_exact_cfdrun_mesh_and_t_u_bytes(tmp_path):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run, "original")
    _write_time(run, "100")
    receipt = _build(case, cfdrun, run)

    assert receipt["format"] == "air-run-result-receipt/v1"
    assert receipt["caseName"] == "Shinaide"
    assert receipt["runRelativePath"] == "log/2026-09-27-12-00-00_Shinaide"
    assert receipt["cfdrunRelativePath"] == "model/executed.cfdrun"
    assert receipt["timeValue"] == 100
    assert receipt["meshLayout"] == "serial"
    assert len(receipt["meshFiles"]) == 5
    assert [item["path"] for item in receipt["fieldFiles"]] == ["100/T", "100/U"]
    assert receipt["fieldFiles"][0]["dimensions"] == [0, 0, 0, 1, 0, 0, 0]
    assert receipt["fieldFiles"][1]["dimensions"] == [0, 1, -1, 0, 0, 0, 0]
    _verify(receipt, case, cfdrun, run)

    cfdrun.write_text(cfdrun.read_text() + "\n")
    with pytest.raises(ValueError, match="receipt.*mismatch"):
        _verify(receipt, case, cfdrun, run)


@pytest.mark.parametrize("change", ["mesh", "T", "U"])
def test_receipt_rejects_later_mesh_or_field_mutation(tmp_path, change):
    case, cfdrun, run = _case(tmp_path)
    poly = _mesh(run, "original")
    time_dir = _write_time(run, "100")
    receipt = _build(case, cfdrun, run)

    target = poly / "faces" if change == "mesh" else time_dir / change
    target.write_bytes(target.read_bytes() + b"\nchanged")
    with pytest.raises(ValueError, match="receipt.*mismatch"):
        _verify(receipt, case, cfdrun, run)


def test_partitioned_receipt_uses_all_ranks_and_numeric_time_aliases(tmp_path):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run / "processor0", "rank0")
    _mesh(run / "processor1", "rank1")
    _write_time(run / "processor0", "100")
    _write_time(run / "processor1", "1e+02", compressed=True)
    receipt = _build(case, cfdrun, run)

    assert receipt["meshLayout"] == "partitioned"
    assert len(receipt["meshFiles"]) == 10
    assert [item["path"] for item in receipt["fieldFiles"]] == [
        "processor0/100/T", "processor0/100/U",
        "processor1/1e+02/T.gz", "processor1/1e+02/U.gz",
    ]
    _verify(receipt, case, cfdrun, run)
    (run / "processor1/1e+02/U.gz").write_bytes(gzip.compress(_field("U") + b"\n"))
    with pytest.raises(ValueError, match="receipt.*mismatch"):
        _verify(receipt, case, cfdrun, run)


def test_receipt_follows_postprocessor_partition_selection_when_root_is_old(tmp_path):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run, "old-root")
    _write_time(run, "0")
    for rank in (0, 1):
        _mesh(run / f"processor{rank}", f"rank{rank}")
        _write_time(run / f"processor{rank}", "100")

    receipt = _build(case, cfdrun, run)
    assert receipt["meshLayout"] == "partitioned"
    assert len(receipt["meshFiles"]) == 10
    _verify(receipt, case, cfdrun, run)


def test_receipt_requires_both_fields_at_same_time_on_every_rank(tmp_path):
    case, cfdrun, run = _case(tmp_path)
    for rank in (0, 1):
        _mesh(run / f"processor{rank}", f"rank{rank}")
        _write_time(run / f"processor{rank}", "100")
    (run / "processor1/100/U").unlink()
    (run / "solver.log").write_text("End\nSolve complete\n")
    with pytest.raises(ValueError, match="U.*missing"):
        _build(case, cfdrun, run)


@pytest.mark.parametrize("bad_time", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_receipt_rejects_nonfinite_or_initial_time(tmp_path, bad_time):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run, "mesh")
    _write_time(run, "100")
    with pytest.raises(ValueError, match="timeValue"):
        _build(case, cfdrun, run, bad_time)


@pytest.mark.parametrize("which", ["cfdrun", "run"])
def test_receipt_rejects_cross_case_paths(tmp_path, which):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run, "mesh")
    _write_time(run, "100")
    other = tmp_path / "Other" / "model"
    other.mkdir(parents=True)
    outsider = other / "external.cfdrun" if which == "cfdrun" else other
    if which == "cfdrun":
        outsider.write_bytes(cfdrun.read_bytes())
        cfdrun = outsider
    else:
        run = outsider
    with pytest.raises(ValueError, match="within.*case|run.*log"):
        _build(case, cfdrun, run)


@pytest.mark.parametrize("field,kwargs", [
    ("T", {"dimensions": "[0 1 -1 0 0 0 0]"}),
    ("U", {"dimensions": "[0 0 0 1 0 0 0]"}),
    ("T", {"object_name": "U"}),
])
def test_receipt_rejects_wrong_field_physical_header(tmp_path, field, kwargs):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run, "mesh")
    time_dir = _write_time(run, "100")
    (time_dir / field).write_bytes(_field(field, **kwargs))
    with pytest.raises(ValueError, match="field.*(dimensions|object)"):
        _build(case, cfdrun, run)


def test_receipt_rejects_ambiguous_numeric_time_and_field_aliases(tmp_path):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run, "mesh")
    _write_time(run, "100")
    _write_time(run, "1e+02")
    with pytest.raises(ValueError, match="ambiguous.*time"):
        _build(case, cfdrun, run)
    (run / "1e+02/T").unlink()
    (run / "1e+02/U").unlink()
    (run / "1e+02").rmdir()
    (run / "100/T.gz").write_bytes(gzip.compress(_field("T")))
    with pytest.raises(ValueError, match="ambiguous.*field"):
        _build(case, cfdrun, run)


def test_partition_receipt_rejects_symlinked_time_from_another_rank(tmp_path):
    case, cfdrun, run = _case(tmp_path)
    for rank in (0, 1):
        _mesh(run / f"processor{rank}", f"rank{rank}")
    source = _write_time(run / "processor0", "100")
    (run / "processor1/100").symlink_to(source, target_is_directory=True)

    with pytest.raises(ValueError, match="linked|symlink"):
        _build(case, cfdrun, run)


def test_receipt_allows_parent_alias_above_the_case_boundary(tmp_path):
    real_base = tmp_path / "real"
    case, cfdrun, run = _case(real_base)
    _mesh(run, "mesh")
    _write_time(run, "100")
    alias = tmp_path / "alias"
    alias.symlink_to(real_base, target_is_directory=True)

    aliased_cfdrun = alias / "Shinaide/model/executed.cfdrun"
    aliased_run = alias / "Shinaide/log/2026-09-27-12-00-00_Shinaide"
    receipt = _build(case, aliased_cfdrun, aliased_run)
    assert receipt["cfdrunRelativePath"] == "model/executed.cfdrun"
    _verify(receipt, case, aliased_cfdrun, aliased_run)


def test_receipt_rejects_unknown_or_tampered_fields(tmp_path):
    case, cfdrun, run = _case(tmp_path)
    _mesh(run, "mesh")
    _write_time(run, "100")
    receipt = _build(case, cfdrun, run)
    receipt["accepted"] = True
    with pytest.raises(ValueError, match="receipt.*mismatch"):
        _verify(receipt, case, cfdrun, run)
