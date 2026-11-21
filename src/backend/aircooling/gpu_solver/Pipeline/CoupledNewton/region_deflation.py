"""Region coarse correction for conjugate heat transfer (docs/research/2026-10-15-cht-heatsink.md section 2; CHT1 README).

A solid region whose conductivity is thousands of times the fluid's has a temperature LEVEL that only the weak
fluid-side interface conductance fixes: inside the solid the constant mode costs nothing (no internal flux), across
the interface it costs the fluid's small conductance. That eigenvalue sits ~kappa_s / kappa_f * (L / h)^2 below the
solid's own stiffness, a multigrid built for the flow does not represent it, and GMRES stalls on it (CHT-1 pilot,
measured: 1,600 iterations without a tenfold reduction from the fifth Newton step on). The classical cure is a
coarse space that carries exactly that mode: one column per solid material, the indicator of its temperature
unknowns, with the Galerkin coarse matrix E = Z^T A Z (k x k, k = number of solid materials) solved exactly.

    z = Q r + M (r - A Q r),    Q = Z E^{-1} Z^T

(the coarse correction first, then the flow preconditioner M on what is left). A Z is computed once per assembled
Jacobian (a sparse product with k columns), so an application costs k gathers, k scatters, k axpys and one k x k
solve -- no extra operator call per Krylov column. With no solid material the wrapper is not built (the shipped path,
unchanged). Attributes the Newton driver reads or writes on the preconditioner (target_rtol, cycle_policy, ...) are
forwarded to M.
"""
from __future__ import annotations

import torch


def region_columns(case_solver, n_cell: int, t_offset: int, device=None):
    """Index tensors of the temperature unknowns of each solid material (empty list when the case has none).

    Schema-v2 cases: ``_region_material_id`` / ``_region_material_kind`` set by the application's region physics
    (kind 1 = solid); the CHT-0a npz route: ``_cht_solid_cell`` (one solid material)."""
    mid = getattr(case_solver, "_region_material_id", None)
    kind = getattr(case_solver, "_region_material_kind", None)
    cols = []
    if mid is not None and kind is not None:
        mid = mid.reshape(-1).long()
        for m in torch.nonzero(kind.reshape(-1) == 1).reshape(-1).tolist():
            cells = torch.nonzero(mid == m).reshape(-1)
            if cells.numel():
                cols.append(cells)
    else:
        solid = getattr(case_solver, "_cht_solid_cell", None)
        if solid is not None and bool(solid.any()):
            cols.append(torch.nonzero(solid.reshape(-1)).reshape(-1))
    for c in cols:
        if int(c.max()) >= n_cell:
            raise ValueError(f"region deflation: cell index {int(c.max())} beyond the {n_cell} cells")
    dev = device if device is not None else (cols[0].device if cols else None)
    return [(c + t_offset).to(dev) for c in cols]


class RegionDeflation:
    """``z = Q r + M (r - A Q r)`` (module docstring). ``A``: the assembled Jacobian (torch sparse, n x n);
    ``M``: the flow preconditioner (callable or None = identity); ``columns``: index tensors (``region_columns``)."""

    def __init__(self, M, A, columns, n: int):
        object.__setattr__(self, "primary", M)
        self.columns = [c.long() for c in columns]
        self.n = int(n)
        if isinstance(A, torch.Tensor):
            dtype = A.dtype if A.is_floating_point() else torch.float64
            device = A.device
        else:
            # an operator that only takes one flat vector (``jacobian._BlockOperator`` of a block assembly,
            # ``--fixed-jacobian-pattern blocks``): float64 as every Newton operator, the columns' device
            dtype = torch.float64
            device = self.columns[0].device
        Z = torch.zeros(self.n, len(self.columns), dtype=dtype, device=device)
        for j, c in enumerate(self.columns):
            Z[c, j] = 1.0
        if isinstance(A, torch.Tensor):
            AZ = A @ Z
        else:
            AZ = torch.stack([A @ Z[:, j].contiguous() for j in range(Z.shape[1])], 1)
        self.AZ = AZ.to_dense() if AZ.layout != torch.strided else AZ
        E = Z.T @ self.AZ
        self.E = E
        # the coarse matrix: well-conditioned at k = 1-3; an exactly singular E means a solid with no conductance to
        # anything that fixes its level (a modelling error, refused rather than regularised)
        if torch.linalg.matrix_rank(E).item() < E.shape[0]:
            raise ValueError(f"region deflation: singular coarse matrix E = {E.tolist()}")
        self.E_inv = torch.linalg.inv(E)

    def __call__(self, r):
        shape = r.shape
        R = r.reshape(self.n, -1)
        zt_r = torch.stack([R.index_select(0, c).sum(0) for c in self.columns], 0)        # [k, m]
        y = self.E_inv.to(R.dtype) @ zt_r                                                  # [k, m]
        r1 = R - self.AZ.to(R.dtype) @ y
        M = object.__getattribute__(self, "primary")
        z = r1 if M is None else M(r1.reshape(shape)).reshape(self.n, -1)
        z = z.clone()
        for j, c in enumerate(self.columns):
            z.index_add_(0, c, y[j:j + 1].expand(c.numel(), -1))
        return z.reshape(shape)

    def __getattr__(self, name):
        # only reached for attributes this wrapper does not have: the flow preconditioner's
        return getattr(object.__getattribute__(self, "primary"), name)

    def __setattr__(self, name, value):
        if name in ("columns", "n", "AZ", "E", "E_inv"):
            object.__setattr__(self, name, value)
        else:
            M = object.__getattribute__(self, "primary")
            if M is not None:
                setattr(M, name, value)
            object.__setattr__(self, name, value)
