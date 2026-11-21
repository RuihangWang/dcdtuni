"""R0-S (patchbed-v2): a graded unit-cube convection-diffusion bed with typed boundary patches.

Design: docs/r0s_design_2026-09-30.md (sections 1-3, approved 2026-09-30). In one place:

  physics    -div(kappa grad u) + div(b u) = 0 on [0,1]^3, kappa = 1, no body source; b = Pe curl(0, psi, 0) with
             psi = sin(pi x) sin(pi z) (1 + cos(pi y)/2) / (1.5 pi), Pe = 10 (Pe = 0 gives the Laplace bed of
             patchbed v1). Face fluxes are edge integrals of the vector potential (Stokes), so the discrete
             divergence is zero and the six walls carry no convective flux.
  boundary   per layout: non-overlapping axis-aligned patches on a 16 x 16 lattice per wall, each of type D (given
             temperature), N (given heat flux into the domain) or R (Robin, kappa du/dn = alpha (u_inf - u)); the
             rest of the walls is insulated (recorded as N with zero flux). Per case every patch carries
             'constant + in-plane linear profile' in the shape functions {1, t1 - c1, t2 - c2}.
  truth      cell-centred finite volumes (diffusion two-point, convection central) on the 64^3 tanh-graded mesh
             (beta 1.5); one sparse LU per layout, a response basis of 3 solves per patch, case = coefficients x
             basis. Boundary-face nodes: D g, N u_P + q d / kappa, R (kappa u_P / d + alpha u_inf) / (kappa / d + alpha).
  meshes     every mesh is a subset of the truth mesh's grid lines that contains the lattice lines, so patches fit
             every mesh exactly and the truth on it is the volume (cells) / area (faces) average of the 64^3 truth:
             learn 32^3 (training), coarse 16^3, reverse-graded 40^3 and the truth 64^3 (deployment meshes, D4).
  nodes      cells (x fastest) then boundary faces (walls x-, x+, y-, y+, z-, z+; t1 fastest), then one fixed
             random renumbering stored with the mesh. Sampling graph: cell-cell only (boundary faces enter a k-hop
             sample only as pivots); message graph: cell-cell plus face <-> owner cell.

Build (CPU only; see cases/r0s/c43-r0s-build/run.sh):
    PYTHONPATH=src python -m scano.Datasets.r0s --build --out data/r0s_v1 --workers 16
The self-check parts and the 176 layout jobs (one sparse LU each, ~9 GiB peak and ~2 min at 64^3) run in one
process pool of single-threaded workers; a job starts only while the machine keeps >= 40 GiB available and a
1-minute load <= 48 (Budget, run_tasks). Self-checks, timings, the observed headroom and file hashes go to
manifest.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl

VERSION = 'r0s_v1'
WALLS = ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1))   # (normal axis, side): x-, x+, y-, y+, z-, z+
TANGENTS = ((1, 2), (0, 2), (0, 1))                        # (t1, t2) of a wall normal to axis 0, 1, 2
D_TYPE, N_TYPE, R_TYPE, WALL_TYPE = 0, 1, 2, 3             # WALL = insulated; its records are N with zero flux
AREA_CLASSES = (1, 2, 4, 8, 16, 32, 64)                    # patch size in lattice cells (x4 = learning faces)
SHAPES = {1: ((1, 1),), 2: ((1, 2), (2, 1)), 4: ((2, 2), (1, 4), (4, 1)), 8: ((2, 4), (4, 2)),
          16: ((4, 4), (2, 8), (8, 2)), 32: ((4, 8), (8, 4)), 64: ((8, 8),)}
STRATUM = {1: 0, 2: 0, 4: 1, 8: 1, 16: 2, 32: 3, 64: 3}   # tiny / small / medium / large (4-8, 16-32, 64, 128-256 faces)
STRATA = ('tiny', 'small', 'medium', 'large')
KNOBS = ('supply', 'heat', 'ambient', 'switch')            # D a + std, N Q x 2, R u_inf + std, D -> N (sibling layout)
KNOB_TYPE = (D_TYPE, N_TYPE, R_TYPE, D_TYPE)
MESH_ROLES = ('truth', 'learn', 'coarse', 'reverse')
KIND_TRAIN, KIND_HELDOUT, KIND_INTERVENTION = 0, 1, 2      # test.npz 'kind'
ROLE_TRAIN, ROLE_HELDOUT, ROLE_SIBLING = 0, 1, 2           # layouts.npz 'role'


@dataclass(frozen=True)
class Config:
    n_truth: int = 64
    beta: float = 1.5
    lattice: int = 16                 # lattice cells per wall and axis (patch edges)
    learn_step: int = 2               # learning mesh = every learn_step-th truth line
    pe: float = 10.0                  # 0 = pure diffusion (patchbed v1 physics)
    kappa: float = 1.0
    p_choices: tuple = (8, 16, 32, 48)
    train_layouts: int = 128
    heldout_layouts: int = 16
    train_per_layout: int = 16        # 128 x 16 = 2048 training cases
    val_per_layout: int = 2           # 256 validation cases (last in train.npz)
    test_per_layout: int = 2          # 256 test cases on training layouts, new coefficients
    heldout_per_layout: int = 16      # 256 test cases on held-out layouts
    intervention_every: int = 8       # every 8th training layout carries interventions (16 of 128)
    bases_per_layout: int = 2         # base cases per intervention layout (32 layouts x 2 = 64)
    seed: int = 20260930
    renumber_seed: int = 20260930


# ---------------------------------------------------------------------------------------------------------
# meshes
# ---------------------------------------------------------------------------------------------------------

def graded_lines(n, beta):
    """x_i = (1 + tanh(beta (2 i/n - 1)) / tanh beta) / 2, i = 0..n (uniform when beta = 0)."""
    xi = np.arange(n + 1, dtype=np.float64) / n
    if beta == 0:
        return xi
    x = 0.5 * (1.0 + np.tanh(beta * (2.0 * xi - 1.0)) / np.tanh(beta))
    x[0], x[-1] = 0.0, 1.0
    return x


def role_line_index(cfg, role):
    """Truth-line indices of a mesh role; every role contains the lattice lines."""
    n, step = cfg.n_truth, cfg.n_truth // cfg.lattice
    if role == 'truth':
        return np.arange(n + 1)
    if role == 'learn':
        return np.arange(0, n + 1, cfg.learn_step)
    if role == 'coarse':
        return np.arange(0, n + 1, step)
    if role == 'reverse':        # the lattice/4 lattice intervals next to each wall merged, the middle at truth resolution
        outer = (cfg.lattice // 4) * step
        return np.unique(np.concatenate([np.arange(0, outer + 1, step), np.arange(outer, n - outer + 1),
                                         np.arange(n - outer, n + 1, step)]))
    raise ValueError(role)


def _along(a, v):
    s = [1, 1, 1]
    s[a] = -1
    return np.asarray(v).reshape(s)


class BoxMesh:
    """Tensor-product box mesh whose lines are a subset of the truth lines. Arrays named *_lex are in the
    unrenumbered order (cells x fastest, then faces wall by wall, t1 fastest); node arrays are in node-id order,
    node id = perm[lex]."""

    def __init__(self, cfg, role, line_index=None):
        self.cfg, self.role = cfg, role
        truth_lines = graded_lines(cfg.n_truth, cfg.beta)
        self.line_index = np.asarray(role_line_index(cfg, role) if line_index is None else line_index)
        step = cfg.n_truth // cfg.lattice
        assert np.isin(np.arange(0, cfg.n_truth + 1, step), self.line_index).all(), 'lattice lines missing'
        self.lines = truth_lines[self.line_index]
        n = self.n = len(self.lines) - 1
        self.width = np.diff(self.lines)
        self.center = 0.5 * (self.lines[:-1] + self.lines[1:])
        self.lattice_of = self.line_index[:-1] // step            # lattice interval of each mesh interval
        self.n_cells, self.n_faces = n ** 3, 6 * n * n
        self.n_nodes = self.n_cells + self.n_faces
        w, c = self.width, self.center
        i, j, k = (a.ravel(order='F') for a in np.meshgrid(np.arange(n), np.arange(n), np.arange(n), indexing='ij'))
        self.cell_ijk = np.stack([i, j, k], 1)
        self.cell_centroid = np.stack([c[i], c[j], c[k]], 1)
        self.cell_volume = w[i] * w[j] * w[k]
        p, q = (a.ravel(order='F') for a in np.meshgrid(np.arange(n), np.arange(n), indexing='ij'))
        wall, fp, fq, owner, cen, area, normal, dist = [], [], [], [], [], [], [], []
        for f, (a, s) in enumerate(WALLS):
            t1, t2 = TANGENTS[a]
            idx = np.zeros((n * n, 3), np.int64)
            idx[:, a] = 0 if s == 0 else n - 1
            idx[:, t1], idx[:, t2] = p, q
            owner.append(idx[:, 0] + n * idx[:, 1] + n * n * idx[:, 2])
            xyz = np.zeros((n * n, 3))
            xyz[:, a] = float(s)
            xyz[:, t1], xyz[:, t2] = c[p], c[q]
            cen.append(xyz)
            nv = np.zeros((n * n, 3))
            nv[:, a] = -1.0 if s == 0 else 1.0
            normal.append(nv)
            area.append(w[p] * w[q])
            dist.append(np.full(n * n, 0.5 * (w[0] if s == 0 else w[-1])))
            wall.append(np.full(n * n, f))
            fp.append(p)
            fq.append(q)
        self.face_wall, self.face_p, self.face_q = np.concatenate(wall), np.concatenate(fp), np.concatenate(fq)
        self.face_owner = np.concatenate(owner)                    # lex cell index
        self.face_centroid, self.face_area = np.concatenate(cen), np.concatenate(area)
        self.face_normal, self.face_dist = np.concatenate(normal), np.concatenate(dist)
        self.face_t = np.stack([c[self.face_p], c[self.face_q]], 1)  # tangential coordinates (t1, t2)
        rng = np.random.default_rng([cfg.renumber_seed, MESH_ROLES.index(role) if role in MESH_ROLES else 99, n])
        self.perm = rng.permutation(self.n_nodes)                  # node id of each lex position

    # --- node-order views -------------------------------------------------------------------------------
    def to_nodes(self, lex_array):
        """Lex-ordered [..., n_nodes] -> node order."""
        a = np.asarray(lex_array)
        out = np.empty_like(a)
        out[..., self.perm] = a
        return out

    def to_lex(self, node_array):
        return np.asarray(node_array)[..., self.perm]

    def node_arrays(self):
        """Everything a dataset needs, in node order (float64 geometry)."""
        nc = self.n_cells
        centroid = self.to_nodes(np.concatenate([self.cell_centroid, self.face_centroid]).T).T
        kind = self.to_nodes(np.concatenate([np.zeros(nc, np.int8), np.ones(self.n_faces, np.int8)]))
        volume = self.to_nodes(np.concatenate([self.cell_volume, np.zeros(self.n_faces)]))
        area = self.to_nodes(np.concatenate([np.zeros(nc), self.face_area]))
        normal = self.to_nodes(np.concatenate([np.zeros((nc, 3)), self.face_normal]).T).T
        cc = self.cell_centroid
        dw = np.minimum(cc, 1.0 - cc).min(1)
        d_w = self.to_nodes(np.concatenate([dw, np.zeros(self.n_faces)]))
        face_node = self.perm[nc:]
        owner_node = self.perm[self.face_owner]
        L3 = np.arange(nc).reshape((self.n,) * 3, order='F')
        pairs = [np.stack([np.take(L3, np.arange(self.n - 1), axis=a).ravel(),
                           np.take(L3, np.arange(1, self.n), axis=a).ravel()]) for a in range(3)]
        cc_edges = self.perm[np.concatenate(pairs, 1)]
        sample = np.concatenate([cc_edges, cc_edges[::-1]], 1)
        fo = np.stack([face_node, owner_node])
        message = np.concatenate([sample, fo, fo[::-1]], 1)
        return {'role': self.role, 'n': self.n, 'n_nodes': self.n_nodes, 'n_cells': nc, 'n_faces': self.n_faces,
                'lines': self.lines, 'line_index': self.line_index, 'perm': self.perm,
                'renumber_seed': self.cfg.renumber_seed, 'centroid': centroid, 'kind': kind, 'volume': volume,
                'face_area_node': area, 'normal': normal, 'd_w': d_w, 'face_node': face_node,
                'owner_node': owner_node, 'face_wall': self.face_wall, 'face_p': self.face_p, 'face_q': self.face_q,
                'lattice_of': self.lattice_of, 'face_t': self.face_t,
                'face_area': self.face_area, 'face_centroid': self.face_centroid, 'face_normal': self.face_normal,
                'sample_edge_index': sample, 'message_edge_index': message}


def restriction_matrix(cfg, line_index):
    """[n_coarse, n_truth] 1-D aggregation: row m holds the truth widths of the truth cells inside cell m."""
    tw = np.diff(graded_lines(cfg.n_truth, cfg.beta))
    W = np.zeros((len(line_index) - 1, cfg.n_truth))
    for m in range(len(line_index) - 1):
        W[m, line_index[m]:line_index[m + 1]] = tw[line_index[m]:line_index[m + 1]]
    return W


def restrict_lex(cfg, truth, mesh, u_lex):
    """Exact finite-volume restriction of truth-mesh lex fields [S, N_truth] to `mesh` (lex order): cells by
    volume averages, boundary faces by area averages."""
    u = np.atleast_2d(u_lex)
    S, nt, n = u.shape[0], truth.n, mesh.n
    W = restriction_matrix(cfg, mesh.line_index)
    cells = u[:, :truth.n_cells].reshape(S, nt, nt, nt)                  # [s, k, j, i]
    cc = np.einsum('Kk,Jj,Ii,skji->sKJI', W, W, W, cells, optimize=True)
    cc /= (mesh.width[:, None, None] * mesh.width[None, :, None] * mesh.width[None, None, :])
    faces = u[:, truth.n_cells:].reshape(S, 6, nt, nt)                  # [s, wall, q, p]
    ff = np.einsum('Qq,Pp,sfqp->sfQP', W, W, faces, optimize=True)
    ff /= (mesh.width[:, None] * mesh.width[None, :])
    return np.concatenate([cc.reshape(S, -1), ff.reshape(S, -1)], 1)


# ---------------------------------------------------------------------------------------------------------
# operator
# ---------------------------------------------------------------------------------------------------------

def psi_nodes(mesh):
    """Psi[i, j, k] = integral over the y-edge (x_i, [y_j, y_j+1], z_k) of psi; zero on the x and z walls."""
    x = mesh.lines
    s = np.sin(np.pi * x)
    s[0] = s[-1] = 0.0
    Y = np.diff(x) + np.diff(np.sin(np.pi * x)) / (2.0 * np.pi)
    return s[:, None, None] * Y[None, :, None] * s[None, None, :] / (1.5 * np.pi)


def interior_fluxes(mesh, pe):
    """Volume fluxes (+axis direction) through the interior faces of each axis: [n-1, n, n], [n, n-1, n], [n, n, n-1]."""
    n = mesh.n
    psi = psi_nodes(mesh)
    fx = -pe * (psi[1:-1, :, 1:] - psi[1:-1, :, :-1])
    fz = pe * (psi[1:, :, 1:-1] - psi[:-1, :, 1:-1])
    return fx, np.zeros((n, n - 1, n)), fz


def divergence(mesh, pe):
    """Discrete divergence per cell (sum of outward fluxes; walls carry none)."""
    fx, fy, fz = interior_fluxes(mesh, pe)
    n = mesh.n
    div = np.zeros((n, n, n))
    for a, f in enumerate((fx, fy, fz)):
        div += np.pad(f, [(0, 1) if b == a else (0, 0) for b in range(3)])    # outward through the upper face
        div -= np.pad(f, [(1, 0) if b == a else (0, 0) for b in range(3)])    # inward through the lower face
    return div


def boundary_coefficients(mesh, ftype, falpha, kappa):
    """Per boundary face: (diag, rhs) coefficients; the boundary contribution to cell P is
    diag * u_P - rhs * value (moved to the right-hand side as rhs * value)."""
    A, g = mesh.face_area, kappa / mesh.face_dist
    heff = np.where(ftype == R_TYPE, falpha * g / np.maximum(falpha + g, 1e-300), 0.0)
    diag = np.where(ftype == D_TYPE, A * g, np.where(ftype == R_TYPE, A * heff, 0.0))
    rhs = np.where(ftype == D_TYPE, A * g, np.where(ftype == N_TYPE, A, np.where(ftype == R_TYPE, A * heff, 0.0)))
    return diag, rhs


def assemble(mesh, ftype, falpha, pe, kappa=1.0):
    """Cell operator (CSC, lex order): diffusion two-point + central convection + boundary terms."""
    n, w, c, x = mesh.n, mesh.width, mesh.center, mesh.lines
    L3 = np.arange(n ** 3).reshape((n, n, n), order='F')
    rows, cols, vals = [], [], []
    for a, F in enumerate(interior_fluxes(mesh, pe)):
        t1, t2 = TANGENTS[a]
        area = _along(t1, w) * _along(t2, w)
        dc = c[1:] - c[:-1]
        lo = np.take(L3, np.arange(n - 1), axis=a)
        up = np.take(L3, np.arange(1, n), axis=a)
        Dm = np.broadcast_to(kappa * area / _along(a, dc), lo.shape)
        wl = np.broadcast_to(_along(a, (c[1:] - x[1:-1]) / dc), lo.shape)    # weight of the lower cell
        wu = 1.0 - wl
        rows += [lo, lo, up, up]
        cols += [lo, up, up, lo]
        vals += [Dm + F * wl, -Dm + F * wu, Dm - F * wu, -Dm - F * wl]
    bdiag, _ = boundary_coefficients(mesh, ftype, falpha, kappa)
    rows.append(mesh.face_owner)
    cols.append(mesh.face_owner)
    vals.append(bdiag)
    rows = np.concatenate([np.ravel(r) for r in rows])
    cols = np.concatenate([np.ravel(r) for r in cols])
    vals = np.concatenate([np.ravel(v) for v in vals])
    return sp.csc_matrix((vals, (rows, cols)), shape=(n ** 3, n ** 3))


def owner_matrix(mesh):
    """[n_cells, n_faces] with 1 at (owner(f), f)."""
    return sp.csr_matrix((np.ones(mesh.n_faces), (mesh.face_owner, np.arange(mesh.n_faces))),
                         shape=(mesh.n_cells, mesh.n_faces))


def face_node_values(mesh, ftype, falpha, fval, u_owner, kappa):
    """Boundary-face node values: D g; N (and insulated) u_P + q d / kappa; R (kappa u_P / d + alpha u_inf) / (kappa / d + alpha)."""
    g = kappa / mesh.face_dist
    return np.where(ftype == D_TYPE, fval,
                    np.where(ftype == R_TYPE, (g * u_owner + falpha * fval) / (g + falpha), u_owner + fval / g))


def factorize(A):
    """Sparse LU (SuperLU; the pattern is symmetric, so minimum degree on A^T + A)."""
    return spl.splu(A, permc_spec='MMD_AT_PLUS_A')


# ---------------------------------------------------------------------------------------------------------
# layouts
# ---------------------------------------------------------------------------------------------------------

def type_counts(P):
    n_d = max(2, int(round(0.30 * P)))
    n_r = int(round(0.25 * P))
    return n_d, P - n_d - n_r, n_r


def make_layout(cfg, P, rng):
    """Non-overlapping patches on the lattice: sizes log-uniform over the area classes, conditioned on the free
    lattice area (a <= free / remaining; the largest class capped at 4 x 8 when P is the largest choice), random
    rejection placement, falling back to smaller classes. Types D/N/R in fixed proportions (>= 2 D)."""
    L = cfg.lattice
    occ = np.full((6, L, L), -1, np.int32)
    cap = 32 if P == max(cfg.p_choices) else 64
    wall, a0, a1, b0, b1 = (np.zeros(P, np.int64) for _ in range(5))
    for p in range(P):
        free, remaining = int((occ < 0).sum()), P - p
        allowed = [a for a in AREA_CLASSES if a <= cap and a * remaining <= free]
        area = allowed[int(rng.integers(len(allowed)))]
        placed = None
        while placed is None:
            shapes = SHAPES[area]
            for _ in range(200):
                sw, sh = shapes[int(rng.integers(len(shapes)))]
                f, x0, y0 = int(rng.integers(6)), int(rng.integers(L - sw + 1)), int(rng.integers(L - sh + 1))
                if (occ[f, x0:x0 + sw, y0:y0 + sh] < 0).all():
                    placed = (f, x0, x0 + sw, y0, y0 + sh)
                    break
            if placed is None:
                if area == 1:
                    fr = np.argwhere(occ < 0)
                    f, x0, y0 = (int(v) for v in fr[int(rng.integers(len(fr)))])
                    placed = (f, x0, x0 + 1, y0, y0 + 1)
                else:
                    area = AREA_CLASSES[AREA_CLASSES.index(area) - 1]
        wall[p], a0[p], a1[p], b0[p], b1[p] = placed
        occ[placed[0], placed[1]:placed[2], placed[3]:placed[4]] = p
    n_d, n_n, n_r = type_counts(P)
    ptype = rng.permutation(np.array([D_TYPE] * n_d + [N_TYPE] * n_n + [R_TYPE] * n_r, np.int64))
    alpha = np.where(ptype == R_TYPE, rng.choice(np.array([1.0, 10.0]), P), 0.0)
    return finish_layout(cfg, {'P': P, 'wall': wall, 'a0': a0, 'a1': a1, 'b0': b0, 'b1': b1, 'ptype': ptype,
                               'alpha': alpha, 'lattice_map': occ})


def finish_layout(cfg, lay):
    """Add the physical patch geometry: centroid (t1, t2), side lengths, area, size class and stratum."""
    tl = graded_lines(cfg.n_truth, cfg.beta)
    step = cfg.n_truth // cfg.lattice
    lo1, hi1 = tl[lay['a0'] * step], tl[lay['a1'] * step]
    lo2, hi2 = tl[lay['b0'] * step], tl[lay['b1'] * step]
    cells = (lay['a1'] - lay['a0']) * (lay['b1'] - lay['b0'])
    lay.update(c1=0.5 * (lo1 + hi1), c2=0.5 * (lo2 + hi2), len1=hi1 - lo1, len2=hi2 - lo2,
               parea=(hi1 - lo1) * (hi2 - lo2), cells=cells,
               stratum=np.array([STRATUM.get(int(a), 3 if a > 64 else 0) for a in cells], np.int64))
    return lay


def switched_layout(cfg, lay, patch):
    """Sibling layout: patch `patch` changed from D to N (insulated 'air conditioning off')."""
    sib = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in lay.items()}
    sib['sibling'] = int(patch)
    assert sib['ptype'][patch] == D_TYPE
    sib['ptype'][patch] = N_TYPE
    sib['alpha'][patch] = 0.0
    return sib


def face_spec(mesh, lay):
    """Per boundary face of `mesh` (lex): patch id (-1 insulated), type, alpha, local (t1 - c1, t2 - c2)."""
    lat = mesh.lattice_of
    pid = lay['lattice_map'][mesh.face_wall, lat[mesh.face_p], lat[mesh.face_q]].astype(np.int64)
    on = pid >= 0
    safe = np.where(on, pid, 0)
    ftype = np.where(on, lay['ptype'][safe], WALL_TYPE)
    falpha = np.where(on, lay['alpha'][safe], 0.0)
    dt = np.where(on[:, None], mesh.face_t - np.stack([lay['c1'][safe], lay['c2'][safe]], 1), 0.0)
    return pid, ftype, falpha, dt


def shape_matrix(mesh, lay, spec=None):
    """[n_faces, 3P] sparse: face values of the shape functions {1, t1 - c1, t2 - c2} of every patch."""
    pid, _, _, dt = face_spec(mesh, lay) if spec is None else spec
    f = np.nonzero(pid >= 0)[0]
    p = pid[f]
    rows = np.concatenate([f, f, f])
    cols = np.concatenate([3 * p, 3 * p + 1, 3 * p + 2])
    vals = np.concatenate([np.ones(len(f)), dt[f, 0], dt[f, 1]])
    return sp.csr_matrix((vals, (rows, cols)), shape=(mesh.n_faces, 3 * lay['P']))


# ---------------------------------------------------------------------------------------------------------
# case coefficients
# ---------------------------------------------------------------------------------------------------------

def slog1p(q):
    return np.sign(q) * np.log1p(np.abs(q))


def draw_cases(lay, n, rng):
    """Coefficients [n, P, 3] (value = c0 + c1 (t1 - c1) + c2 (t2 - c2)): D a ~ N(0,1) min(3, (A_med / A_p)^(1/4));
    N q0 = s Q / A_p with Q log-uniform on [0.02, 0.5], s = +1 (80%) or -1; R u_inf ~ N(0,1). Half of the patches
    (per case) add a linear profile whose change across the patch is ~ N(0, 0.5^2) x the constant's scale."""
    P = lay['P']
    amp_d = np.minimum(3.0, (np.median(lay['parea']) / lay['parea']) ** 0.25)
    coef = np.zeros((n, P, 3))
    for s in range(n):
        Q = np.exp(rng.uniform(np.log(0.02), np.log(0.5), P))
        sign = np.where(rng.random(P) < 0.8, 1.0, -1.0)
        c0 = np.where(lay['ptype'] == D_TYPE, rng.standard_normal(P) * amp_d,
                      np.where(lay['ptype'] == N_TYPE, sign * Q / lay['parea'], rng.standard_normal(P)))
        scale = np.where(lay['ptype'] == D_TYPE, amp_d, np.where(lay['ptype'] == N_TYPE, np.abs(c0), 1.0))
        linear = rng.random(P) < 0.5
        slopes = rng.standard_normal((P, 2)) * 0.5 * scale[:, None] / np.stack([lay['len1'], lay['len2']], 1)
        coef[s, :, 0] = c0
        coef[s, :, 1:] = np.where(linear[:, None], slopes, 0.0)
    return coef


