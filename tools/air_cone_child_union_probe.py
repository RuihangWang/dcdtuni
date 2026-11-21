#!/usr/bin/env python3
"""Read-only same-parent child-union candidates on exact written cone maps.

No native or mesh mutation. A candidate must retain original boundary vertices,
roles and zones. Geometry metrics never replace the complete stock checker.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np

def load(name, path):
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def loop_from_edges(edges):
    """One directed simple outline; exact label pairing, never coordinate weld."""
    remaining=Counter(edges)
    for a,b in list(remaining):
        n=min(remaining[(a,b)],remaining[(b,a)])
        remaining[(a,b)]-=n;remaining[(b,a)]-=n
    kept=[e for e,n in remaining.items() for _ in range(n)]
    if len(kept)<3 or any(n not in (0,1) for n in remaining.values()):return None
    nexts={};incoming=Counter()
    for a,b in kept:
        if a in nexts:return None
        nexts[a]=b;incoming[b]+=1
    if set(nexts)!=set(incoming) or any(n!=1 for n in incoming.values()):return None
    first=min(nexts);out=[first];p=nexts[first]
    while p!=first:
        if p in out:return None
        out.append(p);p=nexts[p]
    return out if len(out)==len(kept) else None

def merged_boundary(geo,cells,trace):
    cells=set(cells);rows=[]
    fs=sorted(set(int(f) for c in cells for f in geo.cell(c)[0]))
    for f in fs:
        own=int(geo.owner[f]);nei=int(geo.neighbour[f]) if f<len(geo.neighbour) else None
        if own in cells and nei in cells:continue
        ids=geo.ids(f).tolist()
        other=nei if own in cells else own
        if own not in cells:ids=list(reversed(ids))
        base=int(trace['faceBase'][f])
        zone=int(trace['zone'][base]) if base>=0 else -1
        rows.append({'faces':[f],'ids':ids,'patch':geo.patch(f),'other':other,'zone':zone})
    return rows

def coalesce(geo,rows,geometry):
    rows=[dict(r) for r in rows]
    merged=0
    while True:
        changed=False
        for i in range(len(rows)):
            if changed:break
            for j in range(i+1,len(rows)):
                a,b=rows[i],rows[j]
                if any(a[k]!=b[k] for k in ['patch','other','zone']):continue
                ga=geometry.Geometry.from_arrays(geo.points,[a['ids']]);gb=geometry.Geometry.from_arrays(geo.points,[b['ids']])
                sa,ca,*_=ga.face(0);sb,cb,*_=gb.face(0)
                ma,mb=np.linalg.norm(sa),np.linalg.norm(sb)
                if min(ma,mb)<=1e-12:continue
                normal=sa/ma;span=max(np.ptp(geo.points[a['ids']+b['ids']],axis=0).max(),1e-6)
                if np.dot(sa,sb)/(ma*mb)<1-1e-10 or abs(np.dot(normal,cb-ca))>1e-10*span:continue
                ea=list(zip(a['ids'],a['ids'][1:]+a['ids'][:1]));eb=list(zip(b['ids'],b['ids'][1:]+b['ids'][:1]))
                if not set(ea)&{(y,x) for x,y in eb}:continue
                ids=loop_from_edges(ea+eb)
                if ids is None:continue
                gg=geometry.Geometry.from_arrays(geo.points,[ids]);sg,cg,plane,_,_,support=gg.face(0)
                if not np.all(np.isfinite(cg)) or plane>1e-10*span or support<-1e-10*span:continue
                error=max(np.linalg.norm(sg-sa-sb)/(ma+mb),np.linalg.norm(cg-(ma*ca+mb*cb)/(ma+mb))/span)
                if not np.isfinite(error) or error>1e-10:continue
                rows[i]={**a,'ids':ids,'faces':a['faces']+b['faces']};rows.pop(j);merged+=1;changed=True;break
        if not changed:break
    return rows,merged

def metrics(geo,cells,trace,geometry,quality,join):
    rows=merged_boundary(geo,cells,trace)
    rows,njoins=coalesce(geo,rows,geometry) if join else (rows,0)
    candidate=geometry.Geometry.from_arrays(geo.points,[r['ids'] for r in rows])
    cls=candidate.classify(0,True)
    directed=Counter((r['ids'][j],r['ids'][(j+1)%len(r['ids'])]) for r in rows for j in range(len(r['ids'])))
    directed_closed=all(n==1 and directed[(b,a)]==1 for (a,b),n in directed.items())
    fs,areas,centres,cc,vol,*_=candidate.cell(0)
    selected=[a for r,a in zip(rows,areas) if r['patch'] is None or r['patch']['type'] in ['cyclic','processor','processorCyclic']]
    det,eigen,_=quality.determinant(selected)
    parentv=sum(geo.cell(int(c))[4] for c in cells)
    face_quality=[];remote_unknown=False
    for r,sf,fc in zip(rows,areas,centres):
        if r['patch'] and r['patch']['type'] in ['cyclic','processor','processorCyclic']:
            remote_unknown=True;continue
        other=r['other']
        nc=geo.cell(int(other)) if other is not None else None
        face_quality.append({'sourceFaces':r['faces'],'otherCell':other,'patch':r['patch'],
            **quality.face_metrics(sf,fc,cc,vol,geo.points[r['ids']],nc[3] if nc else None,nc[4] if nc else None)})
    ratio_error=abs(vol-parentv)/parentv
    finite=all(np.isfinite(r[k]) for r in face_quality for k in ['skewness','normalOwnerDistanceM']) and np.isfinite(det) and np.isfinite(vol) and vol>0 and np.isfinite(ratio_error)
    strict_geometry=cls['classification']=='convex-no-face-plane-trigger' and cls['boundaryEdgesPairExactly'] and directed_closed and ratio_error<=1e-10
    available=not remote_unknown
    limits=(det>=.001 and all(r['skewness']<=4 and r.get('weight',1)>=.05 and r.get('volumeRatio',1)>=.01 for r in face_quality))
    return {'cells':list(map(int,cells)),'coalescedFaces':njoins,'externalFaces':len(rows),'classification':cls['classification'],
        'checkerTriggerCounts':cls['checkerTriggerCounts'],'closure':cls['relativeClosure'],'volume':vol,'relativeVolumeError':ratio_error,
        'allVertexMaximumOutsideMetres':cls['allVertexMaximumOutsideMetres'],'allFacePolygonsConvex':cls['allFacePolygonsConvex'],
        'allFacePolygonsPlanar':cls['allFacePolygonsPlanar'],'edgesPairExactly':cls['boundaryEdgesPairExactly'],'directedEdgeClosure':directed_closed,
        'determinant':det,'eigenvalues':eigen,'minimumWeight':min((r.get('weight',1) for r in face_quality),default=1),
        'minimumVolumeRatio':min((r.get('volumeRatio',1) for r in face_quality),default=1),
        'maximumSkewness':max((r['skewness'] for r in face_quality),default=0),
        'remoteQualityUnresolved':remote_unknown,'passesAvailableLocalGeometryAndQuality':bool(strict_geometry and available and limits and finite),
        'faces':face_quality,'rawFaceSources':[r['faces'] for r in rows]}

def main():
    p=argparse.ArgumentParser();p.add_argument('--mesh',type=Path,required=True);p.add_argument('--integration-tools',type=Path,required=True)
    p.add_argument('--cell',action='append',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--max-group',type=int,default=2)
    args=p.parse_args()
    trace_module=load('child_union_trace',Path(__file__).with_name('air_cone_trace_probe.py'))
    geometry=load('child_union_geometry',args.integration_tools/'audit_air_mesh_geometry.py')
    quality=load('child_union_quality',args.integration_tools/'audit_air_cone_remaining_quality.py')
    ranks={};out=[]
    for key in args.cell:
        rank,c=map(int,key.split(':'))
        if rank not in ranks:ranks[rank]=(geometry.Geometry(args.mesh/f'processor{rank}/constant/polyMesh'),trace_module.read_trace(args.mesh,rank))
        geo,trace=ranks[rank];parent=int(trace['cellParent'][c]);children=np.flatnonzero(trace['cellParent']==parent).tolist()
        neighbors=set()
        for f in geo.cell(c)[0]:
            if f>=len(geo.neighbour):continue
            o=int(geo.owner[f]);n=int(geo.neighbour[f]);other=n if o==c else o
            if int(trace['cellParent'][other])==parent:neighbors.add(other)
        groups={tuple(sorted([c,n])) for n in neighbors}
        layer=set(groups)
        for size in range(3,args.max_group+1):
            next_layer=set()
            for group in layer:
                for child in group:
                    for f in geo.cell(child)[0]:
                        if f>=len(geo.neighbour):continue
                        o=int(geo.owner[f]);n=int(geo.neighbour[f]);other=n if o==child else o
                        if int(trace['cellParent'][other])!=parent or other in group:continue
                        next_layer.add(tuple(sorted((*group,other))))
            groups.update(next_layer);layer=next_layer
        trials=[{'coalesce':True,**metrics(geo,list(group),trace,geometry,quality,True)} for group in sorted(groups)]
        out.append({'rank':rank,'writtenCell':c,'inputParent':parent,'parentChildCount':len(children),'adjacentSameParentChildren':sorted(neighbors),'maxGroup':args.max_group,'trials':trials})
        print(json.dumps({'rank':rank,'cell':c,'parent':parent,'children':len(children),'unionTrials':len(trials),'localPasses':sum(r['passesAvailableLocalGeometryAndQuality'] for r in trials),'classes':dict(Counter(r['classification'] for r in trials))}),flush=True)
    result={'format':'same-parent-child-union-probe/v1','scope':'Read-only candidate union of actual children with exact trace ancestry. No cells or native inputs are changed; only available local metrics are evaluated, not a complete stock checkMesh acceptance.',
        'mesh':str(args.mesh),'toolSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'rows':out}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
