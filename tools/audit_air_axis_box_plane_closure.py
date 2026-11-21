#!/usr/bin/env python3
"""Read-only axis-box interior-plane counting and bounded neighbor closure.

All input nodes remain distinct and unchanged. Ideal tensor cells are only a
metric proposal; source-boundary equivalence and full native quality are not
inferred. A truncated or blocked propagation is not an accepted closure.
"""
from __future__ import annotations
import argparse,collections,hashlib,itertools,json,math
from pathlib import Path
import numpy as np
from air_cone_trace_probe import read_trace,load_geometry

def interior_planes(points,lo,hi,clearance=3e-6):
    planes=[];excluded=[]
    for axis in range(3):
        for x in np.unique(points[:,axis]):
            if not lo[axis]<x<hi[axis]:continue
            if min(x-lo[axis],hi[axis]-x)<clearance:
                excluded.append({'axis':axis,'coordinate':float(x),'distanceToOuterBound':float(min(x-lo[axis],hi[axis]-x))})
            else:planes.append((axis,float(x)))
    return planes,excluded

def selected_box_d(widths,excluded=()):
    a,b,c=map(float,widths)
    if min(a,b,c)<=0 or not np.isfinite([a,b,c]).all():raise ValueError('Invalid ideal box widths')
    sf=np.array([[-b*c,0,0],[b*c,0,0],[0,-a*c,0],[0,a*c,0],[0,0,-a*b],[0,0,a*b]])
    mask=np.ones(6,bool);mask[list(excluded)]=False;sf=sf[mask]
    if not len(sf):raise ValueError('No selected area vector')
    avg=np.linalg.norm(sf,axis=1).mean()
    return float(abs(np.linalg.det((sf/avg).T@(sf/avg)))/8)

def bounded_walk(start,neighbors,classify,limit):
    if limit<1:raise ValueError('Positive closure limit required')
    seen=set();queued={start};stack=[start];blocked=[];edges=[];cap=False;omitted=[]
    while stack:
        cell=stack.pop();queued.remove(cell);seen.add(cell)
        reason=classify(cell)
        if reason:
            blocked.append({'cell':cell,**reason});continue
        for face,other in neighbors(cell):
            edges.append((cell,face,other))
            if other in seen or other in queued:continue
            if len(seen)+len(queued)>=limit:
                cap=True;omitted.append({'fromCell':cell,'face':face,'toCell':other});continue
            queued.add(other);stack.append(other)
        if cap:break
    return {'visitedCellIds':sorted(seen),'queuedCellIdsAtStop':sorted(queued),'uniqueDiscoveredCells':len(seen|queued),'limitReached':cap,'omittedDiscoveryWitnesses':omitted[:4],
      'sharedFacesCrossed':len(edges),'blockerCounts':dict(collections.Counter(r['reason'] for r in blocked)),'blockerWitnesses':blocked[:12],
      'closedWithoutBlockersOrTruncation':not blocked and not cap and not queued}

def source_index(t):
    live=np.flatnonzero(t['faceAlive']!=0);labs=np.concatenate((t['owner'][live],t['neighbour'][live]));faces=np.tile(live,2)
    ok=(labs>=0)&(labs<t['sourceManifest']['cells']);order=np.argsort(labs[ok],kind='stable')
    t['cf']=faces[ok][order];t['co']=np.r_[0,np.cumsum(np.bincount(labs[ok],minlength=t['sourceManifest']['cells']))]
    return t

