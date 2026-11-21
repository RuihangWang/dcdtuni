#!/usr/bin/env python3
'''Independent read-only source binding, compound reconstruction and geometry checks.'''
from pathlib import Path
from fractions import Fraction as F
from itertools import combinations
from collections import Counter
import hashlib,json,sys,copy,importlib.util,time
import numpy as np
R=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
A=R.parent/'air';H=A/'.codex-tmp/internal-polygon-plane-modes-7785-20261005'
OUT=R/'.codex-tmp/shared-plane-independent-20261005'
sys.path.insert(0,str(H))
import audit_internal_polygon_motion as P
import combine_rank_tagged_motion as C
V=P.V
from audit_writer_point_format import exact_core_moments
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def same(a,b):
 a=np.asarray(a,dtype='<f8');b=np.asarray(b,dtype='<f8')
 return a.shape==b.shape and np.array_equal(a.view('<u8'),b.view('<u8'))
def manifest(path):
 m=json.loads(path.read_text());files=m.get('files',m);rows=[]
 for rel,entry in files.items():
  expected=entry if isinstance(entry,str) else entry['sha256']
  p=path.parent/rel
  actual=sha(p);assert actual==expected,(p,actual,expected)
  rows.append({'path':str(p),'sha256':actual,'bytes':p.stat().st_size})
 return {'path':str(path),'sha256':sha(path),'files':rows}
spec=importlib.util.spec_from_file_location('primary_geometry',R/'tools/audit_air_mesh_geometry.py')
G=importlib.util.module_from_spec(spec);spec.loader.exec_module(G)
class Direct:
 def __init__(self,doc):
  self.doc=doc;self.p={int(k):np.array(x,dtype=float) for k,x in doc['points'].items()}
  self.f={int(k):r for k,r in doc['faces'].items()};self.c={int(k):r for k,r in doc['cells'].items()}
  self.fg={};self.cg={}
 def face(self,f):
  if f not in self.fg:
   xyz=np.array([self.p[p] for p in self.f[f]['pointIDs']])
   self.fg[f]=G.Geometry.from_arrays(xyz,[list(range(len(xyz)))]).face(0)[:2]
  return self.fg[f]
 def cell(self,c):
  if c not in self.cg:
   fs=self.c[c]['faces'];sf=[];fc=[]
   for f in fs:
    s,x=self.face(f);sf.append(s if self.f[f]['owner']==c else -s);fc.append(x)
   sf=np.array(sf);fc=np.array(fc);estimate=fc.mean(axis=0)
   pyramid=np.einsum('ij,ij->i',sf,fc-estimate)
   vol=float(pyramid.sum()/3)
   cc=(pyramid[:,None]*(.75*fc+.25*estimate)).sum(axis=0)/pyramid.sum()
   self.cg[c]=(sf,fc,cc,vol)
  return self.cg[c]
 def D(self,c):
  sf,fc,cc,v=self.cell(c)
  sel=[s for f,s in zip(self.c[c]['faces'],sf) if self.f[f]['neighbour'] is not None or V.coupled(self.f[f]['patch'])]
  amount=sum(float(np.linalg.norm(s)) for s in sel)/len(sel)
  # Explicit symmetric 3x3 determinant, independently from the matrix det helper.
  q=np.zeros((3,3))
  for s in sel:q+=np.outer(s/amount,s/amount)
  x,y,z=q[0,0],q[1,1],q[2,2];xy,xz,yz=q[0,1],q[0,2],q[1,2]
  return abs(x*y*z+2*xy*xz*yz-x*yz*yz-y*xz*xz-z*xy*xy)/8
 def metrics(self):
  cells=[{'cell':c,'D':self.D(c)} for c in self.doc['children']+self.doc['outsideCells']]
  core=set(self.doc['children'])
  fs=sorted(f for f,r in self.f.items() if r['owner'] in core or r['neighbour'] in core)
  faces=[]
  for f in fs:
   r=self.f[f];sf,fc=self.face(f);oc,ov=self.cell(r['owner'])[2:]
   nc,nv=(self.cell(r['neighbour'])[2:] if r['neighbour'] is not None else (None,None))
   n=sf/np.linalg.norm(sf)
   d=nc-oc if nc is not None else n*np.dot(n,fc-oc)
   cp=fc-oc;skewvec=cp-(np.dot(sf,cp)/(np.dot(sf,d)+1e-150))*d
   unit=skewvec/(np.linalg.norm(skewvec)+1e-150)
   norm=(.2 if nc is not None else .4)*np.linalg.norm(d)+1e-150
   for p in r['pointIDs']:norm=max(norm,abs(np.dot(unit,self.p[p]-fc)))
   row={'face':f,'skew':float(np.linalg.norm(skewvec)/norm)}
   if nc is not None:
    do=abs(float(np.dot(sf,fc-oc)));dn=abs(float(np.dot(sf,nc-fc)))
    row['weight']=min(do,dn)/(do+dn+1e-300)
    row['ratio']=min(ov,nv)/(max(ov,nv)+1e-300)
    row['nonOrthogonalityDegrees']=float(np.degrees(np.arccos(np.clip(np.dot(sf,d)/(np.linalg.norm(sf)*np.linalg.norm(d)+1e-150),-1,1))))
   faces.append(row)
  return {'cells':cells,'faces':faces}
