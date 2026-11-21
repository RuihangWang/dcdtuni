"""The DCTS model input cache (cache version 1 of scano.Datasets.dcts) of a dcdtuni case.

Layout under a cache root, so a change of boundary conditions never redoes the geometry:
  <root>/geometry/        pos, volume, normal, area, kind, patch, edges (float32 / int8 / int32 .npy) and
                          target.npy (zeros: a new room has no CFD answer), written once per mesh;
  <root>/<state>/         one SCANO "case" per boundary-condition state: symbolic links to the geometry files,
                          its own bc.npy and case.json, and source.json / source_nearest.npy from the vendored
                          scano.Datasets.dcts_source.build_source (the server table F1 / F2 read).
GtcData(<root>, [<state>], ...) reads a state like any cache case of the paper.

Boundary values (``bc``) are the solver's first iteration in the dataset's form (foam_fields and to_dataset_form;
the t1 run of ``pipeline``). ``boundary_from_bindings`` derives a new state from it when only server loads, server
airflows or ACU supply settings changed: the faces of those patches get the values the first iteration gives them
(outlets -(Q/A) n, their temperature and p_rgh following the new temperature rise P / (rho cp Q); supplies -(Q/A) n
and the set point; inlets their profile scaled by the airflow, the outflow condition the solver applies), every
other face keeps its first-iteration value.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np

from .bindings import bindings_of  # noqa: F401  (re-exported for the adapter)
from .foam_mesh import FoamGraph

CACHE_VERSION = 1
GEOMETRY_FILES = ('pos', 'volume', 'normal', 'area', 'kind', 'patch', 'edges', 'target')
ROLE_OF_BINDING = {'fan_inlet': 'server_inlet', 'fan_outlet': 'server_outlet', 'acu_supply': 'acu_supply',
                   'acu_return': 'acu_return'}
RHO, CP = 1.19, 1005.0                 # scano.Datasets.dcts_source (dcdtuni utils/const.py of the dataset)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 22), b''):
            h.update(block)
    return h.hexdigest()


def patch_roles(graph: FoamGraph, bindings: Dict[str, Dict]) -> List[str]:
    """The cache role of every patch: server_inlet / server_outlet / acu_supply / acu_return for the geom_type
    "patch" patches (from the binding's role, or its bcType), wall and cyclic by geometric type. A "patch" patch
    without one of the four roles is refused (the model knows no other inflow or outflow)."""
    roles, bad = [], []
    for p in graph.patches:
        if p.geom_type in ('wall', 'cyclic'):
            roles.append(p.geom_type)
            continue
        b = bindings.get(p.name) or {}
        r = ROLE_OF_BINDING.get(b.get('role')) or ROLE_OF_BINDING.get(b.get('bcType'))
        if r is None:
            bad.append(f"{p.name} ({b.get('bcType', 'no binding')})")
        roles.append(r)
    if bad:
        raise ValueError(f'{len(bad)} open patches have no server / ACU role: {bad[:5]}')
    return roles


def _atomic_dir(final: Path) -> Path:
    part = final.with_name(final.name + '.partial')
    if part.exists():
        import shutil
        shutil.rmtree(part)
    part.mkdir(parents=True)
    return part


def write_geometry(root: Path, graph: FoamGraph, target: Optional[np.ndarray] = None, meta: Optional[Dict] = None):
    """<root>/geometry (never overwritten; a new mesh gets a new root)."""
    final = Path(root) / 'geometry'
    if final.exists():
        raise FileExistsError(f'{final} exists')
    part = _atomic_dir(final)
    n = graph.pos.shape[0]
    arrays = {'pos': graph.pos.astype(np.float32), 'volume': graph.volume.astype(np.float32),
              'normal': graph.normal.astype(np.float32), 'area': graph.area.astype(np.float32),
              'kind': graph.kind.astype(np.int8), 'patch': graph.patch.astype(np.int32),
              'edges': graph.edges.astype(np.int32),
              'target': (np.zeros((n, 5), np.float32) if target is None else np.asarray(target, np.float32))}
    for k, a in arrays.items():
        np.save(part / f'{k}.npy', np.ascontiguousarray(a))
    info = {'n_nodes': int(n), 'n_cells': int(graph.n_cells), 'n_boundary': int(graph.n_boundary),
            'n_faces': int(graph.edges.shape[1]), 'n_internal_faces': int(graph.edges.shape[1] - graph.n_boundary),
            'n_points': int(graph.n_points), 'target_is_zero': target is None, **(meta or {}),
            'created': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    (part / 'geometry.json').write_text(json.dumps(info, indent=1) + '\n')
    os.replace(part, final)
    return final


def write_state(root: Path, state: str, graph: FoamGraph, bc: np.ndarray, roles: List[str],
                provenance: Optional[Dict] = None) -> Path:
    """<root>/<state>: links to the geometry, bc.npy, case.json, then the server table (build_source)."""
    from .infer import import_scano
    import_scano()
    from scano.Datasets import dcts_source
    root = Path(root)
    geo = root / 'geometry'
    final = root / state
    if final.exists():
        raise FileExistsError(f'{final} exists')
    if bc.shape != (graph.n_boundary, 5) or not np.isfinite(bc).all():
        raise ValueError(f'bc has shape {bc.shape} (want [{graph.n_boundary}, 5]) or non-finite values')
    part = _atomic_dir(final)
    for k in GEOMETRY_FILES:
        os.symlink(os.path.relpath(geo / f'{k}.npy', part), part / f'{k}.npy')
    np.save(part / 'bc.npy', np.ascontiguousarray(bc, np.float32))
    starts = graph.n_cells + np.concatenate([[0], np.cumsum([p.n_faces for p in graph.patches])[:-1]])
    patches = [{'name': p.name, 'geom_type': p.geom_type, 'role': r, 'n_faces': int(p.n_faces), 'start': int(s),
                'neighbour_patch': p.neighbour_patch} for p, r, s in zip(graph.patches, roles, starts)]
    geo_info = json.loads((geo / 'geometry.json').read_text())
    kinds = ('cell', 'wall', 'patch', 'cyclic')
    kind = np.load(geo / 'kind.npy', mmap_mode='r')
    rec = {'cache_version': CACHE_VERSION, 'case': state, 'split': None,
           **{k: geo_info[k] for k in ('n_nodes', 'n_cells', 'n_boundary', 'n_faces', 'n_internal_faces', 'n_points')},
           'fields': ['u', 'v', 'w', 'p_rgh', 'T'], 'kinds': list(kinds), 'patches': patches,
           'n_empty_patches': sum(1 for p in patches if p['n_faces'] == 0),
           'kind_counts': {k: int((kind == i).sum()) for i, k in enumerate(kinds)},
           'converter': 'dcdtuni backend.aircooling.scano', 'provenance': provenance or {},
           'created': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    (part / 'case.json').write_text(json.dumps(rec, indent=1) + '\n')
    dcts_source.build_source(part, {'converter': 'dcdtuni backend.aircooling.scano'})
    os.replace(part, final)
    return final


# --- boundary-condition edits without a solver run -----------------------------------------------------------

def binding_values(bindings: Dict[str, Dict]) -> Dict[str, Dict]:
    """{patch: {'kind', 'Q' m^3/s, 'dT' K (server outlets: P / (rho cp Q)) or 'T' K (ACU supplies)}} of the server
    and ACU bindings: the binding parameters the first iteration's boundary values follow."""
    out = {}
    for name, b in bindings.items():
        t = b.get('bcType')
        par = b.get('parameters') or {}
        if t == 'fan_outlet':
            q = float(par['flowRateM3s'])
            out[name] = {'kind': t, 'Q': q, 'dT': float(par.get('powerW', 0.0)) / (RHO * CP * q)}
        elif t == 'fan_inlet':
            out[name] = {'kind': t, 'Q': float(par['flowRateM3s'])}
        elif t == 'acu_supply':
            sp = par.get('setPointTempC', par.get('temperatureC'))
            out[name] = {'kind': t, 'Q': float(par['flowRateM3s']), 'T': float(sp) + 273.15}
    return out


def boundary_from_bindings(graph: FoamGraph, bc_t1: np.ndarray, old: Dict[str, Dict], new: Dict[str, Dict]):
    """(bc, changed patch names): a copy of the dataset-form first iteration bc_t1 (made under the bindings `old`,
    binding_values) with every server / ACU patch whose values differ in `new` set to what the first iteration gives
    it under `new` (module docstring): outlets -(Q/A) n, their temperature shifted by the change of P / (rho cp Q)
    (the inlet temperature they sit on is unchanged) and their p_rgh scaled by the ratio of the rises; supplies
    -(Q/A) n and the set point; inlets their profile scaled by the airflow ratio."""
    bc = np.array(bc_t1, np.float64, copy=True)
    index = {p.name: i for i, p in enumerate(graph.patches)}
    changed = []
    for name in sorted(n for n in new if new[n] != old.get(n)):
        if name not in index or name not in old or old[name]['kind'] != new[name]['kind']:
            raise ValueError(f'{name}: no first-iteration values of the same kind to start from')
        rows = np.flatnonzero(graph.patch == index[name])
        if not rows.size:
            continue
        v, o = new[name], old[name]
        if v['kind'] in ('fan_outlet', 'acu_supply'):
            bc[rows, :3] = -(v['Q'] / graph.area[rows].sum()) * graph.normal[rows]
            if v['kind'] == 'fan_outlet':
                bc[rows, 4] += v['dT'] - o['dT']
                # the outlet's first-iteration p_rgh is the buoyancy of its temperature rise (fixedFluxPressure
                # with gh snGrad(rhok)): it scales with dT (doubled load: exactly doubled, DCTS_0112 10-27)
                if o['dT'] > 0:
                    bc[rows, 3] *= v['dT'] / o['dT']
            else:
                bc[rows, 4] = v['T']
        else:
            bc[rows, :3] *= v['Q'] / o['Q']
        changed.append(name)
    return bc, changed


def to_dataset_form(graph: FoamGraph, bc: np.ndarray, roles: List[str], bindings: Dict[str, Dict]):
    """(bc, notes): the first iteration of the current runner rewritten where its boundary conditions differ from the
    dataset's (dcdtuni 7b53f528), so the model sees the inputs of its training rooms:
      server outlets  T = area-mean first-iteration T of the server's inlet faces + P / (rho cp Q) (the dataset's
                      tSink + dT; the current runner damps the outlet feedback, 0.5 tSink + 0.5 tOutPrev + 0.5 dT,
                      which has the same converged value but carries half the rise at the first iteration);
      ACU returns     U = 0, p_rgh = 0 (the dataset's pressure outlet; the current runner may pin a return to its
                      supply's flow, flowRateOutletVelocity with fixedFluxPressure).
    Server inlets pair with outlets by name stem as scano.Datasets.dcts_source does."""
    bc = np.array(bc, np.float64, copy=True)
    names = [p.name for p in graph.patches]
    rows = {n: np.flatnonzero(graph.patch == i) for i, n in enumerate(names)}
    inlets = {n: r for n, r, role in zip(names, rows.values(), roles) if role == 'server_inlet' and r.size}
    notes = {'server_outlets': 0, 'acu_returns': 0, 'max_outlet_T_change_K': 0.0, 'unpaired_outlets': []}
    for n, role in zip(names, roles):
        r = rows[n]
        if not r.size:
            continue
        if role == 'acu_return':
            bc[r, :4] = 0.0
            notes['acu_returns'] += 1
        elif role == 'server_outlet':
            i = n.rfind('_outlet_')
            stem = n[:i] if i > 0 else None
            par = (bindings.get(n) or {}).get('parameters') or {}
            inl = [v for k, v in inlets.items() if stem and k.startswith(stem + '_inlet')]
            if not inl or not par.get('flowRateM3s'):
                notes['unpaired_outlets'].append(n)
                continue
            ri = np.concatenate(inl)
            t_in = float((bc[ri, 4] * graph.area[ri]).sum() / graph.area[ri].sum())
            t_out = t_in + float(par.get('powerW', 0.0)) / (RHO * CP * float(par['flowRateM3s']))
            notes['max_outlet_T_change_K'] = max(notes['max_outlet_T_change_K'], float(np.abs(bc[r, 4] - t_out).max()))
            bc[r, 4] = t_out
            notes['server_outlets'] += 1
    return bc, notes
