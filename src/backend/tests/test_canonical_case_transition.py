"""Real server routes + real CPP state under controlled two-thread interleavings.

Only the OpenFOAM reader and final source/grant producer are substituted. No
server, solver or GPU is launched. Every lock is a real Lock/RLock. A watchdog
on the CPP RLock makes the original deadlock fail with cleanup rather than hang.
"""
import importlib.util
import io
import json
import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
from backend.aircooling.runner import postprocess as pp

ROOT = Path(os.environ.get('CASE_LOCK_SOURCE_ROOT', Path(__file__).resolve().parents[3])).resolve()

class Handler:
    def __init__(self, body=None, revision=None):
        raw = json.dumps(body or {}).encode()
        self.headers = {'Content-Length': str(len(raw))}
        if revision is not None:
            self.headers['X-PP-Revision'] = revision
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
    def send_response(self, code): self.status = code
    def send_header(self, name, value): pass
    def end_headers(self): pass
    def json(self): return json.loads(self.wfile.getvalue())

class PauseBody(io.BytesIO):
    def __init__(self, raw, entered, release):
        super().__init__(raw); self.entered = entered; self.release = release
    def read(self, *args):
        self.entered.set()
        if not self.release.wait(2):
            raise TimeoutError('controlled request body was not released')
        return super().read(*args)

class SignalBody(io.BytesIO):
    def __init__(self, raw, entered):
        super().__init__(raw); self.entered = entered
    def read(self, *args):
        self.entered.set(); return super().read(*args)

class WatchdogRLock:
    def __init__(self, real, pinned, release, setter_attempt):
        self.real = real; self.pinned = pinned; self.release = release
        self.setter_attempt = setter_attempt; self.route_paused = False
    def __enter__(self):
        name = threading.current_thread().name
        if name == 'case-switch': self.setter_attempt.set()
        if not self.real.acquire(timeout=0.8):
            raise TimeoutError('CPP RLock watchdog: opposite CASE/CPP lock order')
        if name == 'pinned-route' and not self.route_paused:
            self.route_paused = True; self.pinned.set()
            if not self.release.wait(2):
                self.real.release()
                raise TimeoutError('controlled pinned route was not released')
        return self
    def __exit__(self, *exc): self.real.release()

