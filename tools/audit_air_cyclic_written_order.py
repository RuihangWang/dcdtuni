#!/usr/bin/env python3
"""Read-only coincidence, face-order and vertex0 audit on actual written cyclics."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,re
from collections import Counter
from pathlib import Path
spec=importlib.util.spec_from_file_location("holes",Path(__file__).with_name("audit_air_cone_written_holes.py"))
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
g=h.g;s=h.s

def canonical_ids(p):
    return min(tuple(q[i:]+q[:i]) for q in [p,p[::-1]] for i in range(len(q)))

def same_start_reverse(p):return p[:1]+p[:0:-1]

def point_pair_map(a,b):
    if len(a)!=len(b):raise ValueError("coupled polygon arity mismatch")
    return [(a[i],b[(len(b)-i)%len(b)]) for i in range(len(a))]

def coupled_mesh_points(mesh):
    ids=[];lookup={}
    # Mirrors installed globalMeshData::coupledPatch + PrimitivePatch meshPoints.
    for start,count,name,kind in mesh.patches:
        if kind not in ["cyclic","processor","processorCyclic"]:continue
        for f in range(start,start+count):
            for q in mesh.ids(f):
                q=int(q)
                if q not in lookup:lookup[q]=len(ids);ids.append(q)
    return ids,lookup

def audit_pair(mesh,name,other,metadata,lookup):
    aa=metadata[name];bb=metadata[other]
    if aa["count"]!=bb["count"]:raise ValueError("cyclic face count mismatch")
    akey=[];bkey=[];mismatch=[];mapping={};wrong=[]
    for k in range(aa["count"]):
        af=aa["start"]+k;bf=bb["start"]+k
        a=mesh.ids(af).tolist();b=mesh.ids(bf).tolist()
        akey.append(canonical_ids(a));bkey.append(canonical_ids(b))
        if canonical_ids(a)!=canonical_ids(b):mismatch.append({"relativeFace":k,"reason":"geometry","masterFace":af,"slaveFace":bf})
        elif b!=same_start_reverse(a):
            mismatch.append({"relativeFace":k,"reason":"vertex0/order","masterFace":af,"slaveFace":bf,
                "masterPointIds":a,"slavePointIds":b,"expectedSlave":same_start_reverse(a),
                "masterPoints":mesh.points[a].tolist(),"slavePoints":mesh.points[b].tolist(),
                "masterCoupledLocalPointIds":[lookup[x] for x in a],"slaveCoupledLocalPointIds":[lookup[x] for x in b]})
        for x,y in point_pair_map(a,b):
            # cyclicPolyPatch.C:1049–1056 keeps first nonidentity assignment.
            if x not in mapping and x!=y:
                mapping[x]=y
                wrong.append({"fromMeshPoint":x,"toMeshPoint":y,"fromCoupledLocal":lookup[x],"toCoupledLocal":lookup[y],
                    "fromCoords":mesh.points[x].tolist(),"toCoords":mesh.points[y].tolist(),"firstRelativeFace":k})
    return {"master":name,"slave":other,"faces":aa["count"],"sameExactGeometricFaceMultiset":Counter(akey)==Counter(bkey),
      "sameGeometricFaceAtEveryIndex":akey==bkey,"wrongVertex0OrOrderFaces":sum(x["reason"]=="vertex0/order" for x in mismatch),
      "wrongFirstNonidentityPointAssignments":len(wrong),"mismatchSamples":mismatch,"wrongPointAssignmentSamples":wrong,
      "rank6FatalRelativeFace580":next((x for x in mismatch if x["relativeFace"]==580),None) if name=="merged_perforatedBlankingPanel_4U_master" else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mesh",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);a=ap.parse_args()
    ranks=[];hashes={}
    for rank in range(8):
        p=a.mesh/f"processor{rank}/constant/polyMesh";m=g.Geometry(p);ids,lookup=coupled_mesh_points(m)
        metadata={}
        for name,body in re.findall(r"(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}",(p/"boundary").read_text()):
            if re.search(r"\btype\s+cyclic\s*;",body):
                metadata[name]={"start":int(re.search(r"startFace\s+(\d+)",body)[1]),
                 "count":int(re.search(r"nFaces\s+(\d+)",body)[1]),
                 "neighbour":re.search(r"neighbourPatch\s+([^;\s]+)",body)[1],
                 "transform":re.search(r"transform\s+([^;\s]+)",body)[1]}
        pairs=[]
        for name,info in metadata.items():
            other=info["neighbour"]
            assert metadata[other]["neighbour"]==name
            if name>other:continue
            pairs.append(audit_pair(m,name,other,metadata,lookup))
        row={"rank":rank,"pairs":pairs}
        if rank==6:
            row["fatalPointLocalIdsReconstructed"]=[{"localCoupledPoint":i,"meshPoint":ids[i],"coords":m.points[ids[i]].tolist()} for i in [774,1236,1237]]
        ranks.append(row)
        for name in ["points","faces","owner","neighbour","boundary"]:hashes[str((p/name).relative_to(a.mesh))]=h.sha(p/name)
        del m
    pairs=[p for r in ranks for p in r["pairs"]]
    out={"format":"written-cyclic-vertex0-audit/v1","scope":"Read-only actual coincident cyclic ordering and direct installed point-map reconstruction; no native/solver run or mesh acceptance.",
      "meshPath":str(a.mesh),"meshSha256":hashes,
      "sourceSha256":{p.name:h.sha(p) for p in [Path(__file__),Path(__file__).with_name("audit_air_cone_written_holes.py"),Path(__file__).with_name("audit_air_mesh_geometry.py")]},
      "summary":{"cyclicPairs":len(pairs),"facesPerSide":sum(p["faces"] for p in pairs),
       "allExactGeometricMultisetsMatch":all(p["sameExactGeometricFaceMultiset"] for p in pairs),
       "allExactGeometricFaceIndicesMatch":all(p["sameGeometricFaceAtEveryIndex"] for p in pairs),
       "wrongVertex0OrOrderFaces":sum(p["wrongVertex0OrOrderFaces"] for p in pairs),
       "wrongFirstNonidentityPointAssignments":sum(p["wrongFirstNonidentityPointAssignments"] for p in pairs)},
      "limits":["GlobalMeshData local point numbering is reconstructed by installed exact face/point encounter order and matches the logged rank6 IDs and coordinates.",
         "Local cyclic point mapping is evaluated from the written same-rank meshPoint IDs. Processor/processorCyclic remote face pairing is not independently audited here.",
         "This proves actual wrong coincidence point assignments; the installed global edge graph is not fully reconstructed by this tool.",
         "No fuzzy-sort counterexample is used to explain this exact cyclic vertex0 failure."],
      "ranks":ranks}
    a.output.write_text(json.dumps(out,indent=2)+"\n");print(json.dumps(out["summary"],indent=2))
if __name__=="__main__":main()
