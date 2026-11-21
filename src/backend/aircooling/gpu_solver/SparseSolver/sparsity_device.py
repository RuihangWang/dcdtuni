"""The Jacobian sparsity of the lattice colouring built on the device.

``build_sparsity(colouring="lattice", device_build=True)`` (solver flag ``--device-sparsity``) forms here, with torch
on the solver's device, what the host path forms with scipy / numpy: the cell labels (``lattice_labels``: grid lines
= connected components of each axis's faces, a cell's label = its rank along its line), the 2-hop cell pattern
(``adjacency @ adjacency``), the label-difference set of the probed rows (``_lattice_difference_set``) and the
colouring ``(a i + b j + c k) mod m``.  Only the modulus search runs on the host: it reads the few dozen label
differences, not the mesh.  Every result equals the host path's (the same labels, the same pattern as a set, the
same differences, the same colours; ``tests/test_sparsity_device.py``), so the colouring cache key and its entries
are shared by both paths.

Why: on a new 104^3 mesh the host lattice colouring took 3.2 s and the host 2-hop
pattern is rebuilt by every run (the sparsity timer, 0.6-1.2 s with a cache hit).
"""
from __future__ import annotations

import numpy as np
import torch


class DeviceCSR:
    """A row pattern on the device: ``indptr`` [rows + 1] and ``indices`` int64 tensors (no values), ``shape``."""

    def __init__(self, indptr: torch.Tensor, indices: torch.Tensor, shape):
        self.indptr, self.indices, self.shape = indptr, indices, tuple(int(s) for s in shape)

    def row_lengths(self) -> torch.Tensor:
        return self.indptr[1:] - self.indptr[:-1]


def components(a: torch.Tensor, b: torch.Tensor, n: int) -> torch.Tensor:
    """Per node of the undirected graph with edges ``(a, b)``: the smallest node index of its connected component
    (min-label propagation over the edges with pointer jumping, until nothing changes)."""
    lab = torch.arange(int(n), device=a.device)
    if a.numel() == 0:
        return lab
    while True:
        m = torch.minimum(lab[a], lab[b])
        new = lab.clone()
        new.scatter_reduce_(0, a, m, "amin")
        new.scatter_reduce_(0, b, m, "amin")
        while True:                                   # pointer jumping: lab[i] is a node of i's component
            jumped = torch.minimum(new, new[new])
            if torch.equal(jumped, new):
                break
            new = jumped
        if torch.equal(new, lab):
            return lab
        lab = new


def lattice_labels_device(positions: torch.Tensor | None, owner: torch.Tensor, neighbour: torch.Tensor,
                          num_cells: int) -> torch.Tensor | None:
    """``jacobian_assembly.lattice_labels`` on the device: int64 ``[N, 3]`` equal to the host labels, or None.

    The host ranks the cells of a line by ``np.lexsort((cells, position, line))``; here two stable sorts (by
    position, then by line) give the same order, and the component representative replaces scipy's component
    number (only the grouping matters for the rank)."""
    if positions is None or num_cells == 0 or owner.numel() == 0:
        return None
    if tuple(positions.shape) != (num_cells, 3):
        return None
    device = owner.device
    positions = positions.to(device=device, dtype=torch.float64)
    owner, neighbour = owner.long(), neighbour.long()
    axis = torch.argmax((positions[neighbour] - positions[owner]).abs(), dim=1)
    labels = torch.zeros(num_cells, 3, dtype=torch.long, device=device)
    rank = torch.arange(num_cells, device=device)
    for a in range(3):
        on = axis == a
        line = components(owner[on], neighbour[on], num_cells)
        by_pos = torch.argsort(positions[:, a], stable=True)
        order = by_pos[torch.argsort(line[by_pos], stable=True)]
        lo = line[order]
        start = torch.ones(num_cells, dtype=torch.bool, device=device)
        start[1:] = lo[1:] != lo[:-1]
        first = torch.cummax(torch.where(start, rank, torch.zeros_like(rank)), dim=0).values
        labels[order, a] = rank - first
    return labels


def two_hop_pattern(o: torch.Tensor, n: torch.Tensor, num_cells: int) -> DeviceCSR:
    """The pattern of ``(A + I) @ (A + I)`` for the symmetric adjacency ``A`` of the cell pairs ``(o, n)`` (the
    host path's ``cell_pattern``), rows sorted, every entry once."""
    device = o.device
    N = int(num_cells)
    idx = torch.arange(N, device=device)
    r = torch.cat([o, n, idx]).long()
    c = torch.cat([n, o, idx]).long()
    key = torch.unique(r * N + c)                        # sorted: CSR order
    r, c = key // N, key % N
    indptr = torch.zeros(N + 1, dtype=torch.long, device=device)
    indptr[1:] = torch.cumsum(torch.bincount(r, minlength=N), 0)
    deg = indptr[1:] - indptr[:-1]
    # every (i, j) in A+I meets every (j, k): repeat i deg(j) times, k = the columns of row j
    reps = deg[c]
    total = int(reps.sum())
    i2 = torch.repeat_interleave(r, reps, output_size=total)
    base = torch.repeat_interleave(indptr[:-1][c] - torch.cumsum(reps, 0) + reps, reps, output_size=total)
    k2 = c[base + torch.arange(total, device=device)]
    key2 = torch.unique(i2 * N + k2)
    del i2, k2, base
    r2 = key2 // N
    indptr2 = torch.zeros(N + 1, dtype=torch.long, device=device)
    indptr2[1:] = torch.cumsum(torch.bincount(r2, minlength=N), 0)
    return DeviceCSR(indptr2, key2 % N, (N, N))


