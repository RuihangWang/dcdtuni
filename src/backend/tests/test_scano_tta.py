"""AI solve deployments: the memory estimate and automatic choice (ai.memory), the room size from the mesh headers,
the TTA forward of the real checkpoint (ai.infer), and the worker's fallback from an out-of-memory whole room."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backend.aircooling.scano import memory

CARD = {'memory': {'node_bytes': 200, 'row_bytes': 8000, 'tta_rows0': 300_000, 'tta_rows_per_node': 0.1}}
GIB = 2 ** 30


def test_auto_keeps_the_whole_room_when_it_fits_and_takes_tta_when_not():
    small, large = 1_400_000, 8_700_000
    whole_small = memory.estimate(CARD, small, 'whole')
    assert whole_small == 200 * small + 8000 * small
    pick = memory.choose(CARD, small, 20 * GIB)
    assert (pick['mode'], pick['reason'], pick['fits']) == ('whole', 'fits', True)
    pick = memory.choose(CARD, large, 20 * GIB)
    assert (pick['mode'], pick['reason'], pick['fits'], pick['chunk_share']) == ('tta', 'whole does not fit', True, 1.0)
    assert pick['estimate_bytes'] == 200 * large + 8000 * (300_000 + 0.1 * large)
    assert pick['whole_estimate_bytes'] > 20 * GIB
    # what the user asked for is kept, even when it does not fit
    assert memory.choose(CARD, large, 20 * GIB, 'whole')['fits'] is False
    assert memory.choose(CARD, small, 20 * GIB, 'tta')['mode'] == 'tta'
    # no free-memory reading (the CPU): auto keeps the whole room
    assert memory.choose(CARD, large, None)['mode'] == 'whole'
    with pytest.raises(ValueError):
        memory.choose(CARD, small, GIB, 'fast')


def test_tta_chunks_shrink_to_fit_and_say_when_nothing_does():
    n = 8_700_000
    full = memory.estimate(CARD, n, 'tta')
    pick = memory.choose(CARD, n, int(full * memory.MARGIN) - 10 ** 8)
    assert pick['mode'] == 'tta' and pick['chunk_share'] < 1 and pick['fits']
    pick = memory.choose(CARD, n, GIB)
    assert pick['chunk_share'] == memory.MIN_CHUNK_SHARE and pick['fits'] is False


def test_a_measured_peak_replaces_the_estimate(tmp_path):
    memory.record_peak(tmp_path, 'whole', 5 * GIB, 1_400_000, None)
    memory.record_peak(tmp_path, 'tta', None, 1, 1)                 # nothing measured (CPU): nothing recorded
    assert memory.measured(tmp_path) == {'whole': {'peak_bytes': 5 * GIB, 'rows': 1_400_000, 'chunk': None}}
    assert memory.estimate(CARD, 1_400_000, 'whole', tmp_path) == 5 * GIB
    assert memory.choose(CARD, 1_400_000, 6 * GIB, base=tmp_path)['mode'] == 'whole'


def test_shipped_cards_carry_their_memory_fit():
    from backend.aircooling.scano.models import list_models
    cards = [c for c in list_models() if not c.get('error')]
    assert cards
    for c in cards:
        m = c.get('memory') or {}
        assert ({'node_bytes', 'row_bytes', 'tta_rows0', 'tta_rows_per_node'} <= set(m)
                or {'node_bytes', 'query_row_bytes', 'query_row_bytes_per_mface', 'anchor_row_bytes'} <= set(m)), c['id']
        # a DCTS room (docs/benchmarks/scano-tta-2026-10-29): TTA well under the whole room
        assert memory.estimate(c, 1_387_892, 'tta') < 0.5 * memory.estimate(c, 1_387_892, 'whole')


def test_room_nodes_from_the_mesh_headers(tmp_path):
    from backend.aircooling.scano import pipeline
    from backend.aircooling.scano.foam_mesh import read_graph
    from backend.tests.test_scano_ai import reconstructed_case
    case = tmp_path / 'case'
    reconstructed_case(case / 'Mesh')
    graph = read_graph(case / 'Mesh')
    got = pipeline.room_nodes(case)
    assert got == {'nodes': graph.n_cells + graph.n_boundary, 'cells': graph.n_cells, 'exact': False}
    assert pipeline.room_nodes(tmp_path / 'none') is None


def _model():
    pytest.importorskip('torch')
    pytest.importorskip('torch_geometric')
    from backend.aircooling.scano.infer import CHECKPOINTS, ScanoModel
    run = CHECKPOINTS / 'scano_c118_s18'
    if not (run / 'checkpoint.pt').is_file():
        pytest.skip('checkpoint not present')
    return ScanoModel(run, 'cpu')


def test_tta_forward_covers_every_node_and_repeats_exactly(tmp_path):
    from backend.aircooling.scano.adapters.scano_dcts_v1 import WARM_STATE, ensure_warm_cache
    m = _model()
    root = ensure_warm_cache(tmp_path / 'warm')
    whole = m.predict(root, WARM_STATE)
    assert whole['mode'] == 'whole' and whole['tta'] is None
    lines = []
    a = m.predict(root, WARM_STATE, mode='tta', passes=2, chunk=3, progress=lines.append)
    b = m.predict(root, WARM_STATE, mode='tta', passes=2, chunk=3)
    n = whole['n_nodes']
    assert a['pred'].shape == whole['pred'].shape == (n, 5)
    assert np.isfinite(a['pred']).all()
    assert np.array_equal(a['pred'], b['pred'])                     # seeded: the same request, the same answer
    assert a['tta']['passes'] == 2 and a['tta']['chunk'] == 3
    assert a['tta']['forwards'] == 2 * -(-n // 3)
    assert a['tta']['min_count'] >= 2                               # every node predicted in every pass
    assert lines and lines[-1].startswith(f"[SCANO] TTA {a['tta']['forwards']}/{a['tta']['forwards']}")
    assert m.tta_chunk() == int(1_387_891 * 0.05)                   # one training draw on a training room
    with pytest.raises(ValueError):
        m.predict(root, WARM_STATE, mode='fast')


class _Room:
    """A room whose whole-room forward runs out of memory."""

    def __init__(self, oom_whole=True):
        import types
        self.calls, self.oom_whole = [], oom_whole
        self.graph = types.SimpleNamespace(n_boundary=2, n_cells=2)

    def predict(self, model, x, with_error=False, mode='whole', passes=1, chunk=None, progress=None, anchor_scale=1.0):
        import torch
        self.calls.append(mode)
        if mode == 'whole' and self.oom_whole:
            raise torch.OutOfMemoryError('CUDA out of memory. Tried to allocate 15.35 GiB.')
        return {'pred': np.zeros((4, 5)), 'mode': mode, 'timing': {'forward_s': 0.1, 'peak_gpu_bytes': 3 * GIB},
                'tta': {'passes': passes, 'chunk': chunk, 'forwards': 2, 'min_count': 1, 'max_rows': 4}
                if mode == 'tta' else None, 'n_nodes': 4, 'n_cells': 2, 'state': 's'}


class _Model:
    import types as _t
    device = _t.SimpleNamespace(type='cpu')

    def tta_chunk(self):
        return 100


class _Adapter:
    card = {'memory': CARD['memory']}


def test_worker_repeats_an_out_of_memory_auto_run_as_tta(tmp_path):
    pytest.importorskip('torch')
    from backend.aircooling.scano import worker
    room, log = _Room(), []
    res, info = worker._predict(room, _Model(), None, {'inference': 'auto', 'passes': 3}, _Adapter(), tmp_path, 4,
                                log.append)
    assert room.calls == ['whole', 'tta'] and res['mode'] == 'tta'
    assert info['mode'] == 'tta' and info['passes'] == 3 and info['reason'].startswith('whole room ran out of memory')
    assert any('repeating as TTA' in line for line in log)
    assert json.loads((tmp_path / 'memory.json').read_text())['tta']['peak_bytes'] == 3 * GIB
    # asked for the whole room: the error is the answer
    import torch
    with pytest.raises(torch.OutOfMemoryError):
        worker._predict(_Room(), _Model(), None, {'inference': 'whole'}, _Adapter(), tmp_path, 4, log.append)
    res, info = worker._predict(_Room(oom_whole=False), _Model(), None, {}, _Adapter(), tmp_path, 4, log.append)
    assert info['request'] == 'auto' and res['mode'] == 'whole'


def test_a_card_can_make_auto_prefer_tta():
    card = {**CARD, 'inference': {'auto': 'tta'}}
    pick = memory.choose(card, 1_400_000, 20 * GIB)
    assert (pick['mode'], pick['reason']) == ('tta', 'model prefers TTA')
    assert memory.choose(card, 1_400_000, 20 * GIB, 'whole')['mode'] == 'whole'      # asked for: kept
    assert memory.choose(CARD, 1_400_000, 20 * GIB)['mode'] == 'whole'


def test_a_copied_work_directory_reads_its_own_mesh(tmp_path):
    import pickle
    from backend.aircooling.scano import pipeline
    from backend.aircooling.scano.foam_mesh import FoamGraph
    old = tmp_path / 'case_a' / 'ai' / 'scano' / 'k' / 't1'
    graph = FoamGraph(n_cells=1, pos=np.zeros((1, 3)), volume=np.ones(1), normal=np.zeros((0, 3)), area=np.zeros(0),
                      kind=np.zeros(1, np.int8), patch=np.zeros(0, np.int32), edges=np.zeros((2, 0), np.int64),
                      patches=[], n_points=0,
                      rank_dirs=[old / f'processor{r}' / 'constant' / 'polyMesh' for r in range(2)])
    new = tmp_path / 'case_b' / 'ai' / 'scano' / 'k'
    new.mkdir(parents=True)
    (new / 'graph.pkl').write_bytes(pickle.dumps(graph))
    got = pipeline.load_graph(new)
    assert got.rank_dirs == [new / 't1' / f'processor{r}' / 'constant' / 'polyMesh' for r in range(2)]
    old.parent.mkdir(parents=True, exist_ok=True)
    (old.parent / 'graph.pkl').write_bytes(pickle.dumps(graph))
    assert pipeline.load_graph(old.parent).rank_dirs == graph.rank_dirs           # in place: unchanged


def test_a_float64_default_does_not_reach_the_model(tmp_path):
    # the liquid tests set torch's default dtype to float64; the model and its inputs stay float32
    import torch
    from backend.aircooling.scano.adapters.scano_dcts_v1 import WARM_STATE, ensure_warm_cache
    m = _model()
    saved = torch.get_default_dtype()
    torch.set_default_dtype(torch.float64)
    try:
        root = ensure_warm_cache(tmp_path / 'warm')
        assert m.predict(root, WARM_STATE, mode='tta', chunk=3)['pred'].shape[1] == 5
        assert m.predict(root, WARM_STATE)['pred'].dtype == np.float32
        assert torch.get_default_dtype() == torch.float64                     # the caller's setting is kept
    finally:
        torch.set_default_dtype(saved)


def test_a_chunk_out_of_memory_continues_in_smaller_chunks(tmp_path, monkeypatch):
    import torch
    from backend.aircooling.scano.adapters.scano_dcts_v1 import WARM_STATE, ensure_warm_cache
    from scano.Main import cfg_common
    m = _model()
    root = ensure_warm_cache(tmp_path / 'warm')
    real, calls = cfg_common.predict, []

    def predict(model, data, cfg, case, nodes, *a, **k):
        calls.append(int(nodes.numel()))
        if nodes.numel() >= 8 and len(calls) == 1:                 # the first, large chunk does not fit
            raise torch.OutOfMemoryError('CUDA out of memory (test)')
        return real(model, data, cfg, case, nodes, *a, **k)
    monkeypatch.setattr(cfg_common, 'predict', predict)
    lines = []
    res = m.predict(root, WARM_STATE, mode='tta', chunk=8, progress=lines.append)
    assert res['tta']['chunk_shrunk'] == 1 and res['tta']['chunk'] == 4
    assert res['tta']['min_count'] >= 1 and np.isfinite(res['pred']).all()
    assert any('continuing with 4 pivots' in line for line in lines)


def test_anchor_cap_and_the_records_law():
    from backend.aircooling.scano.infer import MIN_ANCHOR_SCALE, TRAINING_NODES, anchor_scale_for
    from backend.aircooling.scano.models import card
    assert anchor_scale_for(TRAINING_NODES) == 1.0 and anchor_scale_for(1000) == 1.0
    assert anchor_scale_for(4 * TRAINING_NODES) == pytest.approx(0.25)
    assert anchor_scale_for(100 * TRAINING_NODES) == MIN_ANCHOR_SCALE
    c = card('scano_c118_s18')
    # measured (docs/benchmarks/scano-tta-2026-10-29): DCTS_0112 TTA 3.75 GiB, Alibaba-hongchou TTA (x0.1) 14.68 GiB
    dcts = memory.estimate(c, 1_387_892, 'tta', n_boundary=490_530) / GIB
    hong = memory.estimate(c, 14_456_342, 'tta', n_boundary=4_081_287) / GIB
    assert 3.75 <= dcts * memory.MARGIN < 6 and 14.68 <= hong * memory.MARGIN < 18
    # more boundary faces, more memory per query row
    assert memory.estimate(c, 5_000_000, 'tta', n_boundary=3_000_000) > memory.estimate(c, 5_000_000, 'tta',
                                                                                          n_boundary=500_000)
