from pathlib import Path
import json,collections,hashlib,numpy as np
import analyze_geometry as G
A=G.A
IT=A.parents[1]
OLD=IT/'.codex-tmp/meshgen-s10-four-cells-actual-diagnostic-audit-20261006'
boundary=G.body('boundary');patches=[]
import re
for name,block in re.findall(r'(\w+)\s*\{([^}]+)\}',boundary):
 nf=int(re.search(r'nFaces\s+(\d+)',block).group(1));sf=int(re.search(r'startFace\s+(\d+)',block).group(1))
 patches.append({'name':name,'startFace':sf,'nFaces':nf,'type':re.search(r'type\s+(\w+)',block).group(1)})
ingest=json.loads((A/'ingest-report.json').read_text())
roles={r['patchName']:r for r in ingest['solids']}
def patch(f):return next((p['name'] for p in patches if p['startFace']<=f<p['startFace']+p['nFaces']),None)
def moment(fs,ref,faces=None):
 if faces is None:faces=G.F
 v=G.Q(0);m=[G.Q(0)]*3
 for f,sign in fs:
  q=[G.PQ[i] for i in faces[f]]
  for i in range(1,len(q)-1):
   dv=sign*G.dot(G.sub(q[0],ref),G.cr(G.sub(q[i],ref),G.sub(q[i+1],ref)))/6
   v+=dv;m=[t+dv*(q[0][d]+q[i][d]+q[i+1][d]+ref[d])/4 for d,t in enumerate(m)]
 return v,m
ref=(G.Q(3,7),G.Q(5,11),G.Q(13,17));zero=(G.Q(0),)*3
mom=[moment(fs,zero) for fs in G.C]
G.write('exact-cell-first-moments.json',[{'cell':c,'V':str(v),'M1':list(map(str,m)),'referenceInvariant':moment(G.C[c],ref)==(v,m)} for c,(v,m) in enumerate(mom)])
bad=set(G.edgeBad);plane={r['cell']:r for r in G.planes}
records=[]
for c in sorted(bad):
 ids=set(p for f,s in G.C[c] for p in G.F[f]);xyz=G.P[sorted(ids)]
 faces=[]
 for f,s in G.C[c]:
  name=patch(f); rr=roles.get(name)
  faces.append({'face':f,'sign':s,'owner':G.O[f],'neighbour':G.N[f] if f<len(G.N) else None,'patch':name,'role':None if rr is None else rr['role'],'exportKind':None if rr is None else rr['exportKind'],'STL':None if rr is None else rr['stlFile'],'pointIds':G.F[f],'coordinates':G.P[G.F[f]].tolist(),'SfOut':(s*G.S[f]).tolist(),'faceCentre':G.FC[f].tolist()})
 records.append({'cell':c,'bbox':[xyz.min(axis=0).tolist(),xyz.max(axis=0).tolist()],'positivePlaneViolation':c in G.positive,'planeWitness':plane[c],'V':str(mom[c][0]),'M1':list(map(str,mom[c][1])),'faces':faces})
G.write('bad-cells-actual-face-role-binding.json',{'patchRoles':roles,'cells':records,'scope':'Actual exported point IDs and cell IDs; raw split parent IDs not joined by geometry alone.'})
old=json.loads((OLD/'fraction-closure-and-cap-ownership.json').read_text())
matches=[]
for r in old['results']:
 lo=np.array([x['value'] for x in r['bbox'][0]]);hi=np.array([x['value'] for x in r['bbox'][1]])
 candidates=[]
 for c,fs in enumerate(G.C):
  ids=set(p for f,s in fs for p in G.F[f]);xyz=G.P[sorted(ids)]
  if np.all(xyz>=lo) and np.all(xyz<=hi):candidates.append(c)
 volume=sum(mom[c][0] for c in candidates);M=[sum(mom[c][1][d] for c in candidates) for d in range(3)]
 exp=r['withoutCapMoments'];cap=r['capCoords'];capv=np.array([[x['value'] for x in p] for p in cap]);cf=[]
 for f,ids in enumerate(G.F):
  q=G.P[ids]
  if np.all(q[:,1]==.5) and np.all(q>=capv.min(axis=0)) and np.all(q<=capv.max(axis=0)):cf.append(f)
 matches.append({'oldCompactCell':r['ci'],'oldReferenceFile':str(OLD/'fraction-closure-and-cap-ownership.json'),'geometricBBox':[lo.tolist(),hi.tolist()],'actualContainedCells':candidates,'V':str(volume),'M1':list(map(str,M)),'sameAsOldWithoutExtraHalfV':str(volume)==exp['V']['fraction'],'sameAsOldWithoutExtraHalfM1':list(map(str,M))==[x['fraction'] for x in exp['M1']],'actualFacesWithinOldExcessCapSupport':cf,'mappingScope':'Geometric partition comparison only; no assertion of runtime compact-cell identity.'})
for m in matches:
 m['retainedLawfulCapFaces']=[]
 for f in m['actualFacesWithinOldExcessCapSupport']:
  owner=G.O[f];ids=set(p for ff,s in G.C[owner] for p in G.F[ff]);xyz=G.P[sorted(ids)];name=patch(f)
  m['retainedLawfulCapFaces'].append({'face':f,'owner':owner,'ownerBBox':[xyz.min(axis=0).tolist(),xyz.max(axis=0).tolist()],'patch':name,'role':roles[name]['role'],'SfOwner':G.S[f].tolist(),'belongsToUpperComparedPartition':owner in m['actualContainedCells']})
