"""Boundary values of an OpenFOAM time directory as the DCTS cache's ``bc`` rows [Nb, 5] (u, v, w, p_rgh, T).

The values are what refine's importer took from the pyvista / VTK OpenFOAM reader on the solver's first iteration:
a patch whose field entry carries ``value`` (every physical patch: buoyantBoussinesqSimpleFoam_new writes each one
as per-face values) gives those values; a cyclic baffle (no value on the reconstructed mesh; on a decomposed one a
processorCyclic part carries an interpolated value, ignored) gives the values of the faces' owner cells, as the
reader does. Read straight from the (ASCII) files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

from .foam_mesh import FoamGraph

FIELDS = (('U', 3), ('p_rgh', 1), ('T', 1))          # cache column order u, v, w, p_rgh, T

_HEAD = re.compile(rb'format\s+(\w+)\s*;')
_PARENS = bytes.maketrans(b'()', b'  ')


def _strip_comments(raw: bytes) -> bytes:
    return re.sub(rb'/\*.*?\*/|//[^\n]*', b'', raw, flags=re.S)


def _parse_list(raw: bytes, pos: int, width: int) -> Tuple[np.ndarray, int]:
    """An ASCII `List<...> N ( ... )` body starting at pos (just after `List<...>`); returns (array, end)."""
    m = re.compile(rb'\s*(\d+)\s*\(').match(raw, pos)
    if m is None:
        raise ValueError('list without a count')
    n = int(m.group(1))
    start = m.end()
    if n == 0:                                         # (np.fromstring of blanks returns [-1.])
        return np.zeros((0, width)), raw.index(b')', start) + 1
    if width == 1:
        j = raw.index(b')', start) + 1
    else:                                              # a long vector list closes on a line of its own
        j = raw.find(b'\n)', start) + 2
        semi = raw.find(b';', start)
        if j == 1 or (semi != -1 and semi < j):        # a short one (10 entries or fewer) is written inline
            depth, j = 1, start
            while depth:
                o, c = raw.find(b'(', j), raw.find(b')', j)
                if o != -1 and o < c:
                    depth, j = depth + 1, o + 1
                else:
                    depth, j = depth - 1, c + 1
    arr = np.fromstring(raw[start:j - 1].translate(_PARENS).decode('ascii'), dtype=np.float64, sep=' ')
    if arr.size != n * width:
        raise ValueError(f'list of {n} x {width} has {arr.size} numbers')
    return arr.reshape(n, width), j


def _value(text: bytes, width: int, n: int) -> np.ndarray:
    """`uniform x` / `nonuniform List<..> N (..)` at the start of text, as [n, width]."""
    m = re.compile(rb'\s*(uniform|nonuniform)\s*').match(text)
    if m.group(1) == b'uniform':
        if width == 1:
            return np.full((n, 1), float(re.compile(rb'([^\s;]+)').match(text, m.end()).group(1)))
        t = re.compile(rb'\(([^)]*)\)').match(text, m.end()).group(1)
        return np.tile(np.array(t.split(), dtype=np.float64), (n, 1))
    arr, _ = _parse_list(text, re.compile(rb'List<\w+>').match(text, m.end()).end(), width)
    if arr.shape[0] != n:
        raise ValueError(f'value list of {arr.shape[0]} for {n} faces')
    return arr


def read_field(path: Path, width: int, patch_sizes: Dict[str, int]):
    """(internal [C, width], or [1, width] when uniform, {patch: [n, width] or None}) of one ASCII volField; None
    marks a patch entry without a top-level `value` (cyclic)."""
    raw = Path(path).read_bytes()
    if _HEAD.search(raw[:4096]) and _HEAD.search(raw[:4096]).group(1) != b'ascii':
        raise ValueError(f'{path}: binary field files are not read here (the first-iteration run writes ASCII)')
    i = raw.index(b'internalField', raw.index(b'FoamFile'))
    head = _strip_comments(raw[:i])
    raw = head + raw[i:]
    i = len(head)
    m = re.compile(rb'internalField\s+nonuniform\s+List<\w+>').match(raw, i)
    # a uniform internal field comes back as one row [1, width] (broadcast over the cells)
    internal = _parse_list(raw, m.end(), width)[0] if m else _value(raw[i + len(b'internalField'):], width, 1)
    bstart = raw.index(b'{', raw.index(b'boundaryField', i)) + 1
    # one pass over the entries: name -> (body start, body end); the bodies hold lists (parentheses only) and
    # possibly nested dictionaries such as uniformValue { ... }
    entries = {}
    entry = re.compile(rb'\s*"?([^\s{}"]+)"?\s*\{')
    pos = bstart
    while True:
        m = entry.match(raw, pos)
        if m is None:
            break
        depth, j = 1, m.end()
        while depth:
            o, c = raw.find(b'{', j), raw.find(b'}', j)
            if o != -1 and o < c:
                depth += 1
                j = o + 1
            else:
                depth -= 1
                j = c + 1
        entries[m.group(1).decode()] = (m.end(), j - 1)
        pos = j
    token = re.compile(rb'\{|\}|(?<![\w"])value(?=\s)')
    out: Dict[str, Optional[np.ndarray]] = {}
    for name, n in patch_sizes.items():
        if name not in entries:
            raise KeyError(f'{path}: no boundaryField entry for {name}')
        s, e = entries[name]
        body = re.sub(rb'//[^\n]*', b'', raw[s:e]) if b'//' in raw[s:min(e, s + 4096)] else raw[s:e]
        depth, found = 0, None
        for t in token.finditer(body):                 # the first top-level `value` key
            if t.group(0) == b'{':
                depth += 1
            elif t.group(0) == b'}':
                depth -= 1
            elif depth == 0:
                found = t.end()
                break
        out[name] = None if found is None else _value(body[found:], width, n)
    return internal, out


def boundary_values(graph: FoamGraph, time_name: str = '1') -> np.ndarray:
    """[Nb, 5] float64 boundary values of the graph's case at time `time_name` (module docstring)."""
    bc = np.zeros((graph.n_boundary, 5))
    owner = graph.edges[0, -graph.n_boundary:] if graph.n_boundary else np.zeros(0, np.int64)
    col = 0
    for fname, width in FIELDS:
        for k, mesh_dir in enumerate(graph.rank_dirs):
            case = mesh_dir.parent.parent                   # <case> or <case>/processorN
            rows = graph.rank_patch_rows[k]
            internal, patches = read_field(case / time_name / fname, width, {p: r.size for p, r in rows.items()})
            for pname, r in rows.items():
                v = patches[pname]
                # cyclic faces take their owner cells' values: what the VTK reader gives a valueless cyclic patch
                # of the reconstructed mesh the dataset was read from (a processorCyclic face's own `value` is an
                # interpolation across the baffle, which that reader never saw)
                if r.size and graph.patches[graph.patch[r[0]]].geom_type == 'cyclic':
                    v = None
                if v is None:
                    v = internal[0] if internal.shape[0] == 1 else internal[owner[r] - graph.rank_cell_offset[k]]
                bc[r, col:col + width] = v
        col += width
    return bc
