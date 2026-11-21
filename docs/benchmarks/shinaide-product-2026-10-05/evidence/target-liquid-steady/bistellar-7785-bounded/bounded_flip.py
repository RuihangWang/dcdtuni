from pathlib import Path
import sys,json,time,copy,hashlib,itertools
from collections import Counter,defaultdict,deque
from fractions import Fraction as F
import numpy as np
N=Path(__file__).resolve().parent;A=N.parents[1]
sys.path[:0]=[str(N/'readers'),str(A/'src')]
import audit_air_mesh_geometry as G
import air_cone_trace_probe as T
import audit_air_bistellar_cavity as B
import audit_air_bistellar_cavity_independent as X
import audit_air_cone_small_union_independent as U
M=A.parent/'integration/.codex-tmp/cases/Shinaide_MeshAbort_CUA/Mesh'
baseline=A/'.codex-tmp/installed-writer-cua-7785c33f'
classification=json.loads((baseline/'classification.json').read_text())
start=time.monotonic();deadline=start+60.
def checktime():
 if time.monotonic()>=deadline:raise TimeoutError('60second search deadline')
cache={};seen=set();trials=[];census=Counter();seedslog=[]
def rankdata(rank):
 checktime()
 if rank not in cache:
  t=T.read_trace(M,rank);g=G.Geometry(M/f'processor{rank}/constant/polyMesh')
  cache[rank]=(t,g)
 return cache[rank]
def tetverts(g,c):
 fs=g.cell(c)[0]
 if len(fs)!=4 or any(len(g.ids(int(f)))!=3 for f in fs):return None
 v=set(int(p) for f in fs for p in g.ids(int(f)))
 return v if len(v)==4 else None
def neighbour(g,c,f):
 if f>=len(g.neighbour):return None
 return int(g.neighbour[f]) if int(g.owner[f])==c else int(g.owner[f])
def zone(t,f):
 b=int(t['faceBase'][f]);return int(t['zone'][b]) if b>=0 else -1
def coupled(p):
 return bool(p and any(v in p['type'].lower() for v in ['processor','cyclic','ami','mapped']))
def compact(t,g,core):
 cf=set(int(f) for c in core for f in g.cell(c)[0])
 inside=[f for f in cf if int(g.owner[f]) in core and f<len(g.neighbour) and int(g.neighbour[f]) in core]
 shell=sorted(cf-set(inside));outside=set()
 for f in shell:
  own=int(g.owner[f]);nei=int(g.neighbour[f]) if f<len(g.neighbour) else None
  other=nei if own in core else own
  if other is not None:outside.add(other)
 touched=set(core)|outside
 if any(coupled(g.patch(int(f))) for c in touched for f in g.cell(c)[0]):
  return None,inside,shell,'touchedCoupledOwner'
 if any(zone(t,int(f))!=-1 for c in touched for f in g.cell(c)[0]):
  return None,inside,shell,'protectedSourceZone'
 if not (g.poly_dir/'cellZones').read_text().strip().endswith('0()'):
  # The native empty writer payload normally includes a trailing newline.
  raw=(g.poly_dir/'cellZones').read_text()
  import re
  if not re.search(r'\n0\s*\(\s*\)',raw):return None,inside,shell,'unknownCellZoneMembership'
 allfaces=sorted({int(f) for c in touched for f in g.cell(c)[0]});points=sorted({int(p) for f in allfaces for p in g.ids(f)})
 if not np.all(np.isfinite(g.points[points])):return None,inside,shell,'nonfiniteSourceCoordinates'
 doc={'points':{str(p):g.points[p].tolist() for p in points},'faces':{},'cells':{},
      'children':sorted(core),'outsideCells':sorted(outside),'apexWrittenPoint':points[0],'originalApex':g.points[points[0]].tolist()}
 for f in allfaces:
  doc['faces'][str(f)]={'owner':int(g.owner[f]),'neighbour':int(g.neighbour[f]) if f<len(g.neighbour) else None,
    'pointIDs':g.ids(f).tolist(),'patch':g.patch(f),'sourceBase':int(t['faceBase'][f]),'sourceZone':zone(t,f),'nativeFace':int(t['faceNative'][f])}
 for c in touched:
  fs,sf,fc,cc,v,*_=g.cell(c)
  doc['cells'][str(c)]={'faces':list(map(int,fs)),'sourceParent':int(t['cellParent'][c]),'nativeCell':int(t['cellNative'][c]),
   'writtenCentre':cc.tolist(),'writtenVolume':v}
 return doc,inside,shell,None
