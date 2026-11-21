#!/usr/bin/env python3
"""Offline verification of accepted literal unions on two frozen written meshes.

Inputs are never modified. Native trace IDs join the OFF baseline, accepted
records, and actual final polyMesh. Floating OF formulas are recomputed from
original points and complete faces; this does not authorize a rejected mesh.
"""
from __future__ import annotations
import argparse, copy, gzip, hashlib, itertools, json, re
from pathlib import Path
import numpy as np
import audit_air_mesh_geometry as g
import audit_air_cone_small_union_independent as u

POLY=("points","faces","owner","neighbour","boundary","faceZones","cellZones","pointZones")
SETS={"D":("underdeterminedCells","cell"),"W":("lowWeightFaces","face"),
      "R":("lowVolRatioFaces","face"),"S":("skewFaces","face"),"N":("nonOrthoFaces","face")}
LIMITS={"D":(.001,False),"weight":(.05,False),"ratio":(.01,False),
        "skew":(4.,True),"nonOrthogonalityDegrees":(70.,True)}

def dump(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")

def rawmap(mesh,rank,name):
    return np.memmap(mesh/f"conforming-written-rank{rank}-{name}.i32",dtype="<i4",mode="r")

def inverse(labels):
    labels=np.asarray(labels)
    if labels.ndim!=1 or not np.issubdtype(labels.dtype,np.integer) or not len(labels) or np.any(labels<0) or len(np.unique(labels))!=len(labels):
        raise ValueError("Native addressing must be nonempty one-dimensional nonnegative unique integers")
    out=np.full(int(np.max(labels))+1,-1,dtype=np.int64)
    out[labels]=np.arange(len(labels));return out

def foam_payload(path):
    text=path.read_text()
    text=re.sub(r"/\*.*?\*/","",text,flags=re.S)
    text=re.sub(r"//[^\n]*","",text)
    start=text.index("{",text.index("FoamFile"));depth=1;pos=start+1
    while depth and pos<len(text):
        depth+=int(text[pos]=="{")-int(text[pos]=="}");pos+=1
    if depth:raise ValueError("Unclosed FoamFile header")
    return text[pos:]

def boundary_roles(path):
    text=foam_payload(path);head=re.match(r"\s*(\d+)\s*\(",text)
    if not head:raise ValueError("Boundary count missing")
    roles=[]
    for hit in re.finditer(r'([^\s{}()]+)\s*\{([^{}]*)\}',text):
        name,body=hit.group(1),hit.group(2)
        start=re.search(r"\bstartFace\s+(\d+)\s*;",body)
        count=re.search(r"\bnFaces\s+(\d+)\s*;",body)
        kind=re.search(r"\btype\s+([^\s;]+)\s*;",body)
        if not start or not count or not kind:raise ValueError("Boundary role incomplete")
        semantic=re.sub(r"\bstartFace\s+\d+\s*;","",body)
        tokens=re.findall(r'"[^"]*"|[^\s;(){}]+|[;(){}]',semantic)
        roles.append({"name":name,"type":kind.group(1),"startFace":int(start.group(1)),
          "nFaces":int(count.group(1)),"semanticTokensExcludingStartFace":tokens})
    if len(roles)!=int(head.group(1)):raise ValueError("Boundary role count mismatch")
    return roles

def validate_patch_roles(old_geo,new_geo,face_old):
    old=boundary_roles(old_geo.poly_dir/"boundary");new=boundary_roles(new_geo.poly_dir/"boundary")
    old_sem={r["name"]:r["semanticTokensExcludingStartFace"] for r in old}
    new_sem={r["name"]:r["semanticTokensExcludingStartFace"] for r in new}
    if len(old_sem)!=len(old) or len(new_sem)!=len(new) or old_sem!=new_sem:
        raise ValueError("Actual boundary name/type/reference/transform role changed")
    names=sorted(old_sem);codes={name:i for i,name in enumerate(names)}
    def assignments(geo,roles):
        ids=np.full(geo.n_faces,-1,dtype=np.int32)
        for role in roles:
            start,n=role["startFace"],role["nFaces"]
            if start<0 or start+n>geo.n_faces or np.any(ids[start:start+n]!=-1):
                raise ValueError("Invalid boundary range")
            ids[start:start+n]=codes[role["name"]]
        if np.any(ids[len(geo.neighbour):]<0) or np.any(ids[:len(geo.neighbour)]>=0):
            raise ValueError("Boundary does not partition physical/coupled face ranges")
        return ids
    before=assignments(old_geo,old);after=assignments(new_geo,new)
    if not np.array_equal(after,before[face_old]):raise ValueError("Actual surviving face patch-role changed")
    return {"everySurvivingBoundaryFaceRoleExact":True,"everyPatchNameTypeAndFullSemanticBodyExact":True,
      "boundaryFaces":int(np.count_nonzero(after>=0)),"patchCount":len(new),"actualBoundaryRoles":new}

def empty_zone_memberships(path):
    text=foam_payload(path);head=re.match(r"\s*(\d+)\s*\(",text)
    if not head:raise ValueError("Zone count missing")
    count=int(head.group(1));name=path.name
    if count==0:
        if not re.match(r"\s*0\s*\(\s*\)",text):raise ValueError("Malformed empty zones")
        return {"zoneCount":0,"membershipsEmpty":True,"zones":[]}
    field={"faceZones":"faceLabels","cellZones":"cellLabels","pointZones":"pointLabels"}[name]
    zones=[]
    for hit in re.finditer(r'([^\s{}()]+)\s*\{([^{}]*)\}',text):
        body=hit.group(2);labels=re.search(r"\b"+field+r"\s+List<label>\s+(\d+)\s*;",body)
        kind=re.search(r"\btype\s+([^\s;]+)\s*;",body)
        if not labels or not kind or int(labels.group(1))!=0:
            raise ValueError("Nonempty/unsupported actual zone membership needs explicit native mapping")
        if name=="faceZones" and not re.search(r"\bflipMap\s+List<bool>\s+0\s*;",body):
            raise ValueError("Face-zone flipMap not empty")
        zones.append({"name":hit.group(1),"type":kind.group(1),"memberCount":0})
    if len(zones)!=count:raise ValueError("Zone payload count mismatch")
    return {"zoneCount":count,"membershipsEmpty":True,"zones":zones}

def validate_zone_memberships(old_poly,new_poly):
    rows={}
    for name in ("faceZones","cellZones","pointZones"):
        before=empty_zone_memberships(old_poly/name);after=empty_zone_memberships(new_poly/name)
        if before!=after:raise ValueError("Actual zone semantic roles changed")
        rows[name]={"baseline":before,"final":after,"emptyMembershipAndRolesExact":True}
    return rows

def source_role_protection(doc,removed,outer,affected):
    for f in removed:
        row=doc["faces"][str(f)]
        if row["patch"] is not None or row["sourceZone"]>=0:raise ValueError("Deleted face has protected patch/zone")
    for f in outer:
        row=doc["faces"][str(f)]
        if row["sourceZone"]>=0:raise ValueError("Exterior source-zone protection violated")
    for c in affected:
        for f in doc["cells"][str(c)]["faces"]:
            patch=doc["faces"][str(f)]["patch"]
            if patch and any(t in patch["type"].lower() for t in ("processor","cyclic","mapped","ami")):
                raise ValueError("Affected coupled state is unsupported")

def metric_pass(value,key):
    threshold,upper=LIMITS[key]
    return bool(np.isfinite(value) and (value<=threshold if upper else value>=threshold))

def face_row(stencil,f):
    row=stencil.face_detail(f)
    row.setdefault("weight",1.);row.setdefault("ratio",1.)
    row.setdefault("nonOrthogonalityDegrees",0.)
    return row

def strict_cell(stencil,c):
    row=stencil.cell_detail(c);faces=stencil.cells[c]["faces"]
    vertices=sorted({p for f in faces for p in stencil.faces[f]["pointIDs"]})
    span=max(float(np.linalg.norm(np.ptp([stencil.points[p] for p in vertices],axis=0))),1e-6)
    planar=max(stencil.face_detail(f)["planarityErrorM"] for f in faces)
    writable=min(float(np.linalg.norm(stencil.face(f)[0])) for f in faces)
    row["planarityErrorM"]=planar;row["spanM"]=span
    row["strictGeometryPass"]=bool(row["V"]>0 and row["edgesExactlyTwo"] and row["edgesOppositeOrientation"]
      and row["faceCentreConcavityPairTriggers"]==0 and row["maximumVertexOutsideM"]<=1e-10*span
      and row["relativeClosure"]<=1e-12 and row["minCentroidPyramidVolume"]>1e-15*row["V"]
      and planar<=1e-10*span and writable>1e-12)
    return row

def unique_tets(doc,outer):
    boundary={frozenset(doc["faces"][str(f)]["pointIDs"]) for f in outer}
    points=sorted(set.union(*(set(x) for x in boundary)))
    solutions=[]
    for a,b in itertools.combinations(itertools.combinations(points,4),2):
        af={frozenset(x) for x in itertools.combinations(a,3)}
        bf={frozenset(x) for x in itertools.combinations(b,3)}
        if len(af&bf)==1 and af^bf==boundary:
            solutions.append({"tetA":list(a),"tetB":list(b),"sharedTriangle":sorted(next(iter(af&bf)))})
    return solutions

def load_compact(geo,point_native,face_native,cell_native,parent,base,zone,records):
    ci=inverse(cell_native);fi=inverse(face_native)
    def cell_faces(c):
        written=int(ci[c])
        return list(map(int,face_native[geo.cell_faces[geo.cell_offsets[written]:geo.cell_offsets[written+1]]]))
    affected={int(row[k]) for row in records for k in ("keep","drop")}
    for row in records:
        for f in row["exteriorFaces"]:
            w=int(fi[f]);affected.add(int(cell_native[geo.owner[w]]))
            if w<len(geo.neighbour):affected.add(int(cell_native[geo.neighbour[w]]))
    allcells=set(affected)
    for c in list(affected):
        for f in cell_faces(c):
            w=int(fi[f]);allcells.add(int(cell_native[geo.owner[w]]))
            if w<len(geo.neighbour):allcells.add(int(cell_native[geo.neighbour[w]]))
    allfaces=sorted({f for c in allcells for f in cell_faces(c)})
    faces={}
    for f in allfaces:
        w=int(fi[f]);b=int(base[w])
        faces[str(f)]={"owner":int(cell_native[geo.owner[w]]),
          "neighbour":int(cell_native[geo.neighbour[w]]) if w<len(geo.neighbour) else None,
          "pointIDs":list(map(int,point_native[geo.ids(w)])),"patch":geo.patch(w),
          "sourceBase":b,"sourceZone":int(zone[b]) if b>=0 else -1}
    vertices=sorted({p for row in faces.values() for p in row["pointIDs"]})
    pi=inverse(point_native);points={str(p):geo.points[pi[p]].tolist() for p in vertices}
    cells={str(c):{"faces":sorted(cell_faces(c)),"sourceParent":int(parent[ci[c]])} for c in sorted(allcells)}
    anchor=vertices[0]
    return {"children":sorted(allcells),"outsideCells":[],"points":points,"faces":faces,"cells":cells,
      "apexWrittenPoint":anchor,"originalApex":points[str(anchor)],
      "nativeIdScope":"All compact point/face/cell labels are actual native trace IDs."}

def rank_audit(old,new,rank,out):
    record_path=new/f"conforming-literal-unions-rank{rank}.jsonl"
    records=[json.loads(line) for line in record_path.read_text().splitlines() if line.strip()]
    source=[]
    for p in sorted(old.glob(f"conforming-source-rank{rank}-*")):
        q=new/p.name
        a=g.sha(p);b=g.sha(q)
        source.append({"file":p.name,"baselineSha256":a,"finalSha256":b,"byteExact":a==b})
    if len(source)!=12 or not all(row["byteExact"] for row in source):raise ValueError("Input-stage source differs or is incomplete")
    identities={tag:{name:g.sha(mesh/f"processor{rank}/constant/polyMesh"/name) for name in POLY}
                for tag,mesh in (("baseline",old),("final",new))}
    counts={};setdetails={}
    maps={tag:{name:rawmap(mesh,rank,name) for name in ("cell-native","cell-parent","face-native","face-base","point-native")}
          for tag,mesh in (("baseline",old),("final",new))}
    for metric,(name,kind) in SETS.items():
        values=[];raw_counts={}
        for tag,mesh in (("baseline",old),("final",new)):
            labels=g.cell_set(mesh/f"processor{rank}/constant/polyMesh/sets"/name)
            ids=maps[tag]["cell-native" if kind=="cell" else "face-native"][labels]
            raw_counts[tag]={"declaredAndParsedCount":len(labels),"uniqueWrittenLabels":len(set(map(int,labels))),
              "uniqueOriginalNativeRoles":len(set(map(int,ids)))}
            if tag=="baseline" and kind=="cell":
                replacements={row["drop"]:row["keep"] for row in records}
                ids=np.array([replacements.get(int(v),int(v)) for v in ids],dtype=np.int64)
            values.append(set(map(int,ids)))
        added=sorted(values[1]-values[0]);counts[metric]=[len(values[0]),len(values[1]),len(added)]
        setdetails[metric]={"originalRawSetCounts":raw_counts,
                           "oldBadRolesMappedToSurvivorCount":len(values[0]),"finalNativeBadCount":len(values[1]),
                           "newBadNativeRoles":added,"removedBadNativeRoles":sorted(values[0]-values[1])}
    result={"rank":rank,"acceptedCount":len(records),"acceptedRecordSha256":g.sha(record_path),
            "inputStageSourceIdentities":source,"polyMeshIdentities":identities,"stockSetRoleComparison":setdetails}
    if not records:
        result["unchangedPolyMeshByteExact"]=identities["baseline"]==identities["final"]
        if not result["unchangedPolyMeshByteExact"]:raise ValueError("Unexpected changes on untouched rank")
        op=old/f"processor{rank}/constant/polyMesh";np_=new/f"processor{rank}/constant/polyMesh"
        result["actualPatchAndZoneSemanticBinding"]={"zones":validate_zone_memberships(op,np_),
          "boundaryByteExact":identities["baseline"]["boundary"]==identities["final"]["boundary"],
          "actualBoundaryRoles":boundary_roles(np_/"boundary")}
        return result
    go=g.Geometry(old/f"processor{rank}/constant/polyMesh");gn=g.Geometry(new/f"processor{rank}/constant/polyMesh")
    mo,mn=maps["baseline"],maps["final"];po,pn=mo["point-native"],mn["point-native"]
    fo,fn=mo["face-native"],mn["face-native"];co,cn=mo["cell-native"],mn["cell-native"]
    poi=inverse(po);foi=inverse(fo);coi=inverse(co)
    fixed=bool(np.array_equal(np.sort(po),np.sort(pn)) and
       np.array_equal(go.points[poi[pn]].view(np.uint64),gn.points.view(np.uint64)))
    if not fixed:raise ValueError("Original native coordinate identity changed")
    deleted={row["deletedFace"] for row in records};dropped={row["drop"] for row in records}
    samefaces=set(map(int,fn))==set(map(int,fo))-deleted
    samecells=set(map(int,cn))==set(map(int,co))-dropped
    if not samefaces or not samecells:raise ValueError("Unexpected face/cell insertion/deletion")
    face_old=foi[fn];own=co[go.owner[face_old]].astype(np.int64);nei=np.full(len(fn),-1,dtype=np.int64)
    internal=face_old<len(go.neighbour);nei[internal]=co[go.neighbour[face_old[internal]]]
    replace=np.arange(max(int(co.max()),int(cn.max()))+1)
    for row in records:replace[row["drop"]]=row["keep"]
    own=replace[own];nei[internal]=replace[nei[internal]]
    actual_own=cn[gn.owner];actual_nei=np.full(len(fn),-1,dtype=np.int64)
    actual_nei[:len(gn.neighbour)]=cn[gn.neighbour]
    role_exact=bool(np.array_equal(np.minimum(own,nei),np.minimum(actual_own,actual_nei))
       and np.array_equal(np.maximum(own,nei),np.maximum(actual_own,actual_nei)))
    arity=np.diff(gn.offsets);old_arity=np.diff(go.offsets)[face_old]
    if not np.array_equal(arity,old_arity):raise ValueError("Exterior polygon arity changed")
    j=np.arange(len(gn.vertices))-np.repeat(gn.offsets[:-1],arity)
    flipped=actual_own!=own;flip_rep=np.repeat(flipped,arity)
    old_pos=np.repeat(go.offsets[face_old],arity)+np.where(flip_rep&(j>0),np.repeat(arity,arity)-j,j)
    cycles=bool(np.array_equal(pn[gn.vertices],po[go.vertices[old_pos]]))
    point0=bool(np.array_equal(pn[gn.vertices[gn.offsets[:-1]]],po[go.vertices[go.offsets[face_old]]]))
    parent_exact=np.array_equal(mn["cell-parent"],mo["cell-parent"][coi[cn]])
    base_exact=np.array_equal(mn["face-base"],mo["face-base"][face_old])
    rotations=[]
    if not cycles:
        differing=np.flatnonzero(pn[gn.vertices]!=po[go.vertices[old_pos]])
        written_faces=np.unique(np.searchsorted(gn.offsets,differing,side="right")-1)
        allowed={f for row in records for f in row["exteriorFaces"]}
        for w in written_faces:
            face=int(fn[w]);a=po[go.ids(int(face_old[w]))].tolist();b=pn[gn.ids(int(w))].tolist()
            if flipped[w]:a=[a[0],*reversed(a[1:])]
            if face not in allowed or not any(b==a[k:]+a[:k] for k in range(len(a))):
                raise ValueError("Unexpected polygon change or rotation outside recorded literal shell")
            patch=gn.patch(int(w))
            if patch and any(x in patch["type"].lower() for x in ("cyclic","processor","mapped","ami")):
                raise ValueError("Coupled common point zero changed")
            rotations.append({"nativeFace":face,"baselineOrientedCycle":a,"actualWrittenCycle":b})
    if not all((role_exact,parent_exact,base_exact)):raise ValueError("Actual role/parent/base mismatch")
    patch_proof=validate_patch_roles(go,gn,face_old)
    zone_proof=validate_zone_memberships(go.poly_dir,gn.poly_dir)
    result["actualPatchAndZoneSemanticBinding"]={"patches":patch_proof,"zones":zone_proof}
    result["wholeRankLiteralIdentity"]={"originalNativeCoordinatesBitExact":fixed,
        "onlyRecordedCellsAndFacesRemoved":samefaces and samecells,"allSurvivingIncidenceRolesExact":role_exact,
         "allExteriorOrderedCyclesExact":cycles,"allCommonPointZeroExact":point0,
        "allExteriorOrientedPolygonsExactModuloCycle":True,
        "onlyRecordedNoncoupledShellPointZeroRotates":rotations,
        "allCoupledAndUnchangedCommonPointZeroExact":True,
        "allSourceParentAndBaseIdentityExact":bool(parent_exact and base_exact),
        "survivingFaces":len(fn),"survivingCells":len(cn),"fixedPoints":len(pn)}
    zone=np.memmap(old/f"conforming-source-rank{rank}-zone.i32",dtype="<i4",mode="r")
    state=load_compact(go,po,fo,co,mo["cell-parent"],mo["face-base"],zone,records)
    compact=json.dumps(state,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    (out/f"rank{rank}-baseline-native-stencil.json.gz").write_bytes(gzip.compress(compact,mtime=0))
    steps=[]
    for number,rec in enumerate(records):
        group=[rec["keep"],rec["drop"]];keep=min(group)
        old_stencil=u.WrittenStencil(state,state["originalApex"])
        merged,removed,outer=u.union_document(state,group)
        merged,flips=u.canonicalize_internal_roles(merged)
        if removed!=[rec["deletedFace"]] or outer!=sorted(rec["exteriorFaces"]):raise ValueError("Record shell differs")
        solutions=unique_tets(state,outer)
        if len(solutions)!=1:raise ValueError("Nonunique two-tet recovery")
        original_sets={frozenset(p for f in state["cells"][str(c)]["faces"] for p in state["faces"][str(f)]["pointIDs"]) for c in group}
        if original_sets!={frozenset(solutions[0]["tetA"]),frozenset(solutions[0]["tetB"])}:raise ValueError("Original tet mismatch")
        new_stencil=u.WrittenStencil(merged,merged["originalApex"])
        affected=set(group)
        for f in outer:
            row=old_stencil.faces[f];affected.add(row["owner"])
            if row["neighbour"] is not None:affected.add(row["neighbour"])
        source_role_protection(state,removed,outer,affected)
        oldcells={c:strict_cell(old_stencil,c) for c in affected}
        newcells={c:strict_cell(new_stencil,c) for c in affected-{rec["drop"]}}
        faces=sorted({f for c in affected for f in old_stencil.cells[c]["faces"]}-{rec["deletedFace"]})
        before=[face_row(old_stencil,f) for f in faces];after=[face_row(new_stencil,f) for f in faces]
        role_failures=[];severity_failures=[]
        for c,row in newcells.items():
            origins=group if c==keep else [c]
            for origin in origins:
                oldv=oldcells[origin]["D"];newv=row["D"]
                if metric_pass(oldv,"D") and not metric_pass(newv,"D"):role_failures.append(["D",origin,c])
                if not metric_pass(oldv,"D") and newv<oldv*(1-1e-10):severity_failures.append(["D",origin,c])
        for a,b in zip(before,after):
            for key in LIMITS:
                if key=="D":continue
                ov,nv=a[key],b[key];upper=LIMITS[key][1]
                if metric_pass(ov,key) and not metric_pass(nv,key):role_failures.append([key,a["face"]])
                if not metric_pass(ov,key) and (nv>ov*(1+1e-10) if upper else nv<ov*(1-1e-10)):severity_failures.append([key,a["face"]])
        after_outer=[row for row in after if row["face"] in outer]
        local={"D":newcells[keep]["D"],"weight":min(row["weight"] for row in after_outer),
          "ratio":min(row["ratio"] for row in after_outer),"skew":max(row["skew"] for row in after_outer),
          "nonOrthogonalityDegrees":max(row["nonOrthogonalityDegrees"] for row in after_outer)}
        original=[old_stencil.cell(c) for c in group];nv=newcells[keep]["V"];nc=np.array(newcells[keep]["CC"])
        ov=sum(row[3] for row in original);origin=original[0][2]
        md=nv*(nc-origin)-original[1][3]*(original[1][2]-origin)
        span=max(float(np.linalg.norm(old_stencil.points[p]-origin)) for f in outer for p in old_stencil.faces[f]["pointIDs"])
        verr=abs(nv-ov)/ov;merr=float(np.linalg.norm(md))/(ov*max(span,1e-6))
        geometry=all(row["strictGeometryPass"] for row in oldcells.values()) and all(row["strictGeometryPass"] for row in newcells.values())
        tet_pass=all(row["minimumCentreFanTetQuality"]>1e-15 and row["bestSharedBasePointMinimumTetQuality"]>1e-15 for row in before+after)
        step={"recordIndex":number,"acceptedRecord":rec,"uniqueIndependentTwoTetRecovery":solutions[0],
          "oldCompleteAffectedCells":list(oldcells.values()),"newCompleteAffectedCells":list(newcells.values()),
          "allAffectedFaceMetricsBefore":before,"allAffectedFaceMetricsAfter":after,
          "canonicalFlips":flips,"passingRoleFailures":role_failures,"oldBadSeverityRegressions":severity_failures,
          "allCompleteAffectedGeometryPass":geometry,"allSharedAnchorAndCentreTetFansPass":tet_pass,
          "relativeVolumeError":verr,"centredFirstMomentRelativeError":merr,"mergedShellActualFiveMetrics":local,
          "mergedShellAllFiveAbsolutePass":all(metric_pass(value,key) for key,value in local.items()),
          "recordOldDMaximumDifference":max(abs(oldcells[c]["D"]-d) for c,d in zip(group,rec["oldD"])),
          "recordNewDDifference":abs(local["D"]-rec["newD"])}
        step["independentGuardPass"]=bool(geometry and tet_pass and verr<=1e-10 and merr<=1e-10
           and not role_failures and not severity_failures and step["mergedShellAllFiveAbsolutePass"])
        if not step["independentGuardPass"]:raise ValueError(f"Independent preflight failed r{rank} record{number}")
        steps.append(step);state=merged
    replay=u.WrittenStencil(state,state["originalApex"]);ci=inverse(cn)
    core={int(row[k]) for row in records for k in ("keep","drop")}
    outside=[]
    for c in sorted(set(map(int,state["cells"]))-core):
        a=go.cell(int(coi[c]));b=gn.cell(int(ci[c]))
        outside.append({"nativeCell":c,"baselineWrittenCell":int(coi[c]),"actualFinalWrittenCell":int(ci[c]),
          "baselineCentre":a[3].tolist(),"actualFinalCentre":b[3].tolist(),"baselineVolume":a[4],"actualFinalVolume":b[4],
          "centreBitExact":bool(np.array_equal(a[3].view(np.uint64),b[3].view(np.uint64))),
          "volumeBitExact":a[4]==b[4],"centreDifferenceM":float(np.linalg.norm(a[3]-b[3])),
          "relativeVolumeDifference":abs(a[4]-b[4])/abs(a[4])})
    face_changes=[]
    fni=inverse(fn)
    for f in sorted({f for row in records for f in row["exteriorFaces"]}):
        w=int(fni[f]);ow=int(foi[f]);a=go.face(ow);b=gn.face(w)
        baseline_owned=int(co[go.owner[ow]])
        if baseline_owned in dropped:baseline_owned=int(replace[baseline_owned])
        sign=1 if baseline_owned==int(cn[gn.owner[w]]) else -1
        face_changes.append({"nativeFace":f,"areaVectorOppositeRoleSign":sign,
           "baselineFaceCentre":a[1].tolist(),"actualFinalFaceCentre":b[1].tolist(),
           "areaVectorDifferenceM2":float(np.linalg.norm(sign*a[0]-b[0])),
           "relativeAreaVectorDifference":float(np.linalg.norm(sign*a[0]-b[0])/np.linalg.norm(a[0])),
           "faceCentreDifferenceM":float(np.linalg.norm(a[1]-b[1]))})
    result["actualWrittenOutsideAndShellFloatingChanges"]={
      "outsideCompleteCells":outside,"outsideCount":len(outside),
      "outsideCentreBitExactCount":sum(x["centreBitExact"] for x in outside),
      "outsideVolumeBitExactCount":sum(x["volumeBitExact"] for x in outside),
      "maximumOutsideCentreDifferenceM":max([0.,*(x["centreDifferenceM"] for x in outside)]),
      "maximumOutsideRelativeVolumeDifference":max([0.,*(x["relativeVolumeDifference"] for x in outside)]),
      "allRecordedExteriorFaceFloatingChanges":face_changes,
      "maximumExteriorAreaVectorDifferenceM2":max([0.,*(x["areaVectorDifferenceM2"] for x in face_changes)]),
      "maximumExteriorRelativeAreaVectorDifference":max([0.,*(x["relativeAreaVectorDifference"] for x in face_changes)]),
      "maximumExteriorFaceCentreDifferenceM":max([0.,*(x["faceCentreDifferenceM"] for x in face_changes)])}

    cc_delta=0.;v_relative=0.;cellsets=True;written_merged=[]
    for c in map(int,state["cells"]):
        w=int(ci[c]);actual=gn.cell(w);simulated=replay.cell(c)
        fs=set(map(int,fn[actual[0]]))
        cellsets=cellsets and fs==set(state["cells"][str(c)]["faces"])
        cc_delta=max(cc_delta,float(np.linalg.norm(actual[3]-simulated[2])))
        v_relative=max(v_relative,abs(actual[4]-simulated[3])/abs(actual[4]))
    actual_doc=copy.deepcopy(state);fini=inverse(fn)
    for key,row in actual_doc["faces"].items():
        w=int(fini[int(key)])
        row["owner"]=int(cn[gn.owner[w]])
        row["neighbour"]=int(cn[gn.neighbour[w]]) if w<len(gn.neighbour) else None
        row["pointIDs"]=pn[gn.ids(w)].tolist()
        row["patch"]=gn.patch(w)
    for key,row in actual_doc["cells"].items():
        w=int(ci[int(key)])
        row["faces"]=fn[gn.cell_faces[gn.cell_offsets[w]:gn.cell_offsets[w+1]]].tolist()
    actual=u.WrittenStencil(actual_doc,actual_doc["originalApex"])
    for rec in records:
        c=rec["keep"];detail=strict_cell(actual,c)
        metrics=[face_row(actual,f) for f in actual.cells[c]["faces"]]
        values={"D":detail["D"],"weight":min(x["weight"] for x in metrics),
          "ratio":min(x["ratio"] for x in metrics),"skew":max(x["skew"] for x in metrics),
          "nonOrthogonalityDegrees":max(x["nonOrthogonalityDegrees"] for x in metrics)}
        absolute=all(metric_pass(v,k) for k,v in values.items())
        if not detail["strictGeometryPass"] or not absolute:raise ValueError("Actual final merged cell/shell fails")
        written_merged.append({"nativeCell":c,"actualWrittenCell":int(ci[c]),"actualFiveMetrics":values,
          "actualStrictGeometryPass":detail["strictGeometryPass"],"actualFiveAbsolutePass":absolute,
          "CC":gn.cell(int(ci[c]))[3].tolist(),"V":gn.cell(int(ci[c]))[4]})
    result["sequentialAcceptedReconstruction"]={"steps":steps,"originalCompleteStencilCells":len(state["cells"])+len(records),
      "allGuardsIndependentlyPass":all(row["independentGuardPass"] for row in steps),
      "actualFinalCompleteCellFaceSetsExact":cellsets,"maximumActualWrittenCentreDifferenceM":cc_delta,
      "maximumActualWrittenRelativeVolumeDifference":v_relative,"actualFinalMergedCells":written_merged}
    if not cellsets or cc_delta>1e-12 or v_relative>1e-10:raise ValueError("Replayed final geometry differs")
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True,type=Path);p.add_argument("--final",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path);args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    if not args.output.resolve().is_relative_to(root):raise ValueError("Output must remain inside this repository")
    args.output.mkdir(parents=True,exist_ok=True);ranks=[]
    for rank in range(8):
        result=rank_audit(args.baseline,args.final,rank,args.output)
        dump(args.output/f"rank{rank}-audit.json",result);ranks.append(result)
        print(json.dumps({"rank":rank,"accepted":result["acceptedCount"],
          "wholeRankIdentity":result.get("wholeRankLiteralIdentity"),
          "guardPass":result.get("sequentialAcceptedReconstruction",{}).get("allGuardsIndependentlyPass"),
          "stockSets":result["stockSetRoleComparison"]}),flush=True)
    summary={"schema":"literal-union-written-independent/v1","baseline":str(args.baseline),"final":str(args.final),
       "acceptedCount":sum(r["acceptedCount"] for r in ranks),"acceptedByRank":[r["acceptedCount"] for r in ranks],
       "all96InputSourceFilesByteExact":all(x["byteExact"] for r in ranks for x in r["inputStageSourceIdentities"]),
       "allAcceptedGuardsIndependentlyPass":all(r.get("sequentialAcceptedReconstruction",{}).get("allGuardsIndependentlyPass",True) for r in ranks),
       "allUntouchedRanksPolyMeshByteExact":all(r.get("unchangedPolyMeshByteExact",True) for r in ranks),
       "allStockSetsHaveNoNewNativeFailedRoles":all(not x["newBadNativeRoles"] for r in ranks for x in r["stockSetRoleComparison"].values()),
       "rawStockSetCounts":{k:{ "baselineRawCount":sum(r["stockSetRoleComparison"][k]["originalRawSetCounts"]["baseline"]["declaredAndParsedCount"] for r in ranks),
            "baselineBadRolesMappedToSurvivorCount":sum(r["stockSetRoleComparison"][k]["oldBadRolesMappedToSurvivorCount"] for r in ranks),
            "finalCount":sum(r["stockSetRoleComparison"][k]["finalNativeBadCount"] for r in ranks)} for k in SETS},
       "scope":"35 accepted literal replacements and complete affected stencils, matched to actual final written topology/points. The whole stock mesh still rejects; this is not mesh acceptance."}
    dump(args.output/"result.json",summary);print(json.dumps(summary,indent=2),flush=True)

if __name__=="__main__":main()
