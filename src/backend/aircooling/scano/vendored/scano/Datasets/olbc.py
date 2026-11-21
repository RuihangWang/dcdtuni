"""LFE olbc Poisson benchmark as converted by Gen-FVGN-refine (poisson-circle-bc4), for sampled-training tests.

Input: the refine H5 conversion ``$SCANO_OLBC_ROOT/poisson-circle-bc4/`` with ``meta.json`` (case_splits:
3000 train, 256 test) and one ``<case>/h5/<case>.h5`` per case. This is exactly what refine's
HomoGraph_dataset reads for field_set "olbc": nodes are the 16,859 cpd cells (``cpd|centroid``), the graph is
``cpd|neighbor_cell`` (used for k-hop sampling), boundary cells are ``cpd|cell_type != 0`` (1000 per case, taken
from 1004 nodes on the unit circle, so the set is stored per case as refine reads it per case), ``olbc|bc`` holds (type, g, alpha) per cell (0=D, 1=N, 2=R), the target is ``sol_cpd_cell`` and the
node inputs are ``cpd|cell_udf`` and ``source_cpd_cell``. Geometry, graph and source are shared by all cases
(checked while building); only the boundary partition and data vary.

The cache adds node areas (Delaunay of the node set; the disk is convex) and arc-length weights of the
boundary nodes. Build once:
    PYTHONPATH=src python -m scano.Datasets.olbc --build [--root $SCANO_OLBC_ROOT] [--out data/olbc_bc4_v1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np

VERSION = 'olbc_bc4_v1'
DATASET = 'poisson-circle-bc4'
NB = 1000
SHARED = ('cpd|centroid', 'cpd|neighbor_cell', 'cpd|cell_udf', 'unv_cpd_cell', 'source_cpd_cell')


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def node_areas(pos):
    """Lumped (barycentric) node areas from a Delaunay triangulation of a convex 2-D node set."""
    from scipy.spatial import Delaunay
    s = Delaunay(pos).simplices
    a, b, c = pos[s[:, 0]], pos[s[:, 1]], pos[s[:, 2]]
    tri = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    area = np.zeros(len(pos))
    np.add.at(area, s.ravel(), np.repeat(tri / 3.0, 3))
    return area


def arc_weights(pos_b):
    """Arc-length quadrature weights of nodes on the unit circle (any order): half the angular gap to both
    angular neighbours."""
    theta = np.arctan2(pos_b[:, 1], pos_b[:, 0])
    order = np.argsort(theta)
    t = theta[order]
    fwd = np.mod(np.roll(t, -1) - t, 2 * np.pi)
    bwd = np.mod(t - np.roll(t, 1), 2 * np.pi)
    w = np.empty_like(theta)
    w[order] = 0.5 * (fwd + bwd)
    return w


def _case_path(root, case):
    return Path(root) / DATASET / case / 'h5' / f'{case}.h5'


def build_cache(root, out):
    import h5py
    root, out = Path(root), Path(out)
    meta = json.loads((root / DATASET / 'meta.json').read_text())
    splits = {'train': meta['case_splits']['train_cases'], 'test': meta['case_splits']['test_cases']}
    out.mkdir(parents=True, exist_ok=False)
    with h5py.File(_case_path(root, splits['train'][0]), 'r') as h:
        shared = {k: h[k][()] for k in SHARED}
    pos = shared['cpd|centroid'].astype(np.float64)
    manifest = {'version': VERSION, 'dataset': DATASET, 'source': 'Gen-FVGN-refine olbc_h5 conversion',
                'boundary_nodes_per_case': NB, 'splits': {}, 'files': {}}
    for split, cases in splits.items():
        n = len(cases)
        sol = np.empty((n, len(pos)), np.float32)
        bidx = np.empty((n, NB), np.int32)
        barc = np.empty((n, NB), np.float64)
        btype = np.empty((n, NB), np.int8)
        bg = np.empty((n, NB), np.float32)
        balpha = np.empty((n, NB), np.float32)
        for i, case in enumerate(cases):
            with h5py.File(_case_path(root, case), 'r') as h:
                if i < 3 or i % 250 == 0:  # shared geometry / graph / source (spot check)
                    for k in SHARED:
                        assert np.array_equal(h[k][()], shared[k]), (case, k)
                b = np.nonzero(h['cpd|cell_type'][()].reshape(-1) != 0)[0]   # refine: mask_boundary_cell, per case
                assert b.size == NB, (case, b.size)
                sol[i] = h['sol_cpd_cell'][()].reshape(-1)
                bc = h['olbc|bc'][()]
                bidx[i] = b
                barc[i] = arc_weights(pos[b, :2])
                btype[i] = bc[b, 0].astype(np.int8)
                bg[i] = bc[b, 1]
                balpha[i] = bc[b, 2]
        np.savez(out / f'{split}.npz', sol=sol, bidx=bidx, barc=barc, btype=btype, bg=bg, balpha=balpha)
        manifest['splits'][split] = {'cases': n, 'first': cases[0], 'last': cases[-1]}
        (out / f'{split}_cases.json').write_text(json.dumps(cases) + '\n')
    assert np.allclose(pos[:, 2], 0.0), 'olbc is planar'
    np.savez(out / 'mesh.npz', centroid=pos, edge_index=shared['cpd|neighbor_cell'].astype(np.int64),
             udf=shared['cpd|cell_udf'].reshape(-1), source=shared['source_cpd_cell'].reshape(-1),
             unv=shared['unv_cpd_cell'], area=node_areas(pos[:, :2]))
    for f in sorted(out.glob('*.npz')):
        manifest['files'][f.name] = _sha(f)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def load(data_dir, split):
    d = Path(data_dir)
    return dict(np.load(d / 'mesh.npz')), dict(np.load(d / f'{split}.npz'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--root', default=os.environ.get('SCANO_OLBC_ROOT', ''),
                    help='directory holding poisson-circle-bc4/ from the refine conversion (env SCANO_OLBC_ROOT)')
    ap.add_argument('--out', default='data/olbc_bc4_v1')
    args = ap.parse_args()
    if args.build:
        assert args.root, 'set --root or SCANO_OLBC_ROOT'
        print(json.dumps(build_cache(args.root, args.out), indent=2))


if __name__ == '__main__':
    main()