def pick_patch(lay, ptype, stratum, rng):
    """A patch of type `ptype` in `stratum` (else the nearest stratum; ties at random)."""
    cand = np.nonzero(lay['ptype'] == ptype)[0]
    gap = np.abs(lay['stratum'][cand] - stratum)
    best = cand[gap == gap.min()]
    return int(best[int(rng.integers(len(best)))])


# ---------------------------------------------------------------------------------------------------------
# design: every layout, case and intervention (cheap, deterministic)
# ---------------------------------------------------------------------------------------------------------

def draw_design(cfg):
    """Layouts (train, held-out, siblings), the case table of every split and the interventions."""
    rng0 = np.random.default_rng([cfg.seed, 0])
    reps = lambda m: np.resize(np.asarray(cfg.p_choices), m)                      # noqa: E731  (1/4 each)
    p_train, p_held = rng0.permutation(reps(cfg.train_layouts)), rng0.permutation(reps(cfg.heldout_layouts))
    layouts, role = [], []
    for i, P in enumerate(p_train):
        layouts.append(make_layout(cfg, int(P), np.random.default_rng([cfg.seed, 1, i])))
        role.append(ROLE_TRAIN)
    for i, P in enumerate(p_held):
        layouts.append(make_layout(cfg, int(P), np.random.default_rng([cfg.seed, 1, 100000 + i])))
        role.append(ROLE_HELDOUT)
    nt = cfg.train_layouts

    def cases(li, stream, n):
        return draw_cases(layouts[li], n, np.random.default_rng([cfg.seed, 2, li, stream]))

    train = [(li, cases(li, 0, cfg.train_per_layout)) for li in range(nt)]
    val = [(li, cases(li, 1, cfg.val_per_layout)) for li in range(nt)]
    test_new = [(li, cases(li, 2, cfg.test_per_layout)) for li in range(nt)]
    held = [(nt + h, cases(nt + h, 3, cfg.heldout_per_layout)) for h in range(cfg.heldout_layouts)]
    # training statistics for the additive knobs: std of D constants and R ambient constants over training cases
    d_c = np.concatenate([c[:, layouts[li]['ptype'] == D_TYPE, 0].ravel() for li, c in train])
    r_c = np.concatenate([c[:, layouts[li]['ptype'] == R_TYPE, 0].ravel() for li, c in train])
    knob_std = {'supply': float(d_c.std()), 'ambient': float(r_c.std())}

    def rows(blocks, kind):
        out = []
        for li, c in blocks:
            for s in range(len(c)):
                out.append({'layout': li, 'coef': c[s], 'kind': kind})
        return out

    tables = {'train': rows(train, KIND_TRAIN) + rows(val, KIND_TRAIN),
              'test': rows(test_new, KIND_TRAIN) + rows(held, KIND_HELDOUT)}
    for r in tables['test']:
        r.update(base_index=-1, knob=-1, target_patch=-1, stratum=-1)
    # interventions: bases = the first bases_per_layout test cases of every intervention layout
    inter_layouts = [li for li in range(0, nt, cfg.intervention_every)] + [nt + h for h in range(cfg.heldout_layouts)]
    sibling_of, switched = [-1] * len(layouts), [-1] * len(layouts)
    rng_i = np.random.default_rng([cfg.seed, 3])
    b = 0
    for j, li in enumerate(inter_layouts):
        lay = layouts[li]
        sw = pick_patch(lay, D_TYPE, j % 4, rng_i)
        layouts.append(switched_layout(cfg, lay, sw))
        role.append(ROLE_SIBLING)
        sibling_of.append(li)
        switched.append(sw)
        sib = len(layouts) - 1
        base_rows = [i for i, r in enumerate(tables['test']) if r['layout'] == li][:cfg.bases_per_layout]
        for bi in base_rows:
            base = tables['test'][bi]
            for k, knob in enumerate(KNOBS):
                coef = base['coef'].copy()
                if knob == 'switch':
                    p, li_k = sw, sib
                    coef[p] = 0.0
                else:
                    p, li_k = pick_patch(lay, KNOB_TYPE[k], (b + k) % 4, rng_i), li
                    if knob == 'supply':
                        coef[p, 0] += knob_std['supply']
                    elif knob == 'heat':
                        coef[p] *= 2.0
                    else:
                        coef[p, 0] += knob_std['ambient']
                tables['test'].append({'layout': li_k, 'coef': coef, 'kind': KIND_INTERVENTION, 'base_index': bi,
                                       'knob': k, 'target_patch': p, 'stratum': int(lay['stratum'][p])})
            b += 1
    return {'layouts': layouts, 'role': np.array(role), 'sibling_of': np.array(sibling_of),
            'switched_patch': np.array(switched), 'tables': tables, 'knob_std': knob_std,
            'n_train': nt * cfg.train_per_layout, 'n_val': nt * cfg.val_per_layout}


