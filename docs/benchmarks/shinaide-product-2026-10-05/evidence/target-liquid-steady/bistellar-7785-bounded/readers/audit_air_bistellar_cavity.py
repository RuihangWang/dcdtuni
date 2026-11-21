#!/usr/bin/env python3
"""Read-only exact first-cavity preflight for a bistellar 2-to-3 candidate.

Original binary64 node coordinates are lifted exactly to rational numbers.
No new point, mesh write, geometry tolerance or quality authorization occurs.
"""
from __future__ import annotations
import argparse,collections,hashlib,json
from fractions import Fraction
from pathlib import Path
import numpy as np
from air_cone_trace_probe import read_trace,load_geometry

def exact_xyz(point):return tuple(Fraction(float(x)) for x in point)
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def determinant(a,b,c):return dot(a,cross(b,c))
def tet_volume(points):
    a,b,c,d=map(exact_xyz,points)
    return abs(determinant(sub(b,a),sub(c,a),sub(d,a)))/6

def preflight_23(triangle,opposites):
    A,B,C=map(exact_xyz,triangle);D,E=map(exact_xyz,opposites)
    de=sub(E,D);u=tuple(-x for x in sub(B,A));v=tuple(-x for x in sub(C,A));rhs=sub(A,D);den=determinant(de,u,v)
    if not den:return {'strictInterior':False,'reason':'exactSingular'}
    tau=determinant(rhs,u,v)/den;beta=determinant(de,rhs,v)/den;gamma=determinant(de,u,rhs)/den
    result={'exactIntersection':{'tau':str(tau),'beta':str(beta),'gamma':str(gamma),'remaining':str(1-beta-gamma)},
      'strictInterior':bool(0<tau<1 and beta>0 and gamma>0 and beta+gamma<1)}
    if not result['strictInterior']:result['reason']='newEdgeIntersectionNotStrictInterior';return result
    coords=[D,E,A,B,C];parts=[[0,1,2,3],[0,1,3,4],[0,1,4,2]]
    volumes=[];moment=np.array([Fraction(0)]*3,dtype=object)
    for ids in parts:
        a,b,c,d=[coords[i] for i in ids];V=abs(determinant(sub(b,a),sub(c,a),sub(d,a)))/6;volumes.append(V)
        moment+=V*np.array([sum(coords[i][axis] for i in ids)/4 for axis in range(3)],dtype=object)
    old=[(D,A,B,C),(E,A,B,C)];oldv=[];oldm=np.array([Fraction(0)]*3,dtype=object)
    for points in old:
        a,b,c,d=points;V=abs(determinant(sub(b,a),sub(c,a),sub(d,a)))/6;oldv.append(V)
        oldm+=V*np.array([sum(p[axis] for p in points)/4 for axis in range(3)],dtype=object)
    result.update(newVolumeExact=[str(v) for v in volumes],newVolumeM3=[float(v) for v in volumes],allNewVolumesPositive=all(v>0 for v in volumes),
      totalVolumeExactlyPreserved=sum(volumes)==sum(oldv),firstMomentExactlyPreserved=all(a==b for a,b in zip(moment,oldm)),
      oldTotalVolumeExact=str(sum(oldv)),oldFirstMomentExact=[str(x) for x in oldm],reason='exactLegalCavityPreflight')
    return result

