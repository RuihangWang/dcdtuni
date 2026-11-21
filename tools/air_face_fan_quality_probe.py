#!/usr/bin/env python3
"""Read-only face fan candidates; no native mesh acceptance or mutation."""
from __future__ import annotations
import argparse,hashlib,json,math
from pathlib import Path
import numpy as np

def newell(points):
    p=np.asarray(points,dtype=float);q=np.roll(p,-1,axis=0)
    return .5*np.array([np.sum((p[:,1]-q[:,1])*(p[:,2]+q[:,2])),np.sum((p[:,2]-q[:,2])*(p[:,0]+q[:,0])),np.sum((p[:,0]-q[:,0])*(p[:,1]+q[:,1]))])

def triangle_quality(triangle):
    a,b,c=np.asarray(triangle,float);cross=np.cross(b-a,c-a)
    denominator=sum(float(np.dot(x,x)) for x in (b-a,c-b,a-c))
    return 2*math.sqrt(3)*float(np.linalg.norm(cross))/denominator if denominator>0 else 0.

def tet_mean_ratio(triangle,apex):
    t=np.array([*triangle,apex],float)
    volume=abs(float(np.dot(np.cross(t[1]-t[0],t[2]-t[0]),t[3]-t[0])))/6
    edge2=sum(float(np.dot(t[j]-t[i],t[j]-t[i])) for i in range(4) for j in range(i+1,4))
    return 12*(3*volume)**(2/3)/edge2 if volume>0 and edge2>0 else 0.

def candidate_fans(points,area_vector):
    p=np.asarray(points,float);av=np.asarray(area_vector,float);area=float(np.linalg.norm(av));normal=av/area
    results=[]
    for k in range(len(p)):
        pieces=[p[[k,(k+j)%len(p),(k+j+1)%len(p)]] for j in range(1,len(p)-1)]
        areas=[float(np.dot(np.cross(t[1]-t[0],t[2]-t[0]),normal))*.5 for t in pieces]
        if not all(math.isfinite(a) and a>1e-15*area and float(np.linalg.norm(newell(t)))>1e-12 for a,t in zip(areas,pieces)):continue
        qualities=[triangle_quality(t) for t in pieces]
        results.append({'anchor':k,'minimumTriangleQuality':min(qualities),'minimumAreaShare':min(areas)/area,'pieces':pieces})
    return results

def evaluate(row):
    fans=candidate_fans(row['points'],row['area'])
    if not fans:return {'face':row['face'],'eligibleFans':0}
    best=max(fans,key=lambda f:(f['minimumTriangleQuality'],f['minimumAreaShare']))
    old=np.asarray(row['pieces'],float)
    normal=np.asarray(row['area'],float);area=float(np.linalg.norm(normal));normal/=area
    old_areas=[float(np.dot(np.cross(t[1]-t[0],t[2]-t[0]),normal))*.5 for t in old]
    out={'face':row['face'],'owner':row['owner'],'neighbour':row['neighbour'],'patch':row['patch'],'eligibleFans':len(fans),'bestAnchor':best['anchor'],
        'currentMinimumTriangleQuality':min(map(triangle_quality,old)),'candidateMinimumTriangleQuality':best['minimumTriangleQuality'],
        'currentMinimumAreaShare':min(old_areas)/area,'candidateMinimumAreaShare':best['minimumAreaShare']}
    for side in ['owner','neighbour']:
        if side=='neighbour' and row['neighbour']<0:continue
        apex=row[side+'Apex']
        out[side+'CurrentMinimumTetMeanRatio']=min(tet_mean_ratio(t,apex) for t in old)
        out[side+'CandidateMinimumTetMeanRatio']=min(tet_mean_ratio(t,apex) for t in best['pieces'])
    return out

def main():
    a=argparse.ArgumentParser();a.add_argument('--witness-dir',type=Path,required=True);a.add_argument('--output',type=Path,required=True);args=a.parse_args()
    rows=[];sha={}
    for p in sorted(args.witness_dir.glob('conforming-cone-partition-rank*.json')):
        sha[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
        for row in json.loads(p.read_text()):rows.append({'rank':int(p.stem.split('rank')[-1]),**evaluate(row)})
    valid=[r for r in rows if r['eligibleFans']]
    out={'format':'canonical-face-fan-quality-candidates/v1','scope':'Read-only candidate shape metrics on exported signed-moment witnesses. Triangle quality and tetrahedron mean ratio are shape proxies, not OpenFOAM checker certification; no geometry is written. This subset does not cover every partitioned base or every failed cell.',
         'sourceWitnessSHA256':sha,'toolSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'faces':len(rows),'eligibleFaces':len(valid),'improvedTriangleQuality':sum(r['candidateMinimumTriangleQuality']>r['currentMinimumTriangleQuality']+1e-14 for r in valid),
         'areaShareImproved':sum(r['candidateMinimumAreaShare']>r['currentMinimumAreaShare']+1e-14 for r in valid),
         'areaShareWorse':sum(r['candidateMinimumAreaShare']<r['currentMinimumAreaShare']-1e-14 for r in valid),'rows':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:out[k] for k in ['faces','eligibleFaces','improvedTriangleQuality','areaShareImproved','areaShareWorse']}))
if __name__=='__main__':main()