# ---------------------------------------------------------------------------------------------------------
# truth per layout
# ---------------------------------------------------------------------------------------------------------

_MESHES = {}


def mesh_for(cfg, role):
    key = (cfg, role)
    if key not in _MESHES:
        _MESHES[key] = BoxMesh(cfg, role)
    return _MESHES[key]


def solve_layout(cfg, lay, coefs, truth_rows=None, basis=True, check=True):
    """Truth fields of the cases `coefs` [S, P, 3] on one layout.

    Returns lex fields on the truth mesh for `truth_rows` (all rows when None), lex fields restricted to the
    learning mesh for every row, the self-check errors (superposition, conservation) and timings."""
    truth, learn = mesh_for(cfg, 'truth'), mesh_for(cfg, 'learn')
    t = {}
    t0 = time.perf_counter()
    spec = face_spec(truth, lay)
    pid, ftype, falpha, _ = spec
    A = assemble(truth, ftype, falpha, cfg.pe, cfg.kappa)
    S_f = shape_matrix(truth, lay, spec)
    bdiag, brhs = boundary_coefficients(truth, ftype, falpha, cfg.kappa)
    Own = owner_matrix(truth)
    RHS = Own @ sp.diags(brhs) @ S_f                                                  # [n_cells, 3P]
    t['assemble'] = time.perf_counter() - t0
    t0 = time.perf_counter()
    lu = factorize(A)
    t['splu'] = time.perf_counter() - t0
    C = coefs.reshape(len(coefs), -1)                                                 # [S, 3P]
    t0 = time.perf_counter()
    if basis:
        B = lu.solve(np.asarray(RHS.todense()))                                       # response basis [n_cells, 3P]
        cells = C @ B.T
        t['solves'] = RHS.shape[1]
    else:
        B = None
        cells = lu.solve(np.asarray((RHS @ C.T))).T
        t['solves'] = len(C)
    t['solve'] = time.perf_counter() - t0
    fval = (S_f @ C.T).T                                                              # [S, n_faces]
    faces = face_node_values(truth, ftype, falpha, fval, cells[:, truth.face_owner], cfg.kappa)
    full = np.concatenate([cells, faces], 1)
    checks = {}
    if check:
        k = min(2, len(C))
        if B is not None and k == 2:                                                  # superposition vs a direct solve
            direct = lu.solve(np.asarray(RHS @ (C[0] + C[1])))
            comp = B @ C[0] + B @ C[1]
            checks['superposition'] = float(np.abs(direct - comp).max() / max(np.abs(comp).max(), 1e-300))
        U = B if B is not None else cells.T
        V = np.asarray(S_f.todense()) if B is not None else fval.T
        flux = brhs[:, None] * V - bdiag[:, None] * U[truth.face_owner]              # inflow per face
        checks['conservation'] = float(np.max(np.abs(flux.sum(0)) / np.maximum(np.abs(flux).sum(0), 1e-300)))
        rhs = np.asarray(RHS.todense()) if B is not None else RHS @ C.T
        checks['residual'] = float(np.abs(A @ U - rhs).max() / max(np.abs(U).max(), 1e-300))
    t0 = time.perf_counter()
    learn_fields = restrict_lex(cfg, truth, learn, full)
    t['restrict'] = time.perf_counter() - t0
    rows = np.arange(len(C)) if truth_rows is None else np.asarray(truth_rows, np.int64)
    return {'truth': full[rows], 'learn': learn_fields, 'checks': checks, 'timings': t,
            'nnz_lu': int(lu.L.nnz + lu.U.nnz)}


