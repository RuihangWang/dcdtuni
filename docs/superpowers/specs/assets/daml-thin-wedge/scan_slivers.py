#!/usr/bin/env python3
"""Face-area sliver scan of an OpenFOAM binary polyMesh.
Reports the count of faces with area < 1e-6 m^2 (and a histogram), the metric
the WP-V6 acceptance uses (DAML-Testbed dcCart 'before' = 102 such faces)."""
import sys, re
import numpy as np

def parse_points(path):
    b = open(path, 'rb').read()
    m = re.search(rb'(\d+)\n\(', b[:2000]); s = m.end(); rp = b.rfind(b')')
    return np.frombuffer(b[s:rp], dtype='<f8').reshape(-1, 3).copy()

def parse_faces(path):
    b = open(path, 'rb').read()
    m1 = re.search(rb'(\d+)\n\(', b); tok1 = int(m1.group(1)); s1 = m1.end()
    offsets = np.frombuffer(b[s1:s1 + 4 * tok1], dtype='<i4').astype(np.int64)
    e1 = s1 + 4 * tok1
    m2 = re.search(rb'(\d+)\n\(', b[e1:]); tok2 = int(m2.group(1)); s2 = e1 + m2.end()
    labels = np.frombuffer(b[s2:s2 + 4 * tok2], dtype='<i4').astype(np.int64)
    return labels, offsets, tok1 - 1   # nFaces = tok1 - 1

def face_areas(points, fd, ip, nf):
    A = np.empty(nf)
    for f in range(nf):
        P = points[fd[ip[f]:ip[f + 1]]]
        fc_v = P.mean(axis=0)
        Q = np.roll(P, -1, axis=0)
        Sf = 0.5 * np.cross(Q - P, fc_v - P).sum(axis=0)
        A[f] = np.linalg.norm(Sf)
    return A

def main():
    d = sys.argv[1]
    pts = parse_points(f"{d}/points")
    fd, ip, nf = parse_faces(f"{d}/faces")
    A = face_areas(pts, fd, ip, nf)
    thr = 1e-6
    n_small = int((A < thr).sum())
    print(f"mesh: {d}")
    print(f"  nFaces = {nf}, nPoints = {len(pts)}")
    print(f"  faces with area < {thr:g} m^2 : {n_small}")
    # histogram of the small-face tail
    bins = [0, 1e-9, 1e-8, 1e-7, 5e-7, 1e-6, 1e-5, 1e-4, 1e-3]
    h, _ = np.histogram(A, bins=bins)
    print("  area histogram:")
    for i in range(len(h)):
        print(f"    [{bins[i]:.0e}, {bins[i+1]:.0e}) : {int(h[i])}")
    print(f"  min face area = {A.min():.6e} m^2")
    print(f"  faces < 1e-6 (the WP-V6 sliver metric) = {n_small}")
    # where are the small faces (centroids) for the record
    if n_small:
        idx = np.where(A < thr)[0]
        cs = []
        for f in idx[:200]:
            P = pts[fd[ip[f]:ip[f + 1]]]
            cs.append(P.mean(axis=0))
        cs = np.array(cs)
        if len(cs):
            print(f"  small-face centroid bbox: x[{cs[:,0].min():.3f},{cs[:,0].max():.3f}] "
                  f"y[{cs[:,1].min():.3f},{cs[:,1].max():.3f}] z[{cs[:,2].min():.3f},{cs[:,2].max():.3f}]")
    return n_small

if __name__ == '__main__':
    main()
