#!/usr/bin/env python3
"""Read-only finite proposal reproduction and complete-stencil independent audit."""
from pathlib import Path
from fractions import Fraction as F
from itertools import combinations
from collections import Counter
import numpy as np
import sys,json,copy,ast,time,hashlib,importlib.util
R=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
A=R.parent/'air';H=A/'.codex-tmp/shared-point-native-feasibility-20261005'
D=H.parent/'internal-polygon-plane-modes-7785-20261005';O=R/'.codex-tmp/finite-native-proposal-independent-20261005'
sys.path[:0]=[str(H),str(D)]
import finite_direction_proposal as S
import select_small_mode as M
import compact_formula_evaluator as E
import audit_internal_polygon_motion as P
import combine_rank_tagged_motion as C
V=P.V
from audit_writer_point_format import exact_core_moments
# AST-select only independent pure geometry helpers. No prior driver is imported/run.
prior=R/'.codex-tmp/shared-plane-independent-20261005/independent_shared_plane_audit.py'
expected='c5c121521ac29002e3e2333d80eef1c8f0faf673d8d5008d0b74c947773cabf8'
assert hashlib.sha256(prior.read_bytes()).hexdigest()==expected
names={'Direct','sha','same','counts','metric_comparison','det4','nontri','eligibility','tagged','pair_documents','json_numpy','boundary_x_volume_moments'}
module=ast.parse(prior.read_text());nodes=[n for n in module.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
assert {n.name for n in nodes}==names
spec=importlib.util.spec_from_file_location('primary_geometry',R/'tools/audit_air_mesh_geometry.py');G=importlib.util.module_from_spec(spec);spec.loader.exec_module(G)
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(prior),'exec'),globals())
def read(name):return json.loads((H/name).read_text())
def verify_manifest(path,expected):
 assert sha(path)==expected;data=json.loads(path.read_text());entries=data['files'];rows=[]
 if isinstance(entries,dict):entries=[{'path':k,**v} for k,v in entries.items()]
 for r in entries:
  p=path.parent/r['path'];assert sha(p)==r['sha256'] and p.stat().st_size==r['bytes'];rows.append({'path':str(p),**{k:r[k] for k in ('bytes','sha256')}})
 return rows
payload=verify_manifest(H/'manifest.json','b58da0fded34de732e35abb89690f605804e3178de2b1cc9d7e7d3d01ceb422a')
assert sha(H/'receipt.json')=='1a80c8cd05a4ab9ab185ac84171ddb6098d8d068a0ce71b8b14a3c4fde3e0548'
identities=read('frozen-input-identities.json')
for r in identities:assert sha(r['path'])==r['sha256'] and Path(r['path']).stat().st_size==r['bytes']
static=[]
for name in ('finite_direction_proposal.py','select_small_mode.py','compact_formula_evaluator.py'):
 tree=ast.parse((H/name).read_text());imports=[]
 for n in ast.walk(tree):
  if isinstance(n,ast.ImportFrom):imports.append(n.module or '')
  if isinstance(n,ast.Import):imports.extend(x.name for x in n.names)
 assert not any('scipy.optimize' in s for s in imports)
 assert not any(isinstance(n,ast.Name) and n.id in {'minimize','linprog','SLSQP','least_squares'} for n in ast.walk(tree))
 static.append({'path':str(H/name),'sha256':sha(H/name),'imports':imports,'optimizerReferencesAbsent':True})
