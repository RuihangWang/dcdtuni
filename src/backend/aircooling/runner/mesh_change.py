"""What an edit asks of the case's existing mesh.

The exported STL solids are the mesh's boundary: each solid is one patch. Comparing the new export with the
triSurface the mesh was built from gives one of

  none      the same surfaces: solve (or infer) on the mesh as it is; boundary values, room temperature,
            porosity and fluid properties are not part of the mesh
  patches   the same surface, divided into patches differently (a patch added on a face, a face opened as an
            ACU supply, a patch removed): the mesh's cells stay, its boundary faces are reassigned to the new
            patches (patch_update)
  geometry  the surface itself moved, grew or shrank: the mesh has to be generated again
  mesh      there is no mesh yet

A patch update keeps the cells, so the new patch's outline follows the existing faces and its faces keep the
refinement level of the surface they were part of.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

_SOLID = re.compile(rb"^[ \t]*solid[ \t]*([^\r\n]*)\r?\n(.*?)^[ \t]*endsolid[^\r\n]*$", re.S | re.M)
_VERTEX = re.compile(rb"vertex[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)")

#: Coupled patch types never take part in a reassignment.
COUPLED = {"processor", "processorCyclic", "cyclic", "cyclicAMI", "cyclicACMI", "mappedPatch", "mappedWall"}


def parse_stl(data: bytes) -> Dict[str, np.ndarray]:
    """Solids of an ASCII STL: {name: [n, 3, 3] vertices}."""
    out: Dict[str, np.ndarray] = {}
    for m in _SOLID.finditer(data):
        name = m.group(1).strip().decode("utf-8", "replace")
        xyz = np.array(_VERTEX.findall(m.group(2)), dtype=np.float64)
        tris = xyz.reshape(-1, 3, 3) if xyz.size else np.zeros((0, 3, 3))
        out[name] = np.concatenate([out[name], tris]) if name in out else tris
    return out


def read_tri_surface(tri_dir: Path) -> Dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(Path(tri_dir).glob("*.stl")) if p.is_file()}


def _solids(files: Dict[str, bytes]) -> Tuple[Dict[str, np.ndarray], List[str]]:
    solids: Dict[str, np.ndarray] = {}
    duplicate: List[str] = []
    for data in files.values():
        for name, tris in parse_stl(data).items():
            if name in solids:
                duplicate.append(name)
            solids[name] = tris
    return solids, duplicate


def _key(tris: np.ndarray) -> str:
    """The triangle set, independent of triangle order and of where each triangle's vertices start."""
    r = np.round(tris, 6).reshape(-1, 3, 3)
    rows = []
    for t in r:
        k = min(range(3), key=lambda i: tuple(t[i]))
        rows.append(np.roll(t, -k, axis=0).ravel())
    if not rows:
        return "empty"
    arr = np.array(rows)
    arr = arr[np.lexsort(arr.T[::-1])]
    return hashlib.sha256(arr.tobytes()).hexdigest()


def _area(tris: np.ndarray) -> float:
    if not len(tris):
        return 0.0
    return float(0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1).sum())