def exact_partition(oldsets,newsets,points):
 olds=[X.exact_tet(v,points) for v in oldsets];news=[X.exact_tet(v,points) for v in newsets]
 ov=sum(v[0] for v in olds);nv=sum(v[0] for v in news)
 om=[sum(v[1][i] for v in olds) for i in range(3)];nm=[sum(v[1][i] for v in news) for i in range(3)]
 return {'oldVolume':str(ov),'newVolume':str(nv),'oldM1':list(map(str,om)),'newM1':list(map(str,nm)),
 'allNewPositive':all(v[0]>0 for v in news),'Vexact':ov==nv,'M1exact':om==nm}
def assemble(doc,inside,shell,newsets,cellStart,faceStart):
 cand=copy.deepcopy(doc);core=set(doc['children']);points={int(k):v for k,v in doc['points'].items()}
 newcells=[cellStart+j for j in range(len(newsets))]
 assert all(c>=cellStart for c in newcells)
 assert all(r['owner']<cellStart and (r['neighbour'] is None or r['neighbour']<cellStart) for r in doc['faces'].values())
 incident=defaultdict(list)
 for c,vs in zip(newcells,newsets):
  for ids in X.outward_faces(vs,points):incident[tuple(sorted(ids))].append((c,ids))
 ext={k:v for k,v in incident.items() if len(v)==1};inter={k:v for k,v in incident.items() if len(v)==2}
 keys={tuple(sorted(doc['faces'][str(f)]['pointIDs'])):f for f in shell}
 if len(keys)!=len(shell) or set(ext)!=set(keys) or any(len(v)>2 for v in incident.values()):
  raise ValueError('exteriorTriangleShellNotExact')
 for f in inside:del cand['faces'][str(f)]
 for c in core:del cand['cells'][str(c)]
 for c in newcells:cand['cells'][str(c)]={'faces':[],'sourceParent':None,'sourceParentsInherited':sorted({doc['cells'][str(c)]['sourceParent'] for c in core})}
 mappings=[]
 for key,parts in sorted(ext.items()):
  c,poly=parts[0];f=keys[key];row=cand['faces'][str(f)];old=doc['faces'][str(f)]
  expected=old['pointIDs'] if old['owner'] in core else [old['pointIDs'][0],*reversed(old['pointIDs'][1:])]
  if Counter(zip(poly,poly[1:]+poly[:1]))!=Counter(zip(expected,expected[1:]+expected[:1])):raise ValueError('exteriorOrientationMismatch')
  if row['owner'] in core:row['owner']=c
  else:row['neighbour']=c
  cand['cells'][str(c)]['faces'].append(f)
  mappings.append({'face':f,'beforeCore':old['owner'] if old['owner'] in core else old['neighbour'],'afterCore':c,
   'ringPoint0PatchZoneBaseExactBeforeCanonical':all(old[k]==row[k] for k in ['pointIDs','patch','sourceZone','sourceBase'])})
 newfaces=[];first=faceStart
 assert all(int(f)<faceStart for f in doc['faces'])
 for j,(key,parts) in enumerate(sorted(inter.items())):
  (owner,ids),(nei,other)=sorted(parts)
  if Counter(zip(ids,ids[1:]+ids[:1]))!=Counter((b,a) for a,b in zip(other,other[1:]+other[:1])):raise ValueError('internalOppositeOrientationMismatch')
  f=first+j;newfaces.append(f)
  cand['faces'][str(f)]={'owner':owner,'neighbour':nei,'pointIDs':ids,'patch':None,'sourceBase':-1,'sourceZone':-1}
  for c in [owner,nei]:cand['cells'][str(c)]['faces'].append(f)
 for c in newcells:cand['cells'][str(c)]['faces'].sort()
 cand['children']=newcells
 return cand,mappings,newfaces