def counts(q):
 return {k:sum(not np.isfinite(r[k]) or (r[k]>th if upper else r[k]<th) for r in q['cells' if k=='D' else 'faces'] if k in r) for k,(th,upper) in V.U.LIMITS.items()}
def metric_comparison(a,b):
 result={}
 for k in V.U.LIMITS:
  typ='cells' if k=='D' else 'faces';key='cell' if k=='D' else 'face'
  x={r[key]:r[k] for r in a[typ] if k in r};y={r[key]:r[k] for r in b[typ] if k in r}
  assert x.keys()==y.keys()
  result[k]=max(abs(x[i]-y[i]) for i in x)
 return result
def det4(doc,ids):
 p=[[F.from_float(float(x)) for x in doc['points'][str(k)]] for k in ids]
 a=[[p[i][j]-p[0][j] for j in range(3)] for i in range(1,4)]
 return a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])-a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])+a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0])
def nontri(old,new,moving):
 a,b=Direct(old),Direct(new);result=[]
 for f,r in a.f.items():
  if len(r['pointIDs'])<4 or not set(moving).intersection(r['pointIDs']):continue
  four=list(combinations(r['pointIDs'],4))
  x=[det4(old,ids) for ids in four];y=[det4(new,ids) for ids in four]
  os,oc=a.face(f);ns,nc=b.face(f);on=os/np.linalg.norm(os);nn=ns/np.linalg.norm(ns)
  offset=max(abs(np.dot(on,b.p[p]-oc)) for p in r['pointIDs'])
  result.append({'face':f,'points':r['pointIDs'],'allFourTupleCount':len(four),
    'allSignedDeterminantsFractionExactUnchanged':x==y,
    'allOriginalFourTuplesExactlyPlanar':all(v==0 for v in x),
    'normalRotationDegrees':float(np.degrees(np.arccos(np.clip(np.dot(on,nn),-1,1)))),
    'proposedMaximumDistanceToOldFacePlaneM':float(offset)})
 assert all(r['allSignedDeterminantsFractionExactUnchanged'] for r in result)
 return result