# Tiny direct safety probes, not implementation mirrors or production tests.
toy={'dimension':1,'q0':np.zeros(2),'faceIDs':[1],'fast':lambda q:(None,None,np.array([.001+.0001*q[0]])),'constraints':lambda q:np.array([1+q[0],1-q[0]])}
probe=S.proposal(toy,{1},max_evaluations=4);assert probe['evaluations']==4 and probe['stop']=='evaluation cap'
refused=S.project(np.ones(2),np.zeros((1,2)),np.array([-1.]));assert not refused[1] and refused[2]==0
smallcap=S.project(np.array([-2.,-2.]),np.eye(2),np.zeros(2),updates=1);assert not smallcap[1] and smallcap[2]<=1
wall=S.proposal(toy,{1},wall_seconds=0);assert wall['evaluations']==0 and wall['stop']=='wall cap'
components=[];reconstructed_old=[];reconstructed_new=[];allmoving=[];polygons=[];begun=time.monotonic()
def format_copy(rank,doc,new,moving):
 from backend.aircooling.runner import polymesh_io as IO
 source=Path(doc['mesh'])/f'processor{rank}/constant/polyMesh/points'
 candidate=H/f'finite-rank{rank}-fraction0.95-points.binary-format-copy'
 x,y=source.read_bytes(),candidate.read_bytes();info=IO.foam_header_info(x);assert info['format']=='binary' and info['scalar_bytes']==8 and b'LSB;label=32;scalar=64' in x[:2048]
 count,offset=IO._binary_list_at(x,IO._payload_start(x));assert len(x)==len(y) and x[:offset]==y[:offset] and x[offset+24*count:]==y[offset+24*count:]
 a,b=IO.read_points(source),IO.read_points(candidate);fixed=np.ones(count,dtype=bool);fixed[moving]=False
 assert same(a[fixed],b[fixed]) and all(same(new['points'][p],b[int(p)]) for p in new['points'])
 changed=np.flatnonzero(np.any(a.view('<u8')!=b.view('<u8'),axis=1)).tolist();assert set(changed)==set(moving)
 return {'sourceSHA256':sha(source),'candidateSHA256':sha(candidate),'changedPointLabels':changed,'headerAndSuffixByteExact':True,'allOtherRankPointsDPExact':True,'candidateDocumentDPExact':True,'nativeWriterInvoked':False}
