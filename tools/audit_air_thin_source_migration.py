#!/usr/bin/env python3
"""Offline source-shell and base-piece D migration; no mesh authorization.
Parent slots only nominate candidates. Complete oriented coordinatecycles/roles
and unique source-base pieces are required for migration. Axis-prism rejection
means unsupported by these sufficient gates, never impossible mesh geometry.
"""
from pathlib import Path
import argparse,collections,hashlib,importlib.util,json,struct,time
from fractions import Fraction
import numpy as np
import audit_air_mesh_geometry as G

def load_trace_reader(path):
    spec=importlib.util.spec_from_file_location("frozen_cone_trace",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.read_trace

def directed_closed(polys):
    edges=collections.Counter((int(a),int(b)) for poly in polys
                              for a,b in zip(poly,poly[1:]+poly[:1]))
    return bool(edges) and all(a!=b and n==1 and edges[(b,a)]==1
                              for (a,b),n in edges.items())

def facet_match(pts,patch,zone,old_faces,old_points,old_polys,
                old_patch,old_zone,centres,normals,tol):
    choices=[]
    for index,old_face in enumerate(old_faces):
        if int(old_patch[old_face])!=int(patch) or int(old_zone[old_face])!=int(zone):continue
        poly=old_points[old_polys[index]];normal=normals[index]
        if np.max(np.abs((pts-centres[index])@normal))>tol:continue
        if all(np.min(np.cross(b-a,pts-a)@normal)>=-tol*np.linalg.norm(b-a)
               for a,b in zip(poly,np.roll(poly,-1,axis=0))):
            choices.append(index)
    if len(choices)!=1:raise ValueError("one role-compatible original support facet required")
    return choices[0]

def cycle(coords):
 rows=[np.asarray(r,dtype='<f8').tobytes() for r in coords]
 return min(b''.join(rows[i:]+rows[:i]) for i in range(len(rows)))
def source_index(t):
 live=np.flatnonzero(t['faceAlive']!=0)
 labs=np.concatenate((t['owner'][live],t['neighbour'][live]));facs=np.tile(live,2)
 ok=(labs>=0)&(labs<t['sourceManifest']['cells'])
 order=np.argsort(labs[ok],kind='stable');t['cf']=facs[ok][order]
 t['co']=np.r_[0,np.cumsum(np.bincount(labs[ok],minlength=t['sourceManifest']['cells']))]
 return t
def parent_record(t,p,detail=False):
 if p<0 or p>=t['sourceManifest']['cells'] or not t['cellAlive'][p]:return {'alive':False,'parent':p}
 fs=t['cf'][t['co'][p]:t['co'][p+1]];keys=[];vertices=set();faces=[]
 for f in map(int,fs):
  ids=t['vertices'][t['offsets'][f]:t['offsets'][f+1]].tolist()
  if t['owner'][f]!=p:ids=ids[::-1]
  xyz=t['points'][ids];vertices.update(ids)
  keys.append(struct.pack('<iiI',int(t['patch'][f]),int(t['zone'][f]),len(ids))+cycle(xyz))
  if detail:faces.append(dict(face=f,patch=int(t['patch'][f]),zone=int(t['zone'][f]),outwardCoordinates=xyz.tolist()))
 coords=t['points'][sorted(vertices)]
 return dict(alive=True,parent=p,faces=len(fs),vertices=len(vertices),signature=hashlib.sha256(b''.join(struct.pack('<I',len(k))+k for k in sorted(keys))).hexdigest(),storedVolume=float(t['volume'][p]),apex=t['apex'][p].tolist(),bbox=[coords.min(0).tolist(),coords.max(0).tolist()],originalFaces=faces if detail else None)

def written_key(geo,t,c):
 fs=geo.cell_faces[geo.cell_offsets[c]:geo.cell_offsets[c+1]];keys=[]
 for f in map(int,fs):
  base=int(t['faceBase'][f])
  if base<0:continue
  ids=geo.ids(f)
  if int(geo.owner[f])!=c:ids=ids[::-1]
  xyz=geo.points[ids]
  keys.append(struct.pack('<iiI',int(t['patch'][base]),int(t['zone'][base]),len(ids))+cycle(xyz))
 if not keys:return None
 return hashlib.sha256(b''.join(struct.pack('<I',len(k))+k for k in sorted(keys))).hexdigest()
def selected_det(geo,c):
 fs=geo.cell_faces[geo.cell_offsets[c]:geo.cell_offsets[c+1]];sf=[]
 for f in map(int,fs):
  patch=geo.patch(f)
  if patch is None or patch['type'].startswith(('processor','cyclic')):sf.append(geo.face(f)[0])
 if not sf:return 0.,0
 a=np.asarray(sf);s=np.linalg.norm(a,axis=1).mean()
 return float(abs(np.linalg.det((a/s).T@(a/s)))/8),len(sf)
def realized_patch_roles(geo,t):
    roles={}
    for start,count,name,kind in geo.patches:
        bases=t["faceBase"][start:start+count];bases=bases[bases>=0]
        for patch in np.unique(t["patch"][bases]):
            if patch<0:continue
            role=(name,kind)
            if int(patch) in roles and roles[int(patch)]!=role:
                raise ValueError("ambiguous realized source-patch name/type namespace")
            roles[int(patch)]=role
    return roles

def audit_migration(old,new,read_trace,rank_count=8):
 rows=[];ranks=[];tally=collections.Counter()
 for rank in range(rank_count):
  start=time.monotonic();t0=source_index(read_trace(old,rank));t1=source_index(read_trace(new,rank))
  bad0=set(map(int,G.cell_set(old/f'processor{rank}/constant/polyMesh/sets/underdeterminedCells')))
  bad1=set(map(int,G.cell_set(new/f'processor{rank}/constant/polyMesh/sets/underdeterminedCells')))
  parents0=collections.Counter(int(t0['cellParent'][c]) for c in bad0)
  parents1=collections.Counter(int(t1['cellParent'][c]) for c in bad1)
  relevant=set(parents0)|set(parents1)
  geom0=G.Geometry(old/f'processor{rank}/constant/polyMesh');geom1=G.Geometry(new/f'processor{rank}/constant/polyMesh')
  roles0=realized_patch_roles(geom0,t0);roles1=realized_patch_roles(geom1,t1)
  if any(roles0[k]!=roles1[k] for k in roles0.keys()&roles1.keys()):raise ValueError('source patch role namespace changed')
  by0=np.argsort(t0['cellParent'],kind='stable');off0=np.r_[0,np.cumsum(np.bincount(t0['cellParent'],minlength=t0['sourceManifest']['cells']))]
  by1=np.argsort(t1['cellParent'],kind='stable');off1=np.r_[0,np.cumsum(np.bincount(t1['cellParent'],minlength=t1['sourceManifest']['cells']))]
  loc=collections.Counter()
  for p in sorted(relevant):
   a=parent_record(t0,p);b=parent_record(t1,p)
   ca=by0[off0[p]:off0[p+1]].tolist() if p<len(off0)-1 else []
   cb=by1[off1[p]:off1[p+1]].tolist() if p<len(off1)-1 else []
   record=dict(rank=rank,parentSlot=p,oldSource=a,newSource=b,oldBadCount=parents0.get(p,0),newBadCount=parents1.get(p,0),oldChildren=len(ca),newChildren=len(cb))
   if a.get('signature') and a.get('signature')==b.get('signature'):
    record['category']='sameRetainedSlotExactSourceGeometry'
    k0=collections.defaultdict(list);k1=collections.defaultdict(list)
    for c in ca:k0[written_key(geom0,t0,c)].append(c)
    for c in cb:k1[written_key(geom1,t1,c)].append(c)
    transitions=[]
    for k in set(k0)|set(k1):
     if k is None or len(k0[k])!=1 or len(k1[k])!=1:
      for c in k1[k]:
       if c in bad1:loc['unchangedParentUnmatchedOrAmbiguousBadPieces']+=1
      continue
     c0=k0[k][0];c1=k1[k][0]
     state=('bad' if c0 in bad0 else 'good')+'To'+('Bad' if c1 in bad1 else 'Good')
     if c0 in bad0 or c1 in bad1:
      d0,n0=selected_det(geom0,c0);d1,n1=selected_det(geom1,c1)
      transitions.append(dict(basePieceSignature=k,oldCell=c0,newCell=c1,state=state,oldD=d0,newD=d1,oldSelectedFaces=n0,newSelectedFaces=n1))
      loc[state]+=1
    record['pieceTransitions']=transitions
   elif p>=t0['sourceManifest']['cells']:record['category']='appendedSourceSlotUnknownAncestry'
   elif not a.get('alive'):record['category']='newAliveOrUnmappedRetainedSlotUnknownAncestry'
   else:record['category']='changedRetainedSourceGeometry'
   loc[record['category']+'NewBad']+=record['newBadCount'];loc[record['category']+'Parents']+=1
   rows.append(record)
  tally.update(loc);ranks.append(dict(rank=rank,oldBad=len(bad0),newBad=len(bad1),categories=dict(loc),sourceManifests=dict(old=t0['sourceManifest'],new=t1['sourceManifest']),realizedSharedPatchRoleCount=len(roles0.keys()&roles1.keys()),patchRoleNamespaceUnchanged=True))
  print(json.dumps(dict(rank=rank,oldBad=len(bad0),newBad=len(bad1),seconds=time.monotonic()-start,categories=dict(loc))),flush=True)
  del geom0,geom1,t0,t1
 out=dict(schema='source-geometry-bound-d-migration/v1',oldMesh=str(old),newMesh=str(new),scope='No raw cross-version cell-ID claim. Retained parents require exact complete outward coordinatecycles/patch/zone bytes. Good/bad child transitions require a unique exact written base-piece signature in both versions; selected D is independently recomputed. Changed/appended parents remain ancestry unknown.',ranks=ranks,totals=dict(tally),parents=rows)
 return out

def source_geom(t,p):
 fs=t['cf'][t['co'][p]:t['co'][p+1]];polys=[]
 for f in map(int,fs):
  ids=t['vertices'][t['offsets'][f]:t['offsets'][f+1]].tolist()
  polys.append(ids if t['owner'][f]==p else ids[::-1])
 gg=G.Geometry.from_arrays(t['points'],polys);raw=gg.cell(0)
 return fs,polys,gg,raw
def body_selected(t,fs,areas,types):
 ids=[i for i,f in enumerate(fs) if t['patch'][f]<0 or types[int(t['patch'][f])].startswith(('cyclic','processor'))]
 a=areas[ids]
 if not len(a):return 0.,0
 s=np.linalg.norm(a,axis=1).mean()
 return float(abs(np.linalg.det((a/s).T@(a/s)))/8),len(ids)
def axis_box(t,fs,polys,gg,raw):
 sf,fc,cc,V,ids,lo,hi=raw[1:];span=hi-lo;long=max(span);tol=1e-12*long;v=t['points'][ids]
 directed=collections.Counter()
 okay=True
 for i,poly in enumerate(polys):
  A=sf[i];amount=np.linalg.norm(A);ax=int(np.argmax(abs(A)))
  if amount<=0 or any(abs(A[j])>1e-12*abs(A[ax]) for j in range(3) if j!=ax):okay=False;break
  plane=hi[ax] if A[ax]>0 else lo[ax]
  if max(abs(t['points'][poly,ax]-plane))>tol:okay=False;break
  if max((v-fc[i])@A)>tol*amount:okay=False;break
  if gg.face(i)[5]<-tol:okay=False;break
  for a,b in zip(poly,poly[1:]+poly[:1]):directed[(a,b)]+=1
 closed=all(n==1 and directed[(b,a)]==1 for (a,b),n in directed.items()) if okay else False
 bv=np.prod(span)
 return bool(okay and closed and bv>0 and V>0 and abs(V-bv)<=1e-10*bv)
def audit_source_classes(new,read_trace,migration,rank_count=8):
 allrows=[];total=collections.Counter()
 for rank in range(rank_count):
  t=source_index(read_trace(new,rank));wg=G.Geometry(new/f'processor{rank}/constant/polyMesh')
  types={}
  for start,count,name,kind in wg.patches:
   base=t['faceBase'][start:start+count];base=base[base>=0]
   for patch in np.unique(t['patch'][base]):
    if patch<0:continue
    if int(patch) in types and types[int(patch)]!=kind:raise ValueError('ambiguous realized source-patch kind')
    types[int(patch)]=kind
  bad=G.cell_set(wg.poly_dir/'sets/underdeterminedCells');badparent=collections.Counter(map(int,t['cellParent'][bad]))
  sourceclasses={r['parentSlot']:r['category'] for r in migration['parents'] if r['rank']==rank}
  loc=collections.Counter()
  for p,bc in sorted(badparent.items()):
   fs,polys,gg,raw=source_geom(t,p)
   sd,nselected=body_selected(t,fs,raw[1],types)
   span=raw[7]-raw[6];aspect=float(max(span)/min(span));box=axis_box(t,fs,polys,gg,raw)
   if box:kind='axisBoxThin' if aspect>4 else 'axisBoxAspectAtMost4'
   else:kind='nonBoxSource'
   sourceGood=sd>=.001
   row=dict(rank=rank,parent=p,newBadChildren=bc,sourceClass=sourceclasses[p],sourceBodySelectedD=sd,sourceSelectedFaces=nselected,sourceFaces=len(fs),sourceVertices=len(raw[5]),sourceVolume=float(raw[4]),span=span.tolist(),aspect=aspect,axisBox=box,classification=kind,sourceBodyDGood=bool(sourceGood))
   allrows.append(row);loc[kind+'Parents']+=1;loc[kind+'DChildren']+=bc
   loc[('sourceBodyGood' if sourceGood else 'sourceBodyBad')+'Parents']+=1;loc[('sourceBodyGood' if sourceGood else 'sourceBodyBad')+'DChildren']+=bc
   if sourceclasses[p]!='sameRetainedSlotExactSourceGeometry':
    loc['changedOrAppend_'+kind+'Parents']+=1;loc['changedOrAppend_'+kind+'DChildren']+=bc
    loc['changedOrAppend_'+('bodyGood' if sourceGood else 'bodyBad')+'Parents']+=1;loc['changedOrAppend_'+('bodyGood' if sourceGood else 'bodyBad')+'DChildren']+=bc
  total.update(loc);print(json.dumps(dict(rank=rank,counts=dict(loc))),flush=True)
 out=dict(schema='current-sourcebody-versus-child-d/v1',newMesh=str(new),scope='Source body selected-Sf determinant is separated from written conechild stock D. AxisBox gate uses full face/outward vertex planes, convex support, directed closure, bboxvolume; not bbox only. Box class is not fanchild or fullchecker proof.',counts=dict(total),rows=allrows)
 return out

def projkey(p,d):return tuple(Fraction.from_float(float(p[j])) for j in range(3) if j!=d)
def orient(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def onseg(a,b,p):return orient(a,b,p)==0 and min(a[0],b[0])<=p[0]<=max(a[0],b[0]) and min(a[1],b[1])<=p[1]<=max(a[1],b[1])
def loop_from_polys(polys):
 edges=collections.Counter()
 for poly in polys:
  for a,b in zip(poly,poly[1:]+poly[:1]):
   if a!=b:edges[(a,b)]+=1
 boundary=[]
 for (a,b),n in edges.items():
  if n!=1 or edges[(b,a)]>1:return None
  if not edges[(b,a)]:boundary.append((a,b))
 outgoing=collections.defaultdict(list);incoming=collections.Counter()
 for a,b in boundary:outgoing[a].append(b);incoming[b]+=1
 if not outgoing or any(len(v)!=1 for v in outgoing.values()) or any(incoming[k]!=1 for k in outgoing):return None
 start=min(outgoing);loop=[start];at=outgoing[start][0]
 while at!=start:
  if at in loop or at not in outgoing:return None
  loop.append(at);at=outgoing[at][0]
 return loop if len(loop)==len(boundary) else None
def simplify(poly):
 out=poly[:]
 while len(out)>3:
  remove=[i for i,b in enumerate(out) if orient(out[i-1],b,out[(i+1)%len(out)])==0 and onseg(out[i-1],out[(i+1)%len(out)],b)]
  if not remove:break
  out=[p for i,p in enumerate(out) if i not in remove]
 return out
def canonical(poly):
 return min(tuple(p[i:]+p[:i]) for p in (poly,poly[::-1]) for i in range(len(p)))
def simple(poly):
 for i,(a,b) in enumerate(zip(poly,poly[1:]+poly[:1])):
  for j in range(i+1,len(poly)):
   c=poly[j];e=poly[(j+1)%len(poly)]
   if (i+1)%len(poly)==j or (j+1)%len(poly)==i:continue
   s=[orient(a,b,c),orient(a,b,e),orient(c,e,a),orient(c,e,b)]
   if s[0]*s[1]<0 and s[2]*s[3]<0:return False
   if onseg(a,b,c) or onseg(a,b,e) or onseg(c,e,a) or onseg(c,e,b):return False
 return True
def source_polys(t,p):
 fs=t['cf'][t['co'][p]:t['co'][p+1]];polys=[]
 for f in map(int,fs):
  ids=t['vertices'][t['offsets'][f]:t['offsets'][f+1]].tolist()
  polys.append(ids if t['owner'][f]==p else ids[::-1])
 return fs,polys
def prism(t,p):
 fs,polys=source_polys(t,p);gg=G.Geometry.from_arrays(t['points'],polys);raw=gg.cell(0)
 sf,fc,CC,V,ids,lo,hi=raw[1:];attempts=[]
 if not np.isfinite(t['points'][ids]).all() or not directed_closed(polys) or V<=0:
  return dict(exactCoordinateAxisPrism=False,attempts=[dict(reason='nonFiniteOrNotPositiveDirectedClosedShell')])
 for d in range(3):
  caps=[[],[]];walls=[];reason=None
  for i,poly in enumerate(polys):
   pts=t['points'][poly];A=sf[i];mag=np.linalg.norm(A)
   if mag<=0:reason='zeroArea';break
   if all(A[j]==0 for j in range(3) if j!=d):
    if np.all(pts[:,d]==lo[d]):caps[0].append([projkey(x,d) for x in pts][::-1])
    elif np.all(pts[:,d]==hi[d]):caps[1].append([projkey(x,d) for x in pts])
    else:reason='internalCapPlane';break
   elif A[d]==0:walls.append([projkey(x,d) for x in pts])
   else:reason='faceNeitherExactCapNorParallelWall';break
  if reason:attempts.append(dict(axis=d,reason=reason));continue
  low=loop_from_polys(caps[0]);high=loop_from_polys(caps[1])
  if low is None or high is None:attempts.append(dict(axis=d,reason='capsNotSingleExactDirectedOutline'));continue
  a=simplify(low);b=simplify(high)
  if len(a)<3 or len(b)<3:attempts.append(dict(axis=d,reason='degenerateCapOutline'));continue
  if canonical(a)!=canonical(b):attempts.append(dict(axis=d,reason='exactProjectedOutlinesDiffer'));continue
  if not simple(a):attempts.append(dict(axis=d,reason='nonSimpleCap'));continue
  for wall in walls:
   if not any(all(onseg(x,y,q) for q in wall) for x,y in zip(a,a[1:]+a[:1])):
    reason='wallProjectionNotAnOriginalOutlineSegment';break
  if reason:attempts.append(dict(axis=d,reason=reason));continue
  crosses=[x[0]*y[1]-y[0]*x[1] for x,y in zip(a,a[1:]+a[:1])]
  signedA=sum(crosses)/2
  if signedA==0:attempts.append(dict(axis=d,reason='zeroCapArea'));continue
  centroid=[sum((x[j]+y[j])*cross for x,y,cross in zip(a,a[1:]+a[:1],crosses))/(6*signedA) for j in range(2)]
  dims=[j for j in range(3) if j!=d];expectedCC=np.zeros(3)
  expectedCC[d]=(float(lo[d])+float(hi[d]))*.5
  for j,v in zip(dims,centroid):expectedCC[j]=float(v)
  expectedV=float(abs(signedA)*(Fraction.from_float(float(hi[d]))-Fraction.from_float(float(lo[d]))))
  verr=abs(V-expectedV)/expectedV;merr=float(np.max(abs(V*CC-expectedV*expectedCC)))/(expectedV*max(1.,np.linalg.norm(expectedCC)))
  if verr>1e-10 or merr>1e-10:attempts.append(dict(axis=d,reason='VOrM1Mismatch',volumeRelativeError=verr,momentRelativeScaleError=merr));continue
  turns=[orient(a[i-1],a[i],a[(i+1)%len(a)]) for i in range(len(a))]
  convex=all(v*signedA>0 for v in turns)
  return dict(exactCoordinateAxisPrism=True,axis=d,capOutlineVertices=len(a),originalCapNodeCounts=[len(low),len(high)],convexOutline=convex,height=float(hi[d]-lo[d]),capArea=float(abs(signedA)),volumeRelativeError=verr,momentRelativeScaleError=merr,projectedOutline=[[float(q) for q in x] for x in a],sourceVertices=raw[5].tolist(),sourceFaces=list(map(int,fs)),scope='Classification simplifies collinear cap vertices only to compare outline; all original source node coordinates remain unmodified and future children must retain them.')
 return dict(exactCoordinateAxisPrism=False,attempts=attempts)
def classify_prism(points,polys):
    """Standalone sufficient classification; original points are never changed."""
    t=dict(points=np.asarray(points,dtype=np.float64),owner=np.zeros(len(polys),dtype=int),
           vertices=np.asarray([v for poly in polys for v in poly],dtype=int),
           offsets=np.r_[0,np.cumsum(list(map(len,polys)))],
           cf=np.arange(len(polys)),co=np.array([0,len(polys)]))
    return prism(t,0)

def audit_prisms(new,read_trace,obj,rank_count=8):
 rows=[];count=collections.Counter()
 for rank in range(rank_count):
  t=source_index(read_trace(new,rank))
  for r in obj['rows']:
   if r['rank']!=rank or r['classification']!='nonBoxSource':continue
   rec=prism(t,r['parent']);row={**r,**rec};rows.append(row)
   key='exactPrismConvex' if rec.get('convexOutline') else 'exactPrismConcave' if rec['exactCoordinateAxisPrism'] else 'unsupported'
   count[key+'Parents']+=1;count[key+'DChildren']+=r['newBadChildren']
  print(json.dumps(dict(rank=rank,sofar=dict(count))),flush=True)
 out=dict(schema='nonbox-exact-axis-prism-classification/v1',mesh=str(new),counts=dict(count),rows=rows,scope='Pure source geometry classification with exact Fraction 2D collinearity/simple outline/parallel coordinates and V/M1 checks; no native split, fan change or acceptance.')
 return out

def geometry_parent(t,p):
 fs=t['cf'][t['co'][p]:t['co'][p+1]]
 polys=[]
 for f in map(int,fs):
  ids=t['vertices'][t['offsets'][f]:t['offsets'][f+1]].tolist()
  polys.append(ids if t['owner'][f]==p else ids[::-1])
 geo=G.Geometry.from_arrays(t['points'],polys)
 raw=geo.cell(0);areas=raw[1];centres=raw[2]
 directed=collections.Counter()
 for poly in polys:
  for a,b in zip(poly,poly[1:]+poly[:1]):directed[(int(a),int(b))]+=1
 closed=all(n==1 and directed[(b,a)]==1 for (a,b),n in directed.items())
 return fs,polys,geo,raw,closed
def audit_box_families(old,new,read_trace,rank,parents):
 t0=source_index(read_trace(old,rank));t1=source_index(read_trace(new,rank))
 newBad=set(map(int,G.cell_set(new/f'processor{rank}/constant/polyMesh/sets/underdeterminedCells')))
 wg=G.Geometry(new/f'processor{rank}/constant/polyMesh')
 rows=[]
 for p in parents:
  fs,polys,geo,raw,closed=geometry_parent(t0,p)
  areas,centres,cc,vol=raw[1:5];normals=areas/np.linalg.norm(areas,axis=1)[:,None]
  if not axis_box(t0,fs,polys,geo,raw):raise ValueError('family proof requires original convex axis-box shell')
  lo,hi=raw[6],raw[7];scale=max(hi-lo);tol=1e-12*scale
  candidate=np.flatnonzero((t1['cellAlive']!=0)&np.all(t1['apex']>=lo-tol,axis=1)&np.all(t1['apex']<=hi+tol,axis=1))
  accepted=[];parts=[];unionfaces=set();excluded=[]
  for c in map(int,candidate):
   nfs,npolys,ng,nraw,nclosed=geometry_parent(t1,c)
   xyz=t1['points'][nraw[5]]
   violation=((xyz[:,None,:]-centres[None,:,:])*normals[None,:,:]).sum(2).max()
   if violation>tol:
    excluded.append(dict(parent=c,maximumOriginalPlaneViolation=float(violation)));continue
   # Parent containment is checked against every original outward support plane.
   if not nclosed or nraw[4]<=0:raise ValueError('candidate source shell invalid')
   if not axis_box(t1,nfs,npolys,ng,nraw):raise ValueError('complete box-family proof requires convex axis-box parts')
   accepted.append(c);unionfaces.update(map(int,nfs))
   selected=[i for i,f in enumerate(nfs) if int(t1['patch'][f])<0]
   a=nraw[1][selected];s=np.linalg.norm(a,axis=1).mean()
   det=float(abs(np.linalg.det((a/s).T@(a/s)))/8) if len(a) else 0.
   children=np.flatnonzero(t1['cellParent']==c).tolist()
   childdet=[]
   for ch in children:
    cf=wg.cell_faces[wg.cell_offsets[ch]:wg.cell_offsets[ch+1]]
    sf=[wg.face(int(f))[0] for f in cf if wg.patch(int(f)) is None or wg.patch(int(f))['type'].startswith(('cyclic','processor'))]
    sf=np.asarray(sf);mean=np.linalg.norm(sf,axis=1).mean();dd=float(abs(np.linalg.det((sf/mean).T@(sf/mean)))/8)
    childdet.append(dict(cell=ch,determinant=dd,selectedFaces=len(sf),bad=ch in newBad))
   span=nraw[7]-nraw[6]
   parts.append(dict(parent=c,faces=len(nfs),vertices=len(nraw[5]),volume=nraw[4],centroid=nraw[3].tolist(),bbox=[nraw[6].tolist(),nraw[7].tolist()],aspect=float(max(span)/min(span)),sourceBodySelectedDeterminant=det,sourceSelectedFaceCount=len(selected),writtenChildren=childdet,fullOriginalPlaneMaxViolation=float(violation)))
  max_overlap=0.
  for i,part in enumerate(parts):
   for other in parts[i+1:]:
    overlap=np.maximum(np.minimum(part['bbox'][1],other['bbox'][1])-np.maximum(part['bbox'][0],other['bbox'][0]),0.)
    max_overlap=max(max_overlap,float(np.prod(overlap)))
  if max_overlap>1e-10*vol:raise ValueError('box-family parts overlap in positive volume')
  aset=set(accepted);external=[];oldmap=collections.defaultdict(list);outpolys=[]
  for f in sorted(unionfaces):
   own=int(t1['owner'][f]);nei=int(t1['neighbour'][f])
   if (own in aset)==(nei in aset):continue
   ids=t1['vertices'][t1['offsets'][f]:t1['offsets'][f+1]].tolist()
   if own not in aset:ids=ids[::-1]
   pts=t1['points'][ids];nface=G.Geometry.from_arrays(t1['points'],[ids]);sf,fc,*_=nface.face(0)
   # Exact source role plus original face support lines; no parent matching from bbox alone.
   i=facet_match(pts,t1['patch'][f],t1['zone'][f],fs,t0['points'],polys,t0['patch'],t0['zone'],centres,normals,tol)
   oldmap[i].append((sf,fc))
   outpolys.append(ids);external.append(dict(face=f,originalFace=int(fs[i]),patch=int(t1['patch'][f]),zone=int(t1['zone'][f])))
  unionGeo=G.Geometry.from_arrays(t1['points'],outpolys);u=unionGeo.cell(0)
  faceRows=[]
  for i,oldf in enumerate(fs):
   nas=np.asarray([v[0] for v in oldmap[i]]);ncs=np.asarray([v[1] for v in oldmap[i]])
   av=nas.sum(0);mv=np.einsum('ij,ik->jk',nas,ncs)
   target=np.outer(areas[i],centres[i])
   faceRows.append(dict(originalFace=int(oldf),subfaces=len(oldmap[i]),areaVectorMaxAbsError=float(abs(av-areas[i]).max()),areaFirstMomentMaxAbsError=float(abs(mv-target).max())))
  sumV=sum(x['volume'] for x in parts);sumM=sum((x['volume']*np.asarray(x['centroid']) for x in parts),np.zeros(3))
  directed=collections.Counter()
  for poly in outpolys:
   for a,b in zip(poly,poly[1:]+poly[:1]):directed[(a,b)]+=1
  uclosed=all(n==1 and directed[(b,a)]==1 for (a,b),n in directed.items())
  rows.append(dict(rank=rank,originalParent=p,originalVolume=vol,originalCentroid=cc.tolist(),originalDirectedClosed=closed,parts=parts,containedSourceParts=len(parts),sourcePartVolumeSum=sumV,volumeAbsoluteError=sumV-vol,firstMomentMaxAbsError=float(abs(sumM-vol*cc).max()),unionVolume=float(u[4]),unionDirectedClosed=uclosed,maximumPartBoxOverlapVolume=max_overlap,originalFacetConservation=faceRows,externalFaces=external,excludedApexBoxCandidates=excluded,newWrittenChildren=sum(len(v['writtenChildren']) for v in parts),newDCount=sum(c['bad'] for v in parts for c in v['writtenChildren'])))
 out=dict(schema='three-thin-original-halfspace-family/v1',oldMesh=str(old),newMesh=str(new),scope='Containment in every original source outward plane, positive directed-closed parts, role-compatible unique original external facet coverage plus area/M1/V/M1; no simple bbox ancestry or checker acceptance.',rows=rows)
 return out

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ("old-mesh","new-mesh","trace-tool","output-dir"):
        parser.add_argument("--"+key,type=Path,required=True)
    parser.add_argument("--rank-count",type=int,default=8)
    parser.add_argument("--box-family",action="append",default=[],help="rank:parent,parent")
    args=parser.parse_args();output=args.output_dir.resolve()
    if not output.is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("output must remain inside this checkout")
    output.mkdir(parents=True,exist_ok=True);read_trace=load_trace_reader(args.trace_tool)
    migration=audit_migration(args.old_mesh,args.new_mesh,read_trace,args.rank_count)
    classes=audit_source_classes(args.new_mesh,read_trace,migration,args.rank_count)
    prisms=audit_prisms(args.new_mesh,read_trace,classes,args.rank_count)
    objects={"exact-source-piece-d-migration.json":migration,
             "sourcebody-versus-child-d.json":classes,"nonbox-exact-axis-prisms.json":prisms}
    for value in args.box_family:
        rank,parents=value.split(":")
        objects[f"box-family-rank{rank}.json"]=audit_box_families(
            args.old_mesh,args.new_mesh,read_trace,int(rank),list(map(int,parents.split(","))))
    for name,obj in objects.items():
        (output/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+"\n")
    print(json.dumps(dict(migration=migration["totals"],source=classes["counts"],prisms=prisms["counts"])))

if __name__=="__main__":main()
