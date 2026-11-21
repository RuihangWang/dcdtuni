"""TTA vs whole on a work directory: time, peak memory, rows per forward, and the difference between the two."""
import json, sys, time, os
from pathlib import Path
import numpy as np
os.environ['SCANO_DCTS_RAM_CACHE'] = '1'
from backend.aircooling.ai import adapters, pipeline
from backend.aircooling.ai.models import card
import torch
base = Path(sys.argv[1]); modes = sys.argv[2].split(',')
c = card(os.environ.get('MODEL', 'scano_c118_s18')); a = adapters.for_card(c); m = a.load_model(Path(c['run_dir']))
r = pipeline.open_room(base, a)
x = r.initial()
r.predict(m, x, mode='tta', passes=1) if 'warm' in modes else None
out = {}
for spec in modes:
    if spec == 'warm': continue
    mode, _, R = spec.partition(':')
    t = time.perf_counter()
    try:
        from backend.aircooling.ai.infer import anchor_scale_for
        res = r.predict(m, x, mode=mode, passes=int(R or 1), progress=print, anchor_scale=anchor_scale_for(r.graph.n_cells + r.graph.n_boundary))
    except torch.OutOfMemoryError as e:
        print(spec, 'OOM', str(e)[:120]); torch.cuda.empty_cache(); continue
    out[spec] = res['pred']
    print(spec, json.dumps({'s': round(time.perf_counter() - t, 2), 'fwd': round(res['timing']['forward_s'], 2),
          'peakGiB': round(res['timing']['peak_gpu_bytes'] / 2**30, 2), 'tta': res['tta'],
          'reserved_after_GiB': round(torch.cuda.memory_reserved() / 2**30, 2)}), flush=True)
n = r.graph.n_cells
ks = list(out)
for k in ks[1:]:
    d = out[k][:n] - out[ks[0]][:n]
    print(f'{k} vs {ks[0]}: |dT| mean {np.abs(d[:,4]).mean():.3f} K p99 {np.percentile(np.abs(d[:,4]),99):.3f} K; '
          f'|dU| mean {np.linalg.norm(d[:,:3],axis=1).mean():.3f} m/s')