def box_info(t,parent,G):
    if parent<0 or parent>=len(t['cellAlive']) or not t['cellAlive'][parent]:return None
    fs=t['cf'][t['co'][parent]:t['co'][parent+1]];polys=[]
    for f in fs:
        p=t['vertices'][t['offsets'][f]:t['offsets'][f+1]].tolist();polys.append(p if t['owner'][f]==parent else p[::-1])
    g=G.Geometry.from_arrays(t['points'],polys);_,sf,fc,cc,V,ids,lo,hi=g.cell(0);span=hi-lo
    tol=1e-12*max(span);xyz=t['points'][ids];directed=collections.Counter();okay=True
    for i,poly in enumerate(polys):
        amount=np.linalg.norm(sf[i]);axis=int(np.argmax(abs(sf[i])));plane=hi[axis] if sf[i,axis]>0 else lo[axis]
        if not np.isfinite(sf[i]).all() or amount<=0 or any(abs(sf[i,j])>1e-12*abs(sf[i,axis]) for j in range(3) if j!=axis):okay=False
        if max(abs(t['points'][poly,axis]-plane))>tol or max((xyz-fc[i])@sf[i])>tol*amount or g.face(i)[5]<-tol:okay=False
        directed.update(zip(poly,poly[1:]+poly[:1]))
    closed=all(n==1 and directed[b,a]==1 for (a,b),n in directed.items());bv=float(np.prod(span))
    isbox=okay and closed and V>0 and bv>0 and abs(V-bv)<=1e-10*bv
    return {'fs':fs,'polys':polys,'ids':ids,'lo':lo,'hi':hi,'V':V,'box':bool(isbox),'sf':sf,'fc':fc}

