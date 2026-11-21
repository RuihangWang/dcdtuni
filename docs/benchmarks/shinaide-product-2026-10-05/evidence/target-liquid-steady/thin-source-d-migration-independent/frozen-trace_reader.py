#!/usr/bin/env python3
"""Read-only exact cone provenance and strict original-parent kernel probe."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,sys
from pathlib import Path
import numpy as np

def load_geometry(path):
    sys.path.insert(0,str(path.parent));spec=importlib.util.spec_from_file_location('trace_geometry_oracle',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def read_trace(mesh,rank):
    source=mesh/f'conforming-source-rank{rank}-';written=mesh/f'conforming-written-rank{rank}-'
    sm=json.loads(Path(str(source)+'manifest.json').read_text());wm=json.loads(Path(str(written)+'manifest.json').read_text())
    if sm.get('format')!='conforming-input-stage/v1' or wm.get('format')!='conforming-written-stage/v1' or sm.get('rank')!=rank or wm.get('rank')!=rank:raise ValueError('Unsupported or mismatched trace identity')
    if not sm['littleEndian'] or sm['labelBytes']!=4 or sm['scalarBytes']!=8 or sm['pointStrideBytes']!=24 or wm['labelBytes']!=4:raise ValueError('Unsupported declared trace storage layout')
    def array(prefix,suffix,dtype,shape):
        path=Path(str(prefix)+suffix);expected=int(np.prod(shape))*np.dtype(dtype).itemsize
        if path.stat().st_size!=expected:raise ValueError(f'Trace size mismatch: {path}')
        return np.memmap(path,mode='r',dtype=dtype,shape=shape)
    return {'sourceManifest':sm,'writtenManifest':wm,
        'points':array(source,'points.f64','<f8',(sm['points'],3)),
        'offsets':array(source,'face-offsets.u64','<u8',(sm['faces']+1,)),
        'vertices':array(source,'face-points.i32','<i4',(sm['facePointLabels'],)),
        'owner':array(source,'owner.i32','<i4',(sm['faces'],)),
        'neighbour':array(source,'neighbour.i32','<i4',(sm['faces'],)),
        'patch':array(source,'patch.i32','<i4',(sm['faces'],)),
        'zone':array(source,'zone.i32','<i4',(sm['faces'],)),
        'faceAlive':array(source,'face-alive.u8','u1',(sm['faces'],)),
        'cellAlive':array(source,'cell-alive.u8','u1',(sm['cells'],)),
        'apex':array(source,'apex.f64','<f8',(sm['cells'],3)),
        'volume':array(source,'parent-volume.f64','<f8',(sm['cells'],)),
        'cellParent':array(written,'cell-parent.i32','<i4',(wm['cells'],)),
        'cellNative':array(written,'cell-native.i32','<i4',(wm['cells'],)),
        'faceBase':array(written,'face-base.i32','<i4',(wm['faces'],)),
        'faceNative':array(written,'face-native.i32','<i4',(wm['faces'],)),
        'pointNative':array(written,'point-native.i32','<i4',(wm['points'],))}

def axis_kernel_centre(faces):
    """Derive the axis-plane kernel; never substitute the outer vertex bbox."""
    lo=np.full(3,-np.inf);hi=np.full(3,np.inf)
    for face in faces:
        area=np.asarray(face['outwardArea'],float);centre=np.asarray(face['OFcentre'],float)
        points=np.asarray(face['coordinates'],float);amount=np.linalg.norm(area)
        if not np.isfinite(amount) or amount<=0 or not np.isfinite(centre).all() or not np.isfinite(points).all():return None
        d=int(np.argmax(np.abs(area)))
        if any(abs(area[j])>1e-12*amount for j in range(3) if j!=d):return None
        scale=max(np.linalg.norm(points-centre,axis=1).max(),1e-6)
        if np.max(np.abs(points[:,d]-centre[d]))>1e-10*scale:return None
        if area[d]>0:hi[d]=min(hi[d],centre[d])
        else:lo[d]=max(lo[d],centre[d])
    if not np.isfinite(lo).all() or not np.isfinite(hi).all() or np.any(lo>=hi):return None
    point=(lo+hi)/2
    margins=[np.dot(np.asarray(f['outwardArea']),np.asarray(f['OFcentre'])-point)/np.linalg.norm(f['outwardArea']) for f in faces]
    if min(margins)<=3e-6:return None
    return {'lo':lo.tolist(),'hi':hi.tolist(),'point':point.tolist(),'minimumOriginalPlaneMarginM':float(min(margins)),'strictAt3Micrometres':True}

def input_parent(trace,parent,geometry):
    if parent<0 or parent>=trace['sourceManifest']['cells'] or not trace['cellAlive'][parent]:raise ValueError('Written child does not map to an alive input-stage parent')
    faces=np.flatnonzero((trace['faceAlive']!=0)&((trace['owner']==parent)|(trace['neighbour']==parent)))
    polygons=[]
    for f in faces:
        ids=trace['vertices'][trace['offsets'][f]:trace['offsets'][f+1]].tolist()
        polygons.append(ids if trace['owner'][f]==parent else ids[::-1])
    g=geometry.Geometry.from_arrays(trace['points'],polygons)
    _,areas,centres,cc,volume,ids,lo,hi=g.cell(0)
    normal=areas/np.linalg.norm(areas,axis=1)[:,None];distance=np.einsum('ij,ij->i',normal,centres)
    apex=np.asarray(trace['apex'][parent]);margin=distance-normal@apex
    out={'inputParent':int(parent),'inputFaces':faces.tolist(),'inputVertexIds':ids.tolist(),'inputApex':apex.tolist(),
         'inputVolume':float(trace['volume'][parent]),'recomputedOriginalVolume':volume,'relativeVolumeError':abs(volume-float(trace['volume'][parent]))/float(trace['volume'][parent]),
         'minimumOriginalPlaneMarginM':float(margin.min()),'bbox':[lo.tolist(),hi.tolist()],
         'originalFaces':[{'face':int(f),'patch':int(trace['patch'][f]),'zone':int(trace['zone'][f]),'coordinates':g.points[g.ids(i)].tolist(),'outwardArea':areas[i].tolist(),'OFcentre':centres[i].tolist()} for i,f in enumerate(faces)]}
    out['derivedAxisKernelCentre']=axis_kernel_centre(out['originalFaces'])
    try:
        from scipy.optimize import linprog
        lp=linprog([0.,0.,0.,-1.],A_ub=np.c_[normal,np.ones(len(normal))],b_ub=distance,bounds=[(None,None)]*3+[(0.,None)],method='highs')
        out['chebyshevKernelCandidate']={'success':bool(lp.success),'status':int(lp.status),'message':lp.message}
        if lp.success:
            point=lp.x[:3];m=distance-normal@point
            out['chebyshevKernelCandidate'].update({'point':point.tolist(),'radiusM':float(lp.x[3]),'minimumPlaneMarginM':float(m.min()),'allMarginsM':m.tolist(),'strictAt3Micrometres':bool(m.min()>3e-6)})
    except ImportError:out['chebyshevKernelCandidate']={'available':False}
    return out

def main():
    a=argparse.ArgumentParser();a.add_argument('--mesh',type=Path,required=True);a.add_argument('--geometry-tool',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--cell',action='append',default=[]);a.add_argument('--face',action='append',default=[]);args=a.parse_args()
    geometry=load_geometry(args.geometry_tool);traces={};parents={};rows=[]
    for kind,items in [('cell',args.cell),('face',args.face)]:
        for key in items:
            rank,label=map(int,key.split(':'))
            if rank not in traces:traces[rank]=read_trace(args.mesh,rank)
            t=traces[rank]
            limit=t['writtenManifest']['cells' if kind=='cell' else 'faces']
            if not 0<=label<limit:raise ValueError('Written sample label is outside the trace map')
            row={'rank':rank,'writtenKind':kind,'writtenLabel':label}
            if kind=='cell':
                parent=int(t['cellParent'][label]);row.update({'nativeCell':int(t['cellNative'][label]),'inputParent':parent})
                if (rank,parent) not in parents:parents[(rank,parent)]=input_parent(t,parent,geometry)
            else:row.update({'nativeFace':int(t['faceNative'][label]),'inputBaseFace':int(t['faceBase'][label])})
            rows.append(row)
    out={'format':'air-cone-exact-provenance-probe/v1','scope':'Read-only actual compact writer maps and input-stage geometry. The Chebyshev point is a strict plane-kernel candidate only; no new mesh is generated and no full checker acceptance is inferred.',
         'mesh':str(args.mesh),'toolSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'geometryToolSHA256':hashlib.sha256(args.geometry_tool.read_bytes()).hexdigest(),
         'samples':rows,'parents':[{'rank':rank,**row} for (rank,parent),row in parents.items()],
         'manifests':{str(rank):{'source':t['sourceManifest'],'written':t['writtenManifest']} for rank,t in traces.items()}}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({'samples':rows,'parents':[{'rank':r,'parent':p,'minMargin':d['minimumOriginalPlaneMarginM'],'kernel':d['chebyshevKernelCandidate']} for (r,p),d in parents.items()]}))
if __name__=='__main__':main()
