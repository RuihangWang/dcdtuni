"""Block ILU(0) of a block-sparse matrix on the device, level-scheduled.

``BlockILU0(row, col, n, F)`` plans the incomplete factorisation A ~ L U on the matrix's own block pattern
(no fill): L unit lower triangular, U upper triangular with the factored diagonal blocks, both F x F blocks
on the pattern of A.  ``factor(val)`` computes the factors of the block values ``val [nnzb, F, F]`` (the IKJ
form: row i, for every lower entry k of the row in increasing k, L_ik = A_ik U_kk^-1 and A_ij -= L_ik U_kj on
the entries j > k of row k that row i holds), ``solve(b)`` applies (L U)^-1 to ``b [n, F]``.

Level scheduling: the rows of one level have no lower entry in the same or a later level, so they are
factored, and their unknowns substituted, together.  The rows of a level take their lower entries in order
(position t = 0, 1, ...), so every batched update of a level and position touches distinct rows and reads only
finished rows.  The plan (levels, positions, the update triples) is built once per pattern on the host; the
numeric factorisation and the triangular solves are batched block products on the device.

``apply_field_blocked(v)`` is the same action on a field-blocked vector (unknown ``f * n + i``), the form a
``preconditioner_factory`` of ``solve_newton`` returns.
"""
from __future__ import annotations

import numpy as np
import torch