def eligibility(doc,moving,physical):
 m=set(moving);core=set(doc['children']);outside=set(doc['outsideCells'])
 incident={f for f,r in ((int(k),v) for k,v in doc['faces'].items()) if m.intersection(r['pointIDs'])}
 actual_phys={p for f in incident for p in doc['faces'][str(f)]['pointIDs'] if p in m and doc['faces'][str(f)]['patch'] is not None}
 assert actual_phys==set(physical)
 protected=[f for c in core|outside for f in doc['cells'][str(c)]['faces'] if V.coupled(doc['faces'][str(f)]['patch']) or doc['faces'][str(f)]['sourceZone']!=-1]
 assert not protected
 return {'completeCells':len(core|outside),'core':len(core),'outside':len(outside),'derivedPhysicalMovingPoints':sorted(actual_phys),
   'protectedFacesInCompleteStencil':protected,'movingSetBounded':len(moving),'incidentFaces':len(incident)}
def tagged(doc,rank,stride):
 o=copy.deepcopy(doc)
 o['points']={str(int(k)+rank*stride):v for k,v in doc['points'].items()}
 o['faces']={}
 for k,r in doc['faces'].items():
  z=copy.deepcopy(r);z['owner']+=rank*stride
  if z['neighbour'] is not None:z['neighbour']+=rank*stride
  z['pointIDs']=[p+rank*stride for p in z['pointIDs']];o['faces'][str(int(k)+rank*stride)]=z
 o['cells']={str(int(k)+rank*stride):{**r,'faces':[f+rank*stride for f in r['faces']]} for k,r in doc['cells'].items()}
 for k in ('children','outsideCells'):o[k]=[c+rank*stride for c in doc[k]]
 o['apexWrittenPoint']+=rank*stride
 return o
def pair_documents(a,b):
 out=copy.deepcopy(b)
 for k in ('points','faces','cells'):
  assert not set(out[k]).intersection(a[k])
  out[k].update(copy.deepcopy(a[k]))
 for k in ('children','outsideCells'):out[k]=a[k]+b[k]
 return out
def json_numpy(value):
 if isinstance(value,np.generic):return value.item()
 if isinstance(value,np.ndarray):return value.tolist()
 raise TypeError(type(value).__name__)
def physical_oracle(old,new,point):
 faces=[int(f) for f,r in old['faces'].items() if r['patch'] is not None and point in r['pointIDs']]
 rows=[old['faces'][str(f)] for f in faces]
 assert rows and all(r['patch']==rows[0]['patch'] and r['sourceZone']==-1 and not V.coupled(r['patch']) for r in rows)
 labels={p for r in rows for p in r['pointIDs']}
 axes=[a for a in range(3) if len({F.from_float(float(old['points'][str(p)][a])) for p in labels})==1]
 assert len(axes)==1
 axis=axes[0];axes=[a for a in range(3) if a!=axis]
 plane=old['points'][str(point)][axis]
 assert all(same(new['points'][str(p)][axis],plane) for p in labels)
 def signed(doc,ids):
  xy=[[F.from_float(float(doc['points'][str(p)][a])) for a in axes] for p in ids]
  out=[F(0)]*3
  for a,b in zip(xy,xy[1:]+xy[:1]):
   cross=a[0]*b[1]-b[0]*a[1]
   out[0]+=cross;out[1]+=(a[0]+b[0])*cross;out[2]+=(a[1]+b[1])*cross
  return tuple(out)
 edges=Counter()
 for r in rows:
  ids=r['pointIDs']
  for x,y in zip(ids,ids[1:]+ids[:1]):edges[(x,y)]+=1
 for x,y in list(edges):
  if x<y and edges[(x,y)] and edges[(y,x)]:
   assert edges[(x,y)]==edges[(y,x)]==1
   del edges[(x,y)];del edges[(y,x)]
 assert all(n==1 for n in edges.values()) and all(point not in e for e in edges)
 assert len({x for x,y in edges})==len(edges)==len({y for x,y in edges})
 nxt={x:y for x,y in edges};first=min(nxt);ring=[first]
 while nxt[ring[-1]]!=first:
  q=nxt[ring[-1]];assert q not in ring;ring.append(q)
 assert len(ring)==len(edges)
 assert all(same(old['points'][str(p)],new['points'][str(p)]) for p in ring)
 before=tuple(sum((signed(old,r['pointIDs'])[i] for r in rows),F(0)) for i in range(3))
 after=tuple(sum((signed(new,r['pointIDs'])[i] for r in rows),F(0)) for i in range(3))
 assert before==after==signed(old,ring)==signed(new,ring)
 sense=1 if before[0]>0 else -1
 for r in rows:
  assert len(r['pointIDs'])==3 and len(set(r['pointIDs']))==3 and signed(new,r['pointIDs'])[0]*sense>0
 return {'point':point,'axis':axis,'plane':plane,'patch':rows[0]['patch'],'faces':faces,'outerRing':ring,
 'normalAndOuterRingDPExact':True,'allPhysicalPiecesPositiveTriangles':True,
 'boundaryOracleSignedTwiceAreaSixFirstMoments':[str(x) for x in signed(old,ring)],
 'pieceSumSignedTwiceAreaSixFirstMoments':[str(x) for x in after],
 'boundaryOracleAndPiecesFractionExact':True,'rawSTLPositionNotChangedOrRecertified':True}