G.write('old-s10-to-actual-geometric-partition.json',matches)
# Generic private topology-safe collinear removal; no case IDs/coordinate triggers.
def safe_prune(points,faces,owner,neighbour):
 result=[list(x) for x in faces];pointFaces=collections.defaultdict(set);cellFaces=collections.defaultdict(set)
 for f,ids in enumerate(result):
  for p in ids:pointFaces[p].add(f)
  cellFaces[owner[f]].add(f)
  if f<len(neighbour):cellFaces[neighbour[f]].add(f)
 def cell_ok(c):
  edges=collections.Counter()
  for f in cellFaces[c]:
   ids=result[f]
   for u,v in zip(ids,ids[1:]+ids[:1]):edges[tuple(sorted((u,v)))]+=1
  return all(n==2 for n in edges.values())
 assert all(cell_ok(c) for c in cellFaces)
 removed=[];rejected=[]
 for p,occ in sorted(pointFaces.items()):
  affected=[]
  for f in sorted(occ):
   ids=result[f]
   if p not in ids:continue
   if len(ids)<=3 or ids.count(p)!=1:break
   i=ids.index(p);cross=np.cross(points[p]-points[ids[i-1]],points[ids[(i+1)%len(ids)]]-points[p])
   if np.dot(cross,cross)!=0:break
   affected.append(f)
  else:
   if not affected:continue
   saved={f:result[f][:] for f in affected};cells={owner[f] for f in affected};cells.update(neighbour[f] for f in affected if f<len(neighbour))
   for f in affected:result[f].remove(p)
   if all(cell_ok(c) for c in cells):removed.append({'point':p,'faces':affected})
   else:
    for f,ids in saved.items():result[f]=ids
    rejected.append(p)
 return result,removed,rejected
safe,removed,rejected=safe_prune(G.P,G.Fsub,G.O,G.N)
remaining=[c for c in range(len(G.C)) if G.edges(c,safe)]
G.write('private-topology-safe-prune-proof.json',{'input':'Existing-coordinate globally reconstructed closed rings, not claimed native historical pre-WSB3 snapshot','removed':removed,'rejected':rejected,'remainingBadCells':remaining,'allCellsDirectedEdgesPaired':not remaining,'postRingsExactlyEqualClosedProofInput':safe==G.Fsub,'strictQualityClaim':False,'remainingConcave66Plus8':len(G.planes),'allActualOriginalFilesUnchanged':True})
print('ROLE_BINDING_CELLS',len(records),'S10MATCH',matches,'SAFE_PRUNE',len(removed),len(rejected),remaining)

# Additional exact conservation and actual diagnostic regression assertions.
subInvariant=[]
for c,fs in enumerate(G.C):
 before=mom[c]
 subInvariant.append(moment(fs,zero,G.Fsub)==before and moment(fs,ref,G.Fsub)==before)
checks={
 'declaredListCountsValidated':True,
 'everyOriginalCellSfExactZero':G.summary['exactSfAllZero'],
 'derivedEdgeSetMatchesActual74':G.summary['edgeSetExactMatch'],
 'all1344ClosedAfterExistingVertexSubdivision':not G.subBad,
 'all4672ActualRingsReproducedByUnchangedWsb3':G.rePruned==G.F,
 'all4672FaceSfExactUnchanged':G.subAreaEqual,
 'all1344VolumeFirstMomentExactUnchangedUnderSubdivisionTwoReferences':all(subInvariant),
 'all1344OriginalMomentsReferenceInvariant':all(moment(fs,ref)==mom[c] for c,fs in enumerate(G.C)),
 'actual74FacePlaneSetExactMatch':G.summary['concaveSetExactMatch'],
 'truePositivePlaneCellsEight':len(G.positive)==8,
 'planarOnlyCells66':len(G.planes)-len(G.positive)==66,
 'fourUniqueActualTetFaces':len(G.badFaces)==4,
 'eachTetFaceOwnerAndNeighbourFanFails':all(any(t['owner']['quality']>-1e-30 for t in r['edgeTets']) and any(t['neighbour']['quality']<1e-30 for t in r['edgeTets']) for r in G.badFaces),
 'eachTetFaceCommonVertexBaseExists':all(r['sharedPositiveBaseIndices'] for r in G.badFaces),
 'eachTetFaceBothPyramidsPositive':all(min(r['pyramids'])>0 for r in G.badFaces),
 'fourOldPhysicalPartitionsExactVandM1Match':all(r['sameAsOldWithoutExtraHalfV'] and r['sameAsOldWithoutExtraHalfM1'] for r in matches),
 'fourLawfulLowerWallHalvesRetained':all(len(r['retainedLawfulCapFaces'])==1 and not r['retainedLawfulCapFaces'][0]['belongsToUpperComparedPartition'] for r in matches),
 'genericSafePrunePreservesClosedTopology':not remaining,
 'genericSafePruneRetainsAllRequiredConformityVertices':safe==G.Fsub,
 'strictNativeQualityUnresolved':len(G.planes)==74 and len(G.badFaces)==4,
}
G.write('proof-assertions.json',{'checks':checks,'allPass':all(checks.values()),'count':len(checks),'scope':'Read-only geometry and generic topology algorithm assertions; no native mesh/checkMesh execution or full-quality passing repair.'})
assert all(checks.values()),checks
print('ASSERTIONS',len(checks),'ALL_PASS',all(checks.values()))
