"""Compare the boundary values extracted from an OpenFOAM t1 run with a SCANO cache's bc.npy, per role.

    python compare_bc.py <case dir> <cache dir> [--layout processors] [--time 1]

The two meshes may differ in face order (or be different meshes): faces are matched within each patch (by name) by
nearest face centre; with the same mesh every match distance is ~0.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from backend.aircooling.ai.foam_fields import boundary_values  # noqa: E402
from backend.aircooling.ai.foam_mesh import read_graph  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('case')
    ap.add_argument('cache')
    ap.add_argument('--layout', default='auto')
    ap.add_argument('--time', default='1')
    a = ap.parse_args()
    g = read_graph(Path(a.case), a.layout)
    bc = boundary_values(g, a.time)
    C = Path(a.cache)
    cj = json.loads((C / 'case.json').read_text())
    names = [p['name'] for p in cj['patches']]
    roles = [p['role'] for p in cj['patches']]
    rpos, rbc, rpatch = np.load(C / 'pos.npy'), np.load(C / 'bc.npy'), np.load(C / 'patch.npy')
    rb = rpos[cj['n_cells']:]
    match = np.full(g.n_boundary, -1)
    dist = np.zeros(g.n_boundary)
    for k, p in enumerate(g.patches):
        mine = np.flatnonzero(g.patch == k)
        if not mine.size or p.name not in names:
            continue
        ref = np.flatnonzero(rpatch == names.index(p.name))
        d, i = cKDTree(rb[ref]).query(g.pos[g.n_cells + mine])
        match[mine], dist[mine] = ref[i], d
    ok = match >= 0
    print(f'faces {g.n_boundary} matched {int(ok.sum())}, match distance max {dist[ok].max():.3g} m')
    rl = np.array([roles[names.index(g.patches[k].name)] if g.patches[k].name in names else '?' for k in g.patch])
    diff = np.abs(bc - rbc[np.maximum(match, 0)])
    print(f"{'role':14s} {'faces':>7s}  max|d| u v w p_rgh T  |  ref range p_rgh, T")
    for r in sorted(set(rl[ok])):
        m = ok & (rl == r)
        print(f'{r:14s} {int(m.sum()):7d}  ' + ' '.join(f'{x:.3g}' for x in diff[m].max(0))
              + f'  |  [{rbc[match[m], 3].min():.3g}, {rbc[match[m], 3].max():.3g}], '
              f'[{rbc[match[m], 4].min():.5g}, {rbc[match[m], 4].max():.5g}]')


if __name__ == '__main__':
    main()
