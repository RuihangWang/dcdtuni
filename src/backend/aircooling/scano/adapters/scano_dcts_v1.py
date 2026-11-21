"""Adapter "scano-dcts-v1": DCTS cache v1 inputs (dcts_cache) and the SCANO forward (infer).

In the worker a room stays in memory (Room): geometry once, each boundary state registered with the data class's
RAM cache under a virtual case name, so a request touches no file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .. import dcts_cache
from ..foam_mesh import FoamGraph, PatchInfo
from . import Adapter, register

WARM_STATE = 'warmup'


@register
class ScanoDctsV1(Adapter):
    id = 'scano-dcts-v1'

    def ingest(self, base: Path, graph: FoamGraph, raw: np.ndarray, bindings: Dict, log=print) -> Dict:
        """bc_t1.npy (the first iteration in the dataset's form) and cache/geometry; returns the notes kept in
        t1.json (bindings, roles, the dataset-form rewrite counts)."""
        roles = dcts_cache.patch_roles(graph, bindings)
        bc, notes = dcts_cache.to_dataset_form(graph, raw, roles, bindings)
        np.save(base / 'bc_t1.npy', bc)
        cache = base / 'cache'
        if (cache / 'geometry').exists():
            shutil.rmtree(cache / 'geometry')
        dcts_cache.write_geometry(cache, graph, meta={'mesh': 'Mesh/ of the case, copied into t1/'})
        log(f"[SCANO] dataset form: {notes['server_outlets']} server outlets, {notes['acu_returns']} ACU returns "
            "rewritten")
        return {'bindings': dcts_cache.binding_values(bindings), 'roles': roles, 'dataset_form': notes}

    def open_room(self, base: Path, graph: FoamGraph, rec: Dict) -> 'Room':
        return Room(base, graph, rec)

    def load_model(self, run_dir: Path, device: Optional[str] = None):
        from ..infer import ScanoModel
        return ScanoModel(Path(run_dir), device)

    def warm_up(self, model, scratch: Path) -> None:
        model.predict(ensure_warm_cache(Path(scratch) / self.id), WARM_STATE)

    def predict_persisted(self, model, base: Path, cfdrun: Dict, graph: FoamGraph, with_error: bool = False,
                          **deploy) -> Dict:
        """Room.predict with the state written to base/cache/<state>/ and read back from there (the benchmark
        tools compare against cache states on disk; the worker otherwise keeps them in memory)."""
        reg = self.persist_state(base, cfdrun, graph)
        res = model.predict(reg['cache_root'], reg['state'], with_error=with_error, **deploy)
        res['state'] = reg['state']
        return res

    def persist_state(self, base: Path, cfdrun: Dict, graph: FoamGraph) -> Dict:
        """The cache state of a request written to base/cache/<state>/."""
        rec = json.loads((base / 't1.json').read_text())
        new = dcts_cache.binding_values(dcts_cache.bindings_of(cfdrun))
        bc, changed = dcts_cache.boundary_from_bindings(graph, np.load(base / 'bc_t1.npy'), rec['bindings'], new)
        state = 's_' + hashlib.sha256(np.ascontiguousarray(bc, np.float32).tobytes()).hexdigest()[:16]
        cache = base / 'cache'
        if not (cache / state / 'case.json').is_file():
            if (cache / state).exists():
                shutil.rmtree(cache / state)
            dcts_cache.write_state(cache, state, graph, bc, rec['roles'], provenance={'changed_from_t1': changed})
        return {'cache_root': cache, 'state': state, 'changed_patches': changed}


class Room:
    """One work directory in memory: graph, cache geometry arrays, the dataset-form first iteration and the
    nearest-server index (it depends only on the outlet faces' positions and areas, which a boundary state never
    changes, so it is computed once)."""

    KEEP = 3                                       # registered states kept per room (each one is its bc array)

    def __init__(self, base: Path, graph: FoamGraph, rec: Dict):
        from ..infer import import_scano
        import_scano()
        from scano.Datasets import dcts_source
        self.base, self.graph, self.rec = Path(base), graph, rec
        self.bc_t1 = np.load(self.base / 'bc_t1.npy')
        geo = self.base / 'cache' / 'geometry'
        self.arrays = {k: np.load(geo / f'{k}.npy') for k in dcts_cache.GEOMETRY_FILES}
        g = self.graph
        starts = g.n_cells + np.concatenate([[0], np.cumsum([p.n_faces for p in g.patches])[:-1]])
        self.patches = [{'name': p.name, 'geom_type': p.geom_type, 'role': r, 'n_faces': int(p.n_faces),
                         'start': int(s), 'neighbour_patch': p.neighbour_patch}
                        for p, r, s in zip(g.patches, self.rec['roles'], starts)]
        self.nearest = dcts_source.nearest_server(self.arrays['pos'], self._servers(self.bc_t1))
        self.registered: List[str] = []

    def _servers(self, bc: np.ndarray) -> list:
        from scano.Datasets import dcts_source
        n = self.graph.n_cells
        servers, problems, _ = dcts_source.server_table(self.patches, self.arrays['patch'], bc, self.arrays['area'],
                                                        self.arrays['normal'], self.arrays['pos'][n:].astype(np.float64))
        if problems:
            raise ValueError('; '.join(problems[:5]))
        return servers

    def initial(self) -> np.ndarray:
        """The first iteration's own input (the warm-up of a worker on this room)."""
        return np.ascontiguousarray(self.bc_t1, np.float32)

    def state(self, cfdrun: Dict):
        """(bc [Nb, 5] float32, changed patch names) of a request's bindings (dcts_cache.boundary_from_bindings)."""
        new = dcts_cache.binding_values(dcts_cache.bindings_of(cfdrun))
        bc, changed = dcts_cache.boundary_from_bindings(self.graph, self.bc_t1, self.rec['bindings'], new)
        return np.ascontiguousarray(bc, np.float32), changed

    def register(self, bc: np.ndarray) -> Dict:
        """Put a boundary state into the data class's RAM cache; {'cache_root', 'state'} to pass to the model."""
        from scano.Main import dcts_common
        state = 'm_' + hashlib.sha256(bc.tobytes()).hexdigest()[:16]
        root = self.base / 'cache'
        key = os.path.abspath(root / state)
        if key not in dcts_common._RAM:
            dcts_common._RAM[key] = ({**self.arrays, 'bc': bc}, self.patches)
            dcts_common._RAM[key + '#source'] = ({'servers': self._servers(bc)}, self.nearest)
            self.registered.append(key)
            while len(self.registered) > self.KEEP:
                old = self.registered.pop(0)
                dcts_common._RAM.pop(old, None)
                dcts_common._RAM.pop(old + '#source', None)
        return {'cache_root': root, 'state': state}

    def predict(self, model, bc: np.ndarray, with_error: bool = False, **deploy) -> Dict:
        """deploy: the forward's mode, passes, chunk, progress (infer.ScanoModel.predict)."""
        reg = self.register(bc)
        res = model.predict(reg['cache_root'], reg['state'], with_error=with_error, **deploy)
        res['state'] = reg['state']
        return res

    def forget(self) -> None:
        from scano.Main import dcts_common
        for key in self.registered:
            dcts_common._RAM.pop(key, None)
            dcts_common._RAM.pop(key + '#source', None)
        self.registered = []


# --- warm-up without a case ------------------------------------------------------------------------------------
# The first CUDA forward of a fresh process pays about ten seconds of one-time work (kernel loading, library
# handles); a forward on any input does it, so this one is two unit cells with a server inlet / outlet pair and
# eight walls. After it the first real room runs at its normal speed (DCTS_0112: 1.2 s, 2026-10-27).

def synthetic_graph() -> FoamGraph:
    """Two unit cubes along x; boundary faces: inlet (x = 0), outlet (x = 2), eight side walls."""
    cells = np.array([[0.5, 0.5, 0.5], [1.5, 0.5, 0.5]])
    faces, normals, owners = [[0, .5, .5], [2, .5, .5]], [[-1, 0, 0], [1, 0, 0]], [0, 1]
    for c in (0, 1):
        x = c + 0.5
        for centre, n in (([x, 0, .5], [0, -1, 0]), ([x, 1, .5], [0, 1, 0]), ([x, .5, 0], [0, 0, -1]),
                          ([x, .5, 1], [0, 0, 1])):
            faces.append(centre)
            normals.append(n)
            owners.append(c)
    nb = len(faces)
    patch = np.array([0, 1] + [2] * 8, np.int32)
    return FoamGraph(
        n_cells=2, pos=np.concatenate([cells, np.array(faces, float)]), volume=np.ones(2),
        normal=np.array(normals, float), area=np.ones(nb), kind=np.array([0, 0, 2, 2] + [1] * 8, np.int8),
        patch=patch, edges=np.stack([np.array([0] + owners), np.array([1] + list(range(2, 2 + nb)))]),
        patches=[PatchInfo('srv_inlet_patch', 'patch', 1, None), PatchInfo('srv_outlet_p0', 'patch', 1, None),
                 PatchInfo('walls', 'wall', 8, None)],
        n_points=12)


def ensure_warm_cache(root: Path) -> Path:
    """The synthetic cache under root (written once); returns root."""
    root = Path(root)
    if (root / WARM_STATE / 'source.json').is_file():
        return root
    g = synthetic_graph()
    bc = np.zeros((g.n_boundary, 5))
    bc[:, 4] = 295.15
    bc[0, :3] = [1.0, 0, 0]                    # the inlet draws air out of the room (outward)
    bc[1, :3] = [-1.0, 0, 0]                   # the outlet blows into it
    bc[1, 4] = 305.15
    if not (root / 'geometry').exists():
        dcts_cache.write_geometry(root, g)
    dcts_cache.write_state(root, WARM_STATE, g, bc, ['server_inlet', 'server_outlet', 'wall'])
    return root