def binary_copy_oracle(rank,old,new,moving):
 from backend.aircooling.runner import polymesh_io as IO
 source=Path(left['actualSourceBinding']['mesh'])/('processor'+str(rank))/'constant/polyMesh/points'
 copy_path=H/('combined-rank'+str(rank)+'-points.binary-format-copy')
 a,b=source.read_bytes(),copy_path.read_bytes()
 header=IO.foam_header_info(a)
 assert header['format']=='binary' and header['scalar_bytes']==8 and b'LSB;label=32;scalar=64' in a[:2048]
 count,offset=IO._binary_list_at(a,IO._payload_start(a))
 assert len(a)==len(b) and a[:offset]==b[:offset] and a[offset+count*24:]==b[offset+count*24:]
 xyz0=IO.read_points(source);xyz1=IO.read_points(copy_path)
 assert xyz0.shape==xyz1.shape==(count,3)
 fixed=np.ones(count,dtype=bool);fixed[moving]=False
 assert same(xyz0[fixed],xyz1[fixed])
 assert all(same(new['points'][str(p)],xyz1[p]) for p in map(int,new['points']))
 changed=np.flatnonzero(np.any(xyz0.view('<u8')!=xyz1.view('<u8'),axis=1)).tolist()
 assert set(changed)==set(moving)
 return {'rank':rank,'sourcePointsSHA256':sha(source),'candidateCopySHA256':sha(copy_path),'pointCount':count,
 'payloadOffset':offset,'headerAndSuffixByteExact':True,'allOtherActualRankPointsDPExact':True,
 'completeLocalDocumentPointsDPExact':True,'actualChangedPointLabels':changed,
 'installedNativeWriterInvoked':False}
start=time.monotonic()
frozen=manifest(H/'manifest.json')
receipt=json.loads((H/'receipt.json').read_text())
assert sha(H/'manifest.json')=='fcaf628d79329232ba73a6d400b49d35132a66d454e43e1087b97836b3897edb'
assert sha(H/'receipt.json')=='638177ec3f2c7623abd568ddef1a4884d1b4b7bd3b1a9cdfb34c0971f22c5308'
deps=[]
for name,expected in receipt['dependencyManifestSHA256'].items():
 path=H.parent/name/'manifest.json';assert sha(path)==expected;deps.append(manifest(path))