def audit(mesh,rank,parent,G,limit):
    t=source_index(read_trace(mesh,rank));wg=G.Geometry(mesh/f'processor{rank}/constant/polyMesh');types={}
    for start,count,name,kind in wg.patches:
        bases=t['faceBase'][start:start+count];bases=bases[bases>=0]
        for patch in np.unique(t['patch'][bases]):
            if patch>=0:
                if int(patch) in types and types[int(patch)]!=kind:raise ValueError('Ambiguous realized source patch kind')
                types[int(patch)]=kind
    protectedFace=(t['faceAlive']!=0)&((t['zone']>=0)|np.isin(t['patch'],[p for p,k in types.items() if k.startswith(('processor','cyclic','mapped')) or 'AMI' in k]))
    protectingFace=np.full(len(t['points']),-1,dtype=np.int32)
    for f in np.flatnonzero(protectedFace):
        ids=t['vertices'][t['offsets'][f]:t['offsets'][f+1]];newids=ids[protectingFace[ids]<0];protectingFace[newids]=f
    cache={}
    def info(c):
        if c not in cache:cache[c]=box_info(t,c,G)
        return cache[c]
    seed=info(parent)
    if seed is None or not seed['box']:raise ValueError('Seed source is not an alive axis box under the declared audit gate')
    planes,excluded=interior_planes(t['points'][seed['ids']],seed['lo'],seed['hi']);physical=[]
    for i,f in enumerate(seed['fs']):
        if t['patch'][f]>=0 and not types[int(t['patch'][f])].startswith(('processor','cyclic')):
            sf=seed['sf'][i];axis=int(np.argmax(abs(sf)));side=0 if sf[axis]<0 else 1
            physical.append({'face':int(f),'axis':axis,'side':side,'kind':types[int(t['patch'][f])],'area':float(np.linalg.norm(sf)),'sideArea':float(np.prod(np.delete(seed['hi']-seed['lo'],axis)))})
    intervals=[np.diff(np.r_[seed['lo'][a],[x for ax,x in planes if ax==a],seed['hi'][a]]) for a in range(3)];ideal=[]
    fullPhysical=all(abs(x['area']-x['sideArea'])<=1e-10*x['sideArea'] for x in physical)
    for indices in itertools.product(*[range(len(v)) for v in intervals]):
        widths=np.array([intervals[a][indices[a]] for a in range(3)]);omit=[]
        if fullPhysical:
            for f in physical:
                if (f['side']==0 and indices[f['axis']]==0) or (f['side']==1 and indices[f['axis']]==len(intervals[f['axis']])-1):omit.append(2*f['axis']+f['side'])
        ideal.append((selected_box_d(widths,omit),float(max(widths)/min(widths)),float(np.prod(widths))))
    rows=[]
    for axis,plane in planes:
        def classify(c):
            q=info(c)
            if q is None:return {'reason':'nonalive'}
            if not q['box']:return {'reason':'nonBoxSource'}
            hit=q['ids'][protectingFace[q['ids']]>=0]
            if len(hit):
                p=int(hit[0]);f=int(protectingFace[p]);return {'reason':'protectedPostGhostPointStar','point':p,'pointCoordinates':t['points'][p].tolist(),'protectingFace':f,'protectingPatch':int(t['patch'][f]),'protectingZone':int(t['zone'][f]),'protectingKind':types.get(int(t['patch'][f]))}
            vals=t['points'][q['ids'],axis];hit=np.flatnonzero((abs(vals-plane)>0)&(abs(vals-plane)<3e-6))
            if len(hit):
                p=int(q['ids'][hit[0]]);return {'reason':'unequalOriginalNodeNearPlane','point':p,'coordinate':float(vals[hit[0]]),'distanceM':float(abs(vals[hit[0]]-plane))}
            return None
        def neighbors(c):
            q=info(c)
            if not q['lo'][axis]<plane<q['hi'][axis]:return []
            edges=[]
            for f,poly in zip(q['fs'],q['polys']):
                v=t['points'][poly,axis]
                if v.min()<plane<v.max():
                    o=int(t['owner'][f]);n=int(t['neighbour'][f]);other=n if o==c else o
                    if other>=0:edges.append((int(f),other))
            return edges
        row={'axis':axis,'plane':plane,**bounded_walk(parent,neighbors,classify,limit)};rows.append(row)
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    return {'format':'axis-box-plane-closure-diagnostic/v1','scope':'Read-only bounded sourcebox proposal. Input coordinates unchanged. Interior clearance exclusion is not snapping or deletion. Source-box classification tolerances do not grant geometry equality. Ideal boxes do not prove original boundary/node equivalence, actual native performance or full mesh quality. Postghost trace cannot prove preghost remote cellRank protection. Any blocker/truncation is not accepted closure.',
      'mesh':str(mesh),'rank':rank,'parent':parent,'sourceManifestSha256':sha(mesh/f'conforming-source-rank{rank}-manifest.json'),'toolSha256':sha(Path(__file__)),
      'sourceBoxGate':{'normalRelative':1e-12,'planeRelative':1e-12,'bboxVolumeRelative':1e-10},'seedSourceFaces':len(seed['fs']),'seedSourceVertices':len(seed['ids']),
      'seedOriginalFaces':[{'face':int(f),'owner':int(t['owner'][f]),'neighbour':int(t['neighbour'][f]),'patch':int(t['patch'][f]),'zone':int(t['zone'][f]),'pointIds':poly,'coordinates':t['points'][poly].tolist()} for f,poly in zip(seed['fs'],seed['polys'])],
      'candidatePlanes':planes,'excludedNearOuterBoundCoordinates':excluded,'physicalFaces':physical,'idealTensorGrid':{'cells':len(ideal),'fullPhysicalSidesAreaMatched':fullPhysical,'minD':min(x[0] for x in ideal),'maxAspect':max(x[1] for x in ideal),'sumV':math.fsum(x[2] for x in ideal),'nativeBodyV':seed['V']},
      'closureLimitPerPlane':limit,'rows':rows,'uniqueSourceCellsInspected':len(cache),'closedPlanes':sum(x['closedWithoutBlockersOrTruncation'] for x in rows)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mesh',type=Path,required=True);ap.add_argument('--geometry-tool',type=Path,required=True);ap.add_argument('--rank',type=int,required=True);ap.add_argument('--parent',type=int,required=True);ap.add_argument('--limit',type=int,default=128);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    g=load_geometry(a.geometry_tool);doc=audit(a.mesh,a.rank,a.parent,g,a.limit);doc['geometryToolSha256']=hashlib.sha256(a.geometry_tool.read_bytes()).hexdigest();a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(doc,indent=2)+'\n')
    print(json.dumps({'idealTensorGrid':doc['idealTensorGrid'],'candidatePlanes':len(doc['candidatePlanes']),'closedPlanes':doc['closedPlanes'],'uniqueSourceCellsInspected':doc['uniqueSourceCellsInspected'],'truncatedPlanes':sum(x['limitReached'] for x in doc['rows'])}))
if __name__=='__main__':main()