def _job(args):
    cfg, lay, coefs, truth_rows, basis, tag = args
    out = solve_layout(cfg, lay, coefs, truth_rows, basis)
    out['worker_maxrss_gb'] = _maxrss_gb()
    return tag, out


def _check_job(args):
    cfg, name, slow = args
    return name, selfcheck_part(cfg, name, slow)


def _maxrss_gb():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20       # kB on Linux -> GiB
    except ImportError:
        return None


# ---------------------------------------------------------------------------------------------------------
# headroom-aware parallel scheduling (shared machine)
# ---------------------------------------------------------------------------------------------------------

def headroom():
    """(available memory in GiB, 1-minute load) from /proc; (inf, 0) where /proc is missing."""
    try:
        avail = float('inf')
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    avail = int(line.split()[1]) / 2 ** 20
                    break
        with open('/proc/loadavg') as f:
            return avail, float(f.read().split()[0])
    except OSError:
        return float('inf'), 0.0


@dataclass(frozen=True)
class Budget:
    """Parallel budget on a shared machine: at most `workers` jobs; a job starts only when the available memory,
    minus the expected peak of the jobs still ramping up (started less than `ramp_s` ago), minus its own expected
    peak, stays >= min_available_gb, and the 1-minute load stays <= max_load after adding it. When headroom drops,
    no new job starts until it recovers (running jobs finish; each takes about two minutes at 64^3)."""
    workers: int = 1
    min_available_gb: float = 40.0
    max_load: float = 48.0
    job_gb: float = 10.0            # measured peak of one 64^3 layout job: ~9.0 GiB (2026-09-30)
    ramp_s: float = 150.0
    max_wait_s: float = 3600.0