class BlockILU0:
    """The plan of block ILU(0) on the block pattern ``(row, col)`` (``nnzb`` entries, sorted by row then col,
    diagonal present in every row) of an ``n x n`` block matrix with ``F x F`` blocks."""

    def __init__(self, row: np.ndarray, col: np.ndarray, n: int, F: int, device):
        row = np.asarray(row, dtype=np.int64)
        col = np.asarray(col, dtype=np.int64)
        key = row * n + col
        if np.any(np.diff(key) <= 0):
            raise ValueError("the block pattern must be sorted by (row, col) without duplicates")
        self.n, self.F, self.nnzb, self.device = int(n), int(F), int(row.size), device
        ptr = np.zeros(n + 1, dtype=np.int64)
        np.add.at(ptr, row + 1, 1)
        ptr = np.cumsum(ptr)
        diag = np.searchsorted(key, np.arange(n, dtype=np.int64) * (n + 1))
        if np.any(diag >= key.size) or np.any(key[np.minimum(diag, key.size - 1)] != np.arange(n) * (n + 1)):
            raise ValueError("every row needs its diagonal block")
        self.diag = diag
        lower = col < row
        upper = col > row
        n_low = np.add.reduceat(lower.astype(np.int64), ptr[:-1]) if row.size else np.zeros(n, np.int64)
        n_low[ptr[:-1] == ptr[1:]] = 0
        # factorisation / forward levels: 1 + the largest level of the rows a row's lower entries name
        level = np.zeros(n, dtype=np.int64)
        for i in range(n):
            a, b = ptr[i], ptr[i] + n_low[i]
            if b > a:
                level[i] = level[col[a:b]].max() + 1
        # backward levels from the upper entries, in reverse
        ulevel = np.zeros(n, dtype=np.int64)
        for i in range(n - 1, -1, -1):
            a, b = diag[i] + 1, ptr[i + 1]
            if b > a:
                ulevel[i] = ulevel[col[a:b]].max() + 1
        self.n_levels, self.n_ulevels = int(level.max()) + 1, int(ulevel.max()) + 1
        # position of every lower entry within its row
        pos = np.arange(row.size) - ptr[row]
        # update triples (slot_ij, slot_ik, slot_kj) of every lower entry (i, k): the upper entries (k, j) of row k
        # that row i holds
        lo = np.nonzero(lower)[0]
        i_lo, k_lo = row[lo], col[lo]
        u_start, u_end = diag[k_lo] + 1, ptr[k_lo + 1]
        cnt = u_end - u_start
        rep = np.repeat(np.arange(lo.size), cnt)
        offs = np.arange(rep.size) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        s_kj = u_start[rep] + offs
        i_r = i_lo[rep]
        j = col[s_kj]
        want = i_r * n + j
        s_ij = np.searchsorted(key, want)
        found = (s_ij < key.size) & (key[np.minimum(s_ij, key.size - 1)] == want)
        rep, s_kj, s_ij = rep[found], s_kj[found], s_ij[found]
        s_ik = lo[rep]
        t = lambda a: torch.as_tensor(np.ascontiguousarray(a), dtype=torch.long, device=device)  # noqa: E731
        # factorisation groups: (level, position) -> the lower entries, their pivots and their updates
        self.groups = []
        lev_lo, pos_lo = level[i_lo], pos[lo]
        lev_up, pos_up = lev_lo[rep], pos_lo[rep]
        order_lo = np.lexsort((pos_lo, lev_lo))
        order_up = np.lexsort((pos_up, lev_up))
        gk_lo = lev_lo[order_lo] * (n + 1) + pos_lo[order_lo]
        gk_up = lev_up[order_up] * (n + 1) + pos_up[order_up]
        lo_sorted, up_sorted = order_lo, order_up
        bounds_lo = np.flatnonzero(np.diff(np.concatenate([[-1], gk_lo, [-2]])))
        keys_lo = gk_lo[bounds_lo[:-1]]
        starts_up = np.searchsorted(gk_up, keys_lo, side="left")
        ends_up = np.searchsorted(gk_up, keys_lo, side="right")
        self.level_rows = [t(r) for r in _split(np.arange(n), level, self.n_levels)]
        for g in range(len(keys_lo)):
            a, b = bounds_lo[g], bounds_lo[g + 1]
            sel = lo_sorted[a:b]
            ua, ub = starts_up[g], ends_up[g]
            us = up_sorted[ua:ub]
            self.groups.append(dict(level=int(keys_lo[g] // (n + 1)), slot=t(lo[sel]), k=t(k_lo[sel]),
                                    ij=t(s_ij[us]), ik=t(s_ik[us]), kj=t(s_kj[us])))
        # schedule: per level, its groups in position order, then the diagonal inverses of its rows
        self.schedule = [[] for _ in range(self.n_levels)]
        for gr in self.groups:
            self.schedule[gr["level"]].append(gr)
        # forward and backward substitution per level: (rows, entry slots, entry columns, entry rows)
        rows_by = _split(np.arange(n), level, self.n_levels)
        ents_by = _split(lo, level[i_lo], self.n_levels)
        self.fwd = [(t(r), t(e), t(col[e]), t(row[e])) for r, e in zip(rows_by, ents_by)]
        up_all = np.nonzero(upper)[0]
        rows_by = _split(np.arange(n), ulevel, self.n_ulevels)
        ents_by = _split(up_all, ulevel[row[up_all]], self.n_ulevels)
        self.bwd = [(t(r), t(e), t(col[e]), t(row[e])) for r, e in zip(rows_by, ents_by)]
        self.diag_t = t(diag)
        self.val = None
        self.inv_diag = None

    @torch.no_grad()
    def factor(self, val: torch.Tensor) -> "BlockILU0":
        """The factors of the block values ``val [nnzb, F, F]`` (copied; ``val`` is not changed)."""
        if val.shape[0] != self.nnzb:
            raise ValueError(f"{val.shape[0]} blocks for a pattern of {self.nnzb}")
        a = val.detach().clone()
        inv = torch.zeros(self.n, self.F, self.F, dtype=a.dtype, device=a.device)
        for lv in range(self.n_levels):
            for gr in self.schedule[lv]:
                Lik = torch.bmm(a.index_select(0, gr["slot"]), inv.index_select(0, gr["k"]))
                a.index_copy_(0, gr["slot"], Lik)
                if gr["ij"].numel():
                    upd = torch.bmm(a.index_select(0, gr["ik"]), a.index_select(0, gr["kj"]))
                    a.index_add_(0, gr["ij"], -upd)
            rows = self.level_rows[lv]
            if rows.numel():
                inv.index_copy_(0, rows, torch.linalg.inv(a.index_select(0, self.diag_t.index_select(0, rows))))
        self.val, self.inv_diag = a, inv
        return self

    @torch.no_grad()
    def solve(self, b: torch.Tensor) -> torch.Tensor:
        """(L U)^-1 b for ``b [n, F]``."""
        a, inv = self.val, self.inv_diag
        y = b.clone()
        for rows, ent, cols, erow in self.fwd:
            if ent.numel():
                c = torch.bmm(a.index_select(0, ent), y.index_select(0, cols).unsqueeze(-1)).squeeze(-1)
                y.index_add_(0, erow, -c)
        x = y
        for rows, ent, cols, erow in self.bwd:
            if ent.numel():
                c = torch.bmm(a.index_select(0, ent), x.index_select(0, cols).unsqueeze(-1)).squeeze(-1)
                x.index_add_(0, erow, -c)
            if rows.numel():
                x.index_copy_(0, rows, torch.bmm(inv.index_select(0, rows),
                                                 x.index_select(0, rows).unsqueeze(-1)).squeeze(-1))
        return x


    def apply_field_blocked(self, v: torch.Tensor) -> torch.Tensor:
        """(L U)^-1 v for a field-blocked vector ``v [F * n]`` (unknown ``f * n + i``)."""
        x = self.solve(v.reshape(self.F, self.n).t().contiguous())
        return x.t().reshape(-1)


def _split(items: np.ndarray, label: np.ndarray, count: int):
    """``items`` grouped by ``label`` (0 .. count - 1), each group in its original order."""
    order = np.argsort(label, kind="stable")
    bounds = np.searchsorted(label[order], np.arange(count + 1))
    return [items[order[bounds[k]:bounds[k + 1]]] for k in range(count)]


def ilu_k_pattern(row: np.ndarray, col: np.ndarray, n: int, k: int):
    """The block pattern of ILU(k): the entries of level at most ``k`` of the symbolic factorisation (level 0 on the
    pattern ``(row, col)`` of A, sorted by row then col; a fill entry (i, j) from the elimination of a lower entry
    (i, m) with an upper entry (m, j) of row m gets level lev(i, m) + lev(m, j) + 1, the smallest over all m).
    Returns ``(row, col)`` sorted by row then col; ``k = 0`` returns the pattern of A."""
    import heapq

    row = np.asarray(row, dtype=np.int64)
    col = np.asarray(col, dtype=np.int64)
    if k <= 0:
        return row, col
    ptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(ptr, row + 1, 1)
    ptr = np.cumsum(ptr)
    upper = [None] * n
    out_r, out_c = [], []
    for i in range(n):
        cols = col[ptr[i]:ptr[i + 1]].tolist()
        lev = dict.fromkeys(cols, 0)
        heap = [c for c in cols if c < i]
        heapq.heapify(heap)
        done = set()
        while heap:
            m = heapq.heappop(heap)
            if m in done:
                continue
            done.add(m)
            lm = lev[m]
            ucols, ulev = upper[m]
            for j, lj in zip(ucols, ulev):
                level = lm + lj + 1
                if level > k:
                    continue
                old = lev.get(j)
                if old is None or level < old:
                    lev[j] = level
                    if old is None and j < i:
                        heapq.heappush(heap, j)
        items = sorted(lev.items())
        upper[i] = ([j for j, _ in items if j > i], [lv for j, lv in items if j > i])
        out_r.extend([i] * len(items))
        out_c.extend(j for j, _ in items)
    return np.asarray(out_r, dtype=np.int64), np.asarray(out_c, dtype=np.int64)


def field_blocked_factory(F: int, device, *, fill: int = 0, order: str = "rcm", pin=None, log=None):
    """``preconditioner_factory(blocks, x)``: block ILU(``fill``) on the device of the cell (or node) ``F x F`` blocks of
    the assembled field-blocked Jacobian ``blocks["J"]`` (unknown ``f * n + i``), factorised and applied on the GPU.
    The blocks are taken in the reverse Cuthill-McKee order of their own pattern (``order="rcm"``, default) or in the
    natural order; the symbolic pattern of ILU(k) and the level-scheduled plan are built once per block pattern.
    ``pin(x)``: None, or a callable returning a field-blocked row (or None) whose diagonal entry is raised by the
    largest block entry for the factorisation only (a free pressure level).  ``log`` (a list) receives each build's
    level count, block count and seconds."""
    import time

    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import reverse_cuthill_mckee

    from Pipeline.CoupledNewton import block_amg

    cache = {}

    def factory(blocks, x=None):
        started = time.perf_counter()
        J = blocks["J"]
        J = J if J.layout == torch.sparse_csr else J.to_sparse_csr()
        n = int(J.shape[0]) // int(F)
        A = block_amg.from_field_blocked_csr(J.crow_indices(), J.col_indices(), J.values(), n, F)
        if "perm" not in cache:
            r, c = A.row.long().cpu().numpy(), A.col.long().cpu().numpy()
            if order == "rcm":
                g = csr_matrix((np.ones(r.size), (r, c)), shape=(n, n))
                perm = np.asarray(reverse_cuthill_mckee((g + g.T).tocsr(), symmetric_mode=True), dtype=np.int64)
            elif order == "mc":                  # a distance-1 colouring: every colour is one level of the schedule
                from types import SimpleNamespace
                from Pipeline.CoupledNewton.compact_operator import multicolour_order
                graph = SimpleNamespace(N=n, row=A.row.long(), col=A.col.long(), device=A.row.device)
                perm = np.asarray(torch.as_tensor(multicolour_order(graph)[0]).cpu(), dtype=np.int64)
            elif order == "natural":
                perm = np.arange(n, dtype=np.int64)
            else:
                raise ValueError(f"order must be 'rcm', 'mc' or 'natural', not {order!r}")
            inv = np.empty(n, dtype=np.int64)
            inv[perm] = np.arange(n, dtype=np.int64)
            cache["perm"] = torch.as_tensor(perm, device=device)
            cache["inv"] = torch.as_tensor(inv, device=device)
        perm_t, inv_t = cache["perm"], cache["inv"]
        key = inv_t.index_select(0, A.row.long()) * n + inv_t.index_select(0, A.col.long())
        sort = torch.argsort(key)
        key_sorted = key.index_select(0, sort)
        plan = cache.get("plan")
        if plan is None or plan[0].numel() != key_sorted.numel() or not torch.equal(plan[0], key_sorted):
            rows = (key_sorted // n).cpu().numpy()
            cols = (key_sorted % n).cpu().numpy()
            er, ec = ilu_k_pattern(rows, cols, n, int(fill))
            ekey = torch.as_tensor(er * n + ec, device=device)
            plan = (key_sorted, BlockILU0(er, ec, n, F, device), torch.searchsorted(ekey, key_sorted), int(er.size), ekey)
            cache["plan"] = plan
        ext = torch.zeros(plan[3], F, F, dtype=A.val.dtype, device=A.val.device)
        ext.index_copy_(0, plan[2], A.val.index_select(0, sort))
        row = pin(x) if pin is not None else None
        if row is not None:
            field, cell = int(row) // n, int(row) % n
            p = int(inv_t[cell])
            at = int(torch.searchsorted(plan[4], torch.tensor([p * n + p], device=device)))
            ext[at, field, field] += A.val.abs().max()
        ilu = plan[1].factor(ext)
        if log is not None:
            if A.val.is_cuda:
                torch.cuda.synchronize(A.val.device)
            log.append({"kind": f"bilu{fill}-{order}", "levels": plan[1].n_levels, "blocks": plan[1].nnzb,
                        "blocks_A": int(key_sorted.numel()), "pinned": row, "seconds": time.perf_counter() - started})

        def apply(v):
            cells = v.reshape(F, n).t().index_select(0, perm_t)
            return ilu.solve(cells).index_select(0, inv_t).t().reshape(-1)

        apply.ilu = ilu                          # the factors, for diagnostics
        return apply

    return factory
