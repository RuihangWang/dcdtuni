"""c09 patch-heterogeneity testbed: fixed-box Laplace with patch-structured Dirichlet BC.

Operator: cell-centered FV 7-point Laplace on the unit cube. z=0/z=1 faces carry
Dirichlet patch degrees of freedom on an nb x nb boundary cell grid per face; the four
side faces are Neumann with fixed zero flux in v1 (type-mixing per case is v2, it needs a
type channel in the shared model input interface). Linearity => any case truth at the
query points is c @ R where R is the per-boundary-cell response basis (one factorization,
B backsolves). Record areas are the boundary cell areas, so duplication/refinement knobs
from c04-c08 remain available on top of patch heterogeneity.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl


def face_layout(nb):
    """Boundary cell centers (B,3), areas (B,), face id (B,), dirichlet flag (B,)."""
    pos, area, face, dirichlet = [], [], [], []
    e = (np.arange(nb) + 0.5) / nb
    a = 1.0 / nb ** 2
    grids = np.stack(np.meshgrid(e, e, indexing='ij'), -1).reshape(-1, 2)
    for f in range(6):
        z = np.zeros((grids.shape[0], 1))
        o = np.ones((grids.shape[0], 1))
        if f == 0:
            p = np.concatenate([grids[:, :1], grids[:, 1:], z], 1)
            d = True
        elif f == 1:
            p = np.concatenate([grids[:, :1], grids[:, 1:], o], 1)
            d = True
        elif f == 2:
            p = np.concatenate([z, grids[:, :1], grids[:, 1:]], 1)
            d = False
        elif f == 3:
            p = np.concatenate([o, grids[:, :1], grids[:, 1:]], 1)
            d = False
        elif f == 4:
            p = np.concatenate([grids[:, :1], z, grids[:, 1:]], 1)
            d = False
        else:
            p = np.concatenate([grids[:, :1], o, grids[:, 1:]], 1)
            d = False
        pos.append(p)
        area.append(np.full(p.shape[0], a))
        face.append(np.full(p.shape[0], f))
        dirichlet.append(np.full(p.shape[0], d))
    return (np.concatenate(pos), np.concatenate(area), np.concatenate(face),
            np.concatenate(dirichlet))


class PatchBed:
    def __init__(self, n=64, nb=8):
        self.n, self.nb = n, nb
        assert n % nb == 0
        self.pos, self.area, self.face, self.dirichlet = face_layout(nb)
        self.b = self.pos.shape[0]
        self.h = 1.0 / n
        self._build_operator()

    def _idx(self, i, j, k):
        n = self.n
        return (i * n + j) * n + k

    def _build_operator(self):
        n, h = self.n, self.h
        rows, cols, vals = [], [], []
        for i in range(n):
            for j in range(n):
                for k in range(n):
                    d = self._idx(i, j, k)
                    diag = 0.0
                    for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                        ii, jj, kk = i + di, j + dj, k + dk
                        if 0 <= ii < n and 0 <= jj < n and 0 <= kk < n:
                            rows.append(d); cols.append(self._idx(ii, jj, kk)); vals.append(-h)
                            diag += h
                        elif dk == -1 and k == 0:
                            diag += 2 * h      # z=0 Dirichlet half-cell conductance
                        elif dk == 1 and k == n - 1:
                            diag += 2 * h      # z=1 Dirichlet half-cell conductance
                        # side faces: Neumann, zero flux, no conductance into diag
                    rows.append(d); cols.append(d); vals.append(diag)
        self.A = sp.csr_matrix((vals, (rows, cols)), shape=(n ** 3, n ** 3))
        self.lu = spl.splu(self.A.tocsc())
        r = n // self.nb
        self.adj = {}
        for f in (0, 1):
            for jb in range(self.nb):
                for kb in range(self.nb):
                    bid = f * self.nb ** 2 + jb * self.nb + kb
                    cells = [self._idx(a0, a1, 0 if f == 0 else n - 1)
                             for a0 in range(jb * r, jb * r + r)
                             for a1 in range(kb * r, kb * r + r)]
                    self.adj[bid] = (f, np.array(cells))

    def rhs(self, c):
        n, h = self.n, self.h
        b = np.zeros(n ** 3)
        for bid, (f, cells) in self.adj.items():
            b[cells] += 2 * h * c[bid]
        return b

    def solve_case(self, c):
        return self.lu.solve(self.rhs(c))

    def cell_flat(self, q):
        qidx = np.rint(q * self.n - 0.5).astype(int)
        return self._idx(qidx[:, 0], qidx[:, 1], qidx[:, 2])

    def basis_responses(self, q):
        flat = self.cell_flat(q)
        R = np.zeros((self.b, q.shape[0]))
        for bid in self.adj:
            e = np.zeros(self.b)
            e[bid] = 1.0
            R[bid] = self.solve_case(e)[flat]
        return R

    def compose(self, c, R):
        return np.asarray(c) @ R


def make_queries(n, seed=7, m_int=1024, m_near=512):
    """Query points as exact cell centers of the n-grid (interior block + near-face shell)."""
    rng = np.random.default_rng(seed)
    qi = (rng.integers(n // 4, 3 * n // 4, (m_int, 3)) + 0.5) / n
    qn = (rng.integers(0, n, (m_near, 3)) + 0.5) / n
    ax = rng.integers(0, 3, m_near)
    shell = rng.choice(np.array([0, 1, 2, n - 3, n - 2, n - 1]), m_near)
    qn[np.arange(m_near), ax] = (shell + 0.5) / n
    return np.concatenate([qi, qn])


def selftest():
    bed = PatchBed(n=32, nb=4)
    q = make_queries(32, m_int=256, m_near=128)
    R = bed.basis_responses(q)
    c = bed.pos[:, 2].copy()
    err1 = float(np.abs(bed.compose(c, R) - q[:, 2]).max())
    rng = np.random.default_rng(0)
    c1 = rng.standard_normal(bed.b) * bed.dirichlet
    c2 = rng.standard_normal(bed.b) * bed.dirichlet
    flat = bed.cell_flat(q)
    err2 = float(np.abs(bed.solve_case(c1 + c2)[flat]
                        - (bed.compose(c1, R) + bed.compose(c2, R))).max())
    bed2 = PatchBed(n=64, nb=4)
    R2 = bed2.basis_responses(q)   # nearest-cell comparison across grids
    cs = np.sin(np.pi * bed.pos[:, 0]) * bed.dirichlet
    err3 = float(np.abs(bed.compose(cs, R) - bed2.compose(cs, R2)).max())
    out = {'linear_profile_max_err': err1, 'superposition_max_err': err2,
           'refinement_32v64_nearest_cell_max_diff': err3}
    assert err1 < 1e-9 and err2 < 1e-9, out
    return out


if __name__ == '__main__':
    import sys
    if '--selftest' in sys.argv:
        print(json.dumps(selftest(), indent=2))
    else:
        root = Path(__file__).resolve().parents[3]
        out = root / 'data' / 'patchbed_v1'
        out.mkdir(parents=True, exist_ok=True)
        bed = PatchBed(n=64, nb=8)
        q = make_queries(64)
        R = bed.basis_responses(q)
        np.savez_compressed(out / 'basis.npz', R=R, q=q, pos=bed.pos, area=bed.area,
                            face=bed.face, dirichlet=bed.dirichlet)
        meta = {'n': 64, 'nb': 8, 'n_boundary_cells': int(bed.b),
                'dirichlet_cells': int(bed.dirichlet.sum()), 'queries': int(q.shape[0]),
                'selftest': selftest(),
                'sha256': hashlib.sha256((out / 'basis.npz').read_bytes()).hexdigest()}
        (out / 'manifest.json').write_text(json.dumps(meta, indent=2) + '\n')
        print(json.dumps(meta, indent=2))
