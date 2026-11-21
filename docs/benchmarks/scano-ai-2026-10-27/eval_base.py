"""Error of a SCANO prediction on a pipeline base against a reference room's CFD target carried onto its mesh.

    python eval_base.py <base dir> <state> <reference cache dir> [--model id]

<state> "t1" is the first iteration's own boundary state (bc_t1.npy), written to <base>/cache/t1/ when missing (the
worker keeps request states in memory only).
Builds <base>/eval_cache/ (the base's geometry with target.npy = the reference target carried over by nearest node:
cells by centroid, boundary faces within their patch by face centre; the state's files linked) and runs the model
with the paper's error. Also reports the dataset-form boundary values against the reference bc.npy per role.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from e2e_example import carry_target  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from backend.aircooling.ai import pipeline  # noqa: E402
from backend.aircooling.ai.infer import CHECKPOINTS, ScanoModel  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('base')
    ap.add_argument('state')
    ap.add_argument('ref')
    ap.add_argument('--model', default='scano_c118_s18')
    a = ap.parse_args()
    base, ref = Path(a.base), Path(a.ref)
    g = pipeline.load_graph(base)
    tgt, dmax, ref_roles, ref_names = carry_target(g, ref)
    print(f'target carried over: max match distance {dmax:.3g} m')
    src = base / 'cache'
    if a.state == 't1' and not (src / 't1' / 'case.json').is_file():
        from backend.aircooling.ai import dcts_cache
        rec = json.loads((base / 't1.json').read_text())
        dcts_cache.write_state(src, 't1', g, np.load(base / 'bc_t1.npy'), rec['roles'])
    ev = base / 'eval_cache'
    if ev.exists():
        shutil.rmtree(ev)
    (ev / 'geometry').mkdir(parents=True)
    for f in (src / 'geometry').iterdir():
        if f.name != 'target.npy':
            os.symlink(f.resolve(), ev / 'geometry' / f.name)
    np.save(ev / 'geometry' / 'target.npy', tgt)
    (ev / a.state).mkdir()
    for f in (src / a.state).iterdir():
        dst = ev / a.state / f.name
        if f.is_symlink():
            os.symlink(os.path.relpath(ev / 'geometry' / f.name, ev / a.state), dst)
        else:
            os.symlink(f.resolve(), dst)
    # boundary values against the reference, per role (nearest face of the same patch)
    cj = json.loads((ref / 'case.json').read_text())
    rpos, rbc, rp = np.load(ref / 'pos.npy'), np.load(ref / 'bc.npy'), np.load(ref / 'patch.npy')
    nc = cj['n_cells']
    bc = np.load(src / a.state / 'bc.npy')
    roles = json.loads((src / a.state / 'case.json').read_text())['patches']
    diff = {}
    for k, p in enumerate(g.patches):
        mine = np.flatnonzero(g.patch == k)
        if not mine.size or p.name not in ref_names:
            continue
        refr = np.flatnonzero(rp == ref_names.index(p.name))
        _, i = cKDTree(rpos[nc + refr]).query(g.pos[g.n_cells + mine])
        d = np.abs(bc[mine] - rbc[refr[i]])
        r = roles[k]['role']
        diff[r] = np.maximum(diff.get(r, np.zeros(5)), d.max(0))
    for r, d in sorted(diff.items()):
        print(f'  bc max|d| {r:14s} u v w p_rgh T = ' + ' '.join(f'{x:.3g}' for x in d))
    m = ScanoModel(CHECKPOINTS / a.model)
    r = m.predict(ev, a.state, with_error=True)
    print(f"{a.model}: err_mean {r['err_mean']:.4f} per field {[round(x, 4) for x in r['err']]} "
          f"(forward {r['timing']['forward_s']:.2f} s)")


if __name__ == '__main__':
    main()
