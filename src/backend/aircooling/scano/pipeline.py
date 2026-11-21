"""The AI solve of one room, with every reusable stage kept on disk. Format-free: inputs are the adapter's
(adapters/), the solver recipe is the card's (models.card).

Work directory <case>/ai/scano/<mesh key>-<physics key>/ holds first_iteration.json, t1/ (the first iteration),
graph.pkl, t1.json and the adapter's files. A request whose keys match reuses it: only server / ACU values changed,
so no solver run is needed. Results go to <case>/log/<stamp>_<case>__SCANO-<model>/.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pickle
import re
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from .bindings import bindings_of
from .foam_fields import boundary_values
from .foam_mesh import FoamGraph, read_graph
from .foam_writer import write_prediction

KEEP_BASES = 3                 # newest work directories kept per case


def ai_root(case_dir: Path) -> Path:
    return Path(case_dir) / 'ai' / 'scano'


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def physics_key(cfdrun: Dict, input_key: str) -> str:
    """Digest of the inputs a first iteration depends on beyond the tunable server / ACU parameters, and of the
    model input it is read into (models.input_key)."""
    sc = cfdrun.get('simulationConfig') or {}
    bindings = bindings_of(cfdrun)
    return _digest({'input': input_key, 'patches': sorted((n, b.get('bcType')) for n, b in bindings.items()),
                    'porous': cfdrun.get('porousBindings') or {}, 'sources': cfdrun.get('sourceTerms') or {},
                    'roomT': sc.get('roomTemperatureC'),
                    'g': [sc.get('gravityX'), sc.get('gravityY'), sc.get('gravityZ')]})


_MESH_KEYS: Dict[tuple, str] = {}


def mesh_key(case_dir: Path) -> str:
    """The mesh identity of <case>/Mesh (openfoam_patch_area.mesh_identity_sha256), remembered per process under
    the stat signature (path, size, mtime) of its topology files: hashing them costs about half a second, a stat
    a millisecond, and a re-mesh rewrites them."""
    from backend.aircooling.runner.mesh_layout import mesh_directories
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
    mesh = Path(case_dir) / 'Mesh'
    sig = tuple((str(f), f.stat().st_size, f.stat().st_mtime_ns) for d in mesh_directories(mesh)
                for f in (d / n for n in ('points', 'faces', 'owner', 'neighbour', 'boundary')) if f.is_file())
    key = _MESH_KEYS.get(sig)
    if key is None:
        key = _MESH_KEYS[sig] = mesh_identity_sha256(mesh)
    return key


def base_dir(case_dir: Path, cfdrun: Dict, input_key: str) -> Path:
    return ai_root(case_dir) / f'{mesh_key(case_dir)[:16]}-{physics_key(cfdrun, input_key)[:16]}'


def plan(case_dir: Path, cfdrun: Dict, card: Dict, mode: str = 'auto') -> Dict:
    """{'base', 'needs_t1'}: whether this request needs a first-iteration run (mode "exact", or no usable base)."""
    from .models import input_key
    if mode not in ('auto', 'exact'):
        raise ValueError(f'mode {mode!r} (auto or exact)')
    base = base_dir(case_dir, cfdrun, input_key(card))
    ready = (base / 't1.json').is_file() and (base / 'graph.pkl').is_file()
    return {'base': base, 'needs_t1': mode == 'exact' or not ready}


def new_t1_dir(base: Path, card: Dict) -> Path:
    """A fresh t1 directory for the runner (an older one, and everything built on it, is set aside), with the
    card's recipe beside it (first_iteration.json, the runner's --first-iteration-recipe)."""
    if base.exists():
        stale = base.with_name(base.name + f'.old-{time.time_ns()}')
        base.rename(stale)
        shutil.rmtree(stale, ignore_errors=True)
    base.mkdir(parents=True)
    (base / 'first_iteration.json').write_text(json.dumps(card['first_iteration'], indent=1) + '\n')
    return base / 't1'


def ingest_t1(base: Path, cfdrun: Dict, adapter, log=print) -> Dict:
    """Read base/t1 (mesh graph + the first iteration's boundary values at the recipe's time), write graph.pkl,
    let the adapter build its input, and write t1.json."""
    from .models import input_key
    t0 = time.perf_counter()
    graph = read_graph(base / 't1', 'processors')
    t1 = time.perf_counter()
    raw = boundary_values(graph, str(adapter.recipe['time']))
    t2 = time.perf_counter()
    with open(base / 'graph.pkl.partial', 'wb') as f:
        pickle.dump(graph, f, protocol=pickle.HIGHEST_PROTOCOL)
    (base / 'graph.pkl.partial').rename(base / 'graph.pkl')
    notes = adapter.ingest(base, graph, raw, bindings_of(cfdrun), log=log)
    rec = {'input_key': input_key(adapter.card), 'adapter': adapter.id, **notes,
           'n_cells': graph.n_cells, 'n_boundary': graph.n_boundary, 'n_patches': len(graph.patches),
           'seconds': {'graph': round(t1 - t0, 2), 'boundary_values': round(t2 - t1, 2),
                       'input': round(time.perf_counter() - t2, 2)}}
    (base / 't1.json').write_text(json.dumps(rec, indent=1) + '\n')
    log(f"[SCANO] first iteration read: {graph.n_cells} cells, {graph.n_boundary} boundary faces, "
        f"{len(graph.patches)} patches ({rec['seconds']})")
    return rec


def load_graph(base: Path) -> FoamGraph:
    """The graph of base/t1. Its rank directories were absolute where the case was when the graph was read: a case
    copied or moved since points them at base/t1 again (else the result writer and the AI initialisation would read
    the other case's mesh files)."""
    with open(base / 'graph.pkl', 'rb') as f:
        graph = pickle.load(f)
    t1 = Path(base) / 't1'
    moved = []
    for d in graph.rank_dirs:
        d = Path(d)
        parts = d.parts
        if 't1' in parts and not d.is_relative_to(t1):
            d = t1.joinpath(*parts[len(parts) - parts[::-1].index('t1'):])
        moved.append(d)
    graph.rank_dirs = moved
    return graph


def open_room(base: Path, adapter):
    """The adapter's in-memory room of a work directory."""
    rec = json.loads((Path(base) / 't1.json').read_text())
    return adapter.open_room(Path(base), load_graph(base), rec)


def result_dir(case_dir: Path, model_id: str) -> Path:
    stamp = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
    return Path(case_dir) / 'log' / f'{stamp}_{Path(case_dir).name}__SCANO-{model_id}'


def write_result(base: Path, case_dir: Path, model_id: str, pred: np.ndarray, record: Dict,
                 graph: Optional[FoamGraph] = None) -> Path:
    graph = graph or load_graph(base)
    out = result_dir(case_dir, model_id)
    return write_prediction(graph, pred, base / 't1', out, record)


def parse_roi(roi) -> Optional[Dict]:
    """{'min': [x, y, z], 'max': [x, y, z]} (metres, min < max on every axis), or None for the whole mesh."""
    if roi is None:
        return None
    try:
        lo = [float(v) for v in roi['min']]
        hi = [float(v) for v in roi['max']]
    except (KeyError, TypeError, ValueError):
        raise ValueError('roi must be {"min": [x, y, z], "max": [x, y, z]}') from None
    if len(lo) != 3 or len(hi) != 3 or not all(np.isfinite(lo + hi)) or any(a >= b for a, b in zip(lo, hi)):
        raise ValueError('roi must be {"min": [x, y, z], "max": [x, y, z]} with min < max on every axis')
    return {'min': lo, 'max': hi}


def restrict_to_roi(graph: FoamGraph, pred: np.ndarray, roi: Dict):
    """The prediction with every node outside the box (cell and boundary-face centroids) set to NaN, and the
    record of the region {'min', 'max', 'cells', 'faces', 'key'}. The model still sees the whole room: a box
    alone as input is outside its training (one forward over all nodes, the values outside discarded)."""
    roi = parse_roi(roi)
    lo, hi = np.asarray(roi['min']), np.asarray(roi['max'])
    inside = np.all((graph.pos >= lo) & (graph.pos <= hi), axis=1)
    cells = int(inside[:graph.n_cells].sum())
    if cells == 0:
        raise ValueError('the ROI box holds no cell of the mesh')
    out = np.array(pred, dtype=np.float64, copy=True)
    out[~inside] = np.nan
    return out, {**roi, 'cells': cells, 'faces': int(inside[graph.n_cells:].sum()), 'key': _digest(roi)[:10]}


def prune(case_dir: Path, keep: int = KEEP_BASES) -> None:
    """Remove all but the newest `keep` work directories of a case (result directories in log/ hard-link their
    meshes and keep them)."""
    root = ai_root(case_dir)
    if not root.is_dir():
        return
    bases = sorted((d for d in root.iterdir() if d.is_dir() and '.old-' not in d.name),
                   key=lambda d: d.stat().st_mtime, reverse=True)
    for d in bases[keep:]:
        shutil.rmtree(d, ignore_errors=True)


def compatibility(graph: FoamGraph, cfdrun: Dict, ranges: Optional[Dict]) -> list:
    """Warnings (text, English, one per finding) where this room leaves the training rooms' ranges (the card's
    training_ranges; none: no warning): mesh size, server loads and airflows, ACU airflows and supply temperature,
    servers and ACUs per room. Outside them a prediction has no support."""
    if not ranges:
        return []
    out = []

    def check(label, values, key, unit, fmt='{:.3g}'):
        r = ranges.get(key)
        vals = [v for v in values if v is not None]
        if not r or not vals:
            return
        lo, hi = min(vals), max(vals)
        if lo < r['min'] or hi > r['max']:
            out.append(f"{label} {fmt.format(lo)}-{fmt.format(hi)} {unit} outside the training range "
                       f"{fmt.format(r['min'])}-{fmt.format(r['max'])} {unit}")
    check('mesh nodes', [graph.pos.shape[0]], 'nodes_per_room', '', '{:.0f}')
    bindings = bindings_of(cfdrun)
    servers = {}
    for n, b in bindings.items():
        if b.get('bcType') == 'fan_outlet':
            par = b.get('parameters') or {}
            s = servers.setdefault(n[:n.rfind('_outlet_')], [0.0, 0.0])
            s[0] += float(par.get('powerW', 0.0))
            s[1] += float(par.get('flowRateM3s', 0.0))
    check('server power', [p for p, _ in servers.values()], 'server_power_W', 'W', '{:.0f}')
    check('server airflow', [q for _, q in servers.values()], 'server_airflow_m3s', 'm3/s')
    acus = [b.get('parameters') or {} for b in bindings.values() if b.get('bcType') == 'acu_supply']
    check('ACU supply airflow', [a.get('flowRateM3s') for a in acus], 'acu_supply_airflow_m3s', 'm3/s')
    check('ACU supply temperature', [a.get('setPointTempC', a.get('temperatureC')) for a in acus],
          'acu_supply_temperature_C', 'C', '{:.1f}')
    check('servers per room', [len(servers)], 'servers_per_room', '', '{:.0f}')
    check('ACUs per room', [len(acus)], 'acus_per_room', '', '{:.0f}')
    return out


def initial_fields(base: Path, pred_path: Path, out: Path) -> Dict:
    """The cell values of a prediction as the runner's --initial-fields .npz (runner/initial_fields): in the case's
    serial cell order through the first iteration's cellProcAddressing, or rank after rank (with rank_cells) when
    the mesh only exists as partitions. Returns {'path', 'cells', 'order'}."""
    from backend.aircooling.runner.polymesh_io import read_label_list
    graph = load_graph(base)
    pred = np.load(pred_path, mmap_mode='r')
    cells = np.asarray(pred[:graph.n_cells], np.float64)
    if not np.isfinite(cells).all():
        raise ValueError('an ROI prediction holds no values outside its box: CFD is initialised from a prediction '
                         'of the whole mesh')
    offsets = list(graph.rank_cell_offset) + [graph.n_cells]
    addr = [Path(d) / 'cellProcAddressing' for d in graph.rank_dirs]
    extra: Dict = {}
    if len(graph.rank_dirs) > 1 and all(a.is_file() for a in addr):
        order = np.empty(graph.n_cells, np.int64)
        for k, a in enumerate(addr):
            order[offsets[k]:offsets[k + 1]] = read_label_list(a).astype(np.int64)
        glob = np.empty_like(cells)
        glob[order] = cells
        cells, how = glob, 'serial'
    elif len(graph.rank_dirs) > 1:
        extra['rank_cells'] = np.diff(offsets)
        how = 'partitions'
    else:
        how = 'serial'
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + '.partial.npz')
    np.savez(tmp, U=cells[:, :3], p_rgh=cells[:, 3], T=cells[:, 4], **extra)
    tmp.replace(out)
    return {'path': out, 'cells': int(graph.n_cells), 'order': how}