def pareto(oq,nq,oldcore,newcore,inside,newfaces):
 out={}
 for metric,(threshold,upper) in U.LIMITS.items():
  coll='cells' if metric=='D' else 'faces';label='cell' if metric=='D' else 'face'
  before={r[label]:r[metric] for r in oq[coll] if metric in r};after={r[label]:r[metric] for r in nq[coll] if metric in r}
  bad=lambda v:not np.isfinite(v) or (v>threshold if upper else v<threshold)
  fail=[]
  for ident,val in after.items():
   orig=(oldcore if ident in newcore else [ident]) if metric=='D' else inside if ident in newfaces else [ident]
   good=[o for o in orig if o in before and not bad(before[o])]
   if good and bad(val):fail.append({'replacement':ident,'oldPassingRoles':good,'value':val})
  oldbad=[v for v in before.values() if bad(v)];newbad=[v for v in after.values() if bad(v)]
  sev=lambda v:float('inf') if not np.isfinite(v) or (not upper and v<=0) else v/threshold if upper else threshold/v
  ow=max([1,*map(sev,oldbad)]);nw=max([1,*map(sev,newbad)])
  out[metric]={'passingRoleFailures':fail,'oldBadCount':len(oldbad),'newBadCount':len(newbad),'oldWorstSeverity':ow,'newWorstSeverity':nw,
   'passes':not fail and len(newbad)<=len(oldbad) and nw<=ow}
 return out