def locate(mesh,rank,geometry,max_examine,only_parent=None):
    trace=read_trace(mesh,rank);g=geometry.Geometry(mesh/f'processor{rank}/constant/polyMesh')
    seeds=sorted(map(int,geometry.cell_set(g.poly_dir/'sets/underdeterminedCells')))
    if only_parent is not None:seeds=[c for c in seeds if trace['cellParent'][c]==only_parent]
    def tet_vertices(c):
        fs=g.cell(c)[0]
        if len(fs)!=4 or any(len(g.ids(int(f)))!=3 for f in fs):return None
        ids=set(int(p) for f in fs for p in g.ids(int(f)))
        return ids if len(ids)==4 else None
    examined=[];seen=set();selected=None;structural=0;truncated=False
    for c in seeds:
        cp=tet_vertices(c)
        if cp is None:continue
        for ff in g.cell(c)[0]:
            f=int(ff)
            if f>=len(g.neighbour):continue
            other=int(g.neighbour[f]) if g.owner[f]==c else int(g.owner[f]);pair=tuple(sorted((c,other)))
            if pair in seen:continue
            seen.add(pair);op=tet_vertices(other);base=int(trace['faceBase'][f])
            if op is None or base<0 or trace['zone'][base]>=0 or g.patch(f) is not None or trace['cellParent'][c]==trace['cellParent'][other] or len(cp&op)!=3:continue
            opposite=[next(iter(cp-op)),next(iter(op-cp))]
            if any(trace['pointNative'][p]<trace['sourceManifest']['points'] for p in opposite):continue
            structural+=1
            if len(examined)>=max_examine:truncated=True;break
            tri=g.ids(f).tolist();row={'cells':[c,other],'sourceParents':[int(trace['cellParent'][c]),int(trace['cellParent'][other])],
              'sharedFace':f,'sourceSharedBase':base,'sourceSharedZone':int(trace['zone'][base]),'sharedVertexIds':tri,'oppositeVertexIds':opposite,
              'sharedCoordinates':g.points[tri].tolist(),'oppositeCoordinates':g.points[opposite].tolist(),
              **preflight_23(g.points[tri],g.points[opposite])}
            if row['strictInterior']:
                group={c,other};shell=sorted({int(h) for p in group for h in g.cell(p)[0]}-{f});allids=sorted(cp|op);violations=[]
                for h in shell:
                    ids=g.ids(h).tolist();ids=ids if g.owner[h] in group else ids[::-1]
                    A,B,C=map(exact_xyz,g.points[ids]);n=cross(sub(B,A),sub(C,A))
                    for p in allids:
                        value=dot(n,sub(exact_xyz(g.points[p]),A))
                        if value>0:violations.append({'face':h,'point':p,'dotExact':str(value)})
                row['outerFaceIds']=shell;row['cavityConvexExact']=not violations;row['convexViolations']=violations
                row['outerFaces']=[{'face':h,'pointIDs':g.ids(h).tolist(),'coordinates':g.points[g.ids(h)].tolist(),
                  'owner':int(g.owner[h]),'neighbour':int(g.neighbour[h]) if h<len(g.neighbour) else None,'patch':g.patch(h),
                  'sourceBase':int(trace['faceBase'][h]),'sourceZone':int(trace['zone'][trace['faceBase'][h]]) if trace['faceBase'][h]>=0 else -1} for h in shell]
                coupled=[h for h in shell if g.patch(h) and g.patch(h)['type'] in {'cyclic','processor','processorCyclic'}]
                row['coupledExteriorFaces']=coupled
                row['newTetPointIDs']=[[opposite[0],opposite[1],tri[i],tri[(i+1)%3]] for i in range(3)]
                if violations:row['reason']='cavityNotConvexExact'
                elif coupled:row['reason']='coupledExteriorRemoteStencilUnsupported'
                elif not row['allNewVolumesPositive'] or not row['totalVolumeExactlyPreserved'] or not row['firstMomentExactlyPreserved']:row['reason']='exactPartitionConservationRefuse'
                else:selected=row
            examined.append(row)
            if selected:break
        if selected or truncated:break
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    return {'format':'bistellar-exact-first-cavity/v1','scope':'Only a bounded structural lookup and exact geometry partition preflight on unchanged original nodes; no mesh write, quality or native acceptance. All source-parent APs are actual written nodes. Original six exterior triangles/patch/zone/outside roles are recorded.','mesh':str(mesh),'rank':rank,'seedSourceParent':only_parent,'seedBadDCells':len(seeds),'maxExamined':max_examine,'structuralVisited':structural,'truncated':truncated,
      'sourceManifestSHA256':sha(mesh/f'conforming-source-rank{rank}-manifest.json'),'writtenManifestSHA256':sha(mesh/f'conforming-written-rank{rank}-manifest.json'),'toolSHA256':sha(Path(__file__)),
      'examinationCounts':dict(collections.Counter(x['reason'] for x in examined)),'examined':examined,'selected':selected}

def main():
    a=argparse.ArgumentParser();a.add_argument('--mesh',type=Path,required=True);a.add_argument('--geometry-tool',type=Path,required=True);a.add_argument('--rank',type=int,required=True);a.add_argument('--source-parent',type=int);a.add_argument('--max-examine',type=int,default=128);a.add_argument('--output',type=Path,required=True);args=a.parse_args()
    if args.max_examine<1:raise ValueError('Positive structural limit required')
    G=load_geometry(args.geometry_tool);doc=locate(args.mesh,args.rank,G,args.max_examine,args.source_parent);doc['geometryToolSHA256']=hashlib.sha256(args.geometry_tool.read_bytes()).hexdigest();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(doc,indent=2)+'\n')
    print(json.dumps({'seedBadD':doc['seedBadDCells'],'examined':len(doc['examined']),'truncated':doc['truncated'],'reasons':doc['examinationCounts'],'selectedCells':doc['selected']['cells'] if doc['selected'] else None}))
if __name__=='__main__':main()