def latest_base(case_dir: Path, card: Dict) -> Optional[Path]:
    """The newest work directory of a case built for this card's model input (the worker's warm-up room), or
    None."""
    from .models import input_key
    root = ai_root(case_dir)
    if not root.is_dir():
        return None
    key = input_key(card)
    found = []
    for b in root.iterdir():
        try:
            if '.old-' not in b.name and json.loads((b / 't1.json').read_text()).get('input_key') == key:
                found.append(b)
        except (OSError, ValueError):
            continue
    return max(found, key=lambda b: (b / 't1.json').stat().st_mtime) if found else None


_NOTE = re.compile(r'nCells:\s*(\d+)')
_PATCH = re.compile(r'\n\s*([^\s{}()]+)\s*\{([^}]*)\}')


def room_nodes(case_dir: Path) -> Optional[Dict]:
    """{'nodes', 'cells', 'exact'} of the case's mesh as a model input (cells plus boundary faces): exact from a
    work directory of this mesh, else counted from the polyMesh headers (owner's nCells, the non-processor patches'
    nFaces in boundary), or None without a mesh."""
    from backend.aircooling.runner.mesh_layout import mesh_directories
    dirs = mesh_directories(Path(case_dir) / 'Mesh')
    if not dirs:
        return None
    key = mesh_key(case_dir)[:16]
    root = ai_root(case_dir)
    if root.is_dir():
        for b in sorted(root.glob(f'{key}-*')):
            try:
                rec = json.loads((b / 't1.json').read_text())
                return {'nodes': int(rec['n_cells']) + int(rec['n_boundary']), 'cells': int(rec['n_cells']),
                        'exact': True}
            except (OSError, ValueError, KeyError):
                continue
    cells = faces = 0
    for d in dirs:
        with open(d / 'owner', 'rb') as f:
            m = _NOTE.search(f.read(4096).decode('latin-1'))
        if m:
            cells += int(m.group(1))
        else:                                       # no note (a hand-written mesh): an ASCII owner list
            text = (d / 'owner').read_text(errors='replace')
            body = text[text.index('(', text.index('}')) + 1:text.rindex(')')] if '}' in text else ''
            ids = [int(v) for v in body.split()] if body.strip() else []
            if not ids:
                return None
            cells += max(ids) + 1
        text = (d / 'boundary').read_text(errors='replace')
        for _name, body in _PATCH.findall(text):
            if not re.search(r'\btype\s+processor\b', body):
                n = re.search(r'\bnFaces\s+(\d+)', body)
                faces += int(n.group(1)) if n else 0
    return {'nodes': cells + faces, 'cells': cells, 'exact': False}