def run_tasks(tasks, budget, on_result, log=print):
    """tasks: list of (fn, args, expected_gb, label). Calls on_result(label, result) as tasks finish.
    Returns the observed headroom (min available GiB, max load1, max concurrent jobs)."""
    obs = {'min_available_gb': float('inf'), 'max_load1': 0.0, 'max_running': 0, 'headroom_waits': 0}

    def sample():
        a, l = headroom()
        obs['min_available_gb'] = min(obs['min_available_gb'], a)
        obs['max_load1'] = max(obs['max_load1'], l)
        return a, l

    if budget.workers <= 1:
        for fn, args, _, label in tasks:
            sample()
            on_result(label, fn(args))
            sample()
        obs['max_running'] = 1 if tasks else 0
        return obs
    import concurrent.futures as cf
    import multiprocessing as mp
    pending, running = list(tasks), {}
    waited = 0.0
    with cf.ProcessPoolExecutor(max_workers=budget.workers, mp_context=mp.get_context('spawn')) as ex:
        while pending or running:
            avail, load = sample()
            now = time.time()
            ramping = sum(gb for _, st, gb in running.values() if now - st < budget.ramp_s)
            if pending and len(running) < budget.workers and \
                    avail - ramping - pending[0][2] >= budget.min_available_gb and load + 1 <= budget.max_load:
                fn, args, gb, label = pending.pop(0)
                running[ex.submit(fn, args)] = (label, now, gb)
                obs['max_running'] = max(obs['max_running'], len(running))
                waited = 0.0
                continue
            if pending and len(running) < budget.workers:
                obs['headroom_waits'] += 1
                if obs['headroom_waits'] % 30 == 1:
                    log(f'headroom: available {avail:.0f} GiB, load {load:.1f}, running {len(running)}; waiting')
            if not running:
                if waited > budget.max_wait_s:
                    raise RuntimeError(f'no headroom for {budget.max_wait_s:.0f} s (available {avail:.0f} GiB, load {load:.1f})')
                time.sleep(10)
                waited += 10
                continue
            done, _ = cf.wait(running, timeout=10, return_when=cf.FIRST_COMPLETED)
            for fut in done:
                label, _, _ = running.pop(fut)
                on_result(label, fut.result())
    return obs


# ---------------------------------------------------------------------------------------------------------
# self-checks (design section 1.5)
# ---------------------------------------------------------------------------------------------------------

def check_geometry(cfg, role):
    m = BoxMesh(cfg, role)
    na = m.node_arrays()
    out = {'volume_sum_err': abs(float(m.cell_volume.sum()) - 1.0), 'area_sum_err': abs(float(m.face_area.sum()) - 6.0)}
    # outward normals: face centroid minus owner centroid points along the normal
    d = m.face_centroid - m.cell_centroid[m.face_owner]
    out['normal_min_dot'] = float(((d * m.face_normal).sum(1) / np.linalg.norm(d, axis=1)).min())
    out['perm_ok'] = bool(np.array_equal(np.sort(m.perm), np.arange(m.n_nodes)))
    out['face_nodes_are_faces'] = bool((na['kind'][na['face_node']] == 1).all())
    return out


