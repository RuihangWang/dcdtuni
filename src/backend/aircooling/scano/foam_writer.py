"""A prediction as an OpenFOAM run directory that post-processing opens like a CFD run: meshes hard-linked
from the first-iteration case, U / p_rgh / T written in binary, plus scano_prediction.json.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from .foam_mesh import FoamGraph, parse_boundary

TIME = '1'
FIELDS = (('U', 'volVectorField', '[0 1 -1 0 0 0 0]', slice(0, 3)),
          ('p_rgh', 'volScalarField', '[0 2 -2 0 0 0 0]', slice(3, 4)),
          ('T', 'volScalarField', '[0 0 0 1 0 0 0]', slice(4, 5)))
CONSTANT_FILES = ('g', 'transportProperties', 'turbulenceProperties', 'thermophysicalProperties')


def _header(cls: str, name: str, location: str) -> bytes:
    return ('FoamFile\n{\n    version     2.0;\n    format      binary;\n    arch        "LSB;label=32;scalar=64";\n'
            f'    class       {cls};\n    location    "{location}";\n    object      {name};\n}}\n'
            '// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n').encode()


def _list(values: np.ndarray, vector: bool) -> bytes:
    v = np.ascontiguousarray(values, '<f8')
    kind = 'vector' if vector else 'scalar'
    return f'nonuniform List<{kind}> {v.shape[0]}('.encode() + v.tobytes() + b')'


def _link_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_dir():
            _link_tree(f, dst / f.name)
            continue
        try:
            os.link(f, dst / f.name)
        except OSError:
            shutil.copy2(f, dst / f.name)


def write_prediction(graph: FoamGraph, pred: np.ndarray, source_case: Path, out_dir: Path,
                     record: Optional[Dict] = None) -> Path:
    """Write `pred` [N, 5] (u, v, w, p_rgh, T at the graph's nodes) as the run directory out_dir (new)."""
    source_case, out_dir = Path(source_case), Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f'{out_dir} exists')
    # an ROI prediction (record['roi']) is NaN outside its box; post-processing shows only the box
    finite = np.isfinite(pred).all(axis=1) if pred.ndim == 2 else None
    if (pred.shape != (graph.pos.shape[0], 5) or finite is None
            or not (finite.any() if (record or {}).get('roi') else finite.all())):
        raise ValueError(f'prediction has shape {pred.shape} (want [{graph.pos.shape[0]}, 5]) or non-finite values')
    part = out_dir.with_name(out_dir.name + '.partial')
    if part.exists():
        shutil.rmtree(part)
    part.mkdir(parents=True)
    shutil.copytree(source_case / 'system', part / 'system')
    (part / 'constant').mkdir()
    for name in CONSTANT_FILES:
        if (source_case / 'constant' / name).is_file():
            shutil.copy2(source_case / 'constant' / name, part / 'constant' / name)
    (part / 'case.foam').touch()
    cells = pred[:graph.n_cells]
    faces = pred[graph.n_cells:]
    for k, mesh_dir in enumerate(graph.rank_dirs):
        rank_case = mesh_dir.parent.parent
        rel = rank_case.relative_to(source_case) if rank_case != source_case else Path('.')
        dst_case = part / rel
        _link_tree(mesh_dir, dst_case / 'constant' / 'polyMesh')
        if rel != Path('.'):
            for name in CONSTANT_FILES:
                if (rank_case / 'constant' / name).is_file():
                    shutil.copy2(rank_case / 'constant' / name, dst_case / 'constant' / name)
        off = graph.rank_cell_offset[k]
        ncell = (graph.rank_cell_offset[k + 1] if k + 1 < len(graph.rank_cell_offset) else graph.n_cells) - off
        rows, procs = graph.rank_patch_rows[k], graph.rank_proc_faces[k]
        tdir = dst_case / TIME
        tdir.mkdir(parents=True)
        for fname, cls, dims, cols in FIELDS:
            vector = cls == 'volVectorField'
            sq = (lambda a: a) if vector else (lambda a: a[:, 0])
            out = [_header(cls, fname, TIME), f'dimensions      {dims};\n\n'.encode(),
                   b'internalField   ', _list(sq(cells[off:off + ncell, cols]), vector), b';\n\nboundaryField\n{\n']
            for name, d in parse_boundary(mesh_dir / 'boundary'):
                t = d['type']
                if t == 'processor':
                    mine, other = procs[name]
                    val = 0.5 * (cells[mine, cols] + cells[other, cols])
                    ptype = 'processor'
                else:
                    val = faces[rows[name], cols]
                    ptype = t if t in ('cyclic', 'processorCyclic') else 'calculated'
                out += [f'    {name}\n    {{\n        type            {ptype};\n        value           '.encode(),
                        _list(sq(val), vector), b';\n    }\n']
            out.append(b'}\n')
            (tdir / fname).write_bytes(b''.join(out))
    (part / 'scano_prediction.json').write_text(json.dumps(record or {}, indent=1) + '\n')
    os.replace(part, out_dir)
    return out_dir
