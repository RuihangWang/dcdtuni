"""CPU USD diagnostic identity regression; no rendered pixel acceptance."""
import copy
import json
from types import SimpleNamespace
import pytest
from test_native_grid_geometry_receipt import fixture
from native_receipt_bridge import display_diagnostics as d


def setup_read(primitives=818):
    stage, coordinator, _, full_view, _ = fixture()
    view = {k: copy.deepcopy(full_view[k]) for k in ('camera', 'selectedUids', 'timeSeconds')}
    context = {'stage': stage, 'sceneRevision': 2, 'projectHash': 'a' * 64,
               'displayGeneration': 1, 'uidPaths': {}, 'primitives': primitives}
    api = SimpleNamespace(stage=stage, camera_path=d.CAMERA, time=0,
                          hydra_engine='rtx', render_mode='RealTimePathTracing')
    request = json.dumps({'requestId': 'actual-count', 'diagnostics': {
        'profile': d.PROFILE, 'expectedSceneRevision': 2, 'expectedProjectHash': 'a' * 64}})
    sampler = lambda *args, **kw: {'phase': 'pending', 'reason': 'stream-layout-unresolved',
                                   'observations': {'layoutProof': 'unresolved'}}
    def run(read_view=lambda: copy.deepcopy(view)):
        return d.ReadbackController(True).read(request, lambda: context, read_view,
            lambda: (api, None), {}, lambda: None, sampler)
    return coordinator, context, view, run


@pytest.mark.parametrize('count', [0, 818])
def test_current_owned_count_is_preserved_by_native_serializer(count):
    coordinator, _, _, run = setup_read(count)
    try:
        reply = run()
        assert reply['ok'] is True and reply['primitives'] == count
        assert reply['sceneRevision'] == 2 and reply['projectHash'] == 'a' * 64
        assert reply['diagnostics']['pixelVerified'] is False
    finally:
        coordinator.close()


def test_count_change_during_getters_refuses_same_hash_revision():
    coordinator, context, view, run = setup_read()
    reads = 0
    def changed():
        nonlocal reads
        reads += 1
        if reads == 2:
            context['primitives'] += 1
        return copy.deepcopy(view)
    try:
        reply = run(changed)
        assert reply['ok'] is False and reply['error'] == 'bounded-display:source-context-changed'
        assert 'primitives' not in reply
    finally:
        coordinator.close()


@pytest.mark.parametrize('invalid', [-1, True, 1.5, 9007199254740992])
def test_invalid_owned_count_is_not_an_accepted_view(invalid):
    coordinator, _, _, run = setup_read(invalid)
    try:
        reply = run()
        assert reply['ok'] is False and reply['error'] == 'bounded-display:invalid-public-value'
    finally:
        coordinator.close()
