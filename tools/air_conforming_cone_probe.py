#!/usr/bin/env python3
"""Diagnostic mixed cone/fan closure. Never edits a native or user mesh."""
from __future__ import annotations
import argparse,importlib.util,json,sys,hashlib
from pathlib import Path
import numpy as np

def load(path,name):
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def canonical_cycle(ids,points):
    # Coordinate order is independent of vertex labels, rotations and reversal.
    rows=[]
    for direction in (list(ids),list(reversed(ids))):
        for k in range(len(direction)):
            cycle=direction[k:]+direction[:k]
            rows.append((tuple(tuple(points[i]) for i in cycle),cycle))
    return min(rows,key=lambda item:item[0])[1]

def redundant_edge(ids,points):
    for j,b in enumerate(ids):
        a=points[ids[j-1]];c=points[ids[(j+1)%len(ids)]];q=points[b]
        x=a-q;y=c-q;scale=np.linalg.norm(x)*np.linalg.norm(y)
        if scale>0 and np.dot(x,y)<0 and np.linalg.norm(np.cross(x,y))<=1e-12*scale:return True
    return False

def positive_vertex_fan(poly,points,normal,area):
    for k in range(len(poly)):
        pieces=[[poly[k],poly[(k+j)%len(poly)],poly[(k+j+1)%len(poly)]] for j in range(1,len(poly)-1)]
        if all(float(np.dot(np.cross(np.array(points[b])-points[a],np.array(points[c])-points[a]),normal))*.5>1e-15*area for a,b,c in pieces):return pieces
    return None

