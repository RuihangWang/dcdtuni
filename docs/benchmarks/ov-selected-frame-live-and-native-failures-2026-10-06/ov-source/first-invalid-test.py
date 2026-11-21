"""Selected OV frames, with fake converters only (no OpenFOAM/Kit)."""
import io
import json
import math
import pytest
import server
import ov_result_dataset as ovd
from test_ov_result_dataset import make_run, make_decomposed_run, fake_foam, run_id

def complete_processors(run):
    for proc in ovd.processor_dirs(run):
        for name in ("points", "faces", "owner", "neighbour", "boundary"):
            (proc / "constant/polyMesh" / name).write_text(name)

def test_explicit_serial_frame_is_not_latest_and_its_grant_binds_time(tmp_path):
    run = make_run(tmp_path, times=("0", "1e-02", "2"))
    calls = []
    out = ovd.prepare_ensight(run, fake_foam(calls), time_value=.01)
    assert out["time"] == "1e-02"
    assert not (calls[0] / "2").exists()
    assert (calls[0] / "1e-02/T").resolve() == run / "1e-02/T"
    grant = ovd.issue_grant(out, run, run_id(run), "g"*64, now=1000)
    assert ovd._grant_module().verify(grant, "g"*64, now=1001, roots=[str(tmp_path)])["time"] == "1e-02"

def test_explicit_common_mpi_frame_preserves_each_directory_spelling(tmp_path):
    run = make_decomposed_run(tmp_path, n=2, times=("0", "1e-02", "2"))
    complete_processors(run)
    (run / "processor1/1e-02").rename(run / "processor1/0.01")
    seen = []
    def convert(mirror, nprocs=None):
        seen.append(nprocs)
        assert (mirror / "processor1/1e-02/T").resolve() == run / "processor1/0.01/T"
        return fake_foam([])(mirror)
    assert ovd.prepare_ensight(run, convert, time_value=.01)["time"] == "1e-02"
    assert seen == [2]

def test_specific_time_missing_one_rank_does_not_fall_back(tmp_path):
    run = make_decomposed_run(tmp_path, n=2)
    complete_processors(run)
    (run / "processor1/5/T").unlink()
    with pytest.raises(ovd.DatasetError) as error:
        ovd.prepare_ensight(run, lambda *a, **k: pytest.fail("must not convert"), time_value=5)
    assert error.value.status == 409

@pytest.mark.parametrize("value", [True, False, None, "1", math.inf, math.nan, 0, -1])
def test_explicit_time_requires_positive_finite_numeric(tmp_path, value):
    with pytest.raises(ovd.DatasetError) as error:
        ovd.prepare_ensight(make_run(tmp_path), fake_foam([]), time_value=value)
    assert error.value.status == 400

def test_numeric_alias_ambiguity_is_refused(tmp_path):
    run = make_run(tmp_path, times=("1", "1.0", "2"))
    with pytest.raises(ovd.DatasetError) as error:
        ovd.prepare_ensight(run, fake_foam([]), time_value=1)
    assert error.value.code == "frame_time_ambiguous"

def test_specific_partition_frame_can_be_newer_than_retained_root(tmp_path):
    run = make_decomposed_run(tmp_path, n=2, times=("5",))
    complete_processors(run)
    mesh = run / "constant/polyMesh"; mesh.mkdir()
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (mesh / name).write_text(name)
    (run / "1").mkdir(); (run / "1/T").write_text("old")
    seen = []
    def convert(mirror, nprocs=None):
        seen.append(nprocs); return fake_foam([])(mirror)
    assert ovd.prepare_ensight(run, convert, time_value=5)["time"] == "5"
    assert seen == [2]

def test_change_while_converter_runs_is_not_signed_or_cached(tmp_path):
    run = make_run(tmp_path)
    def convert(mirror):
        (run / "1/T").write_text("replaced during conversion")
        return fake_foam([])(mirror)
    with pytest.raises(ovd.DatasetError) as error:
        ovd.prepare_ensight(run, convert, time_value=1)
    assert error.value.code == "dataset_source_changed"

def call_route(monkeypatch, tmp_path, body, convert=None):
    run = make_run(tmp_path)
    monkeypatch.setattr(server, "CASE_DIR", tmp_path)
    monkeypatch.setenv("DCDTUNI_OV_GRANT_KEY", "g"*64)
    import backend.aircooling.runner.postprocess as pp
    monkeypatch.setattr(pp, "_foam_source_prefix", lambda: "")
    monkeypatch.setattr(ovd, "foam_to_ensight", lambda _: convert or fake_foam([]))
    replies = []
    monkeypatch.setattr(server, "_send_json", lambda _h, code, value: replies.append((code,value)))
    raw = json.dumps({**body, "runId": run_id(run)}).encode()
    handler = type("Handler", (), {"headers":{"Content-Length":str(len(raw))}, "rfile":io.BytesIO(raw)})()
    server.handle_ov_result_dataset(handler)
    return replies[-1]

def test_route_legacy_latest_and_specific_frame(monkeypatch, tmp_path):
    assert call_route(monkeypatch, tmp_path / "a", {})[1]["time"] == "2"
    code, data = call_route(monkeypatch, tmp_path / "b", {"time":1})
    assert code == 200 and data["time"] == "1" and data["grant"]["payload"]["time"] == "1"

@pytest.mark.parametrize("body", [{"time":True},{"time":"1"},{"time":None},{"time":1,"path":"/x"}])
def test_route_rejects_invalid_or_extra_selectors(monkeypatch, tmp_path, body):
    assert call_route(monkeypatch, tmp_path, body)[0] == 400

def test_case_switch_during_conversion_refuses_old_grant(monkeypatch, tmp_path):
    def convert(mirror):
        monkeypatch.setattr(server, "CASE_DIR", tmp_path / "new-case")
        return fake_foam([])(mirror)
    code, data = call_route(monkeypatch, tmp_path, {}, convert)
    assert code == 409 and data["code"] == "case_changed"

def test_run_listing_uses_complete_actual_result_times(monkeypatch, tmp_path):
    run = make_decomposed_run(tmp_path, n=2, times=("0","5","10"))
    complete_processors(run); (run / "processor1/10/T").unlink()
    monkeypatch.setattr(server, "CASE_DIR", tmp_path)
    replies=[]
    monkeypatch.setattr(server, "_send_json", lambda _h, code, value: replies.append((code,value)))
    server.handle_pp_runs(object())
    data=replies[-1][1]["runs"][0]
    assert data["timesteps"] == [5] and data["runId"] == run_id(run)