def assess(rank,core,newsets,move,witness):
 checktime();t,g=rankdata(rank);ident=(rank,move,tuple(sorted(core)))
 if ident in seen:return
 seen.add(ident);row={'rank':rank,'move':move,'cells':sorted(core),'witness':witness}
 doc,inside,shell,refused=compact(t,g,set(core))
 if refused:row['reason']=refused;trials.append(row);census[refused]+=1;return
 points={int(k):v for k,v in doc['points'].items()};oldsets=[sorted(tetverts(g,c)) for c in core]
 geo=exact_partition(oldsets,newsets,points);row['exactPartition']=geo
 if not all(geo[k] for k in ['allNewPositive','Vexact','M1exact']):
  row['reason']='exactVolumeMomentOrPositiveFailure';trials.append(row);census[row['reason']]+=1;return
 try:cand,shellmap,newfaces=assemble(doc,inside,shell,newsets,g.n_cells,g.n_faces)
 except ValueError as ex:
  row['reason']=str(ex);trials.append(row);census[row['reason']]+=1;return
 old,oq=U.family_metrics(doc);new,nq=U.family_metrics(cand)
 canonical,flips=U.canonicalize_internal_roles(cand);cm,cq=U.family_metrics(canonical)
 unchanged=[]
 for c in doc['outsideCells']:
  a=old.cell(c);b=cm.cell(c)
  unchanged.append({'cell':c,'CCexactDP':bool(np.array_equal(a[2].view(np.uint64),b[2].view(np.uint64))),
    'VexactDP':np.float64(a[3]).view(np.uint64).item()==np.float64(b[3]).view(np.uint64).item()})
 outerbindings=[]
 for f in shell:
  a=doc['faces'][str(f)];b=canonical['faces'][str(f)]
  expected=a['pointIDs'] if f not in flips else [a['pointIDs'][0],*reversed(a['pointIDs'][1:])]
  outsidea=a['neighbour'] if a['owner'] in core else a['owner']
  outsideb=b['neighbour'] if b['owner'] in canonical['children'] else b['owner']
  outerbindings.append({'face':f,'canonicalTailFlip':f in flips,'samePoint0':a['pointIDs'][0]==b['pointIDs'][0],
   'expectedOrientedRingExact':expected==b['pointIDs'],'patchZoneBaseExact':all(a[k]==b[k] for k in ['patch','sourceZone','sourceBase']),
   'outsideIDExact':outsidea==outsideb})
 oqExtended={**oq,'cells':oq['cells']+[old.cell_detail(c) for c in doc['outsideCells']]}
 cqExtended={**cq,'cells':cq['cells']+[cm.cell_detail(c) for c in doc['outsideCells']]}
 lineage=pareto(oqExtended,cqExtended,list(core),canonical['children'],inside,newfaces)
 summary=lambda q:{'Dmin':min(x['D'] for x in q['cells']),'Wmin':min((x.get('weight',1) for x in q['faces']),default=1),
  'Rmin':min((x.get('ratio',1) for x in q['faces']),default=1),'Smax':max((x['skew'] for x in q['faces']),default=0),
  'Nmax':max((x.get('nonOrthogonalityDegrees',0) for x in q['faces']),default=0)}
 before=summary(oq);after=summary(cq)
 absolute=after['Dmin']>=.001 and after['Wmin']>=.05 and after['Rmin']>=.01 and after['Smax']<=4 and after['Nmax']<=70
 geometry=all(x['CCexactDP'] and x['VexactDP'] for x in unchanged) and all(all(x[k] for k in ['samePoint0','expectedOrientedRingExact','patchZoneBaseExact','outsideIDExact']) for x in outerbindings)
 geometry=geometry and all(x['V']>0 and x['relativeClosure']<1e-10 and x['minCentroidPyramidVolume']>0 and x['faceCentreConcavityPairTriggers']==0 and x['edgesExactlyTwo'] and x['edgesOppositeOrientation'] for x in cq['cells'])
 geometry=geometry and all(x['minimumCentreFanTetQuality']>1e-30 and x['bestSharedBasePointMinimumTetQuality']>1e-30 and np.linalg.norm(cm.face(x['face'])[0])>1e-12 for x in cq['faces'])
 passed=geometry and absolute and all(x['passes'] for x in lineage.values())
 row.update(reason='qualifiedPositiveDiagnostic' if passed else 'stockGeometryOrFiveMetricOrLineageRefused',geometricPass=bool(geometry),absoluteFivePass=bool(absolute),
  lineagePass=all(x['passes'] for x in lineage.values()),oldFive=before,newFive=after,lineage=lineage,completeOutside=unchanged,boundaryMapping=outerbindings,
  canonicalOwnerTailFlips=flips,oldDocument=doc,newDocument=canonical,newTetOriginalPointIDs=newsets,oldInternalFaces=inside,newInternalFaces=newfaces,
  oldActualMetrics=oqExtended,newActualMetrics=cqExtended,actualRankCellCount=g.n_cells,actualRankFaceCount=g.n_faces,
  conservativeScreen='Outside canonical-DP equality is an additional sufficient screen. Five values and all inherited passing roles are separately reported; no failed conservative screen proves all legal flips impossible.')
 trials.append(row);census[row['reason']]+=1
 (N/'partial.json').write_text(json.dumps({'trials':trials,'counts':dict(census)},indent=2)+'\n')