def point_triangle_distance(p: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Distance from points p [m, 3] to triangles tri [m, 3, 3], pairwise (Ericson, closest point)."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    denom = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(denom != 0, vb / denom, 0.0)
        w = np.where(denom != 0, vc / denom, 0.0)
        q = a + ab * v[:, None] + ac * w[:, None]                   # inside the face
        t_ab = np.clip(d1 / np.where(d1 - d3 != 0, d1 - d3, 1), 0, 1)
        t_ac = np.clip(d2 / np.where(d2 - d6 != 0, d2 - d6, 1), 0, 1)
        t_bc = np.clip((d4 - d3) / np.where((d4 - d3) + (d5 - d6) != 0, (d4 - d3) + (d5 - d6), 1), 0, 1)
    region = [
        (d1 <= 0) & (d2 <= 0), a,
        (d3 >= 0) & (d4 <= d3), b,
        (d6 >= 0) & (d5 <= d6), c,
        (vc <= 0) & (d1 >= 0) & (d3 <= 0), a + ab * t_ab[:, None],
        (vb <= 0) & (d2 >= 0) & (d6 <= 0), a + ac * t_ac[:, None],
        (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + (c - b) * t_bc[:, None],
    ]
    done = np.zeros(len(p), bool)
    for cond, point in zip(region[0::2], region[1::2]):
        take = cond & ~done
        q[take] = point[take]
        done |= take
    return np.linalg.norm(p - q, axis=1)


def nearest_triangle(points: np.ndarray, tris: np.ndarray, chunk: int = 2_000_000) -> Tuple[np.ndarray, np.ndarray]:
    """For each point, (index of its nearest triangle, distance). Candidates within reach of the nearest
    centroid (every triangle is within its circumradius of its centroid), the exact distance on those."""
    from scipy.spatial import cKDTree
    if not len(tris):
        return np.full(len(points), -1), np.full(len(points), np.inf)
    centroid = tris.mean(axis=1)
    radius = np.linalg.norm(tris - centroid[:, None, :], axis=2).max(axis=1)
    tree = cKDTree(centroid)
    # no triangle is nearer than the nearest centroid's own distance, and each lies within its radius
    bound = tree.query(points)[0] + radius.max()
    best = np.full(len(points), -1)
    dist = np.full(len(points), np.inf)
    rows, cols = [], []
    for i, cand in enumerate(tree.query_ball_point(points, bound + 1e-12)):
        rows.append(np.full(len(cand), i))
        cols.append(np.asarray(cand, dtype=np.int64))
    rows_a, cols_a = np.concatenate(rows), np.concatenate(cols)
    for s in range(0, len(rows_a), chunk):
        r, c = rows_a[s:s + chunk], cols_a[s:s + chunk]
        d = point_triangle_distance(points[r], tris[c])
        order = np.lexsort((d, r))
        r, c, d = r[order], c[order], d[order]
        first = np.r_[True, r[1:] != r[:-1]]
        r, c, d = r[first], c[first], d[first]
        better = d < dist[r]
        best[r[better]], dist[r[better]] = c[better], d[better]
    return best, dist


def _samples(tris: np.ndarray) -> np.ndarray:
    """Points inside each triangle: its centroid and one point toward each corner."""
    c = tris.mean(axis=1)
    return np.concatenate([c] + [(2 * tris[:, k] + c) / 3 for k in range(3)])


def same_surface(a: np.ndarray, b: np.ndarray, tol: float) -> bool:
    """Whether two triangle sets cover the same surface: equal area, and every sample of each on the other."""
    area_a, area_b = _area(a), _area(b)
    if abs(area_a - area_b) > 1e-6 * max(area_a, area_b, 1.0) + tol * tol:
        return False
    if not len(a) or not len(b):
        return len(a) == len(b)
    return bool(nearest_triangle(_samples(b), a)[1].max() <= tol and nearest_triangle(_samples(a), b)[1].max() <= tol)


def classify(tri_dir: Path, new_files: Dict[str, bytes], mesh_exists: bool, tol: float = 1e-4) -> Dict:
    """The change from the STL batch on disk (the mesh's) to `new_files` (module docstring). Also names the
    patches a patch update reassigns (`reassign_from`, the old solids that changed) and where their faces may
    go (`reassign_to`, the new solids that changed)."""
    if not mesh_exists:
        return {"kind": "mesh", "reason": "no mesh yet"}
    old_files = read_tri_surface(tri_dir)
    if not old_files:
        return {"kind": "mesh", "reason": "the mesh's STL surfaces are missing"}
    if old_files == new_files:
        return {"kind": "none"}
    old, dup_old = _solids(old_files)
    new, dup_new = _solids(new_files)
    if dup_old or dup_new:
        return {"kind": "geometry", "reason": f"a solid name repeats across STL files: {sorted(set(dup_old + dup_new))[:5]}"}
    unchanged = {n for n in old if n in new and _key(old[n]) == _key(new[n])}
    old_changed = sorted(set(old) - unchanged)
    new_changed = sorted(set(new) - unchanged)
    result = {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
              "changed": sorted(n for n in old_changed if n in new),
              "reassign_from": old_changed, "reassign_to": new_changed}
    if not old_changed and not new_changed:
        return {"kind": "none", **result}
    a = np.concatenate([old[n] for n in old_changed]) if old_changed else np.zeros((0, 3, 3))
    b = np.concatenate([new[n] for n in new_changed]) if new_changed else np.zeros((0, 3, 3))
    if not same_surface(a, b, tol):
        return {"kind": "geometry", "reason": "the surface changed", **result}
    return {"kind": "patches", **result}


# ---------------------------------------------------------------------------------------------------------------
# Reassigning boundary faces


def boundary_reassignment(poly_dir: Path, reassign_from: Iterable[str], targets: Dict[str, np.ndarray],
                          max_distance: Optional[float] = None) -> Dict[str, np.ndarray]:
    """{target patch: face labels} moving the faces of the `reassign_from` patches of one polyMesh onto the new
    solids `targets` ({name: triangles}), each face to the solid nearest its centre. Faces already in their target
    are left out. Raises ValueError when a face is further from every target than half its own size (plus
    `max_distance`): the surface changed under the mesh."""
    from backend.aircooling.scano.foam_mesh import parse_boundary
    from .polymesh_io import read_all_faces, read_points

    poly_dir = Path(poly_dir)
    wanted = set(reassign_from)
    patches = [(n, d) for n, d in parse_boundary(poly_dir / "boundary")
               if n in wanted and d.get("type") not in COUPLED and int(d.get("nFaces", 0)) > 0]
    if not patches:
        return {}
    names = sorted(targets)
    tris = np.concatenate([targets[n] for n in names]) if names else np.zeros((0, 3, 3))
    owner_of = np.concatenate([np.full(len(targets[n]), k) for k, n in enumerate(names)]) if names else np.zeros(0, int)
    points = read_points(poly_dir / "points")
    offsets, verts = read_all_faces(poly_dir / "faces")
    labels, current = [], []
    for name, d in patches:
        start, n = int(d["startFace"]), int(d["nFaces"])
        labels.append(np.arange(start, start + n))
        current.append(np.full(n, name, dtype=object))
    label = np.concatenate(labels)
    cur = np.concatenate(current)
    lo, hi = np.asarray(offsets)[label], np.asarray(offsets)[label + 1]
    counts = hi - lo
    idx = np.concatenate([np.arange(s, e) for s, e in zip(lo, hi)])
    seg = np.repeat(np.arange(len(label)), counts)
    pts = points[np.asarray(verts)[idx]]
    centre = np.zeros((len(label), 3))
    np.add.at(centre, seg, pts)
    centre /= counts[:, None]
    size = np.zeros(len(label))
    np.maximum.at(size, seg, np.linalg.norm(pts - centre[seg], axis=1))
    if not len(tris):
        raise ValueError(f"{len(label)} faces of {sorted(wanted)} have no new patch to go to")
    near, dist = nearest_triangle(centre, tris)
    limit = 0.5 * size + (max_distance if max_distance is not None else 1e-4)
    far = dist > limit
    if far.any():
        raise ValueError(f"{int(far.sum())} of {len(label)} boundary faces of the changed patches lie off every new "
                         f"patch (max {dist[far].max():.4g} m): the surface changed, generate the mesh again")
    target = np.array(names, dtype=object)[owner_of[near]]
    out: Dict[str, List[int]] = {}
    for f, t, c in zip(label, target, cur):
        if t != c:
            out.setdefault(t, []).append(int(f))
    return {t: np.array(v, dtype=np.int64) for t, v in out.items()}


def write_face_set(path: Path, name: str, labels: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(str(int(v)) for v in labels)
    path.write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n    class       faceSet;\n"
        f'    location    "constant/polyMesh/sets";\n    object      {name};\n}}\n\n'
        f"{len(labels)}\n(\n{body}\n)\n")


_REGION_TYPE = re.compile(r"(?m)^[ \t]*([^\s{};]+)[ \t]*\n?[ \t]*\{[^{}]*?patchInfo[ \t]*\{[ \t]*type[ \t]+(\w+)")


def region_patch_types(snappy_dict: Path, names: Iterable[str], default: Optional[str] = "wall") -> Dict[str, str]:
    """The patchInfo type the runner's snappyHexMeshDict gives each region (what a fresh mesh would have);
    `default` where it gives none (left out when None). One pass over the dictionary."""
    text = Path(snappy_dict).read_text(encoding="utf-8", errors="replace") if Path(snappy_dict).is_file() else ""
    table = {m.group(1): m.group(2) for m in _REGION_TYPE.finditer(text)}
    out = {}
    for name in names:
        if name in table or default is not None:
            out[name] = table.get(name, default)
    return out


def role_changes(old_manifest: Dict, new_manifest: Dict) -> Dict[str, List[Optional[str]]]:
    """{patch: [old bcType, new bcType]} for the patches whose boundary role changed between the manifest the
    mesh was generated from and the current one (None: no binding). A role decides the patch's wall / patch type
    in the mesh, which a patch update sets from the runner's own region table."""
    def roles(m):
        return {b.get("patchName"): b.get("bcType") for b in (m.get("boundaryBindings") or {}).values()
                if b.get("patchName")}
    old, new = roles(old_manifest), roles(new_manifest)
    return {n: [old.get(n), new.get(n)] for n in sorted(set(old) | set(new)) if old.get(n) != new.get(n)}


#: How the mesher was run and what the editor shows: neither changes the cells.
_SETTINGS_IGNORED = {"meshProcess", "parallelMesh", "meshTimeLimitSeconds",
                     "locInMeshVisible", "blockMeshEditActive", "blockMeshMoveMode"}


def mesh_settings_changes(old_manifest: Dict, new_manifest: Dict) -> List[str]:
    """The mesh settings (cell sizes, refinement levels, engine, locationInMesh ...) that differ between the
    manifest the mesh was generated from and the current one; any of them needs the mesh generated again. Only
    keys both carry are compared: a key a newer editor adds was the runner's default when the old mesh was made."""
    changed = set()
    for pick in (lambda m: m.get("meshConfig"), lambda m: (m.get("meshGeometry") or {}).get("meshConfig")):
        old, new = pick(old_manifest), pick(new_manifest)
        if isinstance(old, dict) and isinstance(new, dict):
            changed |= {k for k in set(old) & set(new) if k not in _SETTINGS_IGNORED and old[k] != new[k]}
    return sorted(changed)


def retype_boundary(boundary: Path, retype: Dict[str, str]) -> int:
    """Set the type of the named patches in a polyMesh/boundary file (wall carries the wall group, as snappy
    writes it); the faces are untouched. Returns how many patches changed."""
    text = Path(boundary).read_text(encoding="utf-8")
    changed = 0

    def entry(m):
        nonlocal changed
        lead, name, brace, body = m.group(1), m.group(2), m.group(3), m.group(4)
        want = retype.get(name)
        if not want:
            return m.group(0)
        body = re.sub(r"(?m)^[ \t]*inGroups[^;]*;[ \t]*\n", "", body)
        body = re.sub(r"(?m)^([ \t]*)type[ \t]+\w+;",
                      lambda t: f"{t.group(1)}type            {want};"
                      + (f"\n{t.group(1)}inGroups        1(wall);" if want == "wall" else ""), body, count=1)
        changed += 1
        return f"{lead}{name}\n{brace}{{{body}}}"

    out = re.sub(r"(?m)^([ \t]*)(\S+)[ \t]*\n([ \t]*)\{([^{}]*)\}", entry, text)
    if changed:
        Path(boundary).write_text(out, encoding="utf-8")
    return changed


def create_patch_dict(entries: List[Tuple[str, str, str]]) -> str:
    """createPatchDict moving each face set into its patch: [(patch, face set, type)]."""
    def info(kind: str) -> str:
        return "type wall; inGroups (wall);" if kind == "wall" else f"type {kind};"
    rows = "\n".join(f"    {{ name {patch}; patchInfo {{ {info(kind)} }} constructFrom set; set {fset}; }}"
                     for patch, fset, kind in entries)
    return ("FoamFile\n{\n    version     2.0;\n    format      ascii;\n    class       dictionary;\n"
            "    object      createPatchDict;\n}\n\npointSync false;\n\npatches\n(\n" + rows + "\n);\n")


def _solid_order(stl: Path) -> List[str]:
    return [m.decode("utf-8", "replace").strip()
            for m in re.findall(rb"(?m)^[ \t]*solid[ \t]*([^\r\n]*)", Path(stl).read_bytes())]


def rewrite_native_source_regions(poly_dirs: List[Path], stage: Dict, tri_dir: Path) -> int:
    """Rewrite each rank's dcCartSourceRegions.json after a patch update, from the faces it actually moved: a
    source region keeps the patches the mesher recorded for it, a new region owns the patch of its own solid
    (the face sets were chosen by its triangles). Addresses as the mesher writes them: the surface index of its
    STL file, the region's position among that file's solids. Returns the number of rows per rank."""
    import json as _json
    from backend.aircooling.scano.foam_mesh import parse_boundary

    order: Dict[str, List[str]] = {}
    count = 0
    for rank, poly in enumerate(poly_dirs):
        path = Path(poly).parent / "dcCartSourceRegions.json"
        old = _json.loads(path.read_text(encoding="utf-8"))
        old_rows = {r["sourceRegionUid"]: r for r in old["regions"]}
        surface_of = {r["stlFile"]: r["nativeSurfaceIndex"] for r in old["regions"]}
        table = parse_boundary(Path(poly) / "boundary")
        index = {name: (k, d) for k, (name, d) in enumerate(table)}
        rows = []
        for reg in stage["regions"]:
            if reg.get("unresolvedReason") is not None:
                continue
            uid = reg["sourceRegionUid"]
            prev = old_rows.get(uid)
            if prev is not None and prev.get("unresolvedReason") is not None:
                rows.append(dict(prev))
                continue
            names = [s["patchName"] for s in prev["patchRanges"]] if prev else [reg["solidName"]]
            missing = [n for n in names if n not in index]
            if missing:
                raise ValueError(f"source region {uid}: patches {missing} are not in rank {rank}'s boundary")
            ranges = sorted(({"patchIndex": index[n][0], "patchName": n,
                              "startFace": int(index[n][1]["startFace"]), "nFaces": int(index[n][1]["nFaces"])}
                             for n in names), key=lambda s: s["patchIndex"])
            stl = reg["meshStageStlFile"]
            if stl not in surface_of:
                raise ValueError(f"source region {uid}: STL file {stl} has no native surface index")
            if stl not in order:
                order[stl] = _solid_order(Path(tri_dir) / stl)
            if reg["solidName"] not in order[stl]:
                raise ValueError(f"source region {uid}: solid {reg['solidName']} is not in {stl}")
            rows.append({"sourceEntityUid": reg["sourceEntityUid"], "sourceRegionUid": uid,
                         "sourceRegionSha256": reg["sourceRegionSha256"], "sourceFacetCount": reg["sourceFacetCount"],
                         "sourceFacetUidPrefix": uid + "/facet/", "stlFile": stl, "solidName": reg["solidName"],
                         "operation": reg["operation"], "nativeSurfaceIndex": surface_of[stl],
                         "nativeLocalRegionIndex": order[stl].index(reg["solidName"]),
                         "unresolvedReason": None, "patchRanges": ranges})
        rows.sort(key=lambda r: r["sourceRegionUid"])
        out = {"format": old["format"], "rank": old["rank"], "rankCount": old["rankCount"],
               "bindingGranularity": old["bindingGranularity"], "regions": rows}
        path.write_text(_json.dumps(out, indent=2) + "\n", encoding="utf-8")
        count = len(rows)
    return count
