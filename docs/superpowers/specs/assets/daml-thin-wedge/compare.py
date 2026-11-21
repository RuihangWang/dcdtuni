#!/usr/bin/env python3
"""Canonical rank-invariance comparison: np1 serial mesh vs np4 reconstructParMesh.

Order-independent checks (reconstructParMesh renumbers points/faces/cells):
  1. counts equal              : nPoints, nFaces, nCells
  2. sorted point cloud        : lexicographic sort, max|Delta point| == 0
  3. sorted cell-volume multiset: bit-identical (and max relative Delta)

Cell volume by the divergence theorem with OpenFOAM's area-weighted face
centre (faceAreaCentreOF), summed per cell in a CANONICAL order (each cell's
face contributions sorted by value before summing) so the result does not
depend on face numbering -> bit-comparable across the two meshes.
"""
import sys, re, struct
import numpy as np

def _blob(b):
    """Return (start, end) of the binary data block: after the data '(' , before the last ')'."""
    # data '(' is the one preceded by  <count>\n
    m = None
    for mm in re.finditer(rb'\d+\n\(', b[:2000]):
        m = mm
    if m is None:
        raise RuntimeError("data '(' not found")
    start = m.end()           # byte right after '('
    end = b.rfind(b')')
    return start, end

def parse_points(path):
    b = open(path, 'rb').read()
    s, e = _blob(b)
    a = np.frombuffer(b[s:e], dtype='<f8')
    assert a.size % 3 == 0, a.size
    return a.reshape(-1, 3).copy()

def parse_labels(path):
    b = open(path, 'rb').read()
    s, e = _blob(b)
    return np.frombuffer(b[s:e], dtype='<i4').astype(np.int64).copy()

def parse_faces(path, nFaces):
    """OpenFOAM binary faceList = TWO concatenated binary lists:
         <nFaces+1>\n( <offsets int32[nFaces+1]> )\n  <nLabels>\n( <labels int32> )
    offsets[i]..offsets[i+1] index the vertex labels of face i. Returns (labels, offsets)."""
    b = open(path, 'rb').read()
    m1 = re.search(rb'(\d+)\n\(', b)            # offsets list (first count-paren)
    tok1 = int(m1.group(1)); s1 = m1.end()
    offsets = np.frombuffer(b[s1:s1 + 4 * tok1], dtype='<i4').astype(np.int64)
    e1 = s1 + 4 * tok1                          # offsets blob ends here, b[e1] == ')'
    m2 = re.search(rb'(\d+)\n\(', b[e1:])       # labels list (search AFTER offsets blob)
    tok2 = int(m2.group(1)); s2 = e1 + m2.end()
    labels = np.frombuffer(b[s2:s2 + 4 * tok2], dtype='<i4').astype(np.int64)
    assert tok1 == nFaces + 1, (tok1, nFaces)
    assert int(offsets[nFaces]) == tok2 == labels.size, (offsets[nFaces], tok2, labels.size)
    return labels, offsets, nFaces

def face_geo(P):
    """OpenFOAM area vector Sf and area-weighted centre fc for polygon P (n,3).
    The ring is rotated to start at its lexicographically-smallest vertex so the
    Newell sums reassociate identically regardless of how reconstructParMesh
    renumbered/rotated the face -> bit-comparable across meshes."""
    n = len(P)
    mi = min(range(n), key=lambda i: (P[i, 0], P[i, 1], P[i, 2]))
    if mi:
        P = np.roll(P, -mi, axis=0)
    fc_v = P.mean(axis=0)
    Q = np.roll(P, -1, axis=0)                 # P_{i+1}
    ni = np.cross(Q - P, fc_v - P)             # (n,3)
    Sf = 0.5 * ni.sum(axis=0)
    ai = np.linalg.norm(ni, axis=1)
    sumA = ai.sum()
    fc = (ai[:, None] * (P + Q + fc_v)).sum(axis=0) / (3.0 * sumA) if sumA > 0 else fc_v
    return Sf, fc

def cell_volumes(points, faces_data, indptr, n_faces, owner, neighbour):
    # neighbour holds only the internal faces (OpenFOAM orders them first);
    # pad to nFaces with -1 for boundary faces.
    nb = np.full(n_faces, -1, dtype=np.int64)
    nb[:len(neighbour)] = neighbour
    nCells = int(max(owner.max(), nb.max())) + 1
    # per face contribution = fc . Sf  (signed by orientation later)
    contrib_owner = np.empty(n_faces)
    for f in range(n_faces):
        a, bnd = indptr[f], indptr[f + 1]
        verts = faces_data[a:bnd]
        P = points[verts]
        Sf, fc = face_geo(P)
        contrib_owner[f] = float(np.dot(fc, Sf))
    # build (cell, signed contribution) incidences
    cells = [owner]
    contribs = [contrib_owner]
    internal = nb >= 0
    if internal.any():
        cells.append(nb[internal])
        contribs.append(-contrib_owner[internal])
    cells = np.concatenate(cells)
    contribs = np.concatenate(contribs)
    # canonical order: sort by (cell, contribution) so the per-cell sum is
    # independent of face numbering -> bit-identical across meshes
    order = np.lexsort((contribs, cells))
    cs = cells[order]; cv = contribs[order]
    # group boundaries
    vol = np.zeros(nCells)
    if nCells:
        # reduceat over unique cells
        uniq, start_idx = np.unique(cs, return_index=True)
        sums = np.add.reduceat(cv, start_idx)
        vol[uniq] = sums / 3.0
    return vol, nCells

