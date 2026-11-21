"""A postprocess response must belong to the run loaded by its caller."""

import importlib.util
import io
import json
import struct
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from backend.aircooling.runner import postprocess as pp


SERVER_PATH = Path(__file__).resolve().parents[2] / "server/server.py"


class Handler:
    def __init__(self, body=None, revision=None):
        payload = json.dumps(body or {}).encode()
        self.headers = {"Content-Length": str(len(payload))}
        if revision is not None:
            self.headers["X-PP-Revision"] = revision
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = {}

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue())


@pytest.fixture
def server(monkeypatch, tmp_path):
    pv = pytest.importorskip("pyvista")
    server_dir = str(SERVER_PATH.parent)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    spec = importlib.util.spec_from_file_location("pp_revision_server_under_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    for name in ("runA", "runB"):
        (tmp_path / "log" / name / "constant" / "polyMesh").mkdir(parents=True)

    def layout(run_dir, time_value=None):
        return [Path(run_dir) / "constant" / "polyMesh"], ([100, 200] if time_value == 200 else [100])

    def read(run_dir, time_value):
        value = 301.0 if Path(run_dir).name == "runA" and time_value == 100 else 321.0
        wall = pv.Plane(i_resolution=1, j_resolution=1)
        wall.point_data["T"] = np.full(wall.n_points, value)
        interior = pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid()
        interior.point_data["T"] = np.full(interior.n_points, value)
        data = pv.MultiBlock({
            "internalMesh": interior,
            "boundary": pv.MultiBlock({"wall": wall}),
        })
        return data, {"sourceFormat": "openfoam", "decomposed": False, "timeValue": time_value}

    monkeypatch.setattr(pp, "_openfoam_result_layout", layout)
    monkeypatch.setattr(pp, "read_openfoam_case", read)
    monkeypatch.setattr(module, "CASE_DIR", tmp_path)
    cache = pp.CachedPostProcessor()
    monkeypatch.setattr(module, "_get_cached_pp", lambda: cache)
    return module, cache


def _init(server, name, time_value=None):
    module, _ = server
    body = {"runDir": f"log/{name}"}
    if time_value is not None:
        body["timeValue"] = time_value
    handler = Handler(body)
    module.handle_pp_init(handler)
    assert handler.status == 200
    return handler.json()


def test_old_tab_cannot_read_new_run_field(server):
    module, _ = server
    run_a = _init(server, "runA")
    run_b = _init(server, "runB")
    assert run_a["runId"] != run_b["runId"]
    assert run_a["timeValue"] == run_b["timeValue"] == 100
    assert run_a["revision"] != run_b["revision"]

    stale = Handler(revision=run_a["revision"])
    module.handle_pp_field(stale, "T")
    assert stale.status == 409
    assert stale.json()["code"] == "pp_revision_stale"

    current = Handler(revision=run_b["revision"])
    module.handle_pp_field(current, "T")
    assert current.status == 200
    assert struct.unpack_from("<f", current.wfile.getvalue())[0] == 321.0
    assert current.response_headers["X-PP-Revision"] == run_b["revision"]


def test_old_tab_cannot_read_new_time_of_same_run(server):
    module, _ = server
    first = _init(server, "runA", time_value=100)
    second = _init(server, "runA", time_value=200)
    assert first["runId"] == second["runId"]
    assert first["revision"] != second["revision"]
    assert (first["timeValue"], second["timeValue"]) == (100, 200)

    stale = Handler(revision=first["revision"])
    module.handle_pp_field(stale, "T")
    assert stale.status == 409
    current = Handler(revision=second["revision"])
    module.handle_pp_field(current, "T")
    assert current.status == 200
    assert struct.unpack_from("<f", current.wfile.getvalue())[0] == 321.0


def test_render_source_requires_current_frame_and_fails_closed_without_receipts(server):
    module, _ = server
    first = _init(server, "runA", time_value=100)
    second = _init(server, "runA", time_value=200)
    missing = Handler()
    module.handle_pp_render_source(missing)
    assert missing.status == 428
    stale = Handler(revision=first["revision"])
    module.handle_pp_render_source(stale)
    assert stale.status == 409
    current = Handler(revision=second["revision"])
    module.handle_pp_render_source(current)
    assert current.status == 422
    assert current.json()["code"] == "render_source_unavailable"
    assert current.json()["timeValue"] == 200
    assert isinstance(current.json()["reason"], str) and current.json()["reason"]


def test_mesh_preview_render_source_never_promotes_snapshot_key_to_receipt(server):
    module, _ = server
    poly = module.CASE_DIR / "Mesh/constant/polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text(f"fixture {name}\n")
    key = module._mesh_preview_snapshot_key(module.CASE_DIR / "Mesh", module.CASE_DIR_VERSION)
    missing = Handler()
    module.handle_mesh_preview_render_source(missing, {})
    assert missing.status == 400
    stale = Handler()
    module.handle_mesh_preview_render_source(stale, {"meshSnapshotKey": ["0" * 64]})
    assert stale.status == 409
    current = Handler()
    module.handle_mesh_preview_render_source(current, {"meshSnapshotKey": [key]})
    assert current.status == 422
    assert current.json()["code"] == "render_source_unavailable"
    assert "mesh-quality-receipt.json" in current.json()["error"]


def test_mesh_preview_binary_checks_new_snapshot_key_and_keeps_old_clients_compatible(server, monkeypatch):
    module, _ = server
    poly = module.CASE_DIR / "Mesh/constant/polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text(f"fixture {name}\n")
    key = module._mesh_preview_snapshot_key(module.CASE_DIR / "Mesh", module.CASE_DIR_VERSION)
    loader = type("Loader", (), {"load": lambda self: (b"mesh-binary", [])})()
    monkeypatch.setattr(module, "_get_mesh_preview_cache", lambda: type("Cache", (), {
        "_loader": loader, "_mesh_dir_str": str((module.CASE_DIR / "Mesh").resolve())})())

    current = Handler()
    module.handle_mesh_preview_binary(current, {"meshSnapshotKey": [key]})
    assert current.status == 200
    assert current.wfile.getvalue() == b"mesh-binary"

    (poly / "points").write_text("changed and longer fixture points\n")
    stale = Handler()
    module.handle_mesh_preview_binary(stale, {"meshSnapshotKey": [key]})
    assert stale.status == 409
    assert stale.json()["code"] == "mesh_preview_source_stale"

    changed_key = module._mesh_preview_snapshot_key(module.CASE_DIR / "Mesh", module.CASE_DIR_VERSION)
    module.CASE_DIR_VERSION += 1
    switched = Handler()
    module.handle_mesh_preview_binary(switched, {"meshSnapshotKey": [changed_key]})
    assert switched.status == 409
    assert switched.json()["code"] == "mesh_preview_source_stale"
    module.CASE_DIR_VERSION -= 1

    (poly / "owner").unlink()
    removed = Handler()
    module.handle_mesh_preview_binary(removed, {"meshSnapshotKey": [key]})
    assert removed.status == 409
    assert removed.json()["code"] == "mesh_preview_source_stale"

    legacy = Handler()
    module.handle_mesh_preview_binary(legacy, {})
    assert legacy.status == 200
    assert legacy.wfile.getvalue() == b"mesh-binary"


def test_mesh_preview_load_reports_stale_when_mesh_disappears_during_read(server, monkeypatch):
    module, _ = server
    poly = module.CASE_DIR / "Mesh/constant/polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text(f"fixture {name}\n")

    calls = 0

    def snapshot(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise FileNotFoundError("mesh removed during load")
        return "a" * 64

    loader = type("Loader", (), {"load": lambda self: (b"mesh-binary", []),
                                   "read_counts": lambda self: {"nCells": 1, "nBoundaryFaces": 1,
                                                                "nBoundarySum": 1}})()
    monkeypatch.setattr(module, "_mesh_preview_snapshot_key", snapshot)
    monkeypatch.setattr(module, "_get_mesh_preview_cache", lambda: type("Cache", (), {
        "get": lambda self, mesh_dir: loader})())

    response = Handler()
    module.handle_mesh_preview_load(response)
    assert response.status == 409
    assert response.json()["code"] == "mesh_preview_source_stale"


@pytest.mark.parametrize("endpoint", [
    "handle_pp_mesh", "handle_pp_mesh_binary", "handle_pp_field",
    "handle_pp_slice", "handle_pp_streamlines", "handle_pp_volume",
])
def test_postprocess_data_endpoints_require_revision(server, endpoint):
    module, _ = server
    _init(server, "runA")
    handler = Handler()
    route = getattr(module, endpoint)
    if endpoint == "handle_pp_field":
        route(handler, "T")
    else:
        route(handler)
    assert handler.status == 428
    assert handler.json()["code"] == "pp_revision_required"


def test_mesh_metadata_and_binary_echo_same_revision(server):
    module, _ = server
    run = _init(server, "runA")
    meta = Handler(revision=run["revision"])
    module.handle_pp_mesh(meta)
    assert meta.status == 200
    assert meta.json()["revision"] == run["revision"]
    binary = Handler(revision=run["revision"])
    module.handle_pp_mesh_binary(binary)
    assert binary.status == 200
    assert binary.response_headers["X-PP-Revision"] == run["revision"]
    assert binary.wfile.getvalue()

    _init(server, "runB")
    stale_binary = Handler(revision=run["revision"])
    module.handle_pp_mesh_binary(stale_binary)
    assert stale_binary.status == 409
    assert stale_binary.json()["code"] == "pp_revision_stale"


def test_run_switch_waits_for_inflight_volume_and_cannot_mix_frames(server, monkeypatch):
    module, cache = server
    run_a = _init(server, "runA")
    entered = threading.Event()
    release = threading.Event()
    switch_done = threading.Event()
    original_volume = cache.get_volume

    def paused_volume(*args, **kwargs):
        entered.set()
        assert release.wait(3), "test did not release the volume request"
        return original_volume(*args, **kwargs)

    monkeypatch.setattr(cache, "get_volume", paused_volume)
    volume = Handler({"field": "T", "resolution": 16}, revision=run_a["revision"])
    switch = Handler({"runDir": "log/runB"})
    volume_thread = threading.Thread(target=module.handle_pp_volume, args=(volume,))
    switch_thread = threading.Thread(target=lambda: (module.handle_pp_init(switch), switch_done.set()))
    volume_thread.start()
    try:
        assert entered.wait(3)
        switch_thread.start()
        assert not switch_done.wait(0.1), "run switch replaced a frame in active use"
    finally:
        release.set()
        volume_thread.join(3)
        if switch_thread.ident is not None:
            switch_thread.join(3)

    assert volume.status == 200
    header_length = struct.unpack_from("<I", volume.wfile.getvalue())[0]
    header = json.loads(volume.wfile.getvalue()[4:4 + header_length].rstrip(b"\0"))
    assert header["scalarRange"] == [301.0, 301.0]
    assert switch.status == 200
    assert switch.json()["revision"] != run_a["revision"]


def test_cached_init_reuses_revision_and_failed_init_invalidates_it(server, monkeypatch):
    module, cache = server
    first = _init(server, "runA")
    second = _init(server, "runA")
    assert second["cached"] is True
    assert second["revision"] == first["revision"]

    def broken_read(*_args):
        raise RuntimeError("reader failed")

    monkeypatch.setattr(pp, "read_openfoam_case", broken_read)
    failure = Handler({"runDir": "log/runB"})
    module.handle_pp_init(failure)
    assert failure.status == 500
    stale = Handler(revision=first["revision"])
    module.handle_pp_field(stale, "T")
    assert stale.status == 409
    assert cache.multi_block is None


def test_ai_start_rejects_stale_revision_before_starting_worker(server, monkeypatch):
    module, _ = server
    run_a = _init(server, "runA")
    _init(server, "runB")

    class Worker:
        def start(self, **_kwargs):
            pytest.fail("stale AI start must not reach the worker")

    monkeypatch.setattr(module, "_get_pp_ai_worker", lambda: Worker())
    stale = Handler({
        "modelPath": "synthetic.state",
        "casePath": str((module.CASE_DIR / "log/runA").resolve()),
    }, revision=run_a["revision"])
    module.handle_pp_ai_start(stale)
    assert stale.status == 409
    assert stale.json()["code"] == "pp_revision_stale"


def test_ai_start_accepts_selected_run_relative_to_case_directory(server, monkeypatch):
    module, _ = server
    run = _init(server, "runA")

    class Worker:
        def start(self, **_kwargs):
            return {"status": {"ready": True}, "result": {}}

        def recent_stderr(self):
            return []

    monkeypatch.setattr(module, "_get_pp_ai_worker", lambda: Worker())
    start = Handler({"modelPath": "synthetic.state", "casePath": "log/runA"},
                    revision=run["revision"])
    module.handle_pp_ai_start(start)
    assert start.status == 200
    assert start.json()["revision"] == run["revision"]
    assert start.json()["predictionSource"] == "independent_h5_graph"
    assert start.json()["timeConditioned"] is False


def test_ai_infer_switched_run_rejects_late_result_without_injecting(server, monkeypatch, tmp_path):
    module, cache = server
    run_a = _init(server, "runA")
    output = tmp_path / "inference.npz"
    np.savez(output, AI_U=np.ones((1, 3)), AI_p=np.ones(1), AI_T=np.full(1, 333.0),
             AI_region_mask=np.ones(1))
    entered = threading.Event()
    release = threading.Event()

    class Worker:
        def start(self, **_kwargs):
            return {"status": {"ready": True}, "result": {}}

        def infer(self, **_kwargs):
            entered.set()
            assert release.wait(3)
            return {"result": {"outputPath": str(output)}}

        def recent_stderr(self):
            return []

    monkeypatch.setattr(module, "_get_pp_ai_worker", lambda: Worker())
    started = Handler({"modelPath": "synthetic.state",
                       "casePath": str((module.CASE_DIR / "log/runA").resolve())},
                      revision=run_a["revision"])
    module.handle_pp_ai_start(started)
    assert started.status == 200
    inference = Handler({"box": {"min": [0, 0, 0], "max": [1, 1, 1]}},
                        revision=run_a["revision"])
    thread = threading.Thread(target=module.handle_pp_ai_infer, args=(inference,))
    thread.start()
    try:
        assert entered.wait(3)
        run_b = _init(server, "runB")
    finally:
        release.set()
        thread.join(3)

    assert inference.status == 409
    assert inference.json()["code"] == "pp_revision_stale"
    assert run_b["revision"] != run_a["revision"]
    assert "AI_T" not in cache._internal.cell_data


def test_ai_infer_updates_revision_after_field_injection(server, monkeypatch, tmp_path):
    module, cache = server
    run = _init(server, "runA")
    output = tmp_path / "inference.npz"
    np.savez(output, AI_U=np.ones((1, 3)), AI_p=np.ones(1), AI_T=np.full(1, 333.0),
             AI_region_mask=np.ones(1))

    class Worker:
        def start(self, **_kwargs):
            return {"status": {"ready": True}, "result": {}}

        def infer(self, **_kwargs):
            return {"result": {"outputPath": str(output)}}

        def recent_stderr(self):
            return []

    monkeypatch.setattr(module, "_get_pp_ai_worker", lambda: Worker())
    started = Handler({"modelPath": "synthetic.state",
                       "casePath": str((module.CASE_DIR / "log/runA").resolve())},
                      revision=run["revision"])
    module.handle_pp_ai_start(started)
    assert started.status == 200
    inference = Handler({"box": {"min": [0, 0, 0], "max": [1, 1, 1]}},
                        revision=run["revision"])
    module.handle_pp_ai_infer(inference)
    assert inference.status == 200
    newer = inference.json()["revision"]
    assert inference.json()["predictionSource"] == "independent_h5_graph"
    assert inference.json()["timeConditioned"] is False
    assert newer and newer != run["revision"]
    assert "AI_T" in cache._internal.cell_data
    old = Handler(revision=run["revision"])
    module.handle_pp_field(old, "T")
    assert old.status == 409
    current = Handler(revision=newer)
    module.handle_pp_field(current, "T")
    assert current.status == 200


def test_case_directory_switch_revokes_old_postprocess_revision(server, tmp_path):
    module, _ = server
    run_a = _init(server, "runA")
    another_case = tmp_path / "another-case"
    (another_case / "log/runA").mkdir(parents=True)
    switch = Handler({"caseDir": str(another_case), "createIfMissing": False})
    module.handle_set_case_dir(switch)
    assert switch.status == 200

    stale = Handler(revision=run_a["revision"])
    module.handle_pp_field(stale, "T")
    assert stale.status == 409
    assert stale.json()["code"] == "pp_revision_stale"


def test_case_switch_during_init_cannot_publish_usable_old_case_data(server, monkeypatch, tmp_path):
    module, _ = server
    entered = threading.Event()
    release = threading.Event()
    original_read = pp.read_openfoam_case

    def paused_read(*args):
        entered.set()
        assert release.wait(3)
        return original_read(*args)

    monkeypatch.setattr(pp, "read_openfoam_case", paused_read)
    initial = Handler({"runDir": "log/runA"})
    another_case = tmp_path / "another-case"
    another_case.mkdir()
    switch = Handler({"caseDir": str(another_case), "createIfMissing": False})
    init_thread = threading.Thread(target=module.handle_pp_init, args=(initial,))
    switch_thread = threading.Thread(target=module.handle_set_case_dir, args=(switch,))
    init_thread.start()
    try:
        assert entered.wait(3)
        switch_thread.start()
    finally:
        release.set()
        init_thread.join(3)
        if switch_thread.ident is not None:
            switch_thread.join(3)

    assert not init_thread.is_alive() and not switch_thread.is_alive()
    assert switch.status == 200
    assert initial.status in (200, 409)
    if initial.status == 200:
        stale = Handler(revision=initial.json()["revision"])
        module.handle_pp_field(stale, "T")
        assert stale.status == 409


def test_init_rejects_run_outside_selected_case(server, tmp_path):
    module, _ = server
    outside = tmp_path.parent / "outside-run"
    outside.mkdir(exist_ok=True)
    handler = Handler({"runDir": str(outside)})
    module.handle_pp_init(handler)
    assert handler.status == 400
    assert handler.json()["code"] == "pp_run_outside_case"


def test_init_keeps_explicit_vtm_import_inside_selected_case(server, tmp_path):
    module, _ = server
    pv = pytest.importorskip("pyvista")
    interior = pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid()
    interior.point_data["T"] = np.full(interior.n_points, 301.0)
    boundary = pv.MultiBlock({"wall": interior.extract_surface()})
    exported = pv.MultiBlock({"internal": interior, "boundary": boundary})
    vtm = tmp_path / "VTK" / "case_100.vtm"
    vtm.parent.mkdir()
    exported.save(vtm)

    handler = Handler({"runDir": str(vtm)})
    module.handle_pp_init(handler)
    assert handler.status == 200
    assert handler.json()["sourceFormat"] == "vtm"
    assert handler.json()["timeValue"] is None
    assert handler.json()["revision"]


def test_scan_run_id_matches_the_run_loaded_by_init(server, tmp_path):
    module, _ = server
    name = "2026-09-27-12-00-00_test"
    (tmp_path / "log" / name / "constant/polyMesh").mkdir(parents=True)
    scan = Handler()
    module.handle_scan_case_status(scan)
    assert scan.status == 200
    listed = next(run for run in scan.json()["logRuns"] if run["name"] == name)

    loaded = Handler({"runDir": listed["path"]})
    module.handle_pp_init(loaded)
    assert loaded.status == 200
    assert loaded.json()["runId"] == listed["runId"]


def test_common_render_grant_route_obeys_actual_revision_and_script_import_paths(server,monkeypatch):
    module,cache=server
    first=_init(server,'runA');second=_init(server,'runB')
    missing=Handler({'browserAccepted':True});module.handle_pp_render_grant(missing)
    assert missing.status==428
    stale=Handler({'browserAccepted':True},revision=first['revision']);module.handle_pp_render_grant(stale)
    assert stale.status==409
    monkeypatch.delenv('DCDTUNI_OV_GRANT_KEY',raising=False)
    unavailable=Handler({},revision=second['revision']);module.handle_pp_render_grant(unavailable)
    assert unavailable.status==503
    monkeypatch.setenv('DCDTUNI_OV_GRANT_KEY','CONTROLLED key, never actual launch-key 1234567890')
    unproven=Handler({'schema':'dcdtuni.canonical-render-request/v1','scene':{'projectUid':'browser-invented','projectContentSha256':'a'*64,'revision':1},'displayGeneration':1,'intent':{}},revision=second['revision'])
    module.handle_pp_render_grant(unproven)
    assert unproven.status==422
    assert unproven.json()['code']=='verified_display_unavailable'
    assert unproven.json()['revision']==second['revision']
    assert cache.current_identity()=={key:second[key]for key in ['runId','timeValue','revision']}


def test_common_grant_handler_signs_controlled_actual_cpp_response_products_without_a_second_numeric_algorithm(server,monkeypatch):
    # The existing fixture reader is controlled PyVista data, not a native solve
    # or source receipt. Only this harness replaces the independent verifier.
    import canonical_render_grant as signing
    from backend.aircooling.runner.numeric_product_receipts import NumericProductStore
    from backend.tests.test_canonical_render_packet import SOURCE,PROOF
    module,cache=server;selected=_init(server,'runA');identity=cache.current_identity()
    store=NumericProductStore(identity,'1'*64);cache._numeric_product_store=store
    binary,meta=cache.get_mesh();field,info=cache.get_field('T')
    mesh_token=store.capture('mesh',{},binary,meta,session=identity,native_input_sha256='1'*64)['token']
    field_token=store.capture('field',{'field':'T'},field,info,session=identity,native_input_sha256='1'*64)['token']
    descriptor={'schema':'dcdtuni.verified-air-result-source/v2','source':SOURCE,'postprocess':identity,'nativeInputSha256':'1'*64,'boundaryProvenanceByPatch':{'wall':PROOF},'fieldUnits':{'T':'K'},'runConvergence':{'verdict':'NOT_CONVERGED','receiptSha256':'2'*64}}
    monkeypatch.setattr(signing,'verify_render_source',lambda **_:descriptor)
    monkeypatch.setenv('DCDTUNI_OV_GRANT_KEY','CONTROLLED key, never actual launch-key 1234567890')
    request={'schema':'dcdtuni.canonical-render-request/v1','scene':{'projectUid':SOURCE['projectUid'],'projectContentSha256':SOURCE['projectContentSha256'],'revision':1},'displayGeneration':1,'intent':{'schema':'dcdtuni.common-render-intent/v1','revision':1,'postprocess':identity,'source':SOURCE,'meshToken':mesh_token,'surface':{'fieldToken':field_token,'field':'T','lut':{'palette':'viridis','range':[290,310],'opacity':1},'hidden':[],'visible':True},'slices':[],'streamlines':[],'volume':None}}
    handler=Handler(request,revision=identity['revision']);module.handle_pp_render_grant(handler)
    assert handler.status==201,handler.json()
    result=handler.json();raw=Path(result['grant']['payload']['path']).read_bytes();packet=json.loads(raw)
    assert packet['runConvergence']['verdict']=='NOT_CONVERGED'
    assert result['packetReceipt']['postprocess']==identity
    assert [layer['values']for layer in packet['layers']if layer['kind']=='surfaceField']==[[301.0]*meta[0]['nPoints']]

def test_actual_route_receipt_header_is_small_and_preserves_owned_numeric_frame(server,monkeypatch):
 from backend.aircooling.runner.numeric_product_receipts import NumericProductStore
 module,cache=server;session=_init(server,'runA');identity={key:session[key]for key in ['runId','timeValue','revision']};store=NumericProductStore(identity,'a'*64)
 # Controlled native input/source pin only. The numeric CPP response is real.
 monkeypatch.setattr(cache,'capture_numeric_product',lambda kind,request,payload,metadata:store.capture(kind,request,payload,metadata,session=identity,native_input_sha256='a'*64))
 response=Handler(revision=session['revision']);module.handle_pp_mesh_binary(response)
 assert response.status==200
 raw=response.response_headers['X-PP-Product'];receipt=json.loads(raw)
 assert len(raw)<1024 and set(receipt)=={'schema','token','kind','postprocess','payloadSha256','nativeInputSha256'}
 assert receipt['kind']=='mesh' and receipt['postprocess']=={key:session[key]for key in ['runId','timeValue','revision']}
 stored=store.get(receipt['token'],session=receipt['postprocess'],native_input_sha256='a'*64,kind='mesh')
 assert stored.payload==response.wfile.getvalue()

def test_injected_custom_field_invalidates_native_authority_without_breaking_diagnostic_pp(server):
 from backend.aircooling.runner.numeric_product_receipts import NumericProductStore
 module,cache=server;session=_init(server,'runA');identity={key:session[key]for key in ['runId','timeValue','revision']}
 owned=NumericProductStore(identity,'a'*64);cache._numeric_product_store=owned;cache._native_input_snapshot={'sha256':'a'*64}
 cache.inject_internal_fields({'T':np.full(cache._internal.n_cells,321,dtype=np.float32)})
 assert cache._numeric_product_store is None and cache._native_input_snapshot is None
 assert cache._identity()['revision']!=identity['revision']
 with pytest.raises(ValueError,match='older'):owned.capture('field',{},b'',{},session=identity,native_input_sha256='a'*64)
 with pytest.raises(ValueError,match='pinned native'):cache.capture_numeric_product('field',{},b'',{})
 assert cache.get_available_fields()


def test_real_mesh_preview_handler_receives_source_region_v2_and_only_a_signed_mesh_grant(server,monkeypatch,tmp_path):
    from backend.tests.test_canonical_mesh_preview import fixture as mesh_fixture,KEY
    module,_=server
    values=mesh_fixture(tmp_path/'actual_private_mesh_copy')
    case=values[0];monkeypatch.setattr(module,'CASE_DIR',case);monkeypatch.setenv('DCDTUNI_OV_GRANT_KEY',KEY)
    key=module._mesh_preview_snapshot_key(case/'Mesh',module.CASE_DIR_VERSION)
    source=Handler();module.handle_mesh_preview_render_source(source,{'meshSnapshotKey':[key]})
    assert source.status==200 and source.json()['schema']=='dcdtuni.verified-mesh-preview-source/v2'
    descriptor=source.json();request=values[-1];request['intent'].update(source=descriptor['source'],meshSnapshotKey=key,meshPayloadSha256=descriptor['meshPayloadSha256'])
    granted=Handler(request);module.handle_mesh_preview_render_grant(granted)
    assert granted.status==200 and granted.json()['grant']['payload']['kind']=='rendererMesh'
    assert granted.json()['grant']['payload']['source']['run']is None
    assert granted.json()['grant']['payload']['timeSeconds']is None
    assert 'runId'not in granted.json()['grant']['payload']
