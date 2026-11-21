#!/usr/bin/env python3
"""Pure mathematical audit of one actual trace-bound apex and its complete affected stencil.

No mesh is written. The compact input contains the actual written topology, every
child, complete outside cell geometry and fixed source point mappings. This is
a local candidate diagnostic, never a substitute for full native checkMesh.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib,json,math
from pathlib import Path
import numpy as np

COUPLED={"cyclic","processor","processorCyclic"}

def face_geometry(points):
    mean=np.zeros(3)
    for p in points:mean+=p
    mean/=len(points)
    if len(points)==3:
        return .5*np.cross(points[1]-points[0],points[2]-points[0]),mean
    area=np.zeros(3);weighted=np.zeros(3);total=0.
    for i,p in enumerate(points):
        nxt=points[(i+1)%len(points)]
        v=np.cross(nxt-p,mean-p);a=float(np.linalg.norm(v))
        area+=v;weighted+=a*(p+nxt+mean);total+=a
    return .5*area,(weighted/(3*total) if total else mean)

def tet_quality(a,b,c,d):
    x=b-a;y=c-a;z=d-a
    vol=float(np.dot(np.cross(x,y),z)/6)
    ba=np.cross(y,x);ca=np.cross(z,x);den=float(np.dot(z,ba))
    if abs(den)<1e-150:radius=1e15
    else:
        num=(float(np.dot(z,z))-float(np.dot(x,z)))*ba-(float(np.dot(y,y))-float(np.dot(x,y)))*ca
        radius=min(1e15,float(np.linalg.norm(.5*(x+num/den))))
    return vol/(8/(9*math.sqrt(3))*radius**3+1e-150)

class Stencil:
    def __init__(self,doc,apex):
        self.doc=doc
        self.points={int(k):np.asarray(v,dtype=float) for k,v in doc["points"].items()}
        self.points[doc["apexWrittenPoint"]]=np.asarray(apex,dtype=float)
        self.faces={int(k):v for k,v in doc["faces"].items()}
        self.cells={int(k):v for k,v in doc["cells"].items()}
        self.faceCache={};self.cellCache={}
    def polygon(self,f):
        return np.array([self.points[i] for i in self.faces[f]["pointIDs"]])
    def face(self,f):
        if f not in self.faceCache:self.faceCache[f]=face_geometry(self.polygon(f))
        return self.faceCache[f]
    def cell(self,c):
        if c not in self.cellCache:
            fs=self.cells[c]["faces"];areas=[];centres=[]
            for f in fs:
                sf,fc=self.face(f)
                areas.append(sf if self.faces[f]["owner"]==c else -sf);centres.append(fc)
            areas=np.array(areas);centres=np.array(centres);estimate=centres.mean(axis=0)
            pv=np.einsum("ij,ij->i",areas,centres-estimate);V=float(pv.sum()/3)
            cc=(pv[:,None]*(.75*centres+.25*estimate)).sum(axis=0)/pv.sum()
            self.cellCache[c]=(areas,centres,cc,V)
        return self.cellCache[c]
    def cell_detail(self,c):
        fs=self.cells[c]["faces"];areas,fc,cc,V=self.cell(c)
        selected=[sf for f,sf in zip(fs,areas)
                  if self.faces[f]["patch"] is None or self.faces[f]["patch"]["type"] in COUPLED]
        a=np.array(selected);avg=np.linalg.norm(a,axis=1).mean() if len(a) else 0
        D=float(abs(np.linalg.det((a/avg).T@(a/avg)))/8) if avg else 0
        xyz=np.array([self.points[v] for v in sorted({v for f in fs for v in self.faces[f]["pointIDs"]})])
        normals=areas/np.linalg.norm(areas,axis=1)[:,None]
        outside=float(np.einsum("vfi,fi->vf",xyz[:,None,:]-fc[None,:,:],normals).max())
        pyramids=np.einsum("ij,ij->i",areas,fc-cc)/3
        pairs=0
        for i in range(len(fs)):
            delta=fc-fc[i];length=np.linalg.norm(delta,axis=1)
            d=delta@normals[i]/np.maximum(length,1e-150)
            pairs+=int(np.count_nonzero((d>-1e-6)&(np.arange(len(fs))!=i)))
        edge=Counter();oriented=Counter()
        for f in fs:
            ids=self.faces[f]["pointIDs"]
            if self.faces[f]["owner"]!=c:ids=list(reversed(ids))
            for i,v in enumerate(ids):
                w=ids[(i+1)%len(ids)];edge[tuple(sorted((v,w)))]+=1;oriented[(v,w)]+=1
        return {"cell":c,"D":D,"V":V,"CC":cc.tolist(),"selectedFaces":len(selected),
                "relativeClosure":float(np.linalg.norm(areas.sum(axis=0))/np.linalg.norm(areas,axis=1).sum()),
                "minCentroidPyramidVolume":float(pyramids.min()),"maximumVertexOutsideM":outside,
                "faceCentreConcavityPairTriggers":pairs,"edgesExactlyTwo":all(v==2 for v in edge.values()),
                "edgesOppositeOrientation":all(oriented[(b,a)]==v for (a,b),v in oriented.items())}
    def face_detail(self,f):
        row=self.faces[f];p=self.polygon(f);sf,fc=self.face(f);co=self.cell(row["owner"]);oc=co[2];ov=co[3]
        n=sf/np.linalg.norm(sf);planarity=float(np.abs((p-fc)@n).max())
        edges=np.roll(p,-1,axis=0)-p;lengths=np.linalg.norm(edges,axis=1)
        support=np.einsum("evj,j->ev",np.cross(edges[:,None,:],p[None,:,:]-p[:,None,:]),n)
        polygonSupport=float((support/lengths[:,None]).min())
        nei=row["neighbour"];nc=self.cell(nei) if nei is not None else None
        if nc is None:d=n*np.dot(n,fc-oc);factor=.4
        else:d=nc[2]-oc;factor=.2
        cpf=fc-oc;sv=cpf-(np.dot(sf,cpf)/(np.dot(sf,d)+1e-150))*d
        svhat=sv/(np.linalg.norm(sv)+1e-150);fd=max(factor*np.linalg.norm(d)+1e-150,float(np.abs((p-fc)@svhat).max()))
        skew=float(np.linalg.norm(sv)/fd)
        result={"face":f,"owner":row["owner"],"neighbour":nei,"patch":row["patch"],
                "skew":skew,"planarityErrorM":planarity,"polygonMinimumSupportM":polygonSupport,
                "ownerPyramidVolume":float(np.dot(sf,fc-oc)/3)}
        if nc is not None:
            dn=abs(float(np.dot(sf,nc[2]-fc)));do=abs(float(np.dot(sf,fc-oc)))
            result.update(weight=min(do,dn)/(do+dn+1e-300),ratio=min(ov,nc[3])/(max(ov,nc[3])+1e-300),
                          neighbourPyramidVolume=float(np.dot(sf,nc[2]-fc)/3),
                          nonOrthogonalityDegrees=float(np.degrees(np.arccos(np.clip(np.dot(n,d)/np.linalg.norm(d),-1,1)))))
        centreTets=[];fans=[]
        for i,point in enumerate(p):
            q=p[(i+1)%len(p)]
            centreTets.append(-tet_quality(point,q,fc,oc))
            if nc is not None:centreTets.append(tet_quality(point,q,fc,nc[2]))
        for start in range(len(p)):
            qs=[]
            for j in range(1,len(p)-1):
                a=p[(start+j)%len(p)];b=p[(start+j+1)%len(p)]
                qs.append(tet_quality(oc,p[start],a,b))
                if nc is not None:qs.append(tet_quality(nc[2],p[start],b,a))
            fans.append(min(qs))
        result["minimumCentreFanTetQuality"]=min(centreTets)
        result["bestSharedBasePointMinimumTetQuality"]=max(fans)
        return result

def evaluate(doc,apex):
    m=Stencil(doc,apex);children=[m.cell_detail(c) for c in doc["children"]]
    faces=[m.face_detail(f) for f in doc["affectedFaces"]]
    parent=doc["parentOriginal"];vectors=np.array([v["outwardArea"] for v in parent["originalFaces"]])
    centres=np.array([v["OFcentre"] for v in parent["originalFaces"]]);normals=vectors/np.linalg.norm(vectors,axis=1)[:,None]
    slack=np.einsum("ij,ij->i",centres-np.asarray(apex),normals)
    V=sum(v["V"] for v in children);moment=sum((v["V"]*np.asarray(v["CC"]) for v in children),np.zeros(3))
    estimate=centres.mean(axis=0)
    parentPv=np.einsum("ij,ij->i",vectors,centres-estimate)
    parentVolume=float(parentPv.sum()/3)
    parentCentre=(parentPv[:,None]*(.75*centres+.25*estimate)).sum(axis=0)/parentPv.sum()
    parentMoment=parentVolume*parentCentre
    outside=[]
    for c in doc["outsideCells"]:
        row=m.cell_detail(c);old=doc["cells"][str(c)]
        row["volumeDeltaFromWritten"]=row["V"]-old["writtenVolume"]
        row["centreDeltaFromWritten"]=float(np.linalg.norm(np.asarray(row["CC"])-old["writtenCentre"]))
        outside.append(row)
    grouped={}
    for f in doc["originalParentBoundaryFaces"]:
        raw=m.faces[f];sf,fc=m.face(f);sf=sf if raw["owner"] in doc["children"] else -sf
        key=raw["baseSourceFace"]
        if key not in grouped:grouped[key]=[np.zeros(3),np.zeros(3),0.]
        grouped[key][0]+=sf;grouped[key][1]+=np.linalg.norm(sf)*fc;grouped[key][2]+=np.linalg.norm(sf)
    baseDiff=[]
    for original in parent["originalFaces"]:
        sf,mc,amount=grouped[original["face"]];area=float(np.linalg.norm(original["outwardArea"]))
        baseDiff.append({"sourceBase":original["face"],"relativeAreaVectorError":float(np.linalg.norm(sf-original["outwardArea"])/area),
                         "relativeAreaAmountError":abs(amount-area)/area,
                         "areaWeightedCentreErrorM":float(np.linalg.norm(mc/amount-original["OFcentre"]))})
    minimumD=min(v["D"] for v in children);minimumWeight=min(f["weight"] for f in faces if "weight" in f)
    minimumRatio=min(f["ratio"] for f in faces if "ratio" in f);maxSkew=max(f["skew"] for f in faces)
    summary={"minimumD":minimumD,"minimumWeight":minimumWeight,"minimumRatio":minimumRatio,"maximumSkew":maxSkew,
     "normalizedWorstScore":min(minimumD/.001,minimumWeight/.05,minimumRatio/.01,4/maxSkew),
     "minimumParentKernelSlackM":float(slack.min()),"childVolumeSum":V,"childVolumeRelativeError":abs(V-parent["inputVolume"])/parent["inputVolume"],
     "childFirstMoment":moment.tolist(),"originalParentVolumeRecomputed":parentVolume,
     "originalParentCentreRecomputed":parentCentre.tolist(),
     "firstMomentDeltaFromOriginalParent":float(np.linalg.norm(moment-parentMoment)),
     "minimumVolume":min(v["V"] for v in children),
     "maximumClosure":max(v["relativeClosure"] for v in children),"minimumChildCentroidPyramid":min(v["minCentroidPyramidVolume"] for v in children),
     "maximumVertexOutsideM":max(v["maximumVertexOutsideM"] for v in children),
     "concavityPairTriggerCount":sum(v["faceCentreConcavityPairTriggers"] for v in children),
     "closedEdgesAndOppositeOrientation":all(v["edgesExactlyTwo"] and v["edgesOppositeOrientation"] for v in children),
     "maxAffectedFacePlanarityM":max(f["planarityErrorM"] for f in faces),"minPolygonEdgeSupportM":min(f["polygonMinimumSupportM"] for f in faces),
     "minimumCentreFanTetQuality":min(f["minimumCentreFanTetQuality"] for f in faces),
     "minimumBestSharedBasePointTetQuality":min(f["bestSharedBasePointMinimumTetQuality"] for f in faces),
     "maxParentBoundaryAreaVectorRelativeError":max(v["relativeAreaVectorError"] for v in baseDiff),
     "maxParentBoundaryAreaAmountRelativeError":max(v["relativeAreaAmountError"] for v in baseDiff),
     "maxParentBoundaryCentreErrorM":max(v["areaWeightedCentreErrorM"] for v in baseDiff),
     "maxExternalCellVolumeDelta":max(abs(v["volumeDeltaFromWritten"]) for v in outside),
     "maxExternalCellCentreDeltaM":max(v["centreDeltaFromWritten"] for v in outside),
     "externalCellsWithUnchangedExistingDeterminantFailure":[{"cell":v["cell"],"D":v["D"]} for v in outside if v["D"]<.001],
     "maximumAffectedNonOrthogonalityDegrees":max(f["nonOrthogonalityDegrees"] for f in faces if "nonOrthogonalityDegrees" in f),
     "determinantFailures":sum(v["D"]<.001 for v in children),
     "weightFailures":sum(f.get("weight",1)<.05 for f in faces),"ratioFailures":sum(f.get("ratio",1)<.01 for f in faces),
     "skewFailures":sum(f["skew"]>4 for f in faces)}
    return {"apex":list(map(float,apex)),"summary":summary,"children":children,"faces":faces,"outsideCells":outside,"basePartitions":baseDiff}

def main():
    p=argparse.ArgumentParser();p.add_argument("--input",type=Path,required=True);p.add_argument("--output",type=Path,required=True);args=p.parse_args()
    doc=json.loads(args.input.read_text())
    assert doc["allApexIncidentCellsExactChildren"] and len(doc["children"])==35
    assert all(doc["faces"][str(f)]["patch"] is None or doc["faces"][str(f)]["patch"]["type"] not in COUPLED for f in doc["affectedFaces"])
    rows={name:evaluate(doc,doc[key]) for name,key in [("written","originalApex"),("chebyshev","chebyshevCandidate"),("candidate","candidate")]}
    baseline=rows["written"]["summary"]
    for name,row in rows.items():
        moment=np.asarray(row["summary"]["childFirstMoment"])
        row["summary"]["firstMomentDeltaFromWritten"]=float(np.linalg.norm(moment-np.asarray(baseline["childFirstMoment"])))
    result={"scope":"Actual trace-bound 35 children, all92 affected faces, and33 fixed outside cell geometries; pure math no mesh/native/API acceptance.",
            "inputSha256":hashlib.sha256(args.input.read_bytes()).hexdigest(),"toolSha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "thresholds":{"determinant":.001,"weight":.05,"volumeRatio":.01,"skew":4,"strictKernelM":3e-6,"minTetQuality":1e-30},
            "candidates":rows,"limitations":["No native checkMesh or whole target acceptance","Coupled interfaces absent in this affected stencil","External cells with unrelated existing failures are fixed, not falsely repaired","Changed internal faces are evaluated; only the original parent boundary faces and moments are preserved"]}
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({name:row["summary"] for name,row in rows.items()},indent=2))
if __name__=="__main__":main()
