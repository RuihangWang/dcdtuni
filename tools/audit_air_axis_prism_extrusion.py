#!/usr/bin/env python3
"""One frozen actual axis-prism extrusion and bounded cut-frontier audit.

This is an offline candidate tool. It preserves exact binary input coordinates,
actual outside-cell identities and source facet roles. It cannot authorize a
mesh; actual stock face-centre concavity failures are reported even when D/W/R/
skew/nonorthogonality improve. Exact rational intersections create new points;
no existing point is snapped, removed or shifted.
"""
from pathlib import Path
from fractions import Fraction as F
from collections import Counter,defaultdict
import argparse,hashlib,sys,json,numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import audit_air_thin_source_migration as M
import audit_air_cone_remaining_quality as Q

def rational(point):return tuple(F.from_float(float(x)) for x in point)
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def dot(a,b):return sum((x*y for x,y in zip(a,b)),F())
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def area(poly):
    p=poly[0]
    return tuple(sum((cross(sub(poly[k],p),sub(poly[k+1],p))[j] for k in range(1,len(poly)-1)),F())/2 for j in range(3))
def canon(poly):
    return min(tuple(q[k:]+q[:k]) for q in (poly,poly[::-1]) for k in range(len(q)))
def orient(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def clean(poly):
    out=[]
    for x in poly:
        if not out or x!=out[-1]:out.append(x)
    if len(out)>1 and out[0]==out[-1]:out.pop()
    return out
def clip(poly,n,origin):
    output=[];old=poly[-1];do=dot(n,sub(old,origin))
    for point in poly:
        dn=dot(n,sub(point,origin))
        if (do<=0)!=(dn<=0):
            frac=do/(do-dn)
            output.append(tuple(a+frac*(b-a) for a,b in zip(old,point)))
        if dn<=0:output.append(point)
        old,do=point,dn
    return clean(output)
def node_edges(poly,allpoints):
    out=[]
    for a,b in zip(poly,poly[1:]+poly[:1]):
        delta=sub(b,a);length=dot(delta,delta)
        if not length:raise ValueError("zero edge")
        nodes=[]
        for v in allpoints:
            d=sub(v,a)
            if cross(d,delta)==(0,0,0):
                t=dot(d,delta)/length
                if 0<=t<1:nodes.append((t,v))
        out.extend(v for _,v in sorted(nodes))
    return clean(out)
def merge(polys):
    points={v for p in polys for v in p};edges=Counter()
    for p in polys:
        p=node_edges(p,points)
        edges.update(zip(p,p[1:]+p[:1]))
    boundary=[]
    for (a,b),n in edges.items():
        if n>1 or edges[(b,a)]>1:raise ValueError("duplicate oriented edge")
        if not edges[(b,a)]:boundary.append((a,b))
    outgoing=defaultdict(list);incoming=Counter()
    for a,b in boundary:outgoing[a].append(b);incoming[b]+=1
    if not outgoing or any(len(v)!=1 for v in outgoing.values()) or any(incoming[v]!=1 for v in outgoing):
        raise ValueError("fragment union needs single directed outline")
    first=min(outgoing);poly=[first];v=outgoing[first][0]
    while v!=first:
        if v in poly:raise ValueError("fragment union cycles")
        poly.append(v);v=outgoing[v][0]
    if len(poly)!=len(boundary):raise ValueError("fragment union disconnected")
    return poly

def simplify_cycle(poly):
    """Remove collinear vertices for an outline comparison only, never output."""
    out=list(poly)
    changed=True
    while changed and len(out)>3:
        changed=False
        for i in range(len(out)):
            a,b,c=out[i-1],out[i],out[(i+1)%len(out)]
            if cross(sub(b,a),sub(c,b))==(0,0,0) and dot(sub(b,a),sub(c,b))>=0:
                out.pop(i);changed=True;break
    return out

def closed(polys):
    edges=Counter((a,b) for p in polys for a,b in zip(p,p[1:]+p[:1]))
    return bool(edges) and all(a!=b and n==1 and edges[(b,a)]==1 for (a,b),n in edges.items())

def coupled(role):
    return bool(role and str(role["type"]).startswith(("processor","cyclic")))

def validate(doc):
    if doc.get("schema")!="frozen-actual-axis-prism-stencil/v1":
        raise ValueError("frozen actual prism stencil required")
    source=doc["source"];points=np.asarray(source["points"],float)
    if not np.isfinite(points).all() or len(points)<6 or len({rational(p) for p in points})!=len(points):
        raise ValueError("finite unique original source points required")
    polys=[f["vertices"] for f in source["faces"]]
    if not M.directed_closed(polys):raise ValueError("positive directed source shell required")
    classification=M.classify_prism(points,polys)
    if not classification.get("exactCoordinateAxisPrism"):
        raise ValueError("original source is unsupported by exact prism gates")
    if classification["axis"]!=source["classification"]["axis"]:
        raise ValueError("declared axis differs from reconstructed source")
    geo=M.G.Geometry.from_arrays(points,polys);cc,vol=geo.cell(0)[3:5]
    if vol<=0 or abs(vol/source["volume"]-1)>1e-10:
        raise ValueError("source volume mismatch")
    if np.max(abs(vol*cc-source["volume"]*np.asarray(source["centre"])))>1e-10*vol*max(1,np.linalg.norm(cc)):
        raise ValueError("source first moment mismatch")
    originals={f["face"]:f for f in source["faces"]}
    if len(originals)!=len(source["faces"]):raise ValueError("duplicate source face")
    if any(f["zone"]>=0 or coupled(dict(type=f["role"][1]) if f["role"] else None) for f in source["faces"]):
        raise ValueError("protected zone/coupled source role")
    bybase=defaultdict(list);outside={int(o["cell"]):o for o in doc["outside"]}
    if len(outside)!=len(doc["outside"]):raise ValueError("duplicate outside cell")
    if len({f["face"] for f in doc["externalFaces"]})!=len(doc["externalFaces"]):
        raise ValueError("duplicate actual exterior face")
    for f in doc["externalFaces"]:
        base=originals.get(f["sourceBase"])
        if base is None or (f["sourcePatch"],f["sourceZone"])!=(base["patch"],base["zone"]):
            raise ValueError("source facet patch/zone identity mismatch")
        role=base["role"];expected=None if role is None else dict(name=role[0],type=role[1])
        if f["patch"]!=expected:raise ValueError("source facet physical name/type mismatch")
        poly=list(map(rational,f["coordinates"]))
        if len(poly)<3 or area(poly)==(0,0,0):raise ValueError("nonpositive actual exterior facet")
        sourcepoly=[rational(points[j]) for j in base["vertices"]]
        n=area(sourcepoly)
        if dot(area(poly),n)<=0 or any(dot(n,sub(q,sourcepoly[0]))!=0 for q in poly):
            raise ValueError("actual exterior facet left original support plane/orientation")
        bybase[f["sourceBase"]].append(poly)
        if f["outside"]>=0:
            old=outside.get(f["outside"])
            if old is None:raise ValueError("actual outside cell missing")
            matches=[x for x in old["faces"] if x["face"]==f["face"]]
            if len(matches)!=1 or canon(poly)!=canon(list(map(rational,matches[0]["coordinates"]))):
                raise ValueError("actual outside reverse interface missing")
            if area(list(map(rational,matches[0]["coordinates"])))!=tuple(-x for x in area(poly)):
                raise ValueError("actual outside interface orientation mismatch")
    if set(bybase)!=set(originals):raise ValueError("incomplete source exterior")
    for base,polygons in bybase.items():
        outline=merge(polygons)
        sourcepoly=[rational(points[j]) for j in originals[base]["vertices"]]
        if canon(simplify_cycle(outline))!=canon(simplify_cycle(sourcepoly)):
            raise ValueError("actual exterior does not cover original facet outline")
    for old in outside.values():
        ps=[list(map(rational,f["coordinates"])) for f in old["faces"]]
        allp={v for q in ps for v in q}
        if not closed([node_edges(q,allp) for q in ps]):raise ValueError("incomplete outside-cell shell")
    for old in list(outside.values())+doc["children"]:
        fs=[dict(poly=list(map(rational,f["coordinates"])),other=f["other"] if f["other"]>=0 else None,patch=f["patch"]) for f in old["faces"]]
        gg,stats=standalone(fs);vv=stats["volume"];ccc=np.asarray(stats["centre"])
        if vv<=0 or abs(vv/old["volume"]-1)>1e-10 or np.max(abs(vv*ccc-old["volume"]*np.asarray(old["centre"])))>1e-10*vv*max(1,np.linalg.norm(ccc)):
            raise ValueError("actual written cell V/M1 mismatch")
    if any(x["volume"]<=0 for x in doc["children"]) or abs(sum(x["volume"] for x in doc["children"])/vol-1)>1e-10:
        raise ValueError("original written child volume coverage mismatch")
    familymoment=sum((x["volume"]*np.asarray(x["centre"]) for x in doc["children"]),np.zeros(3))
    if np.max(abs(familymoment-vol*cc))>1e-10*vol*max(1,np.linalg.norm(cc)):
        raise ValueError("original written child first moment coverage mismatch")
    return classification

def standalone(faces):
    points=sorted({p for f in faces for p in f["poly"]});index={p:k for k,p in enumerate(points)}
    geo=M.G.Geometry.from_arrays(np.asarray(points,dtype=float),[[index[p] for p in f["poly"]] for f in faces])
    c=geo.cell(0);selected=[j for j,f in enumerate(faces) if f["other"] is not None or (f.get("patch") and f["patch"]["type"].startswith(("processor","cyclic")))]
    det=Q.determinant(c[1][selected])[0]
    return geo,dict(volume=float(c[4]),D=det,selectedFaces=len(selected),**geo.classify(0,True))

def construct(doc):
    classification=validate(doc)
    source=doc["source"];axis=classification["axis"];dims=[(axis+1)%3,(axis+2)%3]
    sourcepoints=list(map(rational,source["points"]));lo=min(p[axis] for p in sourcepoints);hi=max(p[axis] for p in sourcepoints)
    planes=sorted({p[axis] for p in sourcepoints})
    if len(planes)>4:raise ValueError("only bounded existing-axis-seam slabs")
    if any((b-a)/(hi-lo)<F(1,50) for a,b in zip(planes,planes[1:])):raise ValueError("source seam minShare")
    cap=[f for f in doc["externalFaces"] if all(rational(p)[axis]==lo for p in f["coordinates"])]
    twin=[f for f in doc["externalFaces"] if all(rational(p)[axis]==hi for p in f["coordinates"])]
    def triangles(fs):
        result=[]
        for f in fs:
            p=[tuple(rational(x)[d] for d in dims) for x in f["coordinates"]]
            if len(p)!=3:raise ValueError("minimal candidate keeps actual triangular cap partition")
            if orient(*p)<0:p=p[::-1]
            if orient(*p)<=0:raise ValueError("positive original cap triangle required")
            result.append(p)
        return sorted(result,key=canon)
    tris=triangles(cap)
    if {canon(p) for p in tris}!={canon(p) for p in triangles(twin)}:raise ValueError("actual two-cap triangulations differ")
    ideals=[]
    for slab,(left,right) in enumerate(zip(planes,planes[1:])):
        for tri in tris:
            pts=[]
            for x in (left,right):
                for q in tri:
                    p=[F(0)]*3;p[axis]=x
                    for d,v in zip(dims,q):p[d]=v
                    pts.append(tuple(p))
            polys=[pts[:3][::-1],pts[3:]]+[[pts[k],pts[(k+1)%3],pts[(k+1)%3+3],pts[k+3]] for k in range(3)]
            ideals.append(dict(id="new:"+str(len(ideals)),slab=slab,tri=tri,polys=polys,
                               halfspaces=[(area(p),p[0]) for p in polys]))
    faces={cell["id"]:[] for cell in ideals};exterior=[]
    for cell in ideals:
        groups=defaultdict(list)
        for f in doc["externalFaces"]:
            poly=list(map(rational,f["coordinates"]))
            for normal,p0 in cell["halfspaces"]:
                if len(poly)<3:break
                poly=clip(poly,normal,p0)
            if len(poly)<3 or area(poly)==(0,0,0):continue
            # A source role and exact actual outside cell are immutable.
            key=(f["sourceBase"],f["sourcePatch"],f["sourceZone"],f["outside"],
                 None if not f["patch"] else (f["patch"]["name"],f["patch"]["type"]))
            groups[key].append((poly,f["face"]))
        for key,group in groups.items():
            poly=merge([p for p,_ in group]);base,patch,zone,other,role=key
            face=dict(poly=poly,other=other if other>=0 else None,patch=dict(name=role[0],type=role[1]) if role else None,
                      sourceBase=base,sourcePatch=patch,sourceZone=zone,oldFaces=sorted({f for _,f in group}),
                      owner=cell["id"])
            faces[cell["id"]].append(face);exterior.append(face)
    pairs=defaultdict(list)
    for cell in ideals:
        for poly in cell["polys"]:pairs[canon(poly)].append((cell["id"],poly))
    internal=[]
    for group in pairs.values():
        if len(group)>2:raise ValueError("nonmanifold ideal decomposition")
        if len(group)==2:
            (a,pa),(b,pb)=group
            faces[a].append(dict(poly=pa,other=b,patch=None,owner=a))
            faces[b].append(dict(poly=pb,other=a,patch=None,owner=b))
            internal.append(dict(poly=pa,owner=a,other=b))
    outside={}
    for old in doc["outside"]:
        replacing={f["face"] for f in doc["externalFaces"] if f["outside"]==old["cell"]}
        rows=[dict(poly=list(map(rational,f["coordinates"])),other=f["other"] if f["other"]>=0 else None,
                   patch=f["patch"],oldFaces=[f["face"]],otherCentre=f["otherCentre"],otherVolume=f["otherVolume"])
              for f in old["faces"] if f["face"] not in replacing]
        rows += [dict(**{k:v for k,v in f.items() if k not in ("owner","other","poly")},
                      poly=f["poly"][::-1],other=f["owner"]) for f in exterior if f["other"]==old["cell"]]
        outside[old["cell"]]=rows
    # One common point set on both sides. Outside-other interfaces may gain edge
    # nodes; those unmodelled neighbors need propagation and are reported below.
    allpoints={p for rows in list(faces.values())+list(outside.values()) for f in rows for p in f["poly"]}
    for rows in list(faces.values())+list(outside.values()):
        for f in rows:f["poly"]=node_edges(f["poly"],allpoints)
    for f in internal:
        matching=[q for q in faces[f["owner"]] if q["other"]==f["other"] and canon(simplify_cycle(q["poly"]))==canon(simplify_cycle(f["poly"]))]
        if len(matching)!=1:raise ValueError("internal candidate interface missing")
        f["poly"]=matching[0]["poly"]
    return faces,outside,exterior,internal,dict(axis=axis,slabs=len(planes)-1,triangles=len(tris),
        existingAxisPlanes=[float(p) for p in planes],canonicalCapTriangles=[[[float(v) for v in q] for q in tri] for tri in tris])

def analyze(doc):
    faces,outside,external,internal,recipe=construct(doc)
    combined={**faces,**outside};pairs=[]
    for c,rows in combined.items():
        for f in rows:
            other=f["other"]
            if other not in combined:continue
            matches=[q for q in combined[other] if q["other"]==c and canon(q["poly"])==canon(f["poly"])]
            if len(matches)!=1 or area(matches[0]["poly"])!=tuple(-x for x in area(f["poly"])):
                raise ValueError("modeled two-sided interface mismatch")
            pairs.append((str(c),str(other),canon(f["poly"])))
    if len(set(pairs))!=len(pairs):raise ValueError("duplicate modeled interface")
    unmodeled=[]
    for old in doc["outside"]:
        for f in outside[old["cell"]]:
            if f["other"] not in combined and len(f.get("oldFaces",[]))==1:
                original=next(q for q in old["faces"] if q["face"]==f["oldFaces"][0])
                oldp=list(map(rational,original["coordinates"]))
                if canon(oldp)!=canon(f["poly"]):
                    unmodeled.append(dict(cell=old["cell"],face=original["face"],
                        outsideUnmodeledCell=f["other"],oldVertices=len(oldp),newVertices=len(f["poly"])))
    geometries={};results={}
    for cell,fs in {**faces,**outside}.items():
        geometries[cell],results[cell]=standalone(fs)
    oldvolume=sum(x["volume"] for x in doc["children"])
    oldmoment=sum((x["volume"]*np.asarray(x["centre"]) for x in doc["children"]),np.zeros(3))
    newvolume=sum(results[c]["volume"] for c in faces)
    newmoment=sum((results[c]["volume"]*np.asarray(results[c]["centre"]) for c in faces),np.zeros(3))
    preserved=sorted({rational(p) for p in doc["source"]["points"]})
    used={p for fs in faces.values() for f in fs for p in f["poly"]}
    outside_rows=[]
    for old in doc["outside"]:
        c=old["cell"];r=results[c]
        oldfs=[dict(poly=list(map(rational,f["coordinates"])),other=f["other"] if f["other"]>=0 else None,patch=f["patch"]) for f in old["faces"]]
        _,before=standalone(oldfs)
        outside_rows.append(dict(cell=c,oldD=before["D"],newD=r["D"],
            oldConcavity=before["checkerTriggerCounts"],newConcavity=r["checkerTriggerCounts"],
            volumeChange=r["volume"]-old["volume"],
            momentChange=(r["volume"]*np.asarray(r["centre"])-old["volume"]*np.asarray(old["centre"])).tolist()))
    qualities=[]
    for f in external+internal:
        sf,fc,*_=M.G.Geometry.from_arrays(np.asarray(f["poly"],dtype=float),[list(range(len(f["poly"])))]).face(0)
        own=results[f["owner"]];other=results.get(f["other"])
        q=Q.face_metrics(sf,fc,own["centre"],own["volume"],np.asarray(f["poly"],dtype=float),
                         other["centre"] if other else None,other["volume"] if other else None)
        if other:
            delta=np.asarray(other["centre"])-own["centre"]
            q["nonorthogonality"]=float(np.degrees(np.arccos(np.clip(sf@delta/(np.linalg.norm(sf)*np.linalg.norm(delta)),-1,1))))
        qualities.append(dict(owner=f["owner"],other=f["other"],sourceBase=f.get("sourceBase"),sourcePatch=f.get("sourcePatch"),sourceZone=f.get("sourceZone"),**q))
    ext_proofs=[]
    for base in sorted({f["sourceBase"] for f in external}):
        old=[f for f in doc["externalFaces"] if f["sourceBase"]==base]
        new=[f for f in external if f["sourceBase"]==base]
        def moments(polys):
            areas=[];mom=[]
            for poly in polys:
                sf,fc,*_=M.G.Geometry.from_arrays(np.asarray(poly,dtype=float),[list(range(len(poly)))]).face(0)
                areas.append(sf);mom.append(np.outer(sf,fc))
            return np.sum(areas,axis=0),np.sum(mom,axis=0)
        ar,mo=moments([f["coordinates"] for f in old]);nr,nm=moments([f["poly"] for f in new])
        ext_proofs.append(dict(sourceBase=base,areaVectorMaxAbsError=float(np.max(abs(ar-nr))),areaMomentMaxAbsError=float(np.max(abs(mo-nm))),sourceRoleSet=sorted({(f["sourcePatch"],f["sourceZone"]) for f in new}),outsideActualCellSet=sorted({f["other"] for f in new if f["other"] is not None}),oldOutsideActualCellSet=sorted({f["outside"] for f in old if f["outside"]>=0})))
    def dump(fs):
        return [{**{k:v for k,v in f.items() if k!="poly"},"coordinates":[list(map(float,p)) for p in f["poly"]]} for f in fs]
    newfail=sum(sum(results[c]["checkerTriggerCounts"].values())>0 for c in faces)
    outsidefail=[r["cell"] for r in outside_rows if not sum(r["oldConcavity"].values()) and sum(r["newConcavity"].values())]
    return dict(schema="actual-axis-prism-extrusion-probe/v1",rank=doc["rank"],parent=doc["parent"],recipe=recipe,
        candidateCells=len(faces),sourceNodesAllRetained=all(p in used for p in preserved),
        originalSourceNodes=len(preserved),newPointCount=len(used-set(preserved)),
        volumeChangeAgainstWrittenFamily=newvolume-oldvolume,volumeChangeAgainstSource=newvolume-doc["source"]["volume"],
        firstMomentMaxAbsErrorAgainstWrittenFamily=float(np.max(abs(newmoment-oldmoment))),
        exteriorFacetConservation=ext_proofs,newCells=[dict(id=c,geometry=results[c],faces=dump(fs)) for c,fs in faces.items()],
        outsideChanges=outside_rows,faceQuality=qualities,
        minimumD=min(results[c]["D"] for c in faces),
        candidateConcavityFailures=newfail,
        outsideNewConcavityFailures=outsidefail,
        modeledInterfacePairs=len(pairs)//2,modeledInterfacePairsExact=True,
        unmodeledNeighborInterfacesWithInsertedNodes=unmodeled,
        acceptance=False,verdict="REJECTED_OFFLINE_STOCK_FACE_CENTRE" if newfail or outsidefail else "OFFLINE_CANDIDATE_REQUIRES_GLOBAL_CHECK",
        scope="Offline one-parent candidate. Original coordinates/roles and actual outside IDs retained. No merge across different neighbors. Modeled interfaces are reverse-exact; unmodelled neighbor interfaces with new edge nodes require propagation. Stock face-centre diagnostics remain part of the verdict. No native/checkMesh run or authorization.")
def audit_frontier(doc):
    """One bounded source-parent frontier. Does not recurse or assert closure."""
    if doc.get("schema")!="frozen-bounded-prism-frontier/v1":
        raise ValueError("frozen bounded source frontier required")
    axis=int(doc["axis"]);plane=F.from_float(float(doc["plane"]));members=doc["parents"]
    if axis not in range(3) or not 1<=len(members)<=5:raise ValueError("bounded 1..5 parent audit required")
    ids={int(x["parent"]) for x in members}
    if len(ids)!=len(members):raise ValueError("duplicate bounded source parent")
    rows=[];frontier=set();protected=[]
    for source in members:
        points=np.asarray(source["points"],float);polys=[f["vertices"] for f in source["faces"]]
        if not np.isfinite(points).all() or not M.directed_closed(polys):
            raise ValueError("bounded original source shell is not finite directed closed")
        g=M.G.Geometry.from_arrays(points,polys);raw=g.cell(0);cc,V=raw[3:5]
        if V<=0:raise ValueError("bounded source shell volume must be positive")
        names={}
        if not {f["face"] for f in source["faces"]}.issubset({f["face"] for f in source["vertexStarFaces"]}):
            raise ValueError("original source faces missing from full vertex-star snapshot")
        for f in source["vertexStarFaces"]:
            if f["patch"]>=0 and f["role"] is None:raise ValueError("unresolved source vertex-star role")
            if not set(f["nativePointIDs"]) & set(source["nativePointIDs"]):
                raise ValueError("unrelated face in whole original vertex-star snapshot")
            if f["zone"]>=0 or f["role"] and str(f["role"][1]).startswith(("processor","cyclic")):
                names[f["face"]]=f;protected.append(dict(parent=source["parent"],**f))
        crossing=[]
        for f in source["faces"]:
            xs=[F.from_float(float(points[v,axis])) for v in f["vertices"]]
            if min(xs)<plane<max(xs):
                other=f["neighbour"] if f["owner"]==source["parent"] else f["owner"]
                if other>=0 and other not in ids:frontier.add(other)
                crossing.append(dict(face=f["face"],outsideSourceParent=other,role=f["role"],patch=f["patch"],zone=f["zone"]))
        t=dict(points=points,patch=np.array([f["patch"] for f in source["faces"]]),
               zone=np.array([f["zone"] for f in source["faces"]]))
        isbox=M.axis_box(t,np.arange(len(polys)),polys,g,raw)
        rows.append(dict(parent=source["parent"],axisBox=isbox,volume=float(V),centroid=cc.tolist(),
            span=(raw[7]-raw[6]).tolist(),positiveDirectedClosed=True,sourceFaces=len(polys),
            protectedVertexStarFaces=list(names.values()),crossedSourceFaces=crossing))
    return dict(schema="bounded-single-prism-cut-propagation-frontier/v1",rank=doc["rank"],axis=axis,
        originalPlane=float(plane),initialSourceParents=sorted(ids),rows=rows,
        frontierOutsideTheseFive=sorted(frontier),isClosedWithinTheseFive=not frontier,
        protectedRoleVetoRequired=bool(protected),protectedRoles=protected,acceptance=False,
        scope="Only the focal source parent and at most four direct neighbors examined. Current-source full vertex-star role snapshot; no preGhost cellRank inference. No frontier recursion, CPP, mesh run or acceptance.")

def repo_path(path):
    resolved=Path(path).resolve()
    repo=ROOT
    while repo.parent!=repo and not (repo/".git").is_dir():repo=repo.parent
    if not (repo/".git").is_dir() or not resolved.is_relative_to(repo):
        raise ValueError("input/output must remain inside dcdtuni repository")
    return resolved

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--frontier",action="store_true")
    args=parser.parse_args()
    source=repo_path(args.input);target=repo_path(args.output)
    if source==target:raise ValueError("preserve original frozen input bytes")
    doc=json.loads(source.read_text())
    out=audit_frontier(doc) if args.frontier else analyze(doc)
    out["inputIdentity"]=dict(path=str(source),bytes=source.stat().st_size,sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(out,indent=2,allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in out.items() if k not in ("newCells","faceQuality","exteriorFacetConservation","outsideChanges","rows")},indent=2))

if __name__=="__main__":main()