def lattice_difference_set_device(probed, labels: torch.Tensor, max_offsets: int, max_differences: int):
    """``jacobian_assembly._lattice_difference_set`` for ``DeviceCSR`` patterns: ``(differences, reason)`` with the
    same differences (numpy int64 [D, 3], sorted as ``np.unique(axis=0)`` sorts them) or None and the same reason.

    The rows' offset patterns are reduced on the device (each row's set of offset codes as a bit signature,
    ``torch.unique(dim=0)``); the pairwise differences of the few distinct patterns are formed on the host."""
    found = []
    for pattern in probed:
        rows = pattern.shape[0]
        lengths = pattern.row_lengths()
        if rows == 0 or not bool((lengths > 0).any()):
            continue
        device = pattern.indices.device
        columns = pattern.indices.long()
        row_of = torch.repeat_interleave(torch.arange(rows, device=device), lengths,
                                         output_size=int(columns.numel()))
        reference = torch.full((rows,), torch.iinfo(torch.long).max, dtype=torch.long, device=device)
        reference.scatter_reduce_(0, row_of, columns, "amin")
        shift = labels[columns] - labels[reference[row_of]]
        low = shift.min(dim=0).values
        span = shift.max(dim=0).values - low + 1
        if float(np.prod(span.cpu().numpy().astype(np.float64))) > 2.0 ** 62:
            return None, "label offsets too spread to encode"
        s1, s2 = int(span[1]), int(span[2])
        flat = ((shift[:, 0] - low[0]) * s1 + (shift[:, 1] - low[1])) * s2 + (shift[:, 2] - low[2])
        del shift
        unique, code = torch.unique(flat, return_inverse=True)
        del flat
        K = int(unique.numel())
        if K > max_offsets:
            return None, f"{K} distinct in-row label offsets (> {max_offsets})"
        offsets = torch.stack([unique // (s1 * s2) + low[0], (unique // s2) % s1 + low[1], unique % s2 + low[2]],
                              dim=1).cpu().numpy()
        # each row's set of codes as W words of 62 bits; (row, code) pairs deduplicated first (labels may repeat)
        pair = torch.unique(row_of * K + code)
        prow, pcode = pair // K, pair % K
        W = (K + 61) // 62
        sig = torch.zeros(rows * W, dtype=torch.long, device=device)
        sig.index_add_(0, prow * W + pcode // 62, torch.ones_like(pcode).bitwise_left_shift(pcode % 62))
        sig = sig.view(rows, W)[lengths > 0]
        patterns = torch.unique(sig, dim=0).cpu().numpy()
        bits = np.arange(K)
        for words in patterns:
            present_mask = (words[bits // 62] >> (bits % 62)) & 1
            present = offsets[present_mask.astype(bool)]
            found.append((present[:, None, :] - present[None, :, :]).reshape(-1, 3))
    if not found:
        return np.zeros((0, 3), dtype=np.int64), None
    differences = np.unique(np.concatenate(found), axis=0)
    first = np.where(differences[:, 0] != 0, differences[:, 0],
                     np.where(differences[:, 1] != 0, differences[:, 1], differences[:, 2]))
    differences = differences[first > 0]
    if differences.shape[0] > max_differences:
        return None, f"{differences.shape[0]} label differences (> {max_differences})"
    return differences, None


def lattice_colouring_device(probed, labels: torch.Tensor | None, *, span: int, max_offsets: int,
                             max_differences: int, search):
    """``jacobian_assembly._lattice_colouring`` for ``DeviceCSR`` patterns and device labels: ``(colour, info)``
    with ``colour`` a numpy int32 array equal to the host colouring, or ``(None, info)`` with the reason."""
    bound = max(int(p.row_lengths().max()) if p.shape[0] else 0 for p in probed)
    info = {"lower_bound": bound}
    if labels is None:
        info["reason"] = "no cell centres (or no internal faces) to label the cells by"
        return None, info
    differences, reason = lattice_difference_set_device(probed, labels, max_offsets, max_differences)
    if differences is None:
        info["reason"] = reason
        return None, info
    found = search(differences, bound, bound + span)
    info.update(differences=int(differences.shape[0]),
                extent=[int(e) + 1 for e in labels.max(dim=0).values.cpu().numpy()])
    if found is None:
        info["reason"] = f"no modulus in [{bound}, {bound + span}] separates the {differences.shape[0]} differences"
        return None, info
    m, coefficients = found
    coeffs = torch.as_tensor(np.asarray(coefficients, dtype=np.int64), device=labels.device)
    residue = (labels * coeffs).sum(dim=1) % m           # no integer matmul on CUDA
    _, colour = torch.unique(residue, return_inverse=True)
    info.update(modulus=m, coefficients=list(coefficients))
    return colour.reshape(-1).to(torch.int32).cpu().numpy(), info
