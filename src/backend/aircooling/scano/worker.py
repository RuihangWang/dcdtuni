"""Long-lived AI-solve worker: one process, one GPU, the loaded checkpoints kept on it, each run by its card's
adapter (models.card, adapters.for_card).

    python -m backend.aircooling.scano.worker        (started by the server, CUDA_VISIBLE_DEVICES set to one GPU UUID)

Protocol: one JSON request per stdin line, one JSON reply per stdout line, progress lines on stderr (the server
copies them into the job log). Requests:
  {"cmd": "ping"}
  {"cmd": "memory"}   the GPU memory as this process sees it: {"freeBytes" (driver free + its own unused cache),
        "allocatedBytes", "reservedBytes", "totalBytes"}
  {"cmd": "load", "model": <checkpoint id>, "warm_base": <work directory>?}   load (and keep) a checkpoint, warm
        it up with one forward on that room's first iteration (else on the adapter's synthetic input)
  {"cmd": "run", "case_dir", "base", "cfdrun_path", "model", "ingest": bool, "write_run": bool (default true),
   "persist_state": bool, "with_error": bool, "roi": {"min": [x, y, z], "max": [x, y, z]}?,
   "inference": "auto" | "whole" | "tta", "passes": R}
        ingest base/t1 when asked (a new first iteration), take the input of the cfdrun from the adapter's room
        held in memory, predict, save the prediction (base/pred/<state>__<model>.npy, for injection
        into post-processing) and, when asked, write the result run directory; with "roi" the values outside the
        box are not kept (NaN: the result is that region only); reply {"ok", "predPath",
        "runDir"?, "state", "timing", "changedPatches", "nCells", "inference" {...}, ...}. The deployment
        (memory.choose): "auto" keeps the whole-room forward when its estimate fits the GPU's free memory, else TTA,
        and an auto whole-room forward that still runs out of memory is repeated as TTA; "whole" and "tta" are
        what they say (passes: TTA repetitions).
Every reply carries "ok"; a failure replies {"ok": false, "error", "traceback"} and the worker stays up.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


WARMUP_DIR = Path(__file__).resolve().parents[4] / '.codex-tmp' / 'scano-warmup'


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _free_bytes(device):
    """Free memory this process can use on its GPU: the driver's free plus what its own allocator holds unused."""
    import torch
    if device.type != 'cuda':
        return None
    free, _total = torch.cuda.mem_get_info(device)
    return int(free + torch.cuda.memory_reserved(device) - torch.cuda.memory_allocated(device))


def _gib(b) -> str:
    return f'{b / 2**30:.1f} GiB' if b is not None else '?'


def _predict(r, m, x, req, adapter, base, n_nodes, log):
    """Room.predict with the deployment of the request (module docstring); returns (res, inference record)."""
    import torch
    from backend.aircooling.scano import memory
    request = str(req.get('inference') or memory.AUTO)
    passes = max(1, int(req.get('passes') or 1))
    free = _free_bytes(m.device)
    n_boundary = int(r.graph.n_boundary)
    pick = memory.choose(adapter.card, n_nodes, free, request, base, n_boundary)
    chunk = max(1, int(m.tta_chunk() * pick['chunk_share']))
    log(f"[SCANO] inference {pick['mode']}" + (f' x{passes}' if pick['mode'] == 'tta' else '')
        + f" ({request}: {pick['reason']}); estimated peak {_gib(pick['estimate_bytes'])}, free {_gib(free)}"
        + (f"; whole room would need {_gib(pick['whole_estimate_bytes'])}" if pick.get('whole_estimate_bytes') else '')
        + (f"; chunks of {chunk} pivots" if pick['mode'] == 'tta' else ''))
    from backend.aircooling.scano.infer import anchor_scale_for
    scale = anchor_scale_for(n_nodes)
    deploy = {'mode': pick['mode'], 'passes': passes, 'chunk': chunk, 'progress': log, 'anchor_scale': scale}
    fallback = None
    try:
        res = r.predict(m, x, with_error=bool(req.get('with_error')), **deploy)
    except torch.OutOfMemoryError as exc:
        if not (request == memory.AUTO and pick['mode'] == 'whole'):
            raise
        fallback = f'whole room ran out of memory ({str(exc).splitlines()[0][:160]})'
    if fallback:
        torch.cuda.empty_cache()                 # outside the handler: its traceback held the forward's tensors
        log(f'[SCANO] {fallback}: repeating as TTA')
        pick = memory.choose(adapter.card, n_nodes, _free_bytes(m.device), 'tta', base, n_boundary)
        chunk = max(1, int(m.tta_chunk() * pick['chunk_share']))
        res = r.predict(m, x, with_error=bool(req.get('with_error')), mode='tta', passes=passes, chunk=chunk,
                        progress=log, anchor_scale=scale)
    tta = res.get('tta') or {}
    peak = res['timing'].get('peak_gpu_bytes')
    rows = tta.get('max_rows') or n_nodes
    memory.record_peak(base, res['mode'], peak, rows, tta.get('chunk'))
    info = {'request': request, 'mode': res['mode'], 'reason': fallback or pick['reason'], 'passes': tta.get('passes'),
            'chunk': tta.get('chunk'), 'forwards': tta.get('forwards'), 'minCount': tta.get('min_count'),
            'estimateBytes': pick['estimate_bytes'], 'freeBytes': free, 'peakBytes': peak, 'nNodes': n_nodes,
            'anchorScale': tta.get('anchor_scale'),
            'seed': 'fixed'}
    return res, info


