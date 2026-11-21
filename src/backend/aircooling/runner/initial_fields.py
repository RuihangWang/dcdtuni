"""Initial internal fields for a solve (AI-initialised CFD): replace the uniform internalField of U, p_rgh and T
in 0/ by per-cell values, boundary conditions untouched.

The .npz is in serial cell order (mapped through each rank's cellProcAddressing), or carries `rank_cells` for a
mesh that only exists as partitions. Files keep their own format (ASCII or binary).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import numpy as np

FIELDS = ('U', 'p_rgh', 'T')
#: Where _decompose_for_solve keeps each mesher partition's cellProcAddressing
#: (partition cell -> serial cell) once reconstructParMesh has assembled them.
RECONSTRUCTED_FROM = 'reconstructed-from'


def save_reconstruct_addressing(case_dir: Path, partitions: List[Path]) -> None:
    """Copy each partition's cellProcAddressing (written by reconstructParMesh) to case_dir/RECONSTRUCTED_FROM."""
    import shutil
    out = Path(case_dir) / RECONSTRUCTED_FROM
    out.mkdir(exist_ok=True)
    for k, poly in enumerate(partitions):
        src = Path(poly) / 'cellProcAddressing'
        if not src.is_file():
            return
        shutil.copy(src, out / f'processor{k}.cellProcAddressing')


def _partition_values_to_serial(run_dir: Path, arrays: Dict[str, np.ndarray], counts: List[int]) -> bool:
    """Map values given partition after partition into the serial order through RECONSTRUCTED_FROM; False when
    the solve was not reconstructed from that many partitions."""
    from backend.aircooling.runner.polymesh_io import read_label_list
    saved = Path(run_dir) / RECONSTRUCTED_FROM
    paths = [saved / f'processor{k}.cellProcAddressing' for k in range(len(counts))]
    if not all(p.is_file() for p in paths) or (saved / f'processor{len(counts)}.cellProcAddressing').is_file():
        return False
    order = np.concatenate([read_label_list(p).astype(np.int64) for p in paths])
    if [read_label_list(p).size for p in paths] != counts or np.unique(order).size != order.size:
        raise ValueError(f'{saved}: partition addressing does not match the initial fields')
    for f, values in arrays.items():
        serial = np.empty_like(values)
        serial[order] = values
        arrays[f] = serial
    return True
_INTERNAL = re.compile(rb'^internalField\b', re.MULTILINE)
_FORMAT = re.compile(rb'\bformat\s+(\w+)\s*;')
_LIST_HEAD = re.compile(rb'\s*nonuniform\s+List<(\w+)>\s*(\d+)\s*\(')


def _internal_span(data: bytes, binary: bool) -> tuple:
    """[start, end) of the `internalField ...;` statement (uniform, or an ASCII / binary nonuniform list)."""
    m = _INTERNAL.search(data)
    if not m:
        raise ValueError('no internalField')
    head = _LIST_HEAD.match(data, m.end())
    if head is None:                                             # uniform: no list, the statement ends at ';'
        return m.start(), data.index(b';', m.end()) + 1
    if binary:
        width = {b'scalar': 1, b'vector': 3}[head.group(1)] * 8
        end = head.end() + int(head.group(2)) * width
        if data[end:end + 1] != b')':
            raise ValueError('binary internalField: unexpected list size')
    else:
        depth, end = 1, head.end()
        while depth:
            c = data[end:end + 1]
            depth += (c == b'(') - (c == b')')
            end += 1
        end -= 1
    return m.start(), data.index(b';', end) + 1


def _statement(values: np.ndarray, binary: bool) -> bytes:
    vec = values.ndim == 2
    kind = b'vector' if vec else b'scalar'
    head = b'internalField   nonuniform List<' + kind + b'> \n%d\n(' % values.shape[0]
    if binary:
        return head + np.ascontiguousarray(values, '<f8').tobytes() + b')\n;'
    if vec:
        body = '\n'.join(f'({x:.8g} {y:.8g} {z:.8g})' for x, y, z in values).encode()
    else:
        body = '\n'.join(f'{v:.8g}' for v in values).encode()
    return head + b'\n' + body + b'\n)\n;'


def _write(path: Path, values: np.ndarray) -> None:
    data = path.read_bytes()
    form = _FORMAT.search(data[:2000])
    binary = bool(form and form.group(1) == b'binary')
    if binary and not re.search(rb'scalar=64', data[:2000]):
        raise ValueError(f'{path}: binary field without 64-bit scalars')
    a, b = _internal_span(data, binary)
    path.write_bytes(data[:a] + _statement(values, binary) + data[b:])


def apply_initial_fields(run_dir: Path, npz: Path) -> Dict:
    """Seed run_dir's 0/ (module docstring); returns {'ranks', 'cells', 'fields'}."""
    from backend.aircooling.runner.mesh_layout import processor_mesh_dirs
    from backend.aircooling.runner.polymesh_io import read_label_list
    src = np.load(npz)
    arrays = {f: np.asarray(src[f], np.float64) for f in FIELDS}
    n = arrays['T'].shape[0]
    if arrays['U'].shape != (n, 3) or arrays['p_rgh'].shape != (n,):
        raise ValueError(f'{npz}: U [N, 3], p_rgh [N], T [N] expected')
    ranks = processor_mesh_dirs(run_dir)
    targets: List[tuple] = []
    by_rank = 'rank_cells' in src.files
    if by_rank and len(ranks) != len(src['rank_cells']):
        # the solve repartitioned the mesher's partitions: go through the serial order
        by_rank = not _partition_values_to_serial(run_dir, arrays, [int(c) for c in src['rank_cells']])
    if by_rank:
        counts = [int(c) for c in src['rank_cells']]
        if len(ranks) != len(counts):
            raise ValueError(f'{npz}: values for {len(counts)} partitions, the solve has {len(ranks)}')
        start = 0
        for poly, count in zip(ranks, counts):
            owner = read_label_list(poly / 'owner')
            neighbour = read_label_list(poly / 'neighbour')
            cells = int(max(owner.max(), neighbour.max() if neighbour.size else -1)) + 1
            if cells != count:
                raise ValueError(f'{poly}: {cells} cells, the initial fields have {count} for this partition')
            targets.append((poly.parent.parent / '0', np.arange(start, start + count)))
            start += count
    elif ranks:
        for poly in ranks:
            addr_path = poly / 'cellProcAddressing'
            if not addr_path.is_file():
                raise ValueError(f'{poly}: no cellProcAddressing (the initial fields need the serial cell order)')
            addr = read_label_list(addr_path).astype(np.int64)
            if addr.size and addr.max() >= n:
                raise ValueError(f'{addr_path}: cell {addr.max()} beyond the {n} given values')
            targets.append((poly.parent.parent / '0', addr))
    else:
        owner = read_label_list(run_dir / 'constant' / 'polyMesh' / 'owner')
        neighbour = read_label_list(run_dir / 'constant' / 'polyMesh' / 'neighbour')
        cells = int(max(owner.max(), neighbour.max() if neighbour.size else -1)) + 1
        if cells != n:
            raise ValueError(f'{npz}: {n} values for a mesh of {cells} cells')
        targets.append((run_dir / '0', None))
    for zero, addr in targets:
        for f in FIELDS:
            _write(zero / f, arrays[f] if addr is None else arrays[f][addr])
    return {'ranks': len(targets), 'cells': n, 'fields': list(FIELDS)}
