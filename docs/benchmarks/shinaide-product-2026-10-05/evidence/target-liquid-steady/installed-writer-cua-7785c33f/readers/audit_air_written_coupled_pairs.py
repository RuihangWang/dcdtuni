#!/usr/bin/env python3
"""Read-only exact written coupled-polygon pairing, all actual MPI ranks."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,re
from collections import Counter
from pathlib import Path
import numpy as np
spec=importlib.util.spec_from_file_location("geometry",Path(__file__).with_name("audit_air_mesh_geometry.py"))
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def coord_key(p):return tuple(float(x).hex() for x in p)
def canonical(poly):
    p=[coord_key(q) for q in poly]
    return min(tuple(q[i:]+q[:i]) for q in [p,p[::-1]] for i in range(len(q)))
def pair_check(a,b):
    a=np.asarray(a,dtype=float);b=np.asarray(b,dtype=float)
    if not len(a) or not len(b) or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError("nonempty finite coupled polygons required")
    if len(a)!=len(b):return {"sameArity":False,"samePolygon":False,"samePoint0":False,"sameStartReverse":False,"maximumExpectedPointDistanceM":None}
    expected=np.concatenate((a[:1],a[:0:-1]))
    return {"sameArity":True,"samePolygon":canonical(a)==canonical(b),
        "samePoint0":coord_key(a[0])==coord_key(b[0]),
        "sameStartReverse":all(coord_key(x)==coord_key(y) for x,y in zip(expected,b)),
        "maximumExpectedPointDistanceM":float(np.linalg.norm(expected-b,axis=1).max())}
class Mesh:
    def __init__(self,p):
        self.poly=p;self.points=g.read_points(p/"points")
        self.offsets,self.vertices=g.read_all_faces(p/"faces");self.metadata={}
        for name,body in re.findall(r"(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}",(p/"boundary").read_text()):
            fields=dict(re.findall(r"(\w+)\s+([^;\s]+)\s*;",body))
            if all(k in fields for k in ["startFace","nFaces","type"]):
                self.metadata[name]={"start":int(fields["startFace"]),"count":int(fields["nFaces"]),"type":fields["type"],"fields":fields}
    def face(self,f):return self.points[self.vertices[self.offsets[f]:self.offsets[f+1]]]
def counterpart(rank,name,meshes):
    meta=meshes[rank].metadata[name];fields=meta["fields"];kind=meta["type"]
    if kind=="cyclic":
        other=fields["neighbourPatch"]
        target=meshes[rank].metadata[other]
        if target["type"]!="cyclic" or target["fields"]["neighbourPatch"]!=name:raise ValueError("cyclic patch pair not reciprocal")
        if fields.get("transform")!="coincidentFullMatch" or target["fields"].get("transform")!="coincidentFullMatch":
            raise ValueError("exact world-coordinate audit supports only coincidentFullMatch cyclic")
        return rank,other
    if kind not in ["processor","processorCyclic"]:raise ValueError("not supported coupled type")
    if int(fields["myProcNo"])!=rank:raise ValueError("boundary myProcNo does not match actual directory")
    remote=int(fields["neighbProcNo"])
    candidates=[]
    for other,target in meshes[remote].metadata.items():
        tf=target["fields"]
        if target["type"]!=kind or int(tf.get("myProcNo",-1))!=remote or int(tf.get("neighbProcNo",-1))!=rank:continue
        if kind=="processorCyclic":
            ref=fields["referPatch"];rp=meshes[rank].metadata[ref]
            if rp["type"]!="cyclic":raise ValueError("processorCyclic referPatch is not cyclic")
            expected=rp["fields"]["neighbourPatch"]
            if tf.get("referPatch")!=expected:continue
            rr=meshes[remote].metadata[expected]
            if rr["type"]!="cyclic" or rr["fields"]["neighbourPatch"]!=ref:raise ValueError("remote cyclic reference is not reciprocal")
            if rp["fields"].get("transform")!="coincidentFullMatch" or rr["fields"].get("transform")!="coincidentFullMatch":
                raise ValueError("unsupported transformed processorCyclic")
        candidates.append(other)
    if len(candidates)!=1:raise ValueError(f"missing/ambiguous coupled counterpart: {rank}:{name} -> {remote}, {candidates}")
    return remote,candidates[0]
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--mesh",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);a=ap.parse_args()
    dirs={int(p.name.removeprefix("processor")):p/"constant/polyMesh" for p in a.mesh.glob("processor[0-9]*") if p.is_dir() and p.name.removeprefix("processor").isdigit()}
    if sorted(dirs)!=list(range(len(dirs))) or len(dirs)<2:raise ValueError("need >=2 contiguous actual processor mesh directories")
    meshes={rank:Mesh(p) for rank,p in sorted(dirs.items())};seen=set();rows=[]
    for rank,m in meshes.items():
        for name,meta in m.metadata.items():
            if meta["type"] not in ["cyclic","processor","processorCyclic"]:continue
            remote,other=counterpart(rank,name,meshes)
            identity=tuple(sorted([(rank,name),(remote,other)]))
            if identity in seen:continue
            seen.add(identity)
            back=counterpart(remote,other,meshes)
            if back!=(rank,name):raise ValueError("counterpart pairing not reciprocal")
            target=meshes[remote].metadata[other]
            aa=[m.face(meta["start"]+i) for i in range(meta["count"])]
            bb=[meshes[remote].face(target["start"]+i) for i in range(target["count"])]
            samples=[];counters=Counter();distance=0.
            for i in range(min(len(aa),len(bb))):
                v=pair_check(aa[i],bb[i])
                for field in ["sameArity","samePolygon","samePoint0","sameStartReverse"]:
                    if not v[field]:counters[field]+=1
                if v["maximumExpectedPointDistanceM"] is not None:distance=max(distance,v["maximumExpectedPointDistanceM"])
                if not all(v[k] for k in ["sameArity","samePolygon","samePoint0","sameStartReverse"]) and len(samples)<8:
                    samples.append({"relativeFace":i,"localFace":meta["start"]+i,"remoteFace":target["start"]+i,
                      "localPoints":aa[i].tolist(),"remotePoints":bb[i].tolist(),**v})
            rows.append({"rank":rank,"patch":name,"remoteRank":remote,"remotePatch":other,"type":meta["type"],
               "localFaces":len(aa),"remoteFaces":len(bb),"sameFaceCount":len(aa)==len(bb),
               "sameExactPolygonMultiset":Counter(canonical(p) for p in aa)==Counter(canonical(p) for p in bb),
               "mismatchedArity":counters["sameArity"],"mismatchedRelativePolygon":counters["samePolygon"],
               "mismatchedPoint0":counters["samePoint0"],"mismatchedSameStartReverse":counters["sameStartReverse"],
               "maximumExpectedPointDistanceM":distance,"mismatchSamples":samples})
    if not rows:raise ValueError("no supported coupled patch pairs in actual MPI mesh")
    out={"format":"written-all-coupled-exact-pairs/v1",
      "scope":"Read-only written coincident cyclic/processor/processorCyclic geometry, reciprocal actual boundary roles, relative face and vertex0 mapping; no mesh/product acceptance.",
      "meshPath":str(a.mesh),"ranks":len(meshes),
      "sourceSha256":{p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name("audit_air_mesh_geometry.py")]},
      "meshSha256":{str((p/name).relative_to(a.mesh)):sha(p/name) for p in dirs.values() for name in ["points","faces","boundary"]},
      "summary":{"pairsByType":dict(Counter(r["type"] for r in rows)),
         "facesPerSideByType":{kind:sum(r["localFaces"] for r in rows if r["type"]==kind) for kind in ["cyclic","processor","processorCyclic"]},
         "allActualPatchRolesReciprocal":True,"allFaceCountsEqual":all(r["sameFaceCount"] for r in rows),
         "allExactPolygonMultisetsEqual":all(r["sameExactPolygonMultiset"] for r in rows),
         "mismatchedRelativePolygons":sum(r["mismatchedRelativePolygon"] for r in rows),
         "mismatchedPoint0":sum(r["mismatchedPoint0"] for r in rows),
         "mismatchedSameStartReverse":sum(r["mismatchedSameStartReverse"] for r in rows),
         "maximumExpectedPointDistanceM":max(r["maximumExpectedPointDistanceM"] for r in rows)},
      "limitations":["Exact world-coordinate cyclic audit is restricted to actual coincidentFullMatch. AMI/mapped/transformed coupling is unsupported.",
        "This verifies written mesh face/point geometry and actual patch roles, not complete OpenFOAM global edge graph or field solver conservation.",
        "Complete full checkMesh and whole-job deadline remain separate required gates; geometric equality never overrides failed mesh quality."],
      "pairs":rows}
    cfds=sorted((a.mesh.parent/"model").glob("*.cfdrun"))
    out["inputCfdrunSha256"]={p.name:sha(p) for p in cfds}
    a.output.write_text(json.dumps(out,indent=2)+"\n");print(json.dumps(out["summary"],indent=2))
if __name__=="__main__":main()
