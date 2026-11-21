"""The paper's error of the whole-room forward and of TTA (1, 2, 4 passes) on the DCTS example rooms.

    CUDA_VISIBLE_DEVICES=<uuid> python eval_tta.py [--model id] [--out results.jsonl] <model_input_cache dir> ...

Each directory is a published model-input cache with its CFD target (target.npy). One JSON line per room and setting.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from backend.aircooling.ai.infer import CHECKPOINTS, ScanoModel  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('caches', nargs='+')
    ap.add_argument('--model', default='scano_c118_s18')
    ap.add_argument('--settings', default='whole,tta:1,tta:2,tta:4')
    ap.add_argument('--out')
    a = ap.parse_args()
    m = ScanoModel(CHECKPOINTS / a.model)
    out = open(a.out, 'a') if a.out else None
    for cache in a.caches:
        cache = Path(cache).resolve()
        for spec in a.settings.split(','):
            mode, _, rest = spec.partition(':')
            passes, _, scale = rest.partition('@')            # tta:1@0.25 = one pass, anchor fraction x0.25
            t0 = time.perf_counter()
            r = m.predict(cache.parent, cache.name, with_error=True, mode=mode, passes=int(passes or 1),
                          **({'anchor_scale': float(scale)} if scale else {}))
            row = {'model': a.model, 'room': cache.parent.name, 'setting': spec, 'err_mean': round(r['err_mean'], 4),
                   'err': [round(x, 4) for x in r['err']], 'forward_s': round(r['timing']['forward_s'], 2),
                   'peak_gib': round(r['timing']['peak_gpu_bytes'] / 2**30, 2) if r['timing']['peak_gpu_bytes'] else None,
                   'tta': r['tta'], 'n_nodes': r['n_nodes'], 'seconds': round(time.perf_counter() - t0, 2),
                   'device': os.environ.get('CUDA_VISIBLE_DEVICES')}
            print(json.dumps(row), flush=True)
            if out:
                out.write(json.dumps(row) + '\n')
                out.flush()


if __name__ == '__main__':
    main()