@pytest.fixture
def fixture(monkeypatch, tmp_path):
    pv = pytest.importorskip('pyvista')
    server_path = ROOT/'src/server/server.py'
    sys.path.insert(0, str(server_path.parent))
    spec = importlib.util.spec_from_file_location('canonical_case_transition_server', server_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    old = tmp_path/'old'; new = tmp_path/'new'
    for case in [old, new]: (case/'log/run/constant/polyMesh').mkdir(parents=True)
    def layout(run_dir, time_value=None):
        return [Path(run_dir)/'constant/polyMesh'], [100]
    def read(run_dir, time_value):
        wall = pv.Plane(i_resolution=1,j_resolution=1)
        wall.point_data['T'] = np.full(wall.n_points, 301. if old in Path(run_dir).parents else 321.)
        interior = pv.ImageData(dimensions=(2,2,2)).cast_to_unstructured_grid()
        interior.point_data['T'] = np.full(interior.n_points, 301.)
        return pv.MultiBlock({'internalMesh':interior,'boundary':pv.MultiBlock({'wall':wall})}), {'sourceFormat':'openfoam','decomposed':False,'timeValue':time_value}
    monkeypatch.setattr(pp, '_openfoam_result_layout', layout)
    monkeypatch.setattr(pp, 'read_openfoam_case', read)
    monkeypatch.setattr(module, 'CASE_DIR', old)
    monkeypatch.setattr(module, 'CASE_DIR_VERSION', 11)
    monkeypatch.setattr(module, 'CASE_DIR_LOCK', threading.Lock())
    cache = pp.CachedPostProcessor()
    monkeypatch.setattr(module, '_get_cached_pp', lambda:cache)
    init = Handler({'runDir':'log/run','timeValue':100})
    module.handle_pp_init(init)
    assert init.status == 200, init.json()
    yield module, cache, old, new, init.json()
    cache.invalidate()


def _thread(name, call, errors):
    def run():
        try: call()
        except BaseException as exc: errors.append((name, exc))
    worker = threading.Thread(name=name,target=run,daemon=False)
    worker.start(); return worker


def _finish(workers, releases):
    for event in releases: event.set()
    for worker in workers: worker.join(3)
    assert all(not worker.is_alive() for worker in workers), 'two-thread regression leaked a worker'

@pytest.mark.parametrize('route_name', ['handle_pp_render_source','handle_pp_render_grant'])
def test_pinned_source_or_grant_and_case_switch_do_not_deadlock(fixture, monkeypatch, route_name):
    module, cache, old, new, identity = fixture
    pinned = threading.Event(); release = threading.Event(); attempt = threading.Event()
    monkeypatch.setattr(cache, '_state_lock', WatchdogRLock(cache._state_lock,pinned,release,attempt))
    import verified_render_source as source
    import canonical_render_grant as grant
    def verified(**kw):
        assert kw['case_dir'] == old
        return {'controlledLockProbe':True}
    def issue(**kw):
        assert kw['case_dir'] == old and kw['current_case']()
        assert kw['session'] == cache.current_identity()
        return {'controlledLockProbe':True}
    monkeypatch.setattr(source, 'verify_render_source', verified)
    monkeypatch.setattr(grant, 'issue_canonical_render_grant', issue)
    monkeypatch.setenv('DCDTUNI_OV_GRANT_KEY','0'*64)
    route = Handler({'displayGeneration':1}, identity['revision'])
    switch = Handler({'caseDir':str(new),'createIfMissing':False})
    workers = []; errors = []
    try:
        workers.append(_thread('pinned-route',lambda:getattr(module,route_name)(route),errors))
        assert pinned.wait(2), 'real require_revision did not pin the CPP lock'
        workers.append(_thread('case-switch',lambda:module.handle_set_case_dir(switch),errors))
        assert attempt.wait(2), 'real setter did not attempt the CPP lock'
        release.set()
    finally: _finish(workers,[release])
    assert not errors, repr(errors)
    assert route.status == (201 if route_name.endswith('grant') else 200)
    assert switch.status == 200
    with module.CASE_DIR_LOCK:
        assert (module.CASE_DIR,module.CASE_DIR_VERSION) == (new,12)
    assert cache.multi_block is None
    assert not hasattr(route,'_pp_identity')


def test_case_version_and_invalidate_are_atomic_and_new_init_survives(fixture, monkeypatch):
    module, cache, old, new, identity = fixture
    invalidating = threading.Event(); release = threading.Event(); body_read = threading.Event()
    original = cache.invalidate
    def paused_invalidate():
        # init() re-enters invalidate() too; pause only the older setter thread.
        if threading.current_thread().name == 'case-switch':
            invalidating.set()
            if not release.wait(2): raise TimeoutError('invalidate probe was not released')
        original()
    monkeypatch.setattr(cache,'invalidate',paused_invalidate)
    switch = Handler({'caseDir':str(new),'createIfMissing':False})
    init = Handler({'runDir':'log/run','timeValue':100})
    init.rfile = SignalBody(init.rfile.getvalue(),body_read)
    errors = []; workers = []; premature = False; initialized = threading.Event()
    def initialize_new():
        module.handle_pp_init(init)
        initialized.set()
    try:
        workers.append(_thread('case-switch',lambda:module.handle_set_case_dir(switch),errors))
        assert invalidating.wait(2)
        workers.append(_thread('new-init',initialize_new,errors))
        # read() is reached only AFTER the real route captures CASE + version.
        premature = body_read.wait(0.15)
        if premature:
            # The naive CASE-unlocked variant is allowed to fully initialize
            # the new cache before its older paused invalidate resumes.
            assert initialized.wait(2), repr(errors)
    finally: _finish(workers,[release])
    assert not errors, repr(errors)
    print('ATOMIC_PROBE', {'prematureCaseVersion':premature,'cacheReadyAfterOldInvalidate':cache.multi_block is not None,'cacheVersion':cache._case_version,'selectedVersion':module.CASE_DIR_VERSION})
    assert not premature, 'new case/version escaped before prior invalidate completed'
    assert switch.status == init.status == 200
    assert cache.run_dir == new/'log/run' and cache.multi_block is not None
    assert cache._case_version == module.CASE_DIR_VERSION == 12
    assert cache.current_identity()['revision'] == init.json()['revision']


def test_late_old_init_cannot_replace_new_case_cache(fixture):
    module, cache, old, new, identity = fixture
    parsed_old = threading.Event(); release_old = threading.Event(); fresh_done = threading.Event()
    stale = Handler({'runDir':'log/run','timeValue':100})
    stale.rfile = PauseBody(stale.rfile.getvalue(),parsed_old,release_old)
    switch = Handler({'caseDir':str(new),'createIfMissing':False})
    fresh = Handler({'runDir':'log/run','timeValue':100})
    errors = []; workers = []
    def switch_and_init():
        module.handle_set_case_dir(switch)
        module.handle_pp_init(fresh)
        fresh_done.set()
    try:
        workers.append(_thread('old-init',lambda:module.handle_pp_init(stale),errors))
        assert parsed_old.wait(2), 'old request did not capture its original case/version'
        workers.append(_thread('new-init',switch_and_init,errors))
        assert fresh_done.wait(2), repr(errors)
        assert fresh.status == 200
        new_identity = cache.current_identity()
        release_old.set()
    finally: _finish(workers,[release_old])
    assert not errors, repr(errors)
    assert stale.status == 409 and stale.json()['code'] == 'pp_case_changed'
    assert cache.run_dir == new/'log/run', 'late old init replaced the new case cache'
    assert cache.current_identity() == new_identity
    assert cache._case_version == module.CASE_DIR_VERSION == 12


def test_same_case_set_is_idempotent_and_keeps_loaded_revision(fixture, monkeypatch):
    module, cache, old, new, identity = fixture
    count = []; original = cache.invalidate
    def record(): count.append(True); original()
    monkeypatch.setattr(cache,'invalidate',record)
    handler = Handler({'caseDir':str(old),'createIfMissing':False})
    module.handle_set_case_dir(handler)
    assert handler.status == 200 and not count
    assert module.CASE_DIR == old and module.CASE_DIR_VERSION == 11
    assert cache.current_identity() == {key:identity[key] for key in ['runId','timeValue','revision']}
