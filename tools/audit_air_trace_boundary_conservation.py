#!/usr/bin/env python3
"""Read-only source coordinate, STL, signed boundary volume/moment and veto audit.

A point-prefix check certifies stored original coordinates, not all late writer
aliases. Complete stock checkMesh and actual coupled pairing remain separate.
Signed fan integrals use the original outline and do not use the absolute
OpenFOAM face-centroid fan as an independent physical oracle.
"""
from __future__ import annotations
import argparse,collections,gzip,hashlib,json,math
from pathlib import Path
import numpy as np

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def boundary_moments(points,offsets,vertices,faces,reference):
    faces=np.asarray(faces,dtype=np.int64)
    counts=(offsets[faces+1]-offsets[faces]-2).astype(np.int64)
    if np.any(counts<1):raise ValueError('a boundary polygon has fewer than three vertices')
    rep=np.repeat(faces,counts)
    starts=np.repeat(np.r_[0,np.cumsum(counts)[:-1]],counts)
    j=np.arange(len(rep))-starts+1
    base=offsets[rep].astype(np.int64)
    p0=points[vertices[base]]-reference
    p1=points[vertices[base+j]]-reference
    p2=points[vertices[base+j+1]]-reference
    if not all(np.isfinite(x).all() for x in (p0,p1,p2)):raise ValueError('non-finite original boundary coordinates')
    volume=np.einsum('ij,ij->i',p0,np.cross(p1,p2))/6
    moment=volume[:,None]*(p0+p1+p2)/4
    if not np.isfinite(volume).all() or not np.isfinite(moment).all():raise ValueError('non-finite signed boundary integral')
    total=math.fsum(map(float,volume))
    first=np.array([math.fsum(map(float,moment[:,d])) for d in range(3)])+total*reference
    return total,first

def audit(old,new,ranks,output):
    result={'scope':'source-only immutable STL bytes, complete original coordinate prefix and signed exterior boundary volume/moment; not mesh quality authorization',
      'oldMesh':str(old),'newMesh':str(new),'toolSha256':sha(Path(__file__)),'stls':{},'ranks':[],'vetoReasons':collections.Counter(),'vetoRows':[]}
    a={p.name:p for p in (old/'constant/triSurface').glob('*') if p.is_file()}
    b={p.name:p for p in (new/'constant/triSurface').glob('*') if p.is_file()}
    if a.keys()!=b.keys():raise ValueError('STL input set changed')
    for name,p in a.items():result['stls'][name]={'oldSha256':sha(p),'newSha256':sha(b[name]),'byteExact':p.read_bytes()==b[name].read_bytes()}
    reference=None;totals={label:{'V':0.,'M':np.zeros(3),'nativeV':0.} for label in ['old','new']}
    for rank in range(ranks):
        arrays=[];row={'rank':rank}
        for label,mesh in [('old',old),('new',new)]:
            prefix=mesh/f'conforming-source-rank{rank}-'
            def path(suffix):return Path(str(prefix)+suffix)
            pts=np.memmap(path('points.f64'),dtype='<f8',mode='r').reshape(-1,3);arrays.append(pts)
            if reference is None:reference=(pts.min(axis=0)+pts.max(axis=0))*.5
            alive=np.memmap(path('cell-alive.u8'),dtype='u1',mode='r')
            vol=np.memmap(path('parent-volume.f64'),dtype='<f8',mode='r')
            if len(alive)!=len(vol):raise ValueError('native source-volume layout mismatch')
            native_v=math.fsum(map(float,vol[alive!=0]));totals[label]['nativeV']+=native_v
            fa=np.memmap(path('face-alive.u8'),dtype='u1',mode='r')
            own=np.memmap(path('owner.i32'),dtype='<i4',mode='r');nei=np.memmap(path('neighbour.i32'),dtype='<i4',mode='r')
            offsets=np.memmap(path('face-offsets.u64'),dtype='<u8',mode='r');vertices=np.memmap(path('face-points.i32'),dtype='<i4',mode='r')
            mask=(fa!=0)&(nei<0)&(own>=0);mask&=alive[np.maximum(own,0)]!=0;faces=np.flatnonzero(mask)
            boundary_v,moment=boundary_moments(pts,offsets,vertices,faces,reference)
            totals[label]['V']+=boundary_v;totals[label]['M']+=moment
            row[label]={'sourcePoints':len(pts),'sourceCells':len(alive),'aliveCells':int(np.count_nonzero(alive)),
              'sourceNativeVolumeSumM3':native_v,'signedBoundaryVolumeM3':boundary_v,'signedBoundaryFirstMomentM4':moment.tolist(),
              'boundaryFaces':len(faces),'sourceManifestSha256':sha(path('manifest.json')),'sourcePointsSha256':sha(path('points.f64'))}
        row['oldSourcePointPrefixExactlyUnchanged']=len(arrays[1])>=len(arrays[0]) and np.array_equal(arrays[0],arrays[1][:len(arrays[0])])
        rawpath=new/f'thin-current-star-rank{rank}.jsonl'
        if rawpath.exists():
            raw=rawpath.read_bytes();target=output.parent/(rawpath.name+'.gz');target.write_bytes(gzip.compress(raw,mtime=0))
            row['guardRaw']={'originalBytes':len(raw),'originalSha256':sha(rawpath),'stored':target.name,'storedSha256':sha(target)}
            rows=[json.loads(line) for line in raw.splitlines()];row['guardRows']=len(rows);row['guardVetoRows']=sum(x['globalVeto'] for x in rows)
            result['vetoReasons'].update(x['reason'] for x in rows if x['globalVeto'])
            result['vetoRows'].extend({'rank':rank,**x} for x in rows if x['globalVeto'])
        result['ranks'].append(row)
    for label in totals:totals[label]['M']=totals[label]['M'].tolist()
    result.update({'totals':totals,'referencePointM':reference.tolist(),
      'signedBoundaryRelativeVolumeChange':abs(totals['new']['V']-totals['old']['V'])/abs(totals['old']['V']),
      'signedBoundaryFirstMomentChangeM4':(np.array(totals['new']['M'])-np.array(totals['old']['M'])).tolist(),
      'vetoReasons':dict(result['vetoReasons']),
      'allOldSourcePointPrefixesExactlyUnchanged':all(x['oldSourcePointPrefixExactlyUnchanged'] for x in result['ranks']),
      'allOriginalSTLsByteExact':all(x['byteExact'] for x in result['stls'].values())})
    output.write_text(json.dumps(result,indent=2)+'\n')
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--old',type=Path,required=True);ap.add_argument('--new',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--ranks',type=int,default=8)
    args=ap.parse_args();result=audit(args.old,args.new,args.ranks,args.output)
    print(json.dumps({key:result[key] for key in ['allOldSourcePointPrefixesExactlyUnchanged','allOriginalSTLsByteExact','signedBoundaryRelativeVolumeChange','signedBoundaryFirstMomentChangeM4','vetoReasons']}))
if __name__=='__main__':main()
