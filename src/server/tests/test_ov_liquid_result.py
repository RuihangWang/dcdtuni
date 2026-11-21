"""Liquid DAE result display grants: only bound DAE runs of the selected case,
pinned by file hash, carrying their source project; everything else refused."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ov_liquid_result as olr  # noqa: E402
from ov_result_dataset import DatasetError  # noqa: E402

KEY = "k" * 64
RID = "6096b36ee9af46dfb22552505250db62"
FIXTURE = Path(__file__).resolve().parents[2] / "frontend/shared/testdata/liquid_result_fixture.json"


def make_run(case: Path, rid=RID, result=None, source=True):
    run = case / "Liquid" / "log" / f"run-{rid}"
    run.mkdir(parents=True)
    data = json.loads(FIXTURE.read_text()) if result is None else result
    (run / "result.json").write_text(json.dumps(data))
    liquidrun = {"source_project": {"project_uid": "proj_Shinaide", "sha256": "e" * 64}} if source else {}
    (run / "input.json").write_text(json.dumps({"liquidrun": liquidrun}))
    return run


def test_grant_pins_hash_source_project_and_verifies_as_liquid_kind(tmp_path):
    run = make_run(tmp_path)
    found = olr.find_liquid_run(tmp_path, RID)
    payload = olr.prepare_grant_payload(found, RID)
    assert payload["resultSha256"] == hashlib.sha256((run / "result.json").read_bytes()).hexdigest()
    assert payload["projectUid"] == "proj_Shinaide" and payload["projectContentSha256"] == "e" * 64
    assert payload["samples"] == 3 and payload["solver"] == "differentiable_newton_dae"
    grant = olr.issue_grant(payload, KEY, now=1000.0)
    out = olr._grant_module().verify(grant, KEY, now=1001.0, roots=[str(tmp_path)], kind="liquidResult")
    assert out["runId"] == RID
    with pytest.raises(ValueError, match="kind"):  # not replayable as a dataset load
        olr._grant_module().verify(grant, KEY, now=1001.0, roots=[str(tmp_path)])


def test_bad_ids_unknown_runs_and_path_tricks_are_refused(tmp_path):
    make_run(tmp_path)
    for bad in ("../etc", "x" * 32, RID.upper(), RID + "0"):
        with pytest.raises(DatasetError) as e:
            olr.find_liquid_run(tmp_path, bad)
        assert e.value.status == 400
    with pytest.raises(DatasetError) as e:
        olr.find_liquid_run(tmp_path, "0" * 32)
    assert e.value.status == 404


def test_legacy_hybrid_results_are_refused_with_the_missing_keys(tmp_path):
    run = make_run(tmp_path, result={"converged": True, "time_s": [0], "rack_summaries": []})
    with pytest.raises(DatasetError) as e:
        olr.prepare_grant_payload(run, RID)
    assert e.value.status == 422 and "object_bindings" in e.value.missing


def test_runs_without_source_project_or_failed_are_refused(tmp_path):
    run = make_run(tmp_path, source=False)
    with pytest.raises(DatasetError) as e:
        olr.prepare_grant_payload(run, RID)
    assert e.value.status == 422 and e.value.missing == ["source_project"]
    shutil.rmtree(tmp_path / "Liquid")
    failed = json.loads(FIXTURE.read_text())
    failed["status"] = "failed"
    run = make_run(tmp_path, result=failed)
    with pytest.raises(DatasetError) as e:
        olr.prepare_grant_payload(run, RID)
    assert e.value.status == 409


def test_missing_key_is_503(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(DatasetError) as e:
        olr.issue_grant(olr.prepare_grant_payload(run, RID), "", now=1.0)
    assert e.value.status == 503