for rank in (1,3):
 raw=read(f'finite-rank{rank}-ratio-result.json');old=raw['oldDocument'];before=Direct(old).metrics()
 targets={r['face'] for r in before['faces'] if r.get('ratio',1)<.01};assert len(targets)==1
 target=next(iter(targets));choice=M.select(old,target);moving=choice['movingPoints'];B=choice['motionBasis']
 assert moving==raw['movingPoints'] and B==raw['motionBasis']
 # IDs/dictionary insertion do not choose the mode; only actual geometry/topology does.
 renamed=copy.deepcopy(old);label_map={int(p):123+3*int(p) for p in old['points']}
 renamed['points']={str(label_map[int(p)]):xyz for p,xyz in reversed(list(old['points'].items()))}
 renamed['faces']={k:{**r,'pointIDs':[label_map[p] for p in r['pointIDs']]} for k,r in reversed(list(old['faces'].items()))}
 renamed_choice=M.select(renamed,target)
 assert renamed_choice['movingPoints']==[label_map[p] for p in moving] and renamed_choice['motionBasis']==B
 g,t,binding=V.raw_binding(old,moving,Path(raw['sourceBinding']['mesh']),rank,A/'.codex-tmp/installed-writer-cua-7785c33f/case-file-manifest.json');assert binding==raw['sourceBinding']
 valid=P.validate_internal_polygon if rank==1 else V.validate_structure
 ev=E.evaluator(old,moving,structural_validator=valid,motion_basis=B,target_faces=targets)
 events={'fastCalls':0,'constraintCalls':0,'optimizerCalls':0}
 def profile(frame,event,arg):
  if event!='call':return
  filename=frame.f_code.co_filename.replace(chr(92),'/')
  if '/scipy/optimize/' in filename:events['optimizerCalls']+=1;raise AssertionError('optimizer called')
  if filename==str(H/'compact_formula_evaluator.py'):
   if frame.f_code.co_name=='fast':events['fastCalls']+=1
   if frame.f_code.co_name=='constraints':events['constraintCalls']+=1
 sys.setprofile(profile)
 try:proposal=S.proposal(ev,targets)
 finally:sys.setprofile(None)
 assert events['optimizerCalls']==0 and events['fastCalls']==2*proposal['evaluations'] and events['constraintCalls']==proposal['evaluations']
 assert proposal['evaluations']==raw['proposal']['evaluations'] and proposal['history']==raw['proposal']['history'] and same(proposal['parameters'],raw['proposal']['parameters'])
 cand,qq,lineage=ev['actual'](proposal['parameters']*.95);stored=next(z for z in raw['results'] if z['fraction']==.95)
 assert cand==stored['candidateDocument']
 auditor=P.authorize_internal_polygon if rank==1 else V.authorize
 check=auditor(old,cand,moving);assert check['mathematicalLocalCandidateAccepted']
 after=Direct(cand).metrics();assert counts(before)==check['oldCounts'] and counts(after)==check['newCounts']
 outside=[{'cell':c,'CCDPExact':same(Direct(old).cell(c)[2],Direct(cand).cell(c)[2]),'VDPExact':same(Direct(old).cell(c)[3],Direct(cand).cell(c)[3])} for c in old['outsideCells']]
 assert all(z['CCDPExact'] and z['VDPExact'] for z in outside)
 exact0,shell=boundary_x_volume_moments(old);exact1,nshell=boundary_x_volume_moments(cand);assert exact0==exact1==exact_core_moments(old)==exact_core_moments(cand) and shell==nshell
 gaps=V.global_gap(g,cand,moving);assert all(r['exceedsWriter1MicrometreAnd3MicrometreSafety'] for r in gaps)
 fmt=format_copy(rank,binding,cand,moving);rows=nontri(old,cand,moving)
 eligibility_result=eligibility(old,moving,[])
 for zone in ('faceZones','cellZones'):
  source=g.poly_dir/zone;data=source.read_text();assert ('faceLabels' not in data or all(int(x)==0 for x in __import__('re').findall(r'faceLabels\s+List<label>\s+(\d+)',data)))
  if zone=='cellZones':assert __import__('re').search(r'
0\(\)',data)
 components.append({'rank':rank,'choice':choice,'targetFacesAutomaticallyDerived':sorted(targets),'sourceBinding':binding,'proposal':{**proposal,'parameters':proposal['parameters'].tolist()},'runtimeEvaluationInstrumentation':events,'selectorInvariantUnderPointIDRenamingAndDictionaryOrder':True,
 'candidateCoordinatesAndHistoryDPExactComparedToOriginalArtifact':True,'oldCounts':counts(before),'newCounts':counts(after),'independentScalarMetricDifference':metric_comparison(after,check['newActualMetrics']),'fullEligibility':eligibility_result,'outsideDP':outside,'globalGaps':gaps,'nontriFractionDeterminants':rows,'binaryFormat':fmt,'independentShellVolumeFirstMomentBefore':exact0,'independentShellVolumeFirstMomentAfter':exact1,
 'geometryAndRoleGuard':{k:check[k] for k in ('hardOriginalPassingRoleGuard','cellGeometryGuard','faceGeometryGuard','warningGlobalCeilingGuard','maximumWarningDegrees','sumVolumeRelativeDifference','sumFirstMomentDifferenceM4')},'originalPassingMargins':stored['allPassingMargins']})
 reconstructed_old.append(tagged(old,rank,C.STRIDE));reconstructed_new.append(tagged(cand,rank,C.STRIDE));allmoving.extend(p+rank*C.STRIDE for p in moving)
 if rank==1:polygons.extend(p+rank*C.STRIDE for p in moving[1:])
joint=read('combined-four-point-two-ratio-audit.json');old=pair_documents(*reconstructed_old);new=pair_documents(*reconstructed_new)
assert old==joint['oldDocument'] and new==joint['candidateDocument'] and allmoving==joint['movingPoints'] and polygons==joint['internalPolygonPoints']
check=C.authorize(old,new,allmoving,(),polygons);assert check['mathematicalLocalCandidateAccepted']
a,b=Direct(old).metrics(),Direct(new).metrics();assert counts(a)==joint['jointAudit']['oldCounts'] and counts(b)==joint['jointAudit']['newCounts']
v0,shell=boundary_x_volume_moments(old);v1,_=boundary_x_volume_moments(new);assert v0==v1==exact_core_moments(old)==exact_core_moments(new)
wn0={x['face'] for x in a['faces'] if x.get('nonOrthogonalityDegrees',0)>70};wn1={x['face'] for x in b['faces'] if x.get('nonOrthogonalityDegrees',0)>70}
# Negative long endpoint is independently re-authorized; no long optimizer rerun.
long=read('finite-projection-result.json');negative=next(x for x in long['results'] if x['fraction']==1)
reject=P.authorize_internal_polygon(long['oldDocument'],negative['candidateDocument'],long['movingPoints']);assert not reject['mathematicalLocalCandidateAccepted'] and reject['oldCounts']['D']==8 and reject['newCounts']['D']==9
assert counts(Direct(negative['candidateDocument']).metrics())==reject['newCounts']
report={'format':'independent-finite-native-feasibility/v1','payloadManifestSHA256':sha(H/'manifest.json'),'payloadFilesVerified':payload,'frozenIdentitiesVerified':identities,'staticSourceAudit':static,'independentGeometryHelperPath':str(prior),'independentGeometryHelperSHA256':expected,
 'safetyProbes':{'fourRequestEvaluationCap':True,'zeroWallNoEvaluations':True,'contradictoryZeroGradientRowRefused':True,'insufficientProjectionBudgetRefused':True},'components':components,
 'joint':{'originalAndCandidateDocumentsExactlyReconstructed':True,'movingPoints':allmoving,'core':len(old['children']),'outside':len(old['outsideCells']),'oldCounts':counts(a),'newCounts':counts(b),'independentMetricDifference':metric_comparison(b,check['newActualMetrics']),'hardPassingDWRSkewRoleGuard':check['hardOriginalPassingRoleGuard'],'outsideCCVolumeDPExact':all(r['CC_DP_exact'] and r['V_DP_exact'] for r in check['outsideByteMoments']),'independentBoundaryDivergenceFractionVolumeFirstMomentBefore':v0,'independentBoundaryDivergenceFractionVolumeFirstMomentAfter':v1,'maximumWarningDegrees':check['maximumWarningDegrees'],'newNWarnings':sorted(wn1-wn0),'removedNWarnings':sorted(wn0-wn1),'DPVolumeRelativeDifference':check['sumVolumeRelativeDifference'],'DPFirstMomentDifferenceM4':check['sumFirstMomentDifferenceM4']},
 'longNegative':{'originalEvaluationsStored':long['evaluations'],'endpointNewDRejected':True,'oldCounts':reject['oldCounts'],'newCounts':reject['newCounts'],'actualPassingRoleFailures':reject['lineage']['D']['originalPassingRoleFailures']},
 'limitations':['Prototype selection relies on a complete actual-bound point star supplied by the adapter; raw_binding verifies completeness here. Native port must construct and protect that star itself.',
 'Only3/4-parameter interiors are reproduced. No earlier9/11-dimensional D/flat candidate or whole909D/100W feasibility is implied.',
 'Proposal requests each compute fast fields twice; instrumentation confirms168/8 requests entail336/16 field computations, excluding setup/final guards.',
 'Finite halfspace residuals and nonlinear proposal constraints are not authorization. Actual original-formula geometry, roles, exact total moments, writer gap and format guards are separate.',
 'No coupled or moving physical point occurs in these two complete stencils; no changed MPI or physical-flat-support implementation is proved.',
 'Python replay does not prove C++ numeric/cache/updateGeomPoints/coupled-certificate behavior, memory RSS, parallel stop ordering, or whole-job<=300s.',
 'The current called project path always has finite nonempty trust rows and positive update budget; a general native helper must explicitly guard empty or nonfinite projection inputs and zero budget.'],
 'elapsedSeconds':time.monotonic()-begun,'nativeAcceptance':False,'caseMutation':False,'optimizerLibraryCalled':False,'scriptSHA256':sha(__file__)}
(O/'independent-audit.json').write_text(json.dumps(report,indent=2,default=json_numpy)+'
')
print(json.dumps({'replayedRequests':[x['proposal']['evaluations'] for x in components],'fieldComputations':[x['runtimeEvaluationInstrumentation']['fastCalls'] for x in components],'dimensions':[len(x['choice']['motionBasis'][0]) for x in components],'joint':report['joint'],'negative':report['longNegative'],'auditSHA256':sha(O/'independent-audit.json'),'elapsedSeconds':report['elapsedSeconds']},indent=2,default=json_numpy))