def mixed_partition(g,helper,seeds,prefer_anchor=False):
    # Narrow fixture: convex planar polygons, strict kernel point. Unsupported
    # polygons are rejected; no fallback infers a centroid is always interior.
    points=g.points.tolist();lookup={tuple(p):i for i,p in enumerate(points)}
    def vertex(point):
        key=tuple(float(v) for v in point)
        if key not in lookup:lookup[key]=len(points);points.append(list(key))
        return lookup[key]
    faces=[g.ids(f).tolist() for f in range(g.n_faces)]
    split={f for f,ids in enumerate(faces) if redundant_edge(ids,g.points)}
    marked=set(seeds);waves=0
    while True:
        add=set()
        for f in split:
            o=int(g.owner[f]);n=int(g.neighbour[f]) if f<len(g.neighbour) else -1
            if o in marked or n in marked:
                add.add(o)
                if n>=0:add.add(n)
        new=add-marked
        if not new:break
        marked|=new;waves+=1
    pieces={};split_source=[]
    for f,ids in enumerate(faces):
        if int(g.owner[f]) not in marked and (f>=len(g.neighbour) or int(g.neighbour[f]) not in marked):continue
        area,fc,plane,turn,span,support=g.face(f)
        if plane>1e-12*max(span,1.) or support < -1e-12*max(span,1.):raise ValueError('Unsupported nonplanar/nonconvex base')
        canonical=canonical_cycle(ids,g.points)
        # Canonical evaluation fixes exact centre arithmetic across both sides.
        local=helper.Geometry.from_arrays(g.points,[canonical])
        centre=local.face(0)[1]
        oriented=canonical
        if np.dot(local.face(0)[0],area)<0:oriented=list(reversed(canonical))
        if f in split:
            av=local.face(0)[0];normal=av/np.linalg.norm(av)
            chosen=positive_vertex_fan(canonical,points,normal,np.linalg.norm(av)) if prefer_anchor else None
            q=None
            if chosen is None:
                q=vertex(centre)
                chosen=[[a,b,q] for a,b in zip(canonical,canonical[1:]+canonical[:1])]
            pieces[f]=[tri[::-1] for tri in chosen] if np.dot(av,area)<0 else chosen
            split_source.append({'face':f,'canonicalCoordinates':[g.points[i].tolist() for i in canonical],
                'centrePoint':q,'fanMode':'original-vertex' if q is None else 'face-centre','pieces':pieces[f],
                'owner':int(g.owner[f]),'neighbour':int(g.neighbour[f]) if f<len(g.neighbour) else None})
        else:pieces[f]=[ids]
    reports=[];parents=[];apices=[];volumes=[]
    for c in range(g.n_cells):
        fs,areas,centres,cc,vol,*_=g.cell(c)
        if c not in marked:
            reports.append({'faces':[{'vertexIds':faces[int(f)] if g.owner[f]==c else faces[int(f)][::-1],
                 'points':[points[i] for i in (faces[int(f)] if g.owner[f]==c else faces[int(f)][::-1])]} for f in fs]})
            parents.append(c);continue
        margin=min(float(np.dot(a,fc-cc))/np.linalg.norm(a) for a,fc in zip(areas,centres))
        if not np.isfinite(margin) or margin<=3e-6:raise ValueError('Apex is not strictly inside all base halfspaces')
        ap=vertex(cc);apices.append({'parent':c,'point':ap,'coordinates':cc.tolist(),'minimumBaseInnerMarginM':margin})
        for f in fs:
            f=int(f)
            for poly in pieces[f]:
                base=list(poly) if g.owner[f]==c else list(reversed(poly))
                child=[base]+[[b,a,ap] for a,b in zip(base,base[1:]+base[:1])]
                reports.append({'faces':[{'vertexIds':ids,'points':[points[i] for i in ids]} for ids in child]});parents.append(c)
    result=helper.assemble(reports)
    for c in marked:
        before=g.cell(c)[4];after=sum(result.cell(i)[4] for i,p in enumerate(parents) if p==c)
        volumes.append({'parent':c,'before':before,'after':after,'relativeError':abs(after-before)/before})
    return result,{'seedCells':sorted(seeds),'markedCells':sorted(marked),'closureWaves':waves,'splitSourceFaces':split_source,
           'apices':apices,'parentVolumeConservation':volumes,'outputParentCell':parents,
           'allOriginalCoordinatesUnchanged':np.array_equal(result.points[:len(g.points)],g.points)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--helper',type=Path,required=True);p.add_argument('--air-tool',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--fixture',type=Path,required=True);p.add_argument('--prefer-anchor',action='store_true');args=p.parse_args()
    helper=load(args.helper,'independent_roof_helper');air=load(args.air_tool,'original_roof_probe')
    audit=args.helper.with_name('audit_air_mesh_geometry.py')
    g,original=helper.evaluate_buffer(air,audit,0.)
    seeds={c for c in range(g.n_cells) if g.classify(c)['checkerTriggerCounts']['coplanar']>0}
    new,proof=mixed_partition(g,helper,seeds,args.prefer_anchor)
    row=helper.audit_assembled(new,0.)
    determinants=[]
    for c in range(new.n_cells):
        areas=[new.face(int(f))[0] for f in new.cell_faces[new.cell_offsets[c]:new.cell_offsets[c+1]] if f<len(new.neighbour)]
        avg=sum(np.linalg.norm(a) for a in areas)/len(areas)
        tensor=sum(np.outer(a/avg,a/avg) for a in areas)
        determinants.append(float(abs(np.linalg.det(tensor))/8))
    row['minimumInternalAreaTensorDeterminant']=min(determinants)
    def boundary_area(mesh):
        return sum(float(np.linalg.norm(mesh.face(f)[0])) for f in range(len(mesh.neighbour),mesh.n_faces))
    proof['boundaryAreaBeforeM2']=boundary_area(g)
    proof['boundaryAreaAfterM2']=boundary_area(new)
    proof['boundaryAreaRelativeError']=abs(proof['boundaryAreaAfterM2']-proof['boundaryAreaBeforeM2'])/proof['boundaryAreaBeforeM2']
    helper.write_fixture(new,args.fixture)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    out={'format':'air-conforming-mixed-cone-diagnostic/v1','scope':'54-cell analytic buffered Cartesian transition. All original coordinates unchanged. No native/user-case mesh acceptance.',
         'sourceSha256':{str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [Path(__file__),args.helper,args.air_tool,audit]},
         'before':{k:original[k] for k in ['cells','points','faces','totalVolumeM3','allCellsStrictlyInsideFaceCentreCriterion']},
         'construction':proof,'preferOriginalVertexFan':args.prefer_anchor,'after':row}
    args.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:row[k] for k in ['points','faces','totalVolumeM3','allOppositeEdgeCyclesClosed','allFacesPlanar','allFacesConvex',
      'allCellsStrictlyInsideFaceCentreCriterion','minimumTetFanQuality','minimumInternalWeight','minimumInternalVolumeRatio','minimumInternalAreaTensorDeterminant']}))
if __name__=='__main__':main()