left=json.loads((H/'targeted-ratio-internal-edge-audit.json').read_text())
chosen=next(r for r in left['results'] if r['run']==1 and r['fraction']==.95)
right=json.loads((H.parent/'shared-point-ratio-7785-20261005/combined-eleven-point-audit.json').read_text())
joint=json.loads((H/'combined-fourteen-point-two-rank-audit.json').read_text())
bindings=[];component=[]
for rank,raw,old,new,moving,physical,kind in [
 (1,left,left['oldDocument'],chosen['candidateDocument'],left['movingPoints'],[],'internalPolygon'),
 (3,right,right['oldDocument'],right['candidateDocument'],right['movingPoints'],right['physicalPoints'],'flat')]:
 mesh=Path(raw['actualSourceBinding']['mesh'])
 g,t,binding=V.raw_binding(old,moving,mesh,rank,A/'.codex-tmp/installed-writer-cua-7785c33f/case-file-manifest.json')
 assert binding==raw['actualSourceBinding'];bindings.append(binding)
 state=P.authorize_internal_polygon(old,new,moving) if rank==1 else C.Flat.authorize_flat(old,new,moving,physical_points=physical)
 assert state['mathematicalLocalCandidateAccepted']
 directold,directnew=Direct(old),Direct(new);mq0,mq1=directold.metrics(),directnew.metrics()
 assert counts(mq0)==state['oldCounts'] and counts(mq1)==state['newCounts']
 outside=[{'cell':c,'CC_DPExact':same(directold.cell(c)[2],directnew.cell(c)[2]),'V_DPExact':same(directold.cell(c)[3],directnew.cell(c)[3])} for c in old['outsideCells']]
 assert all(z['CC_DPExact'] and z['V_DPExact'] for z in outside)
 gap=V.global_gap(g,new,moving)
 assert all(z['exceedsWriter1MicrometreAnd3MicrometreSafety'] for z in gap)
 rows=nontri(old,new,moving)
 component.append({'rank':rank,'eligibility':eligibility(old,moving,physical),
  'oldCounts':counts(mq0),'newCounts':counts(mq1),
  'directMaximumDifferenceFromSavedOldMetrics':metric_comparison(mq0,state['oldActualMetrics']),
  'directMaximumDifferenceFromSavedNewMetrics':metric_comparison(mq1,state['newActualMetrics']),
  'allFourTupleNontriDeterminants':rows,'outsideDP':outside,'globalGap':gap,
  'independentPhysicalBoundaryOracles':[physical_oracle(old,new,p) for p in physical],
  'independentBinaryFormatOracle':binary_copy_oracle(rank,old,new,moving),
  'exactCoreBoundaryVolumeFirstMomentFractionEqual':exact_core_moments(old)==exact_core_moments(new),
  'DPVolumeRelativeDifference':state['sumVolumeRelativeDifference'],'DPFirstMomentDifferenceM4':state['sumFirstMomentDifferenceM4'],
  'scope':state['scope'],'allOriginalDWRSkewPassingRolesGuard':state['hardOriginalPassingRoleGuard'],
  'stockAndConservativeGeometryPass':state['cellGeometryGuard'] and state['faceGeometryGuard']})
stride=joint['rankTagStride']
for raw in (left,right):
 assert max(map(int,raw['oldDocument']['points']))<stride
 assert max(map(int,raw['oldDocument']['faces']))<stride
 assert max(map(int,raw['oldDocument']['cells']))<stride
