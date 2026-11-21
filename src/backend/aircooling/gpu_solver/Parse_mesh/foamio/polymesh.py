"""OpenFOAM ``constant/polyMesh`` reader and finite-volume geometry.

Geometry follows OpenFOAM's definitions (``primitiveMeshFaceCentresAndAreas``,
``primitiveMeshCellCentresAndVols``, ``surfaceInterpolation``) so that interpolation weights,
delta coefficients and non-orthogonal correction vectors match the reference solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .foam_dict import FoamDict, read_foam_file, read_foam_list

__all__ = ["Patch", "Zone", "PolyMesh", "read_polymesh"]


@dataclass
class Patch:
    name: str
    type: str
    start: int
    size: int
    dict: FoamDict = field(default_factory=FoamDict)

    @property
    def slice(self) -> slice:
        return slice(self.start, self.start + self.size)

    @property
    def neighbour_patch(self) -> str | None:
        return self.dict.get("neighbourPatch")


@dataclass
class Zone:
    name: str
    labels: np.ndarray
    flip: np.ndarray | None = None


@dataclass
class PolyMesh:
    points: np.ndarray            # (P, 3)
    face_offsets: np.ndarray      # (F+1,)
    face_points: np.ndarray       # (sum face sizes,)
    owner: np.ndarray             # (F,)
    neighbour: np.ndarray         # (Fi,) internal faces only
    patches: list[Patch]
    cell_zones: dict[str, Zone] = field(default_factory=dict)
    face_zones: dict[str, Zone] = field(default_factory=dict)
    path: Path | None = None

    # geometry, filled by compute_geometry()
    Cf: np.ndarray | None = None
    Sf: np.ndarray | None = None
    magSf: np.ndarray | None = None
    C: np.ndarray | None = None
    V: np.ndarray | None = None

    @property
    def n_cells(self) -> int:
        return int(max(self.owner.max(initial=-1), self.neighbour.max(initial=-1)) + 1)

    @property
    def n_faces(self) -> int:
        return int(self.owner.shape[0])

    @property
    def n_internal_faces(self) -> int:
        return int(self.neighbour.shape[0])

    def patch(self, name: str) -> Patch:
        for p in self.patches:
            if p.name == name:
                return p
        raise KeyError(name)

    # ------------------------------------------------------------- geometry
    def compute_geometry(self) -> "PolyMesh":
        off, fp, pts = self.face_offsets, self.face_points, self.points
        F = self.n_faces
        sizes = np.diff(off)
        if sizes.min(initial=3) < 3:
            raise ValueError("face with fewer than three points")
        fid = np.repeat(np.arange(F), sizes)
        P = pts[fp]
        est = np.add.reduceat(P, off[:-1], axis=0) / sizes[:, None]
        nxt = np.arange(fp.size) + 1
        nxt[off[1:] - 1] = off[:-1]
        P1 = P[nxt]
        c3 = est[fid]
        n = np.cross(P1 - P, c3 - P)
        a = np.linalg.norm(n, axis=1)
        sumN = np.add.reduceat(n, off[:-1], axis=0)
        sumA = np.add.reduceat(a, off[:-1])
        sumAc = np.add.reduceat(a[:, None] * (P + P1 + c3), off[:-1], axis=0)
        small = sumA <= 1e-300
        Cf = np.where(small[:, None], est, sumAc / (3.0 * np.where(small, 1.0, sumA))[:, None])
        Sf = 0.5 * sumN
        self.Cf, self.Sf = Cf, Sf
        self.magSf = np.linalg.norm(Sf, axis=1)

        own, nei = self.owner, self.neighbour
        Fi = nei.shape[0]
        N = self.n_cells
        nfc = np.bincount(own, minlength=N) + np.bincount(nei, minlength=N)
        cEst = np.zeros((N, 3))
        np.add.at(cEst, own, Cf)
        np.add.at(cEst, nei, Cf[:Fi])
        cEst /= nfc[:, None]
        vo = np.einsum("ij,ij->i", Sf, Cf - cEst[own])
        vn = np.einsum("ij,ij->i", Sf[:Fi], cEst[nei] - Cf[:Fi])
        V = np.bincount(own, weights=vo, minlength=N) + np.bincount(nei, weights=vn, minlength=N)
        Cc = np.zeros((N, 3))
        np.add.at(Cc, own, vo[:, None] * (0.75 * Cf + 0.25 * cEst[own]))
        np.add.at(Cc, nei, vn[:, None] * (0.75 * Cf[:Fi] + 0.25 * cEst[nei]))
        ok = np.abs(V) > 1e-300
        Cc[ok] /= V[ok, None]
        Cc[~ok] = cEst[~ok]
        self.C = Cc
        self.V = V / 3.0
        return self

    def interpolation_geometry(self) -> dict[str, np.ndarray]:
        """Weights, deltas and non-orthogonal correction data (internal faces then boundary faces)."""
        if self.C is None:
            self.compute_geometry()
        own, nei, Cf, Sf, C = self.owner, self.neighbour, self.Cf, self.Sf, self.C
        Fi = nei.shape[0]
        nhat = Sf / self.magSf[:, None]
        d_own = np.abs(np.einsum("ij,ij->i", Sf[:Fi], Cf[:Fi] - C[own[:Fi]]))
        d_nei = np.abs(np.einsum("ij,ij->i", Sf[:Fi], C[nei] - Cf[:Fi]))
        w = np.ones(self.n_faces)
        w[:Fi] = d_nei / (d_own + d_nei)
        delta = np.empty_like(Cf)
        delta[:Fi] = C[nei] - C[own[:Fi]]
        db = Cf[Fi:] - C[own[Fi:]]
        nb = nhat[Fi:]
        delta[Fi:] = nb * np.einsum("ij,ij->i", nb, db)[:, None]  # OpenFOAM fvPatch::delta(): normal part
        magd = np.linalg.norm(delta, axis=1)
        deltaCoeffs = 1.0 / magd
        nonOrthDeltaCoeffs = 1.0 / np.maximum(np.einsum("ij,ij->i", nhat, delta), 0.05 * magd)
        corr = nhat - delta * nonOrthDeltaCoeffs[:, None]
        corr[Fi:] = 0.0
        return dict(weights=w, delta=delta, deltaCoeffs=deltaCoeffs,
                    nonOrthDeltaCoeffs=nonOrthDeltaCoeffs, nonOrthCorrectionVectors=corr, nhat=nhat)

    def non_orthogonality(self) -> np.ndarray:
        """Angle in degrees between the face normal and the owner->neighbour vector (internal faces)."""
        g = self.interpolation_geometry()
        Fi = self.n_internal_faces
        d = g["delta"][:Fi]
        c = np.einsum("ij,ij->i", g["nhat"][:Fi], d) / np.linalg.norm(d, axis=1)
        return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def _read_zones(path: Path, kind: str) -> dict[str, Zone]:
    if not path.exists() and not path.with_name(path.name + ".gz").exists():
        return {}
    _, body = read_foam_file(path)
    zones: dict[str, Zone] = {}
    # A zones file is a bare list "N ( name { ... } ... )"; the dictionary parser returns it under its
    # first token, so search every value for the alternating name/dict sequence.
    seq = None
    for k, v in body.items():
        if isinstance(v, (list, tuple)):
            seq = list(v)
            break
    if seq is None:
        return zones
    items = [x for x in seq]
    i = 0
    while i < len(items):
        name, d = items[i], items[i + 1]
        i += 2
        if not isinstance(d, FoamDict):
            continue
        key = "cellLabels" if kind == "cell" else "faceLabels"
        lab = d.get(key, np.zeros(0, dtype=np.int64))
        if isinstance(lab, tuple):
            lab = lab[-1]
        flip = d.get("flipMap") if kind == "face" else None
        if isinstance(flip, tuple):
            flip = flip[-1]
        zones[str(name)] = Zone(str(name), np.asarray(lab, dtype=np.int64),
                                None if flip is None else np.asarray(flip, dtype=bool))
    return zones


def _read_boundary(path: Path) -> list[Patch]:
    from .foam_dict import _Parser, _load_bytes, _split_header

    buf = _load_bytes(path)
    header, off = _split_header(buf)
    p = _Parser(buf, path, header)
    p.pos = off
    tok = p.read_word()
    while not tok.isdigit():
        tok = p.read_word()
    n = int(tok)
    assert p.read_word() == b"("
    patches = []
    for _ in range(n):
        name = p.read_word().decode()
        assert p.read_word() == b"{"
        d = p.parse_dict_body([], b"}")
        patches.append(Patch(name, str(d["type"]), int(d["startFace"]), int(d["nFaces"]), d))
    return patches


def read_polymesh(case: str | Path, region: str | None = None) -> PolyMesh:
    case = Path(case)
    pm = case / "constant" / (region or "") / "polyMesh" if region else case / "constant" / "polyMesh"
    _, points = read_foam_list(pm / "points", "vector")
    _, (offsets, labels) = read_foam_list(pm / "faces", "face")
    _, owner = read_foam_list(pm / "owner", "label")
    _, neighbour = read_foam_list(pm / "neighbour", "label")
    mesh = PolyMesh(
        points=np.asarray(points, dtype=np.float64).reshape(-1, 3),
        face_offsets=np.asarray(offsets, dtype=np.int64),
        face_points=np.asarray(labels, dtype=np.int64),
        owner=np.asarray(owner, dtype=np.int64),
        neighbour=np.asarray(neighbour, dtype=np.int64),
        patches=_read_boundary(pm / "boundary"),
        cell_zones=_read_zones(pm / "cellZones", "cell"),
        face_zones=_read_zones(pm / "faceZones", "face"),
        path=pm,
    )
    return mesh.compute_geometry()
