#!/usr/bin/env python3
"""Exact binary-coordinate oracle for two physical triangles' flat quad union.

Only checks the literal exterior-facet operation. Cell closure, complete outside
stencil, stock quality and original passing-role lineage require separate audits.
No mesh coordinates or topology are written.
"""
from __future__ import annotations
import argparse
from collections import Counter
from fractions import Fraction as F
import hashlib
import json
import math
from pathlib import Path


def audit(points, rows, owners):
    if len(rows) != 2 or len(owners) != 2:
        raise ValueError('two original cell and facet roles required')
    if any(row['owner'] not in owners or row['neighbour'] is not None or row['patch'] is None
           for row in rows):
        raise ValueError('physical owner role required')
    for row in rows:
        if any(word in row['patch']['type'].lower() for word in ('cyclic','processor','mapped','ami')):
            raise ValueError('coupled role unsupported')
    if rows[0]['sourceBase'] < 0 or any(rows[0][key] != rows[1][key]
            for key in ('patch','sourceZone','sourceBase')):
        raise ValueError('exact same patch zone and source base required')
    loops=[list(row['pointIDs']) for row in rows]
    if any(len(loop)!=3 or len(set(loop))!=3 for loop in loops):
        raise ValueError('two original triangles required')
    ids=set(loops[0]) | set(loops[1])
    if len(ids)!=4:
        raise ValueError('four original outline nodes required')
    xyz={v:tuple(float(x) for x in points[str(v)]) for v in ids}
    if any(len(p)!=3 or any(not math.isfinite(x) for x in p) for p in xyz.values()):
        raise ValueError('finite binary coordinates required')
    axes=[a for a in range(3) if len({p[a] for p in xyz.values()})==1]
    if not axes:
        raise ValueError('exact common coordinate-axis support required')
    axis=axes[0];uv=[a for a in range(3) if a!=axis]
    q={v:tuple(F.from_float(xyz[v][a]) for a in uv) for v in ids}
    directed=Counter((a,b) for loop in loops for a,b in zip(loop,loop[1:]+loop[:1]))
    shared=[]
    for a,b in list(directed):
        if a<b and directed[(a,b)] and directed[(b,a)]:
            if directed[(a,b)]!=1 or directed[(b,a)]!=1:
                raise ValueError('repeated common edge')
            shared.append((a,b));del directed[(a,b)];del directed[(b,a)]
    if len(shared)!=1 or len(directed)!=4 or any(value!=1 for value in directed.values()):
        raise ValueError('one opposite common edge required')
    successor={};incoming=Counter()
    for a,b in directed:
        if a in successor:
            raise ValueError('branching outline')
        successor[a]=b;incoming[b]+=1
    if any(value!=1 for value in incoming.values()):
        raise ValueError('branching outline')
    outline=[loops[0][0]]
    for _ in range(3):
        nxt=successor[outline[-1]]
        if nxt in outline:
            raise ValueError('premature outline cycle')
        outline.append(nxt)
    if successor[outline[-1]]!=outline[0] or set(outline)!=ids:
        raise ValueError('all four original nodes must remain')
    def moments(loop):
        area2=F(0);moment6=[F(0),F(0)]
        for a,b in zip(loop,loop[1:]+loop[:1]):
            x,y=q[a];u,v=q[b];cross=x*v-u*y
            area2+=cross;moment6[0]+=(x+u)*cross;moment6[1]+=(y+v)*cross
        return area2,moment6
    area2,moment6=moments(outline)
    if not area2:
        raise ValueError('degenerate outline')
    sign=1 if area2>0 else -1
    for j,a in enumerate(outline):
        b=outline[(j+1)%4];x,y=q[a];u,v=q[b]
        for k,c in enumerate(outline):
            if k in (j,(j+1)%4):continue
            z,w=q[c]
            if sign*((u-x)*(w-y)-(v-y)*(z-x))<=0:
                raise ValueError('outline must be strictly convex without lost collinear nodes')
    before=[moments(loop) for loop in loops]
    if any(sign*row[0]<=0 for row in before):
        raise ValueError('positive non-overlapping original triangle orientation required')
    if sum(row[0] for row in before)!=area2 or any(sum(row[1][a] for row in before)!=moment6[a] for a in range(2)):
        raise ValueError('exact signed area and first moment conservation required')
    # Opposite common edge + strict convex outline places each positive triangle
    # on a different side of their shared diagonal: no coverage overlap or gap.
    return {'scope':'exact physical facet union only; no full cell/checkMesh authorization',
        'exactAxis':axis,'exactBinaryPlane':repr(next(iter(xyz.values()))[axis]),
        'outlineOriginalPointIDs':outline,'allOriginalNodesRetained':True,
        'sharedOppositeEdge':list(shared[0]),'sourceBase':rows[0]['sourceBase'],
        'physicalRole':rows[0]['patch'],'sourceZone':rows[0]['sourceZone'],
        'signedAreaTwice':str(area2),'signedFirstMomentSixTimes':[str(x) for x in moment6],
        'exactSignedAreaAndMomentPreserved':True,'strictConvexOutline':True,
        'positiveDisjointTriangleCoverage':True,'pass':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--faces',nargs=2,type=int,required=True)
    parser.add_argument('--owners',nargs=2,type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();body=args.input.read_bytes();raw=json.loads(body)
    result=audit(raw['points'],[raw['faces'][str(f)] for f in args.faces],args.owners)
    result['inputSha256']=hashlib.sha256(body).hexdigest();result['sourceSha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'pass':True,'scope':result['scope']}))

if __name__=='__main__':main()