def check_layout(cfg, lay, min_d=2):
    """Every lattice cell belongs to at most one patch, patches cover exactly their lattice rectangles, >= min_d D
    (2 for generated layouts, 1 for a sibling layout, which has one D switched to N)."""
    occ = np.full((6, cfg.lattice, cfg.lattice), -1)
    for p in range(lay['P']):
        blk = occ[lay['wall'][p], lay['a0'][p]:lay['a1'][p], lay['b0'][p]:lay['b1'][p]]
        if (blk >= 0).any():
            return False
        blk[...] = p
    return bool(np.array_equal(occ, lay['lattice_map']) and (lay['ptype'] == D_TYPE).sum() >= min_d)


def mms_errors(cfg, ns):
    """Manufactured solution on graded n^3 meshes (all-Dirichlet walls, midpoint source): volume-weighted L2 errors."""
    a, b0, cc, d0, e = 1.3, 0.2, 1.1, 0.4, 0.7

    def u_ex(p):
        return np.cos(a * p[:, 0] + b0) * np.sin(cc * p[:, 1] + d0) * np.exp(e * p[:, 2])

    def source(p):
        x, y, z = p[:, 0], p[:, 1], p[:, 2]
        u = u_ex(p)
        ux = -a * np.sin(a * x + b0) * np.sin(cc * y + d0) * np.exp(e * z)
        uz = e * u
        fac = (1.0 + 0.5 * np.cos(np.pi * y)) / 1.5
        bx = -cfg.pe * np.sin(np.pi * x) * np.cos(np.pi * z) * fac          # -psi_z
        bz = cfg.pe * np.cos(np.pi * x) * np.sin(np.pi * z) * fac           #  psi_x
        return -cfg.kappa * (-a * a - cc * cc + e * e) * u + bx * ux + bz * uz

    errs = []
    for n in ns:
        c2 = replace(cfg, n_truth=n, lattice=min(cfg.lattice, n))
        m = BoxMesh(c2, 'truth')
        ftype = np.full(m.n_faces, D_TYPE)
        falpha = np.zeros(m.n_faces)
        A = assemble(m, ftype, falpha, c2.pe, c2.kappa)
        _, brhs = boundary_coefficients(m, ftype, falpha, c2.kappa)
        rhs = owner_matrix(m) @ (brhs * u_ex(m.face_centroid)) + source(m.cell_centroid) * m.cell_volume
        u = factorize(A).solve(rhs)
        err = u - u_ex(m.cell_centroid)
        errs.append(float(np.sqrt((err ** 2 * m.cell_volume).sum())))
    return errs


def v1_lineage(n=64, nb=8):
    """Pe = 0, beta = 0, only the z walls, nb x nb constant Dirichlet blocks: the response equals
    patchbed.PatchBed(n, nb) at the v1 query points. Returns the max abs difference."""
    from .patchbed import PatchBed, make_queries
    lattice = 16 if n >= 16 else n
    cfg = Config(n_truth=n, beta=0.0, lattice=lattice, pe=0.0)
    blk = lattice // nb
    walls, a0, b0 = [], [], []
    for f in (0, 1):                                   # v1 bid = f nb^2 + jb nb + kb, cells (x in jb, y in kb)
        for jb in range(nb):
            for kb in range(nb):
                walls.append(4 + f)
                a0.append(jb * blk)
                b0.append(kb * blk)
    P = len(walls)
    lay = {'P': P, 'wall': np.array(walls), 'a0': np.array(a0), 'a1': np.array(a0) + blk, 'b0': np.array(b0),
           'b1': np.array(b0) + blk, 'ptype': np.zeros(P, np.int64), 'alpha': np.zeros(P),
           'lattice_map': np.full((6, lattice, lattice), -1, np.int32)}
    for p in range(P):
        lay['lattice_map'][walls[p], a0[p]:a0[p] + blk, b0[p]:b0[p] + blk] = p
    lay = finish_layout(cfg, lay)
    m = BoxMesh(cfg, 'truth')
    spec = face_spec(m, lay)
    A = assemble(m, spec[1], spec[2], 0.0, 1.0)
    _, brhs = boundary_coefficients(m, spec[1], spec[2], 1.0)
    const = np.asarray((owner_matrix(m) @ sp.diags(brhs) @ shape_matrix(m, lay, spec)).todense())[:, 0::3]
    ours = factorize(A).solve(const)                                                  # [n_cells, P]
    bed = PatchBed(n=n, nb=nb)
    q = make_queries(n)
    ref = bed.basis_responses(q)                                   # [6 nb^2, Q]; rows past the z walls are zero
    assert not ref[P:].any()
    ref = ref[:P]
    ijk = np.rint(q * n - 0.5).astype(int)
    lex = ijk[:, 0] + n * ijk[:, 1] + n * n * ijk[:, 2]
    return float(np.abs(ours[lex].T - ref).max())


def extremum_check(cfg, rng):
    """A D-only layout (insulated elsewhere): every cell value lies within [min g, max g]."""
    lay = make_layout(cfg, 16, rng)
    lay['ptype'][:] = D_TYPE
    lay['alpha'][:] = 0.0
    coef = draw_cases(lay, 2, rng)
    out = solve_layout(cfg, lay, coef, check=False, basis=False)
    truth = mesh_for(cfg, 'truth')
    spec = face_spec(truth, lay)
    g = (shape_matrix(truth, lay, spec) @ coef.reshape(2, -1).T).T[:, spec[0] >= 0]
    cells = out['truth'][:, :truth.n_cells]
    tol = 1e-12 * np.abs(g).max()
    return {'below': float((g.min(1)[:, None] - cells).max()), 'above': float((cells - g.max(1)[:, None]).max()),
            'ok': bool((cells >= g.min(1)[:, None] - tol).all() and (cells <= g.max(1)[:, None] + tol).all())}


def restriction_check(cfg, rng):
    """Volume integral of a random truth field equals that of its restriction to every mesh; D face values
    from the boundary spec on a coarse face equal the area average of the truth D faces."""
    truth = mesh_for(cfg, 'truth')
    u = rng.standard_normal((2, truth.n_nodes))
    out = {}
    tot = (u[:, :truth.n_cells] * truth.cell_volume).sum(1)
    for role in MESH_ROLES[1:]:
        m = BoxMesh(cfg, role)
        r = restrict_lex(cfg, truth, m, u)
        out[f'{role}_volume_integral_err'] = float(np.abs((r[:, :m.n_cells] * m.cell_volume).sum(1) - tot).max())
        ftot = (u[:, truth.n_cells:] * truth.face_area).sum(1)
        out[f'{role}_area_integral_err'] = float(np.abs((r[:, m.n_cells:] * m.face_area).sum(1) - ftot).max())
    lay = make_layout(cfg, 16, rng)
    coef = draw_cases(lay, 1, rng).reshape(1, -1)
    fine = (shape_matrix(truth, lay) @ coef.T).T
    full = np.concatenate([np.zeros((1, truth.n_cells)), fine], 1)
    learn = mesh_for(cfg, 'learn')
    spec = face_spec(learn, lay)
    coarse = (shape_matrix(learn, lay, spec) @ coef.T).T
    rest = restrict_lex(cfg, truth, learn, full)[:, learn.n_cells:]
    out['spec_vs_restricted_face_values'] = float(np.abs(rest - coarse).max())
    return out


# independent parts of the global self-check and their approximate peak memory at 64^3 (GB), so the build can run
# them next to the layouts; 'v1' also holds patchbed.PatchBed's own 64^3 LU (COLAMD, more fill than ours)
CHECK_PARTS = {'geometry': 4.0, 'mms': 10.0, 'extremum': 10.0, 'v1': 25.0}


