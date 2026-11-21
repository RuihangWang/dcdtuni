"""DCTS heat sources: the server pairs of a cached case and the source features of boundary.source (d49).

Why (scratch/diag_dcts_power, 10-07): a rack's heat load reached the models only as the first-iteration temperature
on its server-outlet faces (T_t1 = 295.15 K + P / (rho cp Q), dcdtuni 7b53f528 runner/fields.py, the fan_outlet
uniformFixedValue expression tSink + dT with tSink = weightAverage(T) on the paired inlet). Those faces are 3-53 per
rack, about 1e-5 of the boundary area, and every route weighs them by area or by count, so the trained models did
not follow rack power (rack2x response 0.3 % of the CFD change near the rack).

A server is the stem of its outlet patch names, `<stem>_outlet_<k>` (role server_outlet), paired with the non-empty
server_inlet patches named `<stem>_inlet...`, as dcdtuni's `_find_server_inlet_patch` resolves the inlet of the
temperature-rise condition. From the cached t1 boundary values (the model's inputs, nothing else):
  Q_s   = sum over the outlet faces of |U| a             (m^3/s into the room)
  dT_s  = area-mean T_t1 on the outlet faces - area-mean T_t1 on the inlet faces   (K; the inlet is 295.15 K at t1)
  P_s   = RHO CP Q_s dT_s                                (W; RHO, CP of dcdtuni utils/const.py)
  q_f   = RHO CP |U_f| dT_s on an outlet face f          (W/m^2, the heat-flux density the server returns to the room)
The outlet condition (flowRateInletVelocity) sets U = -(Q/A) n_f on every face, so |U| is the inflow speed. The speed
is taken rather than -U . n with the cached normal because the cached normal of a few warped faces points the other
way (build-source on the 500 rooms, 10-07: one face in each of five rooms, on a server outlet patch, has U along +n
with |U . n| = |U|; e.g. GTC_dataset_0366 rack 12 srv4); the count of such faces is reported, and a server whose
outlet carries U along +n on half its area or more is refused.

`build-source` (python -m scano.Datasets.dcts build-source <cache root>) writes, next to the v1 cache arrays of every
case (which it never changes): source.json (the server table, the constants, the checks, the sha256 of the cache files
it read and of source_nearest.npy) and source_nearest.npy (int16 [N]: the server whose outlet centroid is nearest to
the node, within D_CAP metres, else -1), and at the cache root source_manifest.json (sha256 of every case's two
files). Required checks (a case that fails one is refused): every non-empty server outlet has a stem and a paired
non-empty inlet; on every outlet face U is non-zero and along the face normal (|U . n| >= ALIGN |U|); inflow
(U . n < 0) on more than half of each server's outlet area; dT_s > 0; fewer than 32767 servers. Facts: the number of
server inlets without an outlet, the number of outlet faces whose cached normal points along U, the largest deviation
of the inlet T_t1 from 295.15 K.

Columns the data class builds from them (scano.Main.dcts_common; fixed physical scales, no statistics):
  F1 (boundary.source = heat or heat_near; boundary-node features and face records):
      q_f / Q_REF on outlet faces (0 elsewhere), P_s / P_REF and dT_s / DT_REF on the outlet and inlet faces of server
      s (0 elsewhere): the jump datum dT_s and the load it carries, as data;
  F2 (boundary.source = heat_near; every node): the nearest server within D_CAP: P_s / P_REF, dT_s / DT_REF,
      (x - x_s) / D_CAP (3), |x - x_s| / D_CAP; beyond D_CAP zeros and distance 1;
  F2 in the exhaust frame (boundary.source = heat_near_jet; every node; R29 diagnosis of c66, 10-07: without the
      exhaust direction the trained response to a doubled rack was an even block over the rack's F2 cells, half of it
      upstream, where the CFD response is a sheet and plume downstream of the outlet): the same nearest server within
      D_CAP, F2J_COLUMNS = P_s / P_REF, dT_s / DT_REF, the offset x - x_s along the server's exhaust jet, lateral and
      vertical / D_CAP (3), the signed distance sign(along) |x - x_s| / D_CAP (positive downstream of the outlet
      plane), |x - x_s| / D_CAP and the jet direction in room axes (3); beyond D_CAP zeros and distance 1. The jet
      direction j_s is the area-weighted mean boundary velocity on the server's outlet faces (server_jets: the t1
      values, or under boundary.bc_input = "spec" the specification's -(Q/A) n), the frame (jet_frames) is j_s, the
      vertical UP made orthogonal to it, and their cross product (right-handed); only inputs enter (no solver output
      beyond the t1 boundary values; none at all under spec);
  source measure (boundary.source_measure; every other record keeps its area):
      flux  the record weight of an outlet face is its mass-flux measure rho cp a_f |U_f| / (rho cp U_MASS) = a_f |U_f| /
            U_MASS (a server's outlet weighs Q_s / U_MASS "m^2"), independent of the load: Proposition W1 (paper section
            measure) makes the server pair a jump condition whose measure is the mass-flux (or area) measure of the
            pair and whose datum is dT_s, so the datum stays a data column (dT, q, P) and the record weight does not
            depend on it;
      heat  the weight is a_f q_f / Q_MASS (the load in kW as "m^2"): the datum inside the measure, kept as one
            comparison arm (c66).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np

SOURCE_VERSION = 1
RHO, CP = 1.19, 1005.0                 # dcdtuni 7b53f528 src/backend/aircooling/utils/const.py (rho_air, air_specific_heat)
T_INIT = 295.15                        # the initial temperature (the server inlets' t1 value)
D_CAP = 3.0                            # m: F2 looks for the nearest server outlet within this distance
Q_REF = RHO * CP * 1.0 * 10.0          # W/m^2: heat flux of 1 m/s of air heated by 10 K (F1 scale of q)
P_REF = 1000.0                         # W (F1 / F2 scale of P_s)
DT_REF = 10.0                          # K (F2 scale of dT_s)
Q_MASS = 1000.0                        # W/m^2: load measure (heat), one "m^2" of record weight per kW of load
U_MASS = 1.0                           # m/s: mass-flux measure (flux), one "m^2" of record weight per m^3/s of air
ALIGN = 0.9                            # outlet velocity along the face normal: |U . n| >= ALIGN |U|
# the vertical of DCTS: gravity (0, -9.81, 0) in every cfdrun (gravityY = -9.81; dcdtuni 7b53f528
# runner/cfdrun_schema.py default {"x": 0, "y": -9.81, "z": 0}), so up is +y
UP = (0.0, 1.0, 0.0)
F2J_COLUMNS = ('P', 'dT', 'along', 'lateral', 'vertical', 'signed_distance', 'distance', 'jet_x', 'jet_y', 'jet_z')
SOURCE_FILES = ('source.json', 'source_nearest.npy')
MANIFEST = 'source_manifest.json'
INPUT_FILES = ('bc', 'patch', 'area', 'normal', 'pos', 'volume')


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 22), b''):
            h.update(block)
    return h.hexdigest()


def module_record():
    """sha256 of this module and whether it differs from the committed version (provenance of the source files)."""
    import subprocess
    here = Path(__file__).resolve()
    try:
        dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', str(here)], cwd=here.parents[3],
                                    capture_output=True, text=True).stdout.strip())
    except OSError:
        dirty = None
    return {'source_module': 'src/scano/Datasets/dcts_source.py', 'source_module_sha256': _sha(here),
            'source_module_uncommitted': dirty}


def stem_of(name, tag):
    i = name.rfind(tag)
    return name[:i] if i > 0 else None


def faces_by_patch(patch_ids, n_patches):
    """[faces of patch 0, faces of patch 1, ...] (indices into the boundary rows)."""
    order = np.argsort(patch_ids, kind='stable')
    bounds = np.searchsorted(patch_ids[order], np.arange(n_patches + 1))
    return [order[bounds[i]:bounds[i + 1]] for i in range(n_patches)]


def server_table(patches, patch_ids, bc, area, normal, pos_b):
    """(servers, checks, facts) of one case from its case.json patch table and cached boundary arrays. servers: list of
    {stem, outlets [patch ids], inlets [patch ids], Q, dT, P, centroid [3], outlet_area}."""
    faces = faces_by_patch(np.asarray(patch_ids), len(patches))
    bc = np.asarray(bc, np.float64)
    area = np.asarray(area, np.float64)
    normal = np.asarray(normal, np.float64)
    roles = [p.get('role') for p in patches]
    if any(r is None for r in roles):
        raise ValueError('case.json has patches without a role (rebuild the cache with text_conditioning)')
    outlets, problems = {}, []
    for i, p in enumerate(patches):
        if roles[i] == 'server_outlet' and len(faces[i]):
            s = stem_of(p['name'], '_outlet_')
            if s is None:
                problems.append(f"server outlet without an '_outlet_' stem: {p['name']}")
                continue
            outlets.setdefault(s, []).append(i)
    inlets = [i for i, r in enumerate(roles) if r == 'server_inlet' and len(faces[i])]
    servers, used, n_along = [], set(), 0
    for s in sorted(outlets):
        inl = [i for i in inlets if patches[i]['name'].startswith(s + '_inlet')]
        if not inl:
            problems.append(f'server {s}: no non-empty server_inlet patch named {s}_inlet*')
            continue
        used.update(inl)
        fo = np.concatenate([faces[i] for i in outlets[s]])
        fi = np.concatenate([faces[i] for i in inl])
        ao, ai = area[fo], area[fi]
        speed = np.linalg.norm(bc[fo, :3], axis=1)
        udn = np.einsum('ij,ij->i', bc[fo, :3], normal[fo])
        if not (speed > 0).all() or not (np.abs(udn) >= ALIGN * speed).all():
            problems.append(f'server {s}: outlet velocity zero or not along the face normal on '
                            f'{int(((speed <= 0) | (np.abs(udn) < ALIGN * speed)).sum())} of {fo.size} faces')
        along = udn > 0                                  # cached normal along U (the BC's inflow is along -n_f)
        n_along += int(along.sum())
        if ao[along].sum() >= 0.5 * ao.sum():
            problems.append(f'server {s}: U along the outward normal on {ao[along].sum() / ao.sum():.0%} of the '
                            'outlet area (an outflow, not the server exhaust)')
        un = speed
        dT = float((bc[fo, 4] * ao).sum() / ao.sum() - (bc[fi, 4] * ai).sum() / ai.sum())
        if not dT > 0:
            problems.append(f'server {s}: temperature rise {dT:g} K <= 0')
        Q = float((un * ao).sum())
        servers.append({'stem': s, 'outlets': outlets[s], 'inlets': inl, 'Q': Q, 'dT': dT, 'P': RHO * CP * Q * dT,
                        'centroid': ((pos_b[fo] * ao[:, None]).sum(0) / ao.sum()).tolist(),
                        'outlet_area': float(ao.sum()), 'inlet_T_t1': float((bc[fi, 4] * ai).sum() / ai.sum())})
    if len(servers) >= 32767:
        problems.append(f'{len(servers)} servers do not fit the int16 nearest-server index')
    facts = {'n_servers': len(servers), 'n_server_inlets_unpaired': len(set(inlets) - used),
             'outlet_faces_normal_along_U': n_along,
             'inlet_T_t1_max_dev_K': max((abs(s['inlet_T_t1'] - T_INIT) for s in servers), default=0.0),
             'P_total_W': float(sum(s['P'] for s in servers))}
    return servers, problems, facts


def nearest_server(pos, servers, cap=D_CAP):
    """int16 [N]: index of the server whose outlet centroid is nearest to each node within `cap` metres, else -1."""
    from scipy.spatial import cKDTree
    pos = np.asarray(pos, np.float64)
    if not servers:
        return np.full(pos.shape[0], -1, np.int16)
    tree = cKDTree(np.asarray([s['centroid'] for s in servers]))
    dist, idx = tree.query(pos, k=1, distance_upper_bound=cap)
    out = np.where(np.isfinite(dist), idx, -1)
    return out.astype(np.int16)


def build_source(case_dir, code=None):
    """Write source.json and source_nearest.npy of one cached case (refused, nothing written, when a required check
    fails; an existing source.json is never overwritten). Returns the source.json record."""
    d = Path(case_dir)
    if (d / 'source.json').exists():
        raise FileExistsError(f'{d / "source.json"} exists (never overwritten)')
    t0 = time.time()
    patches = json.loads((d / 'case.json').read_text())['patches']
    a = {k: np.load(d / f'{k}.npy', mmap_mode='r') for k in INPUT_FILES}
    ncell = a['volume'].shape[0]
    pos = np.asarray(a['pos'], np.float64)
    servers, problems, facts = server_table(patches, np.asarray(a['patch']), a['bc'], a['area'], a['normal'],
                                            pos[ncell:])
    if problems:
        raise AssertionError(f'{d.name}: ' + '; '.join(problems[:5]) + (f' (+{len(problems) - 5} more)'
                                                                        if len(problems) > 5 else ''))
    near = nearest_server(pos, servers)
    tmp = d / 'source_nearest.npy.partial.npy'
    np.save(tmp, near)
    tmp.rename(d / 'source_nearest.npy')
    rec = {'source_version': SOURCE_VERSION, 'case': d.name,
           'constants': {'RHO': RHO, 'CP': CP, 'T_INIT': T_INIT, 'D_CAP': D_CAP, 'Q_REF': Q_REF, 'P_REF': P_REF,
                         'DT_REF': DT_REF, 'Q_MASS': Q_MASS},
           'servers': servers, 'facts': facts,
           'nearest': {'nodes_within_cap': int((near >= 0).sum()), 'nodes': int(near.size),
                       'sha256': _sha(d / 'source_nearest.npy')},
           'inputs_sha256': {f'{k}.npy': _sha(d / f'{k}.npy') for k in INPUT_FILES}
           | {'case.json': _sha(d / 'case.json')},
           **(code or {}), 'seconds': round(time.time() - t0, 2),
           'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    part = d / 'source.json.partial'
    part.write_text(json.dumps(rec, indent=1) + '\n')
    part.rename(d / 'source.json')
    return rec


def write_manifest(root, code=None):
    """source_manifest.json at the cache root: sha256 of source.json and source_nearest.npy of every case that has
    them (rewritten each time: it lists what is there)."""
    root = Path(root)
    cases = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / 'source.json').is_file()):
        cases[d.name] = {f: _sha(d / f) for f in SOURCE_FILES}
    man = {'source_version': SOURCE_VERSION, 'cases': cases, **(code or {}),
           'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (root / MANIFEST).write_text(json.dumps(man, indent=1) + '\n')
    return man


_VERIFIED = set()                      # case directories whose source / spec inputs were hashed in this process
_NEAREST_OK = set()


def verify_inputs(d, rec, what):
    """Refuse a source / spec record whose cache inputs (inputs_sha256) changed since it was built; hashed once per
    process and case directory (about 40 MB per case)."""
    key = (str(Path(d).resolve()), what)
    if key in _VERIFIED:
        return
    for name, want in rec.get('inputs_sha256', {}).items():
        if _sha(Path(d) / name) != want:
            raise ValueError(f'{d}/{what}: input {name} changed since it was built (sha256); rebuild it')
    _VERIFIED.add(key)


def load_source(case_dir):
    """(server table, nearest [N] int16) of a case with source files; refuses a missing or different version, inputs
    that changed since the build (inputs_sha256) and a nearest-server index that does not match its recorded sha256."""
    d = Path(case_dir)
    if not (d / 'source.json').is_file():
        raise FileNotFoundError(f'{d}: no source.json (run `python -m scano.Datasets.dcts build-source '
                                f'{d.parent}`); boundary.source needs it')
    rec = json.loads((d / 'source.json').read_text())
    if rec.get('source_version') != SOURCE_VERSION:
        raise ValueError(f'{d}/source.json: source_version {rec.get("source_version")} != {SOURCE_VERSION}')
    verify_inputs(d, rec, 'source.json')
    if str(Path(d).resolve()) not in _NEAREST_OK:
        if _sha(d / 'source_nearest.npy') != rec['nearest']['sha256']:
            raise ValueError(f'{d}/source_nearest.npy does not match source.json (sha256)')
        _NEAREST_OK.add(str(Path(d).resolve()))
    return rec, np.load(d / 'source_nearest.npy')


def face_columns(servers, patch_ids, n_patches, bc, normal, area):
    """F1 per boundary face, physical units: (q [Nb] W/m^2 on outlet faces, P [Nb] W and dT [Nb] K on outlet and inlet
    faces, speed [Nb] m/s, is_outlet [Nb] bool). Vectorised over the faces (it runs at every case switch)."""
    pid = np.asarray(patch_ids, np.int64)
    out_srv = np.full(n_patches, -1, np.int64)
    in_srv = np.full(n_patches, -1, np.int64)
    for k, s in enumerate(servers):
        out_srv[s['outlets']] = k
        in_srv[s['inlets']] = k
    so, si = out_srv[pid], in_srv[pid]
    out = so >= 0
    dT = np.asarray([s['dT'] for s in servers] + [0.0])           # index -1 -> the trailing 0
    load = np.asarray([s['P'] for s in servers] + [0.0])
    u = np.asarray(bc[:, :3], np.float64)
    speed = np.sqrt((u * u).sum(1))
    q = np.where(out, RHO * CP * speed * dT[so], 0.0)
    P = np.where(out, load[so], load[si])
    dTf = np.where(out, dT[so], dT[si])
    return q, P, dTf, speed, out


def node_columns(servers, nearest, pos):
    """F2 per node [N, 6] float32 (scaled): P_s / P_REF, dT_s / DT_REF, (x - x_s) / D_CAP, |x - x_s| / D_CAP of the
    nearest server within D_CAP; beyond it zeros and distance 1. Torch on the CPU, one gather and no masked copies
    (it runs at every case switch of a training: about 0.03-0.06 s for 1.4M nodes)."""
    import torch
    pos = torch.from_numpy(np.ascontiguousarray(pos, np.float32))
    near = torch.from_numpy(np.asarray(nearest, np.int64))
    tab = torch.tensor([[s['P'] / P_REF, s['dT'] / DT_REF, *s['centroid']] for s in servers] + [[0.0] * 5],
                       dtype=torch.float32)
    t = tab[near]                                          # index -1: the trailing zero row
    ok = (near >= 0).unsqueeze(1)
    off = (pos - t[:, 2:5]) / D_CAP * ok
    dist = torch.where(ok, torch.linalg.vector_norm(off, dim=1, keepdim=True), torch.ones(()))
    return torch.cat([t[:, 0:2], off, dist], 1)


def server_jets(servers, patch_ids, n_patches, bc, area):
    """[S, 3] float64 unit exhaust direction of every server: the area-weighted mean of the boundary velocity `bc`
    [:, :3] on its outlet faces (the t1 values, or the specification's -(Q/A) n under bc_input = "spec"), i.e. the
    direction the outlet blows into the room. A server whose mean outlet velocity vanishes is refused."""
    pid = np.asarray(patch_ids, np.int64)
    out_srv = np.full(n_patches, -1, np.int64)
    for k, s in enumerate(servers):
        out_srv[s['outlets']] = k
    so = out_srv[pid]
    m = so >= 0
    a = np.asarray(area, np.float64)[m]
    u = np.asarray(bc, np.float64)[m, :3]
    S = len(servers)
    J = np.stack([np.bincount(so[m], weights=a * u[:, i], minlength=S) for i in range(3)], 1)
    norm = np.linalg.norm(J, axis=1)
    if S and not (norm > 0).all():
        raise ValueError(f'server_jets: {int((norm <= 0).sum())} server(s) with no mean outlet velocity')
    return J / norm[:, None] if S else J.reshape(0, 3)


def jet_frames(jets, up=UP):
    """[S, 3, 3] float64: per server the rows e_along (the jet), e_lateral and e_vertical (UP made orthogonal to the
    jet), right-handed (e_along x e_lateral = e_vertical). A jet within 1e-3 rad of the vertical is refused (no
    horizontal direction to build the frame on; every DCTS exhaust is horizontal)."""
    e1 = np.asarray(jets, np.float64).reshape(-1, 3)
    up = np.asarray(up, np.float64)
    e3 = up[None, :] - (e1 @ up)[:, None] * e1
    n3 = np.linalg.norm(e3, axis=1)
    if (n3 < 1e-3).any():
        raise ValueError(f'jet_frames: {int((n3 < 1e-3).sum())} exhaust jet(s) along the vertical')
    e3 = e3 / n3[:, None]
    e2 = np.cross(e3, e1)
    return np.stack([e1, e2, e3], 1)


def node_columns_jet(servers, jets, nearest, pos):
    """F2 in the exhaust frame per node [N, 10] float32 (F2J_COLUMNS, scaled): P_s / P_REF, dT_s / DT_REF, the offset
    to the nearest server's outlet centroid within D_CAP along its jet, lateral and vertical / D_CAP, the signed
    distance (+ downstream of the outlet plane, - upstream) / D_CAP, the distance / D_CAP and the jet direction (room
    axes); beyond D_CAP zeros and distance 1. `jets` [S, 3]: server_jets. Torch on the CPU, gathers only."""
    import torch
    pos = torch.from_numpy(np.ascontiguousarray(pos, np.float32))
    near = torch.from_numpy(np.asarray(nearest, np.int64))
    tab = torch.tensor([[s['P'] / P_REF, s['dT'] / DT_REF, *s['centroid']] for s in servers] + [[0.0] * 5],
                       dtype=torch.float32)
    frames = np.concatenate([jet_frames(jets), np.zeros((1, 3, 3))]) if len(servers) else np.zeros((1, 3, 3))
    fr = torch.from_numpy(frames.astype(np.float32))
    t, f = tab[near], fr[near]                             # index -1: the trailing zero row / frame
    ok = (near >= 0).unsqueeze(1)
    off = (pos - t[:, 2:5]) / D_CAP * ok
    loc = torch.einsum('nij,nj->ni', f, off)               # along, lateral, vertical
    dist = torch.linalg.vector_norm(off, dim=1, keepdim=True)
    signed = torch.where(loc[:, :1] > 0, dist, -dist).masked_fill(~ok, 0.0)
    dist = torch.where(ok, dist, torch.ones(()))
    return torch.cat([t[:, 0:2], loc, signed, dist, f[:, 0, :]], 1)


# --- boundary values from the case specification (boundary.bc_input = "spec"; R29 audit item 9) -------------------
# The cfdrun of a case (its boundaryBindings: per patch the bcType and parameters) fixes every t1 boundary value but
# p_rgh: on the 500 rooms (scratch/dcts_source/spec, 10-07) T agrees with the t1 values to float32 precision on every
# face, U on CRAC supplies to 2e-4, on server outlets to the patch area (|U| = Q / A with OpenFOAM's face areas; the
# cached areas give up to 2.5 % on 8 % of the outlet patches), on server inlets in total flow (the outflow condition
# scales the first iteration's profile, 1.5 % face-to-face variation in the median patch); returns, walls and
# baffles are at rest (6,699 of 17.5 million baffle faces carry a small first-iteration velocity).
SPEC_VERSION = 1
SPEC_FILE = 'spec_bc.json'
SPEC_MANIFEST = 'spec_manifest.json'
ROLE_BC = {'server_outlet': 'fan_outlet', 'server_inlet': 'fan_inlet', 'acu_supply': 'acu_supply',
           'acu_return': 'acu_return'}


def find_cfdrun(spec_root, name):
    """The cfdrun spec of a cached case: <spec_root>/<name>/logs/*.cfdrun.patched (interventions) or
    <spec_root>/case_<number>/logs/*.cfdrun.patched (the 500 rooms); exactly one file."""
    spec_root = Path(spec_root)
    cands = [spec_root / name / 'logs']
    num = name[len('GTC_dataset_'):] if name.startswith('GTC_dataset_') else ''
    if num.isdigit():
        cands.append(spec_root / f'case_{num}' / 'logs')
    for d in cands:
        files = sorted(d.glob('*.cfdrun.patched')) if d.is_dir() else []
        if len(files) > 1:
            raise AssertionError(f'{d}: {len(files)} cfdrun files')
        if files:
            return files[0]
    raise FileNotFoundError(f'no cfdrun spec for {name} under {spec_root}')


def spec_patch_values(patches, bindings, patch_area):
    """(un [P] m/s along the outward face normal, T [P] K, problems) per patch from the boundary bindings: outlets
    -Q/A and T_INIT + P / (RHO CP Q), inlets +Q/A and T_INIT, CRAC supplies -Q/A and the set point, everything else at
    rest and T_INIT."""
    P = len(patches)
    un, T, problems = np.zeros(P), np.full(P, T_INIT), []
    for i, p in enumerate(patches):
        b = bindings.get(p['name'])
        t = b['bcType'] if b else None
        want = ROLE_BC.get(p.get('role'))
        if p['n_faces'] and want and t != want:
            problems.append(f"{p['name']}: role {p.get('role')} but binding {t}")
            continue
        if not p['n_faces'] or t in (None, 'wall', 'acu_return'):
            continue
        par = b.get('parameters', {})
        if t == 'fan_outlet':
            un[i] = -par['flowRateM3s'] / patch_area[i]
            T[i] = T_INIT + par['powerW'] / (RHO * CP * par['flowRateM3s'])
        elif t == 'fan_inlet':
            un[i] = par['flowRateM3s'] / patch_area[i]
        elif t == 'acu_supply':
            un[i] = -par['flowRateM3s'] / patch_area[i]
            T[i] = par['setPointTempC'] + 273.15
        else:
            problems.append(f"{p['name']}: bcType {t} not handled")
    return un, T, problems


def spec_bc(rec, patch_ids, normal):
    """[Nb, 5] physical boundary values u, v, w, p_rgh, T of a spec record (p_rgh 0: not specified)."""
    pid = np.asarray(patch_ids, np.int64)
    un = np.asarray(rec['patch_un'])[pid]
    out = np.zeros((len(pid), 5))
    out[:, :3] = un[:, None] * np.asarray(normal, np.float64)
    out[:, 4] = np.asarray(rec['patch_T'])[pid]
    return out


def build_spec(case_dir, spec_root, code=None):
    """Write spec_bc.json of one cached case (never overwritten; refused with nothing written when a role and its
    binding disagree): per patch the normal velocity and temperature of the specification, the server table computed
    from them, and the agreement with the t1 values per role (facts)."""
    d = Path(case_dir)
    if (d / SPEC_FILE).exists():
        raise FileExistsError(f'{d / SPEC_FILE} exists (never overwritten)')
    t0 = time.time()
    patches = json.loads((d / 'case.json').read_text())['patches']
    a = {k: np.load(d / f'{k}.npy', mmap_mode='r') for k in INPUT_FILES}
    pid = np.asarray(a['patch'], np.int64)
    area = np.asarray(a['area'], np.float64)
    patch_area = np.bincount(pid, weights=area, minlength=len(patches))
    cfd = find_cfdrun(spec_root, d.name)
    bindings = {b['patchName']: b for b in json.loads(cfd.read_text())['boundaryBindings'].values()}
    un, T, problems = spec_patch_values(patches, bindings, patch_area)
    if problems:
        raise AssertionError(f'{d.name}: ' + '; '.join(problems[:5]))
    rec = {'patch_un': un.tolist(), 'patch_T': T.tolist()}
    bc = spec_bc(rec, pid, a['normal'])
    ncell = a['volume'].shape[0]
    pos_b = np.asarray(a['pos'], np.float64)[ncell:]
    servers, sp, _ = server_table(patches, pid, bc, area, a['normal'], pos_b)
    if sp:
        raise AssertionError(f'{d.name} (spec): ' + '; '.join(sp[:5]))
    t1 = np.asarray(a['bc'], np.float64)
    facts = {}
    for role in ('server_outlet', 'server_inlet', 'acu_supply', 'acu_return', 'wall', 'cyclic'):
        m = np.isin(pid, [i for i, p in enumerate(patches) if p.get('role') == role])
        if not m.any():
            continue
        speed = np.linalg.norm(bc[m, :3], axis=1)
        du = np.linalg.norm(t1[m, :3] - bc[m, :3], axis=1)
        facts[role] = {'faces': int(m.sum()), 'max_abs_dT_vs_t1': float(np.abs(t1[m, 4] - bc[m, 4]).max()),
                       'max_rel_dU_vs_t1': float((du / np.maximum(speed, 1e-30))[speed > 0].max()) if (speed > 0).any()
                       else None, 'max_abs_U_t1_where_spec_at_rest': float(np.linalg.norm(t1[m, :3], axis=1)[speed == 0]
                                                                            .max()) if (speed == 0).any() else None}
    rec.update({'spec_version': SPEC_VERSION, 'case': d.name, 'cfdrun': {'file': cfd.name, 'sha256': _sha(cfd)},
                'constants': {'RHO': RHO, 'CP': CP, 'T_INIT': T_INIT}, 'servers': servers, 'facts_vs_t1': facts,
                'inputs_sha256': {f'{k}.npy': _sha(d / f'{k}.npy') for k in INPUT_FILES} | {'case.json': _sha(d / 'case.json')},
                **(code or {}), 'seconds': round(time.time() - t0, 2),
                'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    part = d / (SPEC_FILE + '.partial')
    part.write_text(json.dumps(rec) + '\n')
    part.rename(d / SPEC_FILE)
    return rec


def write_spec_manifest(root, code=None):
    root = Path(root)
    cases = {d.name: _sha(d / SPEC_FILE) for d in sorted(p for p in root.iterdir() if (p / SPEC_FILE).is_file())}
    man = {'spec_version': SPEC_VERSION, 'cases': cases, **(code or {}),
           'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (root / SPEC_MANIFEST).write_text(json.dumps(man, indent=1) + '\n')
    return man


def load_spec(case_dir):
    d = Path(case_dir)
    if not (d / SPEC_FILE).is_file():
        raise FileNotFoundError(f'{d}: no {SPEC_FILE} (run `python -m scano.Datasets.dcts build-spec {d.parent} '
                                '--spec-root <cfdrun root>`); boundary.bc_input = "spec" needs it')
    rec = json.loads((d / SPEC_FILE).read_text())
    if rec.get('spec_version') != SPEC_VERSION:
        raise ValueError(f'{d}/{SPEC_FILE}: spec_version {rec.get("spec_version")} != {SPEC_VERSION}')
    verify_inputs(d, rec, SPEC_FILE)
    return rec
