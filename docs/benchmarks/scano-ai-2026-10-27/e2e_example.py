"""End-to-end check on a DCTS example room: our extraction (mesh + t1 run) -> cache -> SCANO, error vs the CFD target.

    python e2e_example.py <t1 case dir> <reference cache dir> <cfdrun> <out root> [--layout processors] [--model id]

The target is the reference cache's target.npy carried over by nearest node (cells by centroid, faces within their
patch by face centre), so the error is the paper's metric on the same room.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from backend.aircooling.ai import dcts_cache  # noqa: E402
from backend.aircooling.ai.foam_fields import boundary_values  # noqa: E402
from backend.aircooling.ai.foam_mesh import read_graph  # noqa: E402
from backend.aircooling.ai.infer import CHECKPOINTS, ScanoModel  # noqa: E402


def carry_target(g, C):
    cj = json.loads((C / 'case.json').read_text())
    names = [p['name'] for p in cj['patches']]
    rpos, rt, rp = np.load(C / 'pos.npy'), np.load(C / 'target.npy'), np.load(C / 'patch.npy')
    nc = cj['n_cells']
    tgt = np.zeros((g.pos.shape[0], 5), np.float32)
    d, i = cKDTree(rpos[:nc]).query(g.pos[:g.n_cells])
    tgt[:g.n_cells] = rt[i]
    dmax = [float(d.max())]
    for k, p in enumerate(g.patches):
        mine = np.flatnonzero(g.patch == k)
        if not mine.size:
            continue
        ref = np.flatnonzero(rp == names.index(p.name))
        d, i = cKDTree(rpos[nc + ref]).query(g.pos[g.n_cells + mine])
        tgt[g.n_cells + mine] = rt[nc + ref[i]]
        dmax.append(float(d.max()))
    return tgt, max(dmax), [p['role'] for p in cj['patches']], names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('case')
    ap.add_argument('ref')
    ap.add_argument('cfdrun')
    ap.add_argument('out')
    ap.add_argument('--layout', default='auto')
    ap.add_argument('--model', default='scano_c118_s18')
    a = ap.parse_args()
    t = {}
    t0 = time.perf_counter()
    g = read_graph(Path(a.case), a.layout)
    t['graph'] = time.perf_counter() - t0
    t0 = time.perf_counter()
    bc = boundary_values(g, '1')
    t['bc'] = time.perf_counter() - t0
    roles = dcts_cache.patch_roles(g, dcts_cache.bindings_of(json.loads(Path(a.cfdrun).read_text())))
    tgt, dmax, ref_roles, ref_names = carry_target(g, Path(a.ref))
    mism = [p.name for p, r in zip(g.patches, roles) if ref_roles[ref_names.index(p.name)] != r]
    print(f'target carried over (max match distance {dmax:.3g} m); roles differing from the reference: {mism[:5]}')
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    t0 = time.perf_counter()
    dcts_cache.write_geometry(out, g, target=tgt)
    dcts_cache.write_state(out, 'state0', g, bc, roles)
    t['write'] = time.perf_counter() - t0
    m = ScanoModel(CHECKPOINTS / a.model)
    for _ in range(2):
        r = m.predict(out, 'state0', with_error=True)
    print('timing', {k: round(v, 2) for k, v in t.items()}, 'forward', round(r['timing']['forward_s'], 2))
    print(f"{a.model}: err_mean {r['err_mean']:.4f} per field {[round(x, 4) for x in r['err']]}")


if __name__ == '__main__':
    main()
