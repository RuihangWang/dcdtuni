"""Independent P12 structural census; no saved census/classification is read."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import re
import time
import datetime
import collections
import numpy as np

ROOT = Path(__file__).resolve().parent
INPUT = ROOT.parent / "p12-D-eligibility-audit-20261005"
INPUT_MANIFEST_SHA = "170d73782d902e0b08fccc243972db9a5ea1160f13568770a850523b7a90213d"
READER_SHA = "835d870e0c1c99b124b473061d5e1b8d2993a48e80b967fb40a3aad3aa60a492"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1 << 20), b""):
            digest.update(data)
    return digest.hexdigest()


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sets(path, limit):
    raw = path.read_bytes()
    start = reader._payload_start(raw)
    text = raw[start:].decode("ascii")
    match = re.match(r"\s*(\d+)\s*\(\s*(.*?)\s*\)", text, re.S)
    assert match, ("missing ASCII labelset body", str(path))
    tokens = match[2].split()
    assert all(re.fullmatch(r"-?\d+", token) for token in tokens)
    labels = [int(token) for token in tokens]
    assert len(labels) == int(match[1])
    assert len(set(labels)) == len(labels), ("duplicate label", str(path))
    assert all(0 <= label < limit for label in labels)
    return set(labels)


def boundary_roles(path, nf, ni):
    raw = path.read_bytes()
    text = raw[reader._payload_start(raw):].decode("ascii")
    count = int(re.match(r"\s*(\d+)", text)[1])
    dictionaries = re.findall(r"([^\s{}()]+)\s*\{([^{}]*)\}", text)
    assert len(dictionaries) == count
    roles = np.zeros(nf, dtype=np.uint8)
    covered = np.zeros(nf - ni, dtype=np.uint8)
    patch_types = collections.Counter()
    for name, body in dictionaries:
        typ = re.search(r"\btype\s+(\S+)\s*;", body)[1]
        n = int(re.search(r"\bnFaces\s+(\d+)\s*;", body)[1])
        start = int(re.search(r"\bstartFace\s+(\d+)\s*;", body)[1])
        assert ni <= start <= start + n <= nf
        patch_types[typ] += 1
        if n:
            assert not np.any(covered[start-ni:start-ni+n])
            covered[start-ni:start-ni+n] = 1
            if typ in ("wall", "patch", "empty", "symmetry", "symmetryPlane", "wedge"):
                role = 1
            elif typ.startswith("processor") or typ.startswith("cyclic"):
                role = 2
            else:
                role = 3
            roles[start:start+n] = role
    assert np.all(covered == 1)
    return roles, dict(patch_types)


started = time.monotonic()
assert sha(INPUT / "manifest.json") == INPUT_MANIFEST_SHA
manifest = json.loads((INPUT / "manifest.json").read_text())
expected = {record["path"]: record for record in manifest["files"]}
p12 = json.loads((INPUT / "P12-full-case-raw-manifest.json").read_text())
raw_expected = p12["files"]
selected = sorted(name for name in expected if name.startswith("selected/"))
set_names = sorted(name for name in expected if name.startswith("input-failure-sets/"))
assert len(selected) == 52 and len(set_names) == 16
support = ["manifest.json", "P12-full-case-raw-manifest.json",
           "frozen-zone-membership-binding.json", "polymesh_io.reader.py",
           "source-review/dcCartWeightUnionPreview.H"]
names = selected + set_names + support
bindings = []
for name in names:
    path = INPUT / name
    observed = sha(path)
    size = path.stat().st_size
    if name in expected:
        assert observed == expected[name]["sha256"] and size == expected[name]["bytes"], name
    if name.startswith(("selected/", "input-failure-sets/")):
        raw_name = name.split("/", 1)[1]
        assert raw_name in raw_expected
        assert observed == raw_expected[raw_name]["sha256"] and size == raw_expected[raw_name]["bytes"], name
    bindings.append({"path": str(path), "relativeToFreeze": name, "bytes": size, "sha256": observed})
assert sha(INPUT / "polymesh_io.reader.py") == READER_SHA
spec = importlib.util.spec_from_file_location("frozen_native_io", INPUT / "polymesh_io.reader.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
zones = json.loads((INPUT / "frozen-zone-membership-binding.json").read_text())
assert zones["all24MatchFrozenEmptyWrittenMembership"] is True
assert len(zones["zoneRecords"]) == 24
for item in zones["zoneRecords"]:
    rec = raw_expected[f'Mesh/processor{item["rank"]}/constant/polyMesh/{item["file"]}']
    assert rec == item["P12Record"] and item["equalsOriginal"] is True
    assert item["matchesFrozenActualCensusSHA"] is True
assert len(zones["membershipSummary"]) == 8
for item in zones["membershipSummary"]:
    assert all(value["members"] == 0 for value in item["zones"].values())
print("inputs verified: 52 selected binary members + 16 actual ASCII sets; no saved census/classification read", flush=True)

rows, pair_rows, rank_rows = [], [], []
for rank in range(8):
    poly = INPUT / f"selected/Mesh/processor{rank}/constant/polyMesh"
    owner = reader.read_label_list(poly / "owner")
    neighbour = reader.read_label_list(poly / "neighbour")
    offsets, vertices = reader.read_all_faces(poly / "faces")
    parent = np.fromfile(INPUT / f"selected/Mesh/conforming-written-rank{rank}-cell-parent.i32", dtype="<i4")
    native = np.fromfile(INPUT / f"selected/Mesh/conforming-written-rank{rank}-cell-native.i32", dtype="<i4")
    nf, ni, nc = len(owner), len(neighbour), len(parent)
    note = reader.read_owner_note(poly / "owner")
    assert len(native) == nc and len(offsets) == nf + 1
    assert note["nCells"] == nc and note["nFaces"] == nf and note["nInternalFaces"] == ni
    assert offsets[0] == 0 and offsets[-1] == len(vertices) and np.all(np.diff(offsets) >= 3)
    assert int(owner.min()) >= 0 and int(owner.max()) < nc
    assert int(neighbour.min()) >= 0 and int(neighbour.max()) < nc
    roles, patch_types = boundary_roles(poly / "boundary", nf, ni)
    set_dir = INPUT / f"input-failure-sets/Mesh/processor{rank}/constant/polyMesh/sets"
    bad_d = sets(set_dir / "underdeterminedCells", nc)
    bad_w = sets(set_dir / "lowWeightFaces", nf)
    incidence_cells = np.concatenate((owner, neighbour))
    incidence_faces = np.concatenate((np.arange(nf, dtype=np.int32), np.arange(ni, dtype=np.int32)))
    order = np.argsort(incidence_cells, kind="stable")
    cf = incidence_faces[order]
    cf_offsets = np.empty(nc + 1, dtype=np.int64)
    cf_offsets[0] = 0
    np.cumsum(np.bincount(incidence_cells, minlength=nc), out=cf_offsets[1:])
    del incidence_cells, incidence_faces, order
    face_arity = np.diff(offsets)
    shape_cache = {}
    def faces_of(c):
        return cf[cf_offsets[c]:cf_offsets[c+1]]
    def face_points(f):
        return vertices[offsets[f]:offsets[f+1]]
    def shape(c):
        if c not in shape_cache:
            fs = faces_of(c)
            pts = set()
            for f in fs:
                pts.update(map(int, face_points(int(f))))
            arities = sorted(map(int, face_arity[fs]))
            kind = "trueTet" if arities == [3,3,3,3] and len(pts) == 4 else (
                "quadPyramid" if arities == [3,3,3,3,4] and len(pts) == 5 else "other")
            shape_cache[c] = (kind, arities, len(pts))
        return shape_cache[c]
    def adjacent(c):
        output = set()
        for f in faces_of(c):
            if f < ni:
                output.add(int(owner[f]))
                output.add(int(neighbour[f]))
        output.discard(c)
        return output

    pairs = {}
    direct_flags = {}
    for c in sorted(bad_d):
        fs = faces_of(c)
        kind, arities, point_count = shape(c)
        same_tet, different_tet = False, False
        for f in fs:
            if f >= ni:
                continue
            n = int(neighbour[f] if int(owner[f]) == c else owner[f])
            if shape(n)[0] == "trueTet":
                if int(parent[n]) == int(parent[c]):
                    same_tet = True
                else:
                    different_tet = True
                if kind == "trueTet":
                    a,b = sorted((c,n))
                    key = (a,b)
                    if key in pairs:
                        assert pairs[key] == int(f), ("multiple shared faces", rank, key)
                    pairs[key] = int(f)
        direct_flags[c] = {"sameParentTetNeighbour": same_tet, "differentParentTetNeighbour": different_tet,
                           "physicalTouched": bool(np.any(roles[fs] == 1)),
                           "coupledTouched": bool(np.any(roles[fs] == 2)),
                           "unknownBoundaryTouched": bool(np.any(roles[fs] == 3))}
    cross_covered, same_covered = set(), set()
    eligible_cross = eligible_same = blocked_cross = blocked_same = 0
    max_halo = [0,0,0]
    for (a,b), shared in sorted(pairs.items()):
        assert set(map(int, faces_of(a))) & set(map(int, faces_of(b))) == {shared}
        outer = (set(map(int, faces_of(a))) | set(map(int, faces_of(b)))) - {shared}
        assert len(outer) == 6
        first = {a,b} | adjacent(a) | adjacent(b)
        complete = set(first)
        for c in first:
            complete.update(adjacent(c))
        halo_faces = set()
        for c in complete:
            halo_faces.update(map(int, faces_of(c)))
        halo_points = set()
        for f in halo_faces:
            halo_points.update(map(int, face_points(f)))
        coupled = any(roles[f] == 2 for f in halo_faces)
        unknown = any(roles[f] == 3 for f in halo_faces)
        cap_ok = len(complete) <= 128 and len(halo_faces) <= 512 and len(halo_points) <= 1024
        eligible = not coupled and not unknown and cap_ok
        different = int(parent[a]) != int(parent[b])
        bad_endpoints = {a,b} & bad_d
        if eligible:
            (cross_covered if different else same_covered).update(bad_endpoints)
            if different: eligible_cross += 1
            else: eligible_same += 1
        else:
            if different: blocked_cross += 1
            else: blocked_same += 1
        sizes = [len(complete),len(halo_faces),len(halo_points)]
        max_halo = [max(x,y) for x,y in zip(max_halo,sizes)]
        pair_rows.append({"rank":rank,"compactCells":[a,b],"nativeCells":[int(native[a]),int(native[b])],
          "sourceParents":[int(parent[a]),int(parent[b])],"sharedFace":shared,"differentParent":different,
          "badDEndpoints":sorted(bad_endpoints),"sharedFaceBadW":shared in bad_w,
          "outerFaces":sorted(outer),"firstHaloCells":len(first),"completeSecondHaloCells":len(complete),
          "completeHaloFaces":len(halo_faces),"completeHaloPoints":len(halo_points),
          "completeHaloCoupled":coupled,"completeHaloUnknownBoundary":unknown,"capsPass":cap_ok,
          "metadataQualifies":eligible})
    partition = collections.Counter()
    shape_counts = collections.Counter()
    for c in sorted(bad_d):
        kind, arities, point_count = shape(c)
        shape_counts[kind] += 1
        if kind == "trueTet":
            route = "crossParentMetadataCovered" if c in cross_covered else (
                    "sameParentOnlyMetadataCovered" if c in same_covered else "trueTetNeitherMetadataCovered")
        elif kind == "quadPyramid":
            route = "quadPyramidOutsideTrueTetGrammar"
        else:
            route = "otherShapeUnknown"
        partition[route] += 1
        rows.append({"rank":rank,"compactCell":c,"nativeCell":int(native[c]),"sourceParent":int(parent[c]),
          "shape":kind,"faceCount":len(arities),"faceArities":arities,"distinctPoints":point_count,
          "crossParentMetadataCovered":c in cross_covered,"sameParentMetadataCovered":c in same_covered,
          "exclusiveRoute":route,**direct_flags[c]})
    rr={"rank":rank,"badD":len(bad_d),"badW":len(bad_w),"nCells":nc,"nFaces":nf,"nInternalFaces":ni,
        "shapeCounts":dict(shape_counts),"exclusivePartition":dict(partition),
        "physicalTouched":sum(v["physicalTouched"] for v in direct_flags.values()),
        "coupledTouched":sum(v["coupledTouched"] for v in direct_flags.values()),
        "unknownBoundaryTouched":sum(v["unknownBoundaryTouched"] for v in direct_flags.values()),
        "distinctTetPairs":len(pairs),"eligibleCrossParentPairs":eligible_cross,"eligibleSameParentPairs":eligible_same,
        "blockedCrossParentPairs":blocked_cross,"blockedSameParentPairs":blocked_same,
        "maxCompleteHaloCellsFacesPoints":max_halo,"boundaryPatchTypes":patch_types}
    rank_rows.append(rr)
    print(json.dumps(rr, sort_keys=True), flush=True)
    del owner,neighbour,offsets,vertices,parent,native,cf,cf_offsets,roles,face_arity,shape_cache,pairs

assert len({(r["rank"],r["compactCell"]) for r in rows}) == len(rows)
shape_counts = dict(collections.Counter(r["shape"] for r in rows))
partition = dict(collections.Counter(r["exclusiveRoute"] for r in rows))
pair_counts = {
 "all":len(pair_rows),
 "sameParent":sum(not x["differentParent"] for x in pair_rows),
 "differentParent":sum(x["differentParent"] for x in pair_rows),
 "eligibleSameParent":sum(x["metadataQualifies"] and not x["differentParent"] for x in pair_rows),
 "eligibleDifferentParent":sum(x["metadataQualifies"] and x["differentParent"] for x in pair_rows),
 "differentParentBothDBad":sum(x["differentParent"] and len(x["badDEndpoints"])==2 for x in pair_rows),
 "differentParentSharedFaceBadW":sum(x["differentParent"] and x["sharedFaceBadW"] for x in pair_rows)}
for record in bindings:
    path = Path(record["path"])
    assert path.stat().st_size == record["bytes"] and sha(path) == record["sha256"], ("input changed during read", str(path))
summary={"format":"P12-census-independent-recompute/v1","job":"52d0e89c","utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
 "elapsedSeconds":time.monotonic()-started,"sourceScriptSha256":sha(Path(__file__)),
 "inputFreezeManifestSha256":INPUT_MANIFEST_SHA,"readerSha256":READER_SHA,
 "inputSelectedCount":52,"inputSelectedBytes":sum(x["bytes"] for x in bindings if x["relativeToFreeze"].startswith("selected/")),
 "rawASCIIFailureSetCount":16,"inputsStableBeforeAfter":True,"badDUnique":len(rows),
 "shapeCounts":shape_counts,"exclusivePartition":partition,"distinctNativeSourceParents":len({(r["rank"],r["sourceParent"]) for r in rows}),
 "fewerThan3Faces":sum(r["faceCount"]<3 for r in rows),
 "physicalTouched":sum(r["physicalTouched"] for r in rows),"coupledTouched":sum(r["coupledTouched"] for r in rows),
 "unknownBoundaryTouched":sum(r["unknownBoundaryTouched"] for r in rows),
 "pairCounts":pair_counts,"perRank":rank_rows,
 "directTetNeighbourFlags":{"trueTetSameParent":sum(r["shape"]=="trueTet" and r["sameParentTetNeighbour"] for r in rows),
 "trueTetDifferentParent":sum(r["shape"]=="trueTet" and r["differentParentTetNeighbour"] for r in rows)},
 "verification":{"expectedBadD833":len(rows)==833,"expectedTrueTet699QuadPyramid134":shape_counts=={"trueTet":699,"quadPyramid":134},
 "expectedExclusive337199163134":partition=={"crossParentMetadataCovered":337,"sameParentOnlyMetadataCovered":199,"trueTetNeitherMetadataCovered":163,"quadPyramidOutsideTrueTetGrammar":134}},
 "zoneScope":"Written empty memberships are inherited ONLY from the SHA-bound frozen 24-zone binding; this selected52 run does not freshly parse absent zone payloads. Empty written zones are not planned material/source authority.",
 "scope":"Pure structure/metadata only. No candidate geometry/CC/V/quality evaluation, no emitted native queue or per-pair runtime rejection proof, no new source-authority approval, no mesh/checkMesh/solver/API/browser/Git/index/service operation."}
save("independent-summary.json",summary)
save("raw-D-cell-classification.json",rows)
save("raw-two-tet-pair-metadata.json",pair_rows)
save("input-bindings.json",{"format":"independent-census-input-bindings/v1","records":bindings,"allBeforeAfterShaAndSizesMatched":True})
assert all(summary["verification"].values()), summary["verification"]
print("FINAL " + json.dumps({"badDUnique":len(rows),"shapeCounts":shape_counts,"exclusivePartition":partition,"pairCounts":pair_counts,
 "elapsedSeconds":summary["elapsedSeconds"],"verification":summary["verification"]}, sort_keys=True),flush=True)
