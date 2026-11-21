"""Mesh case schema v2: regions, cell zones and interfaces (docs/development/mesh-schema-v2.md).

The one place that knows how a multi-region case is stored in the mesh h5: the parser writes it with ``write``, the
loader reads it with ``read`` and checks it with ``validate``. A file without ``schema_version`` (v1, every case parsed
without regions) reads as one fluid region without zones or interfaces, so every existing case loads unchanged.

Stored (all additive, flat top-level names, so ``H5CFDdataset``'s generic dataset loop and the scripts that read the h5
with h5py keep working):

* root attrs ``schema_version`` (int), ``regions`` / ``zones`` / ``interfaces`` / ``provenance`` (JSON);
* ``cell|region_id`` [n_cells] int16 -- written only when the case has more than one region;
* ``zone|cells`` / ``zone|cell_ptr`` [n_members] int32 -- zone membership in the pointer-array format of this codebase
  (``zone|cell_ptr`` is the zone index of each entry);
* ``iface|face_a`` / ``iface|face_b`` [n_pairs] int64, ``iface|id`` [n_pairs] int16 -- the face pairs of every
  interface, in side a's patch order.

Material properties and sources are NOT stored here: they are query parameters (fvconfig ``[regions.*]``,
``[zones.*]``, ``[sources.*]``), resolved per query by ``FVdomain.region_physics``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

SCHEMA_VERSION = 2
REGION_KINDS = ("fluid", "solid")
INTERFACE_KINDS = ("conjugate",)
INTERFACE_PAIRINGS = ("mapped", "geometric")

DS_CELL_REGION = "cell|region_id"
DS_ZONE_CELLS = "zone|cells"
DS_ZONE_PTR = "zone|cell_ptr"
DS_IFACE_A = "iface|face_a"
DS_IFACE_B = "iface|face_b"
DS_IFACE_ID = "iface|id"
# static per mesh: shared by every slot of an h5 in the mmap pool (MmapManager.STATIC_TENSOR_KEYS)
SCHEMA_TENSOR_KEYS = [DS_CELL_REGION, DS_ZONE_CELLS, DS_ZONE_PTR, DS_IFACE_A, DS_IFACE_B, DS_IFACE_ID]


@dataclass
class RegionSchema:
    """Regions, zones and interfaces of one mesh. ``cell_region_id`` None means every cell is in region 0."""
    num_cells: int
    regions: List[Dict]
    zones: List[Dict] = field(default_factory=list)
    interfaces: List[Dict] = field(default_factory=list)
    cell_region_id: Optional[np.ndarray] = None
    zone_cells: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))
    zone_ptr: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))
    iface_face_a: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    iface_face_b: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    iface_id: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int16))
    provenance: Dict = field(default_factory=dict)
    version: int = SCHEMA_VERSION

    # ---- queries -------------------------------------------------------------------------------------------------
    @property
    def is_trivial(self) -> bool:
        """One region, no zones, no interfaces: the physics of a v1 case."""
        return len(self.regions) == 1 and not self.zones and not self.interfaces

    def region_ids(self) -> np.ndarray:
        if self.cell_region_id is None:
            return np.zeros(self.num_cells, np.int16)
        return np.asarray(self.cell_region_id)

    def region_index(self, name: str) -> int:
        for i, r in enumerate(self.regions):
            if r["name"] == name:
                return i
        raise KeyError(f"no region {name!r} (regions: {[r['name'] for r in self.regions]})")

    def zone_index(self, name: str) -> int:
        for i, z in enumerate(self.zones):
            if z["name"] == name:
                return i
        raise KeyError(f"no cell zone {name!r} (zones: {[z['name'] for z in self.zones]})")

    def zone_members(self, name: str) -> np.ndarray:
        return np.asarray(self.zone_cells)[np.asarray(self.zone_ptr) == self.zone_index(name)].astype(np.int64)

    def interface_of_patch(self, patch: str):
        """(interface dict, own side 'a' or 'b') of the interface that has ``patch`` as a side, or (None, None)."""
        for i, f in enumerate(self.interfaces):
            if f["patch_a"] == patch:
                return dict(f, index=i), "a"
            if f["patch_b"] == patch:
                return dict(f, index=i), "b"
        return None, None

    def partner_local_order(self, patch: str, patch_dict: Dict) -> Optional[np.ndarray]:
        """For an interface patch: ``perm`` with partner face ``start_partner + perm[k]`` paired to own face
        ``start_own + k``; None if the patch is not an interface side."""
        f, side = self.interface_of_patch(patch)
        if f is None:
            return None
        sel = np.asarray(self.iface_id) == f["index"]
        a = np.asarray(self.iface_face_a)[sel].astype(np.int64)
        b = np.asarray(self.iface_face_b)[sel].astype(np.int64)
        own, par = (a, b) if side == "a" else (b, a)
        partner = f["patch_b"] if side == "a" else f["patch_a"]
        own0 = int(patch_dict[patch]["start_idx_face"])
        par0 = int(patch_dict[partner]["start_idx_face"])
        perm = np.empty(own.shape[0], np.int64)
        perm[own - own0] = par - par0
        return perm


def single_region(num_cells: int, name: str = "domain", kind: str = "fluid", provenance: Dict = None) -> RegionSchema:
    """The schema of a one-region case (and the reading of every v1 file)."""
    return RegionSchema(num_cells=int(num_cells),
                        regions=[{"name": name, "kind": kind, "n_cells": int(num_cells), "cell_offset": 0,
                                  "source": None}],
                        provenance=dict(provenance or {}), version=SCHEMA_VERSION)


def from_labels(region_names: List[str], region_kinds: List[str], cell_region_id: np.ndarray,
                zones: Dict[str, np.ndarray] = None, interfaces: List[Dict] = None,
                region_sources: List[Optional[str]] = None, provenance: Dict = None) -> RegionSchema:
    """Build a schema from per-cell region labels, ``zones`` = {name: cell indices} and ``interfaces`` = [{"name",
    "kind", "patch_a", "patch_b", "region_a", "region_b", "pairing", "face_a", "face_b"}] (face arrays global)."""
    rid = np.asarray(cell_region_id).astype(np.int16)
    n = int(rid.shape[0])
    regions = []
    for i, (nm, kd) in enumerate(zip(region_names, region_kinds)):
        cells = np.flatnonzero(rid == i)
        contiguous = cells.size > 0 and int(cells[-1]) - int(cells[0]) + 1 == cells.size
        regions.append({"name": nm, "kind": kd, "n_cells": int(cells.size),
                        "cell_offset": int(cells[0]) if contiguous else None,
                        "source": (region_sources or [None] * len(region_names))[i]})
    zl, zc, zp = [], [], []
    for k, (nm, cells) in enumerate((zones or {}).items()):
        cells = np.unique(np.asarray(cells, np.int64))
        rz = np.unique(rid[cells]) if cells.size else np.zeros(0, np.int16)
        zl.append({"name": nm, "region": region_names[int(rz[0])] if rz.size == 1 else None,
                   "n_cells": int(cells.size), "source": None})
        zc.append(cells.astype(np.int32))
        zp.append(np.full(cells.size, k, np.int32))
    il, fa, fb, fi = [], [], [], []
    for k, f in enumerate(interfaces or []):
        a = np.asarray(f["face_a"], np.int64)
        b = np.asarray(f["face_b"], np.int64)
        il.append({key: f.get(key) for key in ("name", "kind", "patch_a", "patch_b", "region_a", "region_b",
                                              "pairing")} | {"n_faces": int(a.size)})
        fa.append(a)
        fb.append(b)
        fi.append(np.full(a.size, k, np.int16))
    cat = lambda xs, dt: np.concatenate(xs).astype(dt) if xs else np.zeros(0, dt)
    return RegionSchema(num_cells=n, regions=regions, zones=zl, interfaces=il,
                        cell_region_id=None if len(regions) == 1 else rid,
                        zone_cells=cat(zc, np.int32), zone_ptr=cat(zp, np.int32),
                        iface_face_a=cat(fa, np.int64), iface_face_b=cat(fb, np.int64), iface_id=cat(fi, np.int16),
                        provenance=dict(provenance or {}))


def pair_coincident_faces(pos_a: np.ndarray, pos_b: np.ndarray, tol_rel: float = 1e-6) -> np.ndarray:
    """``perm`` with ``pos_b[perm] == pos_a`` (face centres [n, 3]) to ``tol_rel`` of the extent: centres rounded to
    the tolerance grid and sorted on both sides, then checked face by face, so a non-conformal interface is refused
    and never paired approximately (the numpy twin of setConjugate.coincident_face_permutation)."""
    pos_a, pos_b = np.asarray(pos_a, np.float64), np.asarray(pos_b, np.float64)
    if pos_a.shape != pos_b.shape:
        raise ValueError(f"interface sides have {pos_a.shape[0]} and {pos_b.shape[0]} faces")
    if pos_a.shape[0] == 0:
        return np.zeros(0, np.int64)
    lo = np.minimum(pos_a.min(0), pos_b.min(0))
    tol = tol_rel * max(float((np.maximum(pos_a.max(0), pos_b.max(0)) - lo).max()), 1e-300)
    order = lambda p: np.lexsort(np.round((p - lo) / tol).astype(np.int64).T[::-1])
    sa, sb = order(pos_a), order(pos_b)
    perm = np.empty(pos_a.shape[0], np.int64)
    perm[sa] = sb
    gap = float(np.abs(pos_b[perm] - pos_a).max())
    if gap > 10.0 * tol:
        raise ValueError(f"interface faces do not coincide (largest centre gap {gap:.3e}, tolerance {tol:.3e}); "
                         f"a non-conformal interface is not implemented")
    return perm


# ---- h5 --------------------------------------------------------------------------------------------------------
def write(hf, schema: RegionSchema) -> None:
    """Write ``schema`` into an open h5py file (writable). Single-region cases get the attrs only."""
    hf.attrs["schema_version"] = SCHEMA_VERSION
    hf.attrs["regions"] = json.dumps(schema.regions)
    hf.attrs["zones"] = json.dumps(schema.zones)
    hf.attrs["interfaces"] = json.dumps(schema.interfaces)
    hf.attrs["provenance"] = json.dumps(schema.provenance, default=str)
    for key in SCHEMA_TENSOR_KEYS:
        if key in hf:
            del hf[key]
    if len(schema.regions) > 1:
        hf.create_dataset(DS_CELL_REGION, data=schema.region_ids().astype(np.int16))
    if schema.zones:
        hf.create_dataset(DS_ZONE_CELLS, data=np.asarray(schema.zone_cells, np.int32))
        hf.create_dataset(DS_ZONE_PTR, data=np.asarray(schema.zone_ptr, np.int32))
    if schema.interfaces:
        hf.create_dataset(DS_IFACE_A, data=np.asarray(schema.iface_face_a, np.int64))
        hf.create_dataset(DS_IFACE_B, data=np.asarray(schema.iface_face_b, np.int64))
        hf.create_dataset(DS_IFACE_ID, data=np.asarray(schema.iface_id, np.int16))


def _attr_json(hf, key, default):
    if key not in hf.attrs:
        return default
    v = hf.attrs[key]
    if isinstance(v, bytes):
        v = v.decode("utf-8")
    return json.loads(v)


def read(hf, num_cells: int = None) -> RegionSchema:
    """Read the schema of an open h5py file; a v1 file reads as one fluid region."""
    n = int(num_cells if num_cells is not None else hf["cell|cell_pos"].shape[0])
    version = int(hf.attrs.get("schema_version", 1))
    if version == 1:
        s = single_region(n)
        s.version = 1
        return s
    if version > SCHEMA_VERSION:
        raise ValueError(f"mesh h5 has schema_version {version}; this code reads up to {SCHEMA_VERSION}")
    ds = lambda key, dt: np.asarray(hf[key][()], dt) if key in hf else np.zeros(0, dt)
    regions = _attr_json(hf, "regions", None)
    if not regions:
        raise ValueError("schema v2 mesh h5 without a 'regions' table")
    return RegionSchema(num_cells=n, regions=regions, zones=_attr_json(hf, "zones", []),
                        interfaces=_attr_json(hf, "interfaces", []),
                        cell_region_id=(np.asarray(hf[DS_CELL_REGION][()], np.int16)
                                        if DS_CELL_REGION in hf else None),
                        zone_cells=ds(DS_ZONE_CELLS, np.int32), zone_ptr=ds(DS_ZONE_PTR, np.int32),
                        iface_face_a=ds(DS_IFACE_A, np.int64), iface_face_b=ds(DS_IFACE_B, np.int64),
                        iface_id=ds(DS_IFACE_ID, np.int16), provenance=_attr_json(hf, "provenance", {}),
                        version=version)


# ---- checks ----------------------------------------------------------------------------------------------------
def validate(schema: RegionSchema, *, num_faces: int = None, patch_dict: Dict = None, owner: np.ndarray = None,
             face_pos: np.ndarray = None, tol_rel: float = 1e-6, where: str = "mesh h5") -> None:
    """Refuse an inconsistent schema with the reason. ``owner`` [n_faces] (owner cell of each face) and ``face_pos``
    enable the region-of-side and geometric-coincidence checks of the interfaces."""
    def bad(msg):
        raise ValueError(f"{where}: {msg}")

    n = schema.num_cells
    names = [r["name"] for r in schema.regions]
    if not names or len(set(names)) != len(names):
        bad(f"region names must be non-empty and unique: {names}")
    for r in schema.regions:
        if r.get("kind") not in REGION_KINDS:
            bad(f"region {r['name']!r} has kind {r.get('kind')!r}; known kinds {REGION_KINDS}")
    rid = schema.region_ids()
    if rid.shape != (n,):
        bad(f"cell|region_id has shape {rid.shape}, the mesh has {n} cells")
    if n and (rid.min() < 0 or rid.max() >= len(names)):
        bad(f"cell|region_id outside 0..{len(names) - 1}")
    counts = np.bincount(rid.astype(np.int64), minlength=len(names))
    for i, r in enumerate(schema.regions):
        if int(r.get("n_cells", counts[i])) != int(counts[i]):
            bad(f"region {r['name']!r} lists {r['n_cells']} cells, cell|region_id has {int(counts[i])}")
        off = r.get("cell_offset")
        if off is not None and counts[i] and not np.all(rid[off:off + counts[i]] == i):
            bad(f"region {r['name']!r}: cells {off}..{off + counts[i] - 1} are not all in the region")
    zc, zp = np.asarray(schema.zone_cells), np.asarray(schema.zone_ptr)
    if zc.shape != zp.shape:
        bad("zone|cells and zone|cell_ptr differ in length")
    if zc.size and (zc.min() < 0 or zc.max() >= n or zp.min() < 0 or zp.max() >= len(schema.zones)):
        bad("zone membership index out of range")
    znames = [z["name"] for z in schema.zones]
    if len(set(znames)) != len(znames):
        bad(f"zone names must be unique: {znames}")
    for k, z in enumerate(schema.zones):
        cells = zc[zp == k]
        if int(z.get("n_cells", cells.size)) != cells.size:
            bad(f"zone {z['name']!r} lists {z['n_cells']} cells, the membership has {cells.size}")
        if z.get("region") is not None and cells.size and not np.all(rid[cells] == names.index(z["region"])):
            bad(f"zone {z['name']!r} has cells outside its region {z['region']!r}")
    fa, fb, fi = (np.asarray(schema.iface_face_a), np.asarray(schema.iface_face_b), np.asarray(schema.iface_id))
    if not (fa.shape == fb.shape == fi.shape):
        bad("iface|face_a, iface|face_b and iface|id differ in length")
    for k, f in enumerate(schema.interfaces):
        if f.get("kind") not in INTERFACE_KINDS:
            bad(f"interface {f.get('name')!r} has kind {f.get('kind')!r}; known kinds {INTERFACE_KINDS}")
        if f.get("pairing") not in INTERFACE_PAIRINGS:
            bad(f"interface {f.get('name')!r} has pairing {f.get('pairing')!r}; known {INTERFACE_PAIRINGS}")
        sel = fi == k
        a, b = fa[sel], fb[sel]
        if int(f.get("n_faces", a.size)) != a.size:
            bad(f"interface {f['name']!r} lists {f['n_faces']} faces, the pair table has {a.size}")
        if num_faces is not None and a.size and (min(a.min(), b.min()) < 0 or max(a.max(), b.max()) >= num_faces):
            bad(f"interface {f['name']!r}: face index out of range")
        if patch_dict is not None:
            for own, other in (("patch_a", "patch_b"), ("patch_b", "patch_a")):
                mirror = (patch_dict.get(f.get(own)) or {}).get("interface_partner")
                if mirror is not None and mirror != f.get(other):
                    bad(f"interface {f['name']!r}: patchinfo.toml pairs {f.get(own)!r} with {mirror!r}, the h5 with "
                        f"{f.get(other)!r}")
            for side, faces in (("patch_a", a), ("patch_b", b)):
                p = f.get(side)
                if p not in patch_dict:
                    bad(f"interface {f['name']!r}: {side} {p!r} is not a patch of patchinfo.toml")
                s0, s1 = int(patch_dict[p]["start_idx_face"]), int(patch_dict[p]["end_idx_face"])
                if s1 - s0 != faces.size:
                    bad(f"interface {f['name']!r}: patch {p!r} has {s1 - s0} faces, the pair table {faces.size}")
                if faces.size and (np.unique(faces).size != faces.size or faces.min() < s0 or faces.max() >= s1):
                    bad(f"interface {f['name']!r}: side {p!r} is not a permutation of the patch's faces")
        if owner is not None and a.size:
            for side, faces in (("region_a", a), ("region_b", b)):
                want = f.get(side)
                got = np.unique(rid[np.asarray(owner)[faces]])
                if want is not None and (got.size != 1 or names[int(got[0])] != want):
                    bad(f"interface {f['name']!r}: {side} {want!r}, the faces' owners are in "
                        f"{[names[int(g)] for g in got]}")
        if face_pos is not None and a.size:
            pa, pb = np.asarray(face_pos, np.float64)[a], np.asarray(face_pos, np.float64)[b]
            ext = float(np.ptp(np.concatenate([pa, pb]), axis=0).max())
            gap = float(np.abs(pa - pb).max())
            if gap > 10.0 * tol_rel * max(ext, 1e-300):
                bad(f"interface {f['name']!r}: paired face centres differ by up to {gap:.3e} "
                    f"(extent {ext:.3e}); the interface is not conformal or the pairing is wrong")