def lexsort_points(P):
    return np.lexsort((P[:, 2], P[:, 1], P[:, 0]))

def main():
    np1, np4 = sys.argv[1], sys.argv[2]
    print(f"np1 = {np1}\nnp4 = {np4}\n")
    res = {}
    p1 = parse_points(f"{np1}/points"); p4 = parse_points(f"{np4}/points")
    o1 = parse_labels(f"{np1}/owner"); o4 = parse_labels(f"{np4}/owner")
    n1 = parse_labels(f"{np1}/neighbour"); n4 = parse_labels(f"{np4}/neighbour")
    nf1 = len(o1); nf4 = len(o4)          # one owner entry per face
    fd1, ip1, _ = parse_faces(f"{np1}/faces", nf1)
    fd4, ip4, _ = parse_faces(f"{np4}/faces", nf4)

    res['nPoints'] = (len(p1), len(p4))
    res['nFaces'] = (nf1, nf4)
    res['nInternalFaces'] = (len(n1), len(n4))
    nC1 = int(max(o1.max(), n1.max())) + 1
    nC4 = int(max(o4.max(), n4.max())) + 1
    res['nCells'] = (nC1, nC4)

    # 2. sorted point cloud
    a = p1[lexsort_points(p1)]; b = p4[lexsort_points(p4)]
    if a.shape == b.shape:
        dmax = float(np.abs(a - b).max())
    else:
        dmax = float('nan')
    res['max|dPoint|'] = dmax

    # 3. cell volumes
    v1, _ = cell_volumes(p1, fd1, ip1, nf1, o1, n1)
    v4, _ = cell_volumes(p4, fd4, ip4, nf4, o4, n4)
    v1s = np.sort(v1); v4s = np.sort(v4)
    if v1s.shape == v4s.shape:
        exact = int(np.sum(v1s.tobytes() == v4s.tobytes()))  # placeholder
        bitwise = bool(v1s.tobytes() == v4s.tobytes())
        denom = np.maximum(np.abs(v1s), 1e-300)
        relmax = float(np.max(np.abs(v1s - v4s) / denom)) if v1s.size else 0.0
        absmax = float(np.max(np.abs(v1s - v4s))) if v1s.size else 0.0
    else:
        bitwise = False; relmax = float('nan'); absmax = float('nan')
    res['vol_bitwise_identical'] = bitwise
    res['vol_max_rel_delta'] = relmax
    res['vol_max_abs_delta'] = absmax
    res['vol_min'] = (float(v1s.min()), float(v4s.min()))
    res['vol_total'] = (float(v1s.sum()), float(v4s.sum()))

    print("=== CANONICAL COMPARISON ===")
    ok = True
    for k in ['nPoints', 'nFaces', 'nCells']:
        x, y = res[k]; e = (x == y); ok &= e
        print(f"  {k:10s}: np1={x:<10d} np4={y:<10d} {'EQUAL' if e else 'DIFFER'}")
    e = (res['max|dPoint|'] == 0.0); ok &= e
    print(f"  sorted point cloud max|Delta|: {res['max|dPoint|']:.3e}  {'== 0 PASS' if e else 'FAIL'}")
    print(f"  cell-volume multiset bitwise identical: {res['vol_bitwise_identical']}")
    print(f"  cell-volume max rel delta: {res['vol_max_rel_delta']:.3e}   max abs delta: {res['vol_max_abs_delta']:.3e}")
    print(f"  cell-volume min: np1={res['vol_min'][0]:.6e} np4={res['vol_min'][1]:.6e}")
    print(f"  cell-volume total: np1={res['vol_total'][0]:.9f} np4={res['vol_total'][1]:.9f}")
    vol_ok = res['vol_bitwise_identical'] or res['vol_max_rel_delta'] < 1e-12

    # 4. decisive order-invariant connectivity+geometry proof: the multiset of
    #    per-face SORTED vertex coordinates. Bit-identical points => identical
    #    keys => identical face sets => identical cell volumes geometrically (so
    #    any residual volume delta is pure FP reassociation, not geometry).
    def face_keys(points, fd, ip, nf):
        keys = []
        for f in range(nf):
            C = points[fd[ip[f]:ip[f + 1]]]
            C = C[np.lexsort((C[:, 2], C[:, 1], C[:, 0]))]
            keys.append(C.tobytes())
        keys.sort()
        return keys
    k1 = face_keys(p1, fd1, ip1, nf1)
    k4 = face_keys(p4, fd4, ip4, nf4)
    fk_identical = (k1 == k4)
    res['face_vertex_multiset_identical'] = fk_identical
    print(f"  face vertex-coordinate multiset identical: {fk_identical}"
          f"  (np1 {len(k1)} faces, np4 {len(k4)} faces)")
    ok &= vol_ok and fk_identical
    print(f"\n  REDLINE: {'PASS' if ok else 'FAIL'}")
    import json
    json.dump({k: (v if not isinstance(v, tuple) else list(v)) for k, v in res.items()},
              open('/tmp/redline_v6/comparison.json', 'w'), indent=1, default=str)
    print("  (wrote /tmp/redline_v6/comparison.json)")
    sys.exit(0 if ok else 1)

if __name__ == '__main__':
    main()