rebuiltold=pair_documents(tagged(left['oldDocument'],1,stride),tagged(right['oldDocument'],3,stride))
rebuiltnew=pair_documents(tagged(chosen['candidateDocument'],1,stride),tagged(right['candidateDocument'],3,stride))
assert rebuiltold==joint['oldDocument'] and rebuiltnew==joint['candidateDocument']
old,new=joint['oldDocument'],joint['candidateDocument']
a=C.authorize(old,new,joint['movingPoints'],joint['physicalPoints'],joint['internalPolygonPoints'])
assert a['mathematicalLocalCandidateAccepted']
q0,q1=Direct(old).metrics(),Direct(new).metrics()
assert counts(q0)==joint['jointAudit']['oldCounts'] and counts(q1)==joint['jointAudit']['newCounts']
oldwarn={z['face'] for z in q0['faces'] if z.get('nonOrthogonalityDegrees',0)>70}
newwarn={z['face'] for z in q1['faces'] if z.get('nonOrthogonalityDegrees',0)>70}
exact_old,exact_new=exact_core_moments(old),exact_core_moments(new)
assert exact_old==exact_new and list(exact_old)==joint['coreFractionVolumeMomentBefore'] and list(exact_new)==joint['coreFractionVolumeMomentAfter']
result={'scope':'Independent bounded two-rank/14-point offline verification; no mesh/native/API/product acceptance.',
 'frozenManifest':frozen,'dependencyManifests':deps,'actualSourceBindings':bindings,'components':component,
 'unified':{'tagStride':stride,'originalAndCandidateCompoundDocumentsExactlyReconstructed':True,
   'coreCount':len(old['children']),'completeOutsideCount':len(old['outsideCells']),
   'oldCounts':counts(q0),'newCounts':counts(q1),
   'explicitScalarOFDeterminantAndOtherMetricsRecomputedOnUnifiedDocuments':True,
   'maximumDifferenceFromSavedOldMetrics':metric_comparison(q0,joint['jointAudit']['oldActualMetrics']),
   'maximumDifferenceFromSavedNewMetrics':metric_comparison(q1,joint['jointAudit']['newActualMetrics']),
   'originalDWRSkewRolesProtected':a['hardOriginalPassingRoleGuard'],
   'oldNmax':max(z.get('nonOrthogonalityDegrees',0) for z in q0['faces']),
   'newNmax':max(z.get('nonOrthogonalityDegrees',0) for z in q1['faces']),
   'new70DegreeWarningFaces':sorted(newwarn-oldwarn),'removed70DegreeWarningFaces':sorted(oldwarn-newwarn),
   'newWarningCount':len(newwarn-oldwarn),'removedWarningCount':len(oldwarn-newwarn),
   'separateComponentGeometryGuardsPassed':all(x['stockAndConservativeGeometryPass'] for x in component),
   'outsideDPExact':all(z['CC_DP_exact'] and z['V_DP_exact'] for z in a['outsideByteMoments']),
   'fractionExactTotalBoundaryVolumeFirstMoment':True,'exactCoreBefore':exact_old,'exactCoreAfter':exact_new,
   'DPVolumeRelativeDifference':a['sumVolumeRelativeDifference'],'DPFirstMomentDifferenceM4':a['sumFirstMomentDifferenceM4']},
 'claimLimitations':['The two stencils are disjoint rank-tagged domains with no coupled face in either complete touched stencil, not a new cross-rank coupled solve.',
   'Internal nontri planes may rotate; exact signed 4-tuple determinants preserve original planarity/warp amount. Old support-plane locking is not an authorization gate.',
   'D/W/R/skew old passing roles protected. N warning labels may exchange; not every old <=70 face remains <=70.',
   'One rank3 physical-flat interior node moves tangentially with fixed exact literal boundary footprint/normal; the existing raw-STL 1.6203125mm offset is not corrected or certified.',
   'Python explicit scalar determinant arithmetic differs by quantified small rounding from NumPy matrix determinant and is not installed C++ binary/writer/checker acceptance.'],
 'elapsedSeconds':time.monotonic()-start,'nativeAcceptance':False,'meshMutation':False,
 'independentScriptSHA256':sha(__file__)}
(OUT/'independent-audit.json').write_text(json.dumps(result,indent=2,default=json_numpy)+chr(10))
print(json.dumps({'manifestFiles':len(frozen['files']),'dependencyPayloadFiles':sum(len(z['files']) for z in deps),'boundFiles':sum(len(z['boundFileSHA256']) for z in bindings),
 'components':[{k:z[k] for k in ('rank','eligibility','oldCounts','newCounts','DPVolumeRelativeDifference','DPFirstMomentDifferenceM4')} for z in component],
 'unified':result['unified'],'elapsed':result['elapsedSeconds'],'outputSHA256':sha(OUT/'independent-audit.json')},indent=2,default=json_numpy))