def selfcheck_part(cfg, name, slow=True):
    """One part of the global checks of design section 1.5 (per-layout superposition and conservation run inside
    every layout job). Returns a dict to merge into the self-check record."""
    rng = np.random.default_rng([cfg.seed, 9, list(CHECK_PARTS).index(name)])
    n = cfg.n_truth
    if name == 'geometry':
        return {'geometry': {r: check_geometry(cfg, r) for r in MESH_ROLES},
                'divergence_max': float(np.abs(divergence(mesh_for(cfg, 'truth'), cfg.pe)).max()),
                'restriction': restriction_check(cfg, rng)}
    if name == 'mms':
        ns = (n // 4, n // 2, n) if slow else (max(8, n // 8), n // 4, n // 2)
        errs = mms_errors(cfg, ns)
        return {'mms': {'n': list(ns), 'l2': errs, 'order': [float(np.log2(errs[i] / errs[i + 1])) for i in range(2)]}}
    if name == 'extremum':
        return {'extremum': extremum_check(cfg, rng)}
    if name == 'v1':
        return {'v1_lineage_max_diff': v1_lineage(n, 8)} if slow else {}
    raise ValueError(name)


def selfcheck(cfg, slow=True):
    """All global checks of design section 1.5, one after the other."""
    out = {}
    for name in CHECK_PARTS:
        out.update(selfcheck_part(cfg, name, slow))
    return out


def assert_selfcheck(sc, layout_checks):
    bad = []
    for role, g in sc.get('geometry', {}).items():
        if g['volume_sum_err'] > 1e-12 or g['area_sum_err'] > 1e-12 or g['normal_min_dot'] < 1 - 1e-12 \
                or not g['perm_ok'] or not g['face_nodes_are_faces']:
            bad.append(f'geometry {role}')
    if sc.get('divergence_max', 0.0) > 1e-12:
        bad.append('divergence')
    if any(v > 1e-10 for v in sc.get('restriction', {}).values()):
        bad.append('restriction')
    if 'mms' in sc and sc['mms']['order'][-1] < 1.7:
        bad.append('mms order')
    if 'extremum' in sc and not sc['extremum']['ok']:
        bad.append('extremum')
    if sc.get('v1_lineage_max_diff', 0.0) > 1e-12:
        bad.append('v1 lineage')
    for tag, c in layout_checks.items():
        if c.get('superposition', 0.0) > 1e-10 or c.get('conservation', 0.0) > 1e-10 \
                or c.get('residual', 0.0) > 1e-10 or not c.get('layout_ok', True):
            bad.append(f'layout {tag}')
    if bad:
        raise AssertionError('self-checks failed: ' + ', '.join(bad))


# ---------------------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------------------

def savez_deterministic(path, **arrays):
    """np.savez with a fixed zip timestamp (1980-01-01) and no compression: equal arrays give byte-identical files,
    so SHA256SUMS does not depend on when (or with how many workers) the data were built. np.load reads it as usual."""
    import zipfile
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for key, val in arrays.items():
            info = zipfile.ZipInfo(key + '.npy', date_time=(1980, 1, 1, 0, 0, 0))
            with zf.open(info, 'w', force_zip64=True) as f:
                np.lib.format.write_array(f, np.asanyarray(val), allow_pickle=False)


def generator_provenance():
    """git commit of the checkout, sha256 of this generator file and whether it differs from the commit (None
    outside a git checkout)."""
    import subprocess
    here = Path(__file__).resolve()
    root = here.parents[3]
    rel = here.relative_to(root).as_posix()
    out = {'generator_source': rel, 'generator_sha256': _sha(here), 'git_commit': None, 'generator_uncommitted': None}
    try:
        out['git_commit'] = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True,
                                           check=True).stdout.strip()
        out['generator_uncommitted'] = bool(subprocess.run(['git', 'status', '--porcelain', '--', rel], cwd=root,
                                                           capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return out


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def layout_table(cfg, design):
    lays = design['layouts']
    pm = max(l['P'] for l in lays)
    L = len(lays)
    tab = {'P': np.array([l['P'] for l in lays]), 'role': design['role'], 'sibling_of': design['sibling_of'],
           'switched_patch': design['switched_patch'],
           'lattice_map': np.stack([l['lattice_map'] for l in lays]).astype(np.int16)}
    for k, dt in (('wall', np.int64), ('a0', np.int64), ('a1', np.int64), ('b0', np.int64), ('b1', np.int64),
                  ('ptype', np.int64), ('alpha', np.float64), ('c1', np.float64), ('c2', np.float64),
                  ('len1', np.float64), ('len2', np.float64), ('parea', np.float64), ('cells', np.int64),
                  ('stratum', np.int64)):
        a = np.full((L, pm), -1 if dt == np.int64 else np.nan, dt)
        for i, l in enumerate(lays):
            a[i, :l['P']] = l[k]
        tab[k] = a
    return tab


def layout_from_table(tab, i):
    P = int(tab['P'][i])
    lay = {'P': P, 'lattice_map': tab['lattice_map'][i].astype(np.int32)}
    for k in ('wall', 'a0', 'a1', 'b0', 'b1', 'ptype', 'alpha', 'c1', 'c2', 'len1', 'len2', 'parea', 'cells',
              'stratum'):
        lay[k] = tab[k][i, :P]
    return lay


def build(cfg, out, workers=1, slow_checks=True, log=print, selfchecks=True, budget=None):
    """Generate the whole dataset into `out` (refuses an existing directory). The global self-check parts and the
    layouts run as one set of jobs under `budget` (default: Budget(workers)). `selfchecks=False` skips the global
    checks (unit-test fixtures only; the per-layout superposition/conservation checks always run)."""
    budget = budget or Budget(workers=workers)
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    part = out.with_name(out.name + '.partial')
    part.mkdir(parents=True, exist_ok=False)
    T = {}
    t0 = time.perf_counter()
    design = draw_design(cfg)
    T['design'] = time.perf_counter() - t0
    lays = design['layouts']
    tables = design['tables']
    log(f'design: {len(lays)} layouts, train {len(tables["train"])}, test {len(tables["test"])} ({T["design"]:.1f}s)')
    truth, learn = mesh_for(cfg, 'truth'), mesh_for(cfg, 'learn')
    # jobs: one per layout; rows of every split that live on it
    n_val_first = design['n_train']
    jobs = {}
    for split in ('train', 'test'):
        for i, r in enumerate(tables[split]):
            keep = split == 'test' or i >= n_val_first                   # truth on 64^3: validation and test only
            jobs.setdefault(r['layout'], []).append((split, i, r['coef'], keep))
    scale = (cfg.n_truth / 64) ** 3                           # expected memory scales roughly with the unknowns
    tasks = [(_check_job, (cfg, name, slow_checks), gb * scale, ('check', name))
             for name, gb in CHECK_PARTS.items()] if selfchecks else []
    for li, rows in sorted(jobs.items(), key=lambda kv: -lays[kv[0]]['P']):          # large bases first
        coefs = np.stack([c for _, _, c, _ in rows])
        truth_rows = [k for k, (_, _, _, keep) in enumerate(rows) if keep]
        sibling = design['role'][li] == ROLE_SIBLING
        tasks.append((_job, (cfg, lays[li], coefs, truth_rows, not sibling, li), budget.job_gb * scale, ('layout', li)))
    fields = {s: np.zeros((len(tables[s]), learn.n_nodes), np.float32) for s in ('train', 'test')}
    tfields = {'val': np.zeros((len(tables['train']) - n_val_first, truth.n_nodes), np.float32),
               'test': np.zeros((len(tables['test']), truth.n_nodes), np.float32)}
    layout_checks, timings, sc = {}, {}, {}
    n_layouts, done = len(jobs), [0]
    t0 = time.perf_counter()

    def on_result(label, result):
        kind, key = label
        if kind == 'check':
            sc.update(result[1])
            log(f'self-check {key} ({time.perf_counter() - t0:.0f}s): {json.dumps(result[1])}')
            return
        li, res = result
        _collect(li, res, jobs, learn, truth, fields, tfields, n_val_first, layout_checks, timings, cfg, lays)
        done[0] += 1
        if done[0] % 8 == 0 or done[0] == n_layouts:
            a, l = headroom()
            log(f'layouts {done[0]}/{n_layouts} ({time.perf_counter() - t0:.0f}s; available {a:.0f} GiB, load {l:.1f})')

    obs = run_tasks(tasks, budget, on_result, log)
    T['jobs'] = time.perf_counter() - t0
    log(f'jobs done ({T["jobs"]:.0f}s); headroom observed: {json.dumps(obs)}')
    assert_selfcheck(sc, layout_checks)
    # files
    t0 = time.perf_counter()
    for role in MESH_ROLES:
        savez_deterministic(part / f'mesh_{role}.npz', **BoxMesh(cfg, role).node_arrays())
    savez_deterministic(part / 'layouts.npz', **layout_table(cfg, design))
    pm = max(l['P'] for l in lays)

    def coef_pad(rows):
        a = np.zeros((len(rows), pm, 3))
        for i, r in enumerate(rows):
            a[i, :len(r['coef'])] = r['coef']
        return a

    tr, te = tables['train'], tables['test']
    savez_deterministic(part / 'train.npz', u=fields['train'], layout=np.array([r['layout'] for r in tr]),
                        coef=coef_pad(tr))
    savez_deterministic(part / 'test.npz', u=fields['test'], layout=np.array([r['layout'] for r in te]),
                        coef=coef_pad(te), kind=np.array([r['kind'] for r in te]),
                        base_index=np.array([r['base_index'] for r in te]), knob=np.array([r['knob'] for r in te]),
                        target_patch=np.array([r['target_patch'] for r in te]),
                        stratum=np.array([r['stratum'] for r in te]),
                        heldout_layout=np.array([design['role'][design['sibling_of'][r['layout']]]
                                                 if design['role'][r['layout']] == ROLE_SIBLING
                                                 else design['role'][r['layout']] for r in te]) == ROLE_HELDOUT)
    savez_deterministic(part / 'truth_val.npz', u=tfields['val'])
    savez_deterministic(part / 'truth_test.npz', u=tfields['test'])
    T['write'] = time.perf_counter() - t0
    agg = {k: float(sum(t[k] for t in timings.values())) for k in ('assemble', 'splu', 'solve', 'restrict')}
    manifest = {'version': VERSION, 'config': asdict(cfg), 'n_train': design['n_train'], 'n_val': design['n_val'],
                'n_test': len(te), 'n_layouts': len(lays),
                'layout_roles': {'train': int((design['role'] == ROLE_TRAIN).sum()),
                                 'heldout': int((design['role'] == ROLE_HELDOUT).sum()),
                                 'sibling': int((design['role'] == ROLE_SIBLING).sum())},
                'knob_std': design['knob_std'], 'knobs': list(KNOBS), 'strata': list(STRATA),
                'meshes': {r: {'n': int(BoxMesh(cfg, r).n), 'nodes': int(BoxMesh(cfg, r).n_nodes)} for r in MESH_ROLES},
                'selfcheck': sc,
                'layout_checks_max': {k: float(max(c.get(k, 0.0) for c in layout_checks.values()))
                                      for k in ('superposition', 'conservation', 'residual')},
                'timings_total_s': T, 'timings_sum_over_layouts_s': agg, 'budget': asdict(budget),
                'headroom_observed': obs,
                'worker_maxrss_gb_max': max((t.get('worker_maxrss_gb') or 0.0) for t in timings.values()),
                'numpy': np.__version__, 'scipy': __import__('scipy').__version__, **generator_provenance(),
                'files': {}}
    for f in sorted(part.glob('*.npz')):
        manifest['files'][f.name] = _sha(f)
    (part / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (part / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in sorted(manifest['files'].items())))
    (part / 'layout_timings.json').write_text(json.dumps(timings, indent=1) + '\n')
    part.rename(out)
    return manifest


def _collect(li, res, jobs, learn, truth, fields, tfields, n_val_first, layout_checks, timings, cfg, lays):
    k_truth = 0
    for k, (split, i, _, keep) in enumerate(jobs[li]):
        fields[split][i] = learn.to_nodes(res['learn'][k]).astype(np.float32)
        if keep:
            tgt = tfields['val'] if split == 'train' else tfields['test']
            tgt[i - n_val_first if split == 'train' else i] = truth.to_nodes(res['truth'][k_truth]).astype(np.float32)
            k_truth += 1
    sibling = lays[li]['ptype'].size and 'sibling' in lays[li]
    layout_checks[li] = dict(res['checks'], layout_ok=check_layout(cfg, lays[li], 1 if sibling else 2))
    timings[li] = dict(res['timings'], nnz_lu=res['nnz_lu'], worker_maxrss_gb=res.get('worker_maxrss_gb'))


# ---------------------------------------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------------------------------------

def load(data_dir, split, role='learn'):
    """(mesh node arrays, layout table, split arrays) of a built dataset."""
    d = Path(data_dir)
    return (dict(np.load(d / f'mesh_{role}.npz')), dict(np.load(d / 'layouts.npz')),
            dict(np.load(d / f'{split}.npz')))


def config_of(data_dir):
    c = json.loads((Path(data_dir) / 'manifest.json').read_text())['config']
    return Config(**{k: tuple(v) if isinstance(v, list) else v for k, v in c.items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--selfcheck', action='store_true', help='only the global self-checks, printed as JSON')
    ap.add_argument('--out', default='data/r0s_v1')
    ap.add_argument('--workers', type=int, default=1, help='maximum parallel jobs (each single-threaded)')
    ap.add_argument('--min-available-gb', type=float, default=Budget().min_available_gb)
    ap.add_argument('--max-load', type=float, default=Budget().max_load)
    ap.add_argument('--job-gb', type=float, default=Budget().job_gb, help='expected peak of one 64^3 layout job')
    ap.add_argument('--quick-checks', action='store_true', help='MMS on smaller meshes, no v1 lineage (smoke only)')
    for f in ('n_truth', 'lattice', 'train_layouts', 'heldout_layouts', 'train_per_layout', 'val_per_layout',
              'test_per_layout', 'heldout_per_layout', 'intervention_every'):
        ap.add_argument('--' + f.replace('_', '-'), type=int, default=getattr(Config(), f))
    ap.add_argument('--pe', type=float, default=Config().pe, help='0 = pure diffusion')
    ap.add_argument('--beta', type=float, default=Config().beta)
    args = ap.parse_args()
    cfg = Config(**{k: getattr(args, k) for k in ('n_truth', 'lattice', 'train_layouts', 'heldout_layouts',
                                                  'train_per_layout', 'val_per_layout', 'test_per_layout',
                                                  'heldout_per_layout', 'intervention_every', 'pe', 'beta')})
    if args.selfcheck:
        print(json.dumps(selfcheck(cfg, slow=not args.quick_checks), indent=2))
    if args.build:
        budget = Budget(workers=args.workers, min_available_gb=args.min_available_gb, max_load=args.max_load,
                        job_gb=args.job_gb)
        m = build(cfg, args.out, args.workers, slow_checks=not args.quick_checks,
                  log=lambda s: print(time.strftime('%H:%M:%S'), s, flush=True), budget=budget)
        print(json.dumps({k: m[k] for k in ('n_train', 'n_val', 'n_test', 'n_layouts', 'timings_total_s',
                                             'timings_sum_over_layouts_s', 'layout_checks_max', 'headroom_observed',
                                             'worker_maxrss_gb_max')}, indent=2))


if __name__ == '__main__':
    main()
