"""Compare a freshly meshed DCTS room with the JUPITER reference case (mesh + 0/ boundary conditions).

    python -I compare_case.py <reference case dir> <new case dir> [--json out.json]

Either case may be reconstructed (constant/polyMesh) or decomposed (processor*/constant/polyMesh); a decomposed
mesh is summed over its ranks with the processor patches left out (their faces are internal faces of the whole
mesh). Geometry follows OpenFOAM's primitiveMesh: face centres / areas by triangle fans about the face's point
average, cell volumes by pyramids about the mean of the cell's face centres.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from backend.aircooling.runner.polymesh_io import read_all_faces, read_label_list, read_points  # noqa: E402


def parse_boundary(path):
    """[(name, {type, nFaces, startFace, ...}), ...] of a polyMesh/boundary file, in file order."""
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', Path(path).read_text(), flags=re.S)
    text = text[text.index('}', text.index('FoamFile')) + 1:]
    body = text[text.index('(') + 1:text.rindex(')')]
    out = []
    for m in re.finditer(r'(\S+)\s*\{([^{}]*)\}', body):
        d = {}
        for k, v in re.findall(r'(\w+)\s+([^;]*);', m.group(2)):
            d[k] = v.strip()
        out.append((m.group(1), d))
    return out


def face_geometry(points, offsets, verts):
    """(centres [F, 3], area vectors [F, 3]) of every face (OpenFOAM's triangle fan about the point average)."""
    n = np.diff(offsets)
    fid = np.repeat(np.arange(n.size), n)
    p = points[verts]
    est = np.zeros((n.size, 3))
    np.add.at(est, fid, p)
    est /= n[:, None]
    nxt = np.arange(verts.size) + 1
    last = offsets[1:] - 1
    nxt[last] = offsets[:-1]                      # each face's last point closes to its first
    q = points[verts[nxt]]
    c = p + q + est[fid]
    nv = np.cross(q - p, est[fid] - p)
    a = np.linalg.norm(nv, axis=1)
    sum_n = np.zeros((n.size, 3))
    sum_a = np.zeros(n.size)
    sum_ac = np.zeros((n.size, 3))
    for k in range(3):
        sum_n[:, k] = np.bincount(fid, nv[:, k], n.size)
        sum_ac[:, k] = np.bincount(fid, a * c[:, k], n.size)
    sum_a = np.bincount(fid, a, n.size)
    ctr = np.where(sum_a[:, None] > 1e-300, sum_ac / (3 * np.maximum(sum_a, 1e-300))[:, None], est)
    return ctr, 0.5 * sum_n


def cell_volumes(fc, fa, owner, neighbour, ncell):
    """Cell volumes by face pyramids about the mean of each cell's face centres (OpenFOAM's estimate)."""
    nint = neighbour.size
    cnt = np.bincount(owner, minlength=ncell) + np.bincount(neighbour, minlength=ncell)
    est = np.zeros((ncell, 3))
    for k in range(3):
        est[:, k] = np.bincount(owner, fc[:, k], ncell) + np.bincount(neighbour, fc[:nint, k], ncell)
    est /= cnt[:, None]
    vo = np.maximum(np.einsum('ij,ij->i', fa, fc - est[owner]), 1e-300)
    vn = np.maximum(np.einsum('ij,ij->i', fa[:nint], est[neighbour] - fc[:nint]), 1e-300)
    return (np.bincount(owner, vo, ncell) + np.bincount(neighbour, vn, ncell)) / 3.0


def read_rank(poly):
    pts = read_points(poly / 'points').astype(np.float64)
    off, verts = read_all_faces(poly / 'faces')
    own = read_label_list(poly / 'owner').astype(np.int64)
    nei = read_label_list(poly / 'neighbour').astype(np.int64)
    ncell = int(max(own.max(), nei.max() if nei.size else 0)) + 1
    fc, fa = face_geometry(pts, off, verts)
    vol = cell_volumes(fc, fa, own, nei, ncell)
    return {'points': pts.shape[0], 'cells': ncell, 'faces': own.size, 'internal': nei.size, 'vol': vol,
            'area': np.linalg.norm(fa, axis=1), 'centre': fc, 'boundary': parse_boundary(poly / 'boundary')}


def mesh_summary(case):
    case = Path(case)
    polys = [case / 'constant' / 'polyMesh'] if (case / 'constant' / 'polyMesh' / 'faces').is_file() else \
        sorted((p / 'constant' / 'polyMesh' for p in case.glob('processor*')), key=lambda p: int(p.parts[-3][9:]))
    if not polys:
        raise SystemExit(f'{case}: no mesh')
    tot = {'layout': 'reconstructed' if len(polys) == 1 and polys[0].parts[-3] != 'processor0' else
           f'{len(polys)} ranks', 'cells': 0, 'internal_faces': 0, 'boundary_faces': 0, 'points_sum': 0}
    vols, patches = [], {}
    proc_faces = 0
    for poly in polys:
        r = read_rank(poly)
        tot['cells'] += r['cells']
        tot['points_sum'] += r['points']
        tot['internal_faces'] += r['internal']
        vols.append(r['vol'])
        for name, d in r['boundary']:
            s, n = int(d['startFace']), int(d['nFaces'])
            if d['type'] in ('processor', 'processorCyclic'):
                proc_faces += n
                if d['type'] == 'processorCyclic':          # a baffle face whose partner is on another rank
                    name = d['referPatch']
                else:
                    continue
            p = patches.setdefault(name, {'type': d['type'] if d['type'] != 'processorCyclic' else 'cyclic',
                                          'nFaces': 0, 'area': 0.0, 'centroid_sum': np.zeros(3)})
            p['nFaces'] += n
            a = r['area'][s:s + n]
            p['area'] += float(a.sum())
            p['centroid_sum'] += (r['centre'][s:s + n] * a[:, None]).sum(0)
    tot['internal_faces'] += proc_faces // 2                # each processor face is stored on both ranks
    tot['boundary_faces'] = sum(p['nFaces'] for p in patches.values())
    vol = np.concatenate(vols)
    q = np.quantile(vol, [0, 0.01, 0.1, 0.5, 0.9, 0.99, 1])
    tot['volume_total'] = float(vol.sum())
    tot['volume_quantiles'] = q.tolist()
    tot['edge_quantiles_m'] = np.cbrt(q).tolist()
    # cell size histogram in octree levels below the largest cell (refinement level of each cell)
    lev = np.round(np.log2(np.cbrt(vol.max() / vol))).astype(int)
    tot['cells_per_level'] = {int(k): int(v) for k, v in zip(*np.unique(np.clip(lev, 0, 8), return_counts=True))}
    for p in patches.values():
        p['centroid'] = (p.pop('centroid_sum') / p['area']).tolist() if p['area'] > 0 else None
    return tot, patches


# --- 0/ boundary conditions ------------------------------------------------------------------------------------

def field_patches(path):
    """{patch name: normalised text of its boundaryField entry} of a 0/ field file (regex patterns kept as keys)."""
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', Path(path).read_text(), flags=re.S)
    i = text.index('boundaryField')
    body = text[text.index('{', i) + 1:]
    out, pos = {}, 0
    while True:
        m = re.compile(r'\s*("[^"]*"|[^\s{}]+)\s*\{').match(body, pos)
        if not m:
            break
        depth, j = 1, m.end()
        while depth:
            depth += {'{': 1, '}': -1}.get(body[j], 0)
            j += 1
        out[m.group(1).strip('"')] = ' '.join(body[m.end():j - 1].split())
        pos = j
    return out


def bc_diff(ref_case, new_case, fields=('U', 'T', 'p_rgh', 'p', 'k', 'epsilon', 'nut', 'alphat')):
    def zero_dir(case):
        case = Path(case)
        for d in (case / '0', case / 'processor0' / '0'):
            if d.is_dir():
                return d
    rz, nz = zero_dir(ref_case), zero_dir(new_case)
    out = {'ref_0': str(rz), 'new_0': str(nz)}
    if rz is None or nz is None:
        out['error'] = 'no 0/ directory in one of the cases'
        return out
    for f in fields:
        if not (rz / f).is_file() or not (nz / f).is_file():
            out[f] = {'missing': [str(p) for p in (rz / f, nz / f) if not p.is_file()]}
            continue
        a, b = field_patches(rz / f), field_patches(nz / f)
        a = {k: v for k, v in a.items() if not k.startswith('procBoundary')}
        b = {k: v for k, v in b.items() if not k.startswith('procBoundary')}
        same = [k for k in a if k in b and a[k] == b[k]]
        differ = {k: (a[k][:300], b[k][:300]) for k in a if k in b and a[k] != b[k]}
        kinds = {}
        for k, (x, y) in differ.items():
            tx = re.search(r'type (\S+);', x)
            ty = re.search(r'type (\S+);', y)
            key = f"{tx.group(1) if tx else '?'} -> {ty.group(1) if ty else '?'}"
            kinds.setdefault(key, []).append(k)
        out[f] = {'patches_ref': len(a), 'patches_new': len(b), 'identical': len(same), 'different': len(differ),
                  'only_ref': sorted(set(a) - set(b))[:20], 'only_new': sorted(set(b) - set(a))[:20],
                  'difference_kinds': {k: {'count': len(v), 'example': v[0], 'ref': differ[v[0]][0],
                                           'new': differ[v[0]][1]} for k, v in kinds.items()}}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ref')
    ap.add_argument('new')
    ap.add_argument('--json')
    a = ap.parse_args()
    (rt, rp), (nt, npch) = mesh_summary(a.ref), mesh_summary(a.new)
    print(f"{'':22s} {'reference':>16s} {'new':>16s} {'new/ref':>8s}")
    for k in ('cells', 'internal_faces', 'boundary_faces', 'points_sum', 'volume_total'):
        print(f'{k:22s} {rt[k]:16.6g} {nt[k]:16.6g} {nt[k] / rt[k]:8.4f}')
    print('layout', rt['layout'], '|', nt['layout'])
    print('cell edge quantiles (m) ref', np.round(rt['edge_quantiles_m'], 4).tolist())
    print('cell edge quantiles (m) new', np.round(nt['edge_quantiles_m'], 4).tolist())
    print('cells per level ref', rt['cells_per_level'])
    print('cells per level new', nt['cells_per_level'])
    only_r, only_n = sorted(set(rp) - set(npch)), sorted(set(npch) - set(rp))
    common = sorted(set(rp) & set(npch))
    tmis = [k for k in common if rp[k]['type'] != npch[k]['type']]
    rel = {k: npch[k]['nFaces'] / rp[k]['nFaces'] - 1 for k in common if rp[k]['nFaces']}
    arel = {k: npch[k]['area'] / rp[k]['area'] - 1 for k in common if rp[k]['area'] > 0}
    print(f'patches: ref {len(rp)}, new {len(npch)}, common {len(common)}, only ref {len(only_r)}, only new '
          f'{len(only_n)}, type mismatch {len(tmis)}')
    if only_r:
        print('  only ref:', only_r[:10])
    if only_n:
        print('  only new:', only_n[:10])
    if tmis:
        print('  type mismatch:', [(k, rp[k]['type'], npch[k]['type']) for k in tmis[:10]])
    empty_r = [k for k in common if rp[k]['nFaces'] == 0]
    empty_n = [k for k in common if npch[k]['nFaces'] == 0]
    print(f'  empty patches ref {len(empty_r)}, new {len(empty_n)}')
    if rel:
        v = np.array(list(rel.values()))
        print(f'  face-count rel diff: median {np.median(v):+.3f}, |.|>10% on {int((np.abs(v) > .1).sum())} patches')
    if arel:
        v = np.array(list(arel.values()))
        print(f'  patch-area rel diff: median {np.median(v):+.2e}, max |.| {np.abs(v).max():.2e}, '
              f'|.|>1% on {int((np.abs(v) > .01).sum())} patches')
        worst = sorted(arel, key=lambda k: -abs(arel[k]))[:5]
        print('  worst area:', [(k, round(arel[k], 4), rp[k]['nFaces'], npch[k]['nFaces']) for k in worst])
    by_type = {}
    for k in common:
        t = rp[k]['type']
        by_type.setdefault(t, [0, 0])
        by_type[t][0] += rp[k]['nFaces']
        by_type[t][1] += npch[k]['nFaces']
    print('  boundary faces by type (ref, new):', by_type)
    bc = bc_diff(a.ref, a.new)
    print(json.dumps(bc, indent=1)[:6000])
    if a.json:
        Path(a.json).write_text(json.dumps({'ref': {**rt, 'patches': rp}, 'new': {**nt, 'patches': npch},
                                            'bc': bc, 'face_rel': rel, 'area_rel': arel}, indent=1, default=float))


if __name__ == '__main__':
    main()