def process_seed(rank,c,origin):
 checktime();t,g=rankdata(rank);v=tetverts(g,c)
 seedslog.append({'rank':rank,'cell':c,'origin':origin,'isTrueTet':v is not None})
 if v is None:return
 # 2-to-3: all four actual face-adjacent slots, no arbitrary-neighbour truncation.
 for ff in g.cell(c)[0]:
  checktime();f=int(ff);n=neighbour(g,c,f)
  if n is None:continue
  nv=tetverts(g,n)
  if nv is None:census['23adjacentNotTet']+=1;continue
  tri=g.ids(f).tolist();op=sorted(v-set(tri))+sorted(nv-set(tri))
  if len(op)!=2 or len(v|nv)!=5:census['23notFiveOriginalNodes']+=1;continue
  pre=B.preflight_23(g.points[tri],g.points[op])
  if not pre['strictInterior']:
   row={'rank':rank,'move':'2to3','cells':sorted([c,n]),'witness':{'sharedFace':f,'triangle':tri,'opposites':op,**pre},'reason':'newEdgeNotStrictInterior'}
   ident=(rank,'2to3',tuple(sorted([c,n])))
   if ident not in seen:seen.add(ident);trials.append(row);census[row['reason']]+=1
   continue
  newsets=[[op[0],op[1],tri[i],tri[(i+1)%3]] for i in range(3)]
  assess(rank,[c,n],newsets,'2to3',{'sharedFace':f,'triangle':tri,'opposites':op,**pre})
 # 3-to-2: complete connected face-ring around each edge, reject >3 or boundary.
 for edge in itertools.combinations(sorted(v),2):
  checktime();todo=[c];visited=set();boundary=False;too_many=False
  while todo:
   z=todo.pop()
   if z in visited:continue
   visited.add(z)
   if len(visited)>3:too_many=True;break
   for ff in g.cell(z)[0]:
    f=int(ff)
    if not set(edge)<=set(map(int,g.ids(f))):continue
    n=neighbour(g,z,f)
    if n is None:boundary=True
    elif n not in visited:todo.append(n)
  if boundary or too_many or len(visited)!=3:
   census['32edgeStarNotThreeClosed']+=1;continue
  sets=[tetverts(g,z) for z in visited]
  if any(z is None for z in sets):census['32starHasNonTet']+=1;continue
  if set.intersection(*sets)!=set(edge) or len(set.union(*sets))!=5:census['32notFiveOriginalNodes']+=1;continue
  tri=sorted(set.union(*sets)-set(edge));pre=B.preflight_23(g.points[tri],g.points[list(edge)])
  if not pre['strictInterior']:
   ident=(rank,'3to2',tuple(sorted(visited)))
   if ident not in seen:seen.add(ident);trials.append({'rank':rank,'move':'3to2','cells':sorted(visited),'witness':{'edge':edge,'triangle':tri,**pre},'reason':'reverseNewFaceNotStrictInterior'});census['reverseNewFaceNotStrictInterior']+=1
   continue
  assess(rank,sorted(visited),[[edge[0],*tri],[edge[1],*tri]],'3to2',{'removedEdge':edge,'newTriangle':tri,**pre})
timedout=False
try:
 # R-first finite one-face neighbourhood around both actual low-ratio pairs.
 for rr in classification['ranks']:
  for f in rr['failedFaces']:
   if 'R' not in f['failedFlags']:continue
   rank=rr['rank'];t,g=rankdata(rank);star={f['owner']['cell'],f['neighbour']['cell']}
   for z in list(star):
    for ff in g.cell(z)[0]:
     n=neighbour(g,z,int(ff))
     if n is not None:star.add(n)
   for z in sorted(star):
    if tetverts(g,z) is not None:process_seed(rank,z,{'Rface':f['face'],'scope':'R endpoints and actual immediate face neighbours'})
 # Exactly16 poorest actual D tetrahedra, stable determinant/rank/label order.
 bad=[dict(c,rank=rr['rank']) for rr in classification['ranks'] for c in rr['Dcells'] if c['shape']=='tetrahedron']
 for row in sorted(bad,key=lambda x:(x['determinant'],x['rank'],x['cell']))[:16]:
  process_seed(row['rank'],row['cell'],{'D':row['determinant'],'scope':'first16actualDtetrahedra'})
except TimeoutError:timedout=True
elapsed=time.monotonic()-start
out={'format':'actual7785boundedbistellarflip/v1','scope':'Read-only R2 immediate true-tet face-neighbour stars followed by first16actualbadD tets, 60s total data/search deadline. Original nodes/exterior patch-zone-base and actual outside identities kept. Any touched cell owning coupled face or protected source-zone face is refused. Canonical owner swaps use documented tail reverse with original point0; world-coordinate/orientation equivalence is not falsely called identical raw oriented ring. No CPP/native/API/mesh/index mutation.',
 'mesh':str(M),'deadlineSeconds':60,'elapsedSeconds':elapsed,'timedOut':timedout,'seedLog':seedslog,'counts':dict(census),
 'positiveCount':sum(x['reason']=='qualifiedPositiveDiagnostic' for x in trials),'trials':trials,
 'sourceReaders':json.loads((N/'reader-sha256.json').read_text()),'sourceCaseManifest':str(baseline/'case-file-manifest.json'),
 'sourceCaseManifestSHA256':hashlib.sha256((baseline/'case-file-manifest.json').read_bytes()).hexdigest(),
 'toolSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'noNativeAcceptance':True}
(N/'result.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ['trials','seedLog','sourceReaders']},indent=2))