def main() -> None:
    import warnings
    # the vendored data class wraps read-only memory maps (never written): not a finding for the user's log
    warnings.filterwarnings('ignore', message='The given NumPy array is not writable')
    out = os.fdopen(os.dup(1), 'w', buffering=1)
    os.dup2(2, 1)                                   # library prints go to the log, not the reply channel
    os.environ['SCANO_DCTS_RAM_CACHE'] = '1'        # scano-dcts-v1 keeps boundary states in the data class's RAM
    import numpy as np
    from backend.aircooling.scano import adapters, pipeline
    from backend.aircooling.scano.models import card as model_card
    models = {}                                     # id -> (adapter, model)
    rooms = {}                                      # work directory -> room

    def model(mid: str):
        if mid not in models:
            c = model_card(mid)
            adapter = adapters.for_card(c)
            m = adapter.load_model(Path(c['run_dir']))
            models[mid] = (adapter, m)
            _log(f'[SCANO] model {mid} ({adapter.id}) loaded on {m.device} in {m.load_seconds:.1f} s')
        return models[mid]

    def room(base: Path, adapter):
        r = rooms.get(str(base))
        if r is None:
            r = rooms[str(base)] = pipeline.open_room(base, adapter)
        return r

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            cmd = req.get('cmd')
            if cmd == 'ping':
                import torch
                reply = {'ok': True, 'device': str(torch.cuda.get_device_name(0)) if torch.cuda.is_available()
                         else 'cpu', 'loaded': sorted(models)}
            elif cmd == 'memory':
                import torch
                if torch.cuda.is_available():
                    dev = torch.device('cuda')
                    _free, total = torch.cuda.mem_get_info(dev)
                    reply = {'ok': True, 'freeBytes': _free_bytes(dev), 'totalBytes': int(total),
                             'allocatedBytes': int(torch.cuda.memory_allocated(dev)),
                             'reservedBytes': int(torch.cuda.memory_reserved(dev))}
                else:
                    reply = {'ok': True, 'freeBytes': None}
            elif cmd == 'load':
                adapter, m = model(req['model'])
                if not getattr(m, 'warmed', False):
                    # the first CUDA forward of a process costs about ten seconds more than the next ones; any input
                    # pays it: the case's room when there is one, else the adapter's synthetic input
                    t0 = time.perf_counter()
                    warm = req.get('warm_base')
                    how = 'synthetic'
                    if warm:
                        from backend.aircooling.scano import memory
                        r = room(Path(warm), adapter)
                        n = int(r.graph.n_cells + r.graph.n_boundary)
                        if memory.choose(adapter.card, n, _free_bytes(m.device), memory.AUTO,
                                         Path(warm), int(r.graph.n_boundary))['mode'] == 'whole':
                            r.predict(m, r.initial())
                            how = 'room'
                        else:                    # a room too large for one forward: TTA runs, the warm-up need not
                            _log(f'[SCANO] warm-up: the room ({n} nodes) runs as TTA; warming up on the synthetic '
                                 'input instead')
                    if how == 'synthetic':
                        adapter.warm_up(m, WARMUP_DIR)
                    m.warmed = True
                    _log(f"[SCANO] model {req['model']} warmed up in {time.perf_counter() - t0:.1f} s ({how} input)")
                reply = {'ok': True, 'model': req['model'], 'adapter': adapter.id, 'device': str(m.device),
                         'matmul': getattr(m, 'matmul', None)}
            elif cmd == 'run':
                t0 = time.perf_counter()
                case_dir, base = Path(req['case_dir']), Path(req['base'])
                cfdrun = json.loads(Path(req['cfdrun_path']).read_text())
                adapter, m = model(req['model'])
                timing = {}
                if req.get('ingest'):
                    pipeline.ingest_t1(base, cfdrun, adapter, log=_log)
                    timing['ingest_s'] = time.perf_counter() - t0
                    stale = rooms.pop(str(base), None)
                    if stale is not None:
                        stale.forget()
                t1 = time.perf_counter()
                fresh = str(base) not in rooms
                r = room(base, adapter)
                if fresh:
                    timing['room_s'] = time.perf_counter() - t1
                x, changed = r.state(cfdrun)
                if changed:
                    _log(f'[SCANO] boundary values: first iteration with {len(changed)} server / ACU patches rewritten')
                timing['state_s'] = time.perf_counter() - t1
                t2 = time.perf_counter()
                n_nodes = int(r.graph.n_cells + r.graph.n_boundary)
                if req.get('persist_state'):               # the benchmark tools read the model input from disk
                    res = adapter.predict_persisted(m, base, cfdrun, r.graph, with_error=bool(req.get('with_error')),
                                                    mode=str(req.get('inference') or 'whole'),
                                                    passes=int(req.get('passes') or 1))
                    inference = {'request': req.get('inference') or 'whole', 'mode': res['mode']}
                else:
                    res, inference = _predict(r, m, x, req, adapter, base, n_nodes, _log)
                timing['predict_s'] = time.perf_counter() - t2
                timing['forward_s'] = res['timing']['forward_s']
                t3 = time.perf_counter()
                pred, roi = res['pred'], None
                if req.get('roi'):
                    pred, roi = pipeline.restrict_to_roi(r.graph, pred, req['roi'])
                    _log(f"[SCANO] ROI: {roi['cells']} of {r.graph.n_cells} cells, {roi['faces']} boundary faces kept")
                warnings = pipeline.compatibility(r.graph, cfdrun, adapter.card.get('training_ranges'))
                for w in warnings:
                    _log(f'[SCANO] outside the training data: {w}')
                record = {'format': 'dcdtuni.scano-prediction/v1', 'model': req['model'], 'adapter': adapter.id,
                          'checkpoint_sha256': adapter.card.get('checkpoint_sha256'), 'state': res['state'],
                          'base': str(base), 'changed_patches': changed, 'matmul': getattr(m, 'matmul', None),
                          'peak_gpu_bytes': res['timing']['peak_gpu_bytes'], 'device': str(m.device),
                          'fields': ['U', 'p_rgh', 'T'], 'warnings': warnings, 'roi': roi, 'inference': inference,
                          'note': 'SCANO prediction (AI); CFD remains the reference',
                          'created': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
                reply = {'ok': True, 'state': res['state'], 'warnings': warnings, 'changedPatches': changed,
                         'nCells': res['n_cells'], 'nNodes': res['n_nodes'], 'base': str(base),
                         'peakGpuBytes': res['timing']['peak_gpu_bytes'], 'err': res.get('err'), 'roi': roi,
                         'inference': inference}
                pred_dir = base / 'pred'
                pred_dir.mkdir(exist_ok=True)
                stem = (f"{res['state']}__{req['model']}" + (f"__roi-{roi['key']}" if roi else '')
                        + (f"__tta{inference.get('passes') or 1}" if res['mode'] == 'tta' else ''))
                pred_path = pred_dir / f'{stem}.npy'
                np.save(pred_path, pred)
                (pred_dir / f'{stem}.json').write_text(json.dumps(record) + '\n')
                reply['predPath'] = str(pred_path)
                if req.get('write_run', True):
                    run_dir = pipeline.write_result(base, case_dir, req['model'], pred, record, r.graph)
                    reply['runDir'] = str(run_dir)
                timing['write_s'] = time.perf_counter() - t3
                timing['total_s'] = time.perf_counter() - t0
                reply['timing'] = {k: round(v, 3) for k, v in timing.items()}
                _log(f"[SCANO] prediction ready ({reply['timing']})")
            else:
                raise ValueError(f'unknown command {cmd!r}')
        except Exception as exc:                    # noqa: BLE001 - reported to the caller, the worker stays up
            reply = {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc()}
            _log(f"[SCANO] request failed: {reply['error']}")
        if not reply.get('ok') and 'torch' in sys.modules and sys.modules['torch'].cuda.is_available():
            # the traceback held a failed forward's tensors until here: give their cached blocks back
            sys.modules['torch'].cuda.empty_cache()
        out.write(json.dumps(reply) + '\n')


if __name__ == '__main__':
    main()
