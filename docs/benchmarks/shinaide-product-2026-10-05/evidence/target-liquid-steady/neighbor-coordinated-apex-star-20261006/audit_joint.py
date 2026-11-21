from pathlib import Path
from fractions import Fraction as F
from collections import Counter
import json,math,struct,hashlib,sys,copy
here=Path(__file__).parent
S=here.parent/'complete-interior-edge-vertex-star-20261006'
ns={'__file__':str(S/'audit_complete_star.py'),'__name__':'fraction_readonly'}
exec(compile((S/'audit_complete_star.py').read_text().split('def run():')[0],str(S/'audit_complete_star.py'),'exec'),ns)
vm,summed,outward,ulpdiff=[ns[k]for k in ['vm','summed','outward','ulpdiff']]
old=json.loads((here/'complete-star-input.json').read_text())
new=json.loads((here/'joint-six-dimensional-v2/candidate-best.json').read_text())
top=json.loads((here/'star-topology-report.json').read_text())
raw=[json.loads(l)for l in (here/'typed-joint-v2/installed-kernel-raw.jsonl').read_text().splitlines()if l.startswith('{"kind"')]
def certify(old,new):
 oc=set(old['children']);nc=set(new['children'])
 assert not (nc-oc)&{v for r in old['faces'].values()for v in [r['owner'],r['neighbour']]},'label-collision'
 moved=[]
 for p,xyz in old['points'].items():
  if struct.pack('ddd',*xyz)!=struct.pack('ddd',*new['points'][p]):moved.append(int(p))
 assert moved==[28144],'non-approved-point-movement'
 assert set(new['points'])-set(old['points'])=={'591824'},'new-point-identity'
 assert all(28142 not in r['pointIDs']for r in new['faces'].values()),'removed-point-still-used'
 for c,r in new['cells'].items():
  assert set(r['faces'])=={int(f)for f,rr in new['faces'].items()if int(c)in [rr['owner'],rr['neighbour']]},'incomplete-cell-incidence'
 shells=[]
 for f in top['vertexStarExteriorFaces']:
  a,b=old['faces'][str(f)],new['faces'][str(f)]
  for k in ['patch','sourceBase','sourceZone','nativeFace']:assert a[k]==b[k],'outer-role'
  r,s=outward(a,oc),outward(b,nc);assert r==s,'outer-ring-or-point0'
  assert [old['points'][str(p)]for p in r]==[new['points'][str(p)]for p in s],'outer-coordinates'
  ao=a['neighbour']if a['owner']in oc else a['owner'];bo=b['neighbour']if b['owner']in nc else b['owner']
  assert ao==bo,'outside-endpoint'
  shells.append({'face':f,'orderedCoreOutwardRing':r,'actualOutside':ao,'patch':a['patch'],'sourceBase':a['sourceBase'],'sourceZone':a['sourceZone'],'nativeFace':a['nativeFace']})
 assert summed(old,oc)==summed(new,nc),'cavity-V-M1'
 outside=[]
 for c in old['outsideCells']:
  assert vm(old,c)==vm(new,c),'outside-V-M1'
  ov={p for f in old['cells'][str(c)]['faces']for p in old['faces'][str(f)]['pointIDs']}
  assert 28144 not in ov,'moving-point-outside'
  outside.append(c)
 for c in nc:
  assert vm(new,c)[0]>0,'positive-volume'
  edges=Counter()
  for f in new['cells'][str(c)]['faces']:
   rr=new['faces'][str(f)];ids=rr['pointIDs']if rr['owner']==c else [rr['pointIDs'][0]]+rr['pointIDs'][:0:-1]
   for a,b in zip(ids,ids[1:]+ids[:1]):edges[a,b]+=1
  assert all(edges[a,b]==edges[b,a]==1 for a,b in edges),'directed-edge-closure'
 return {'movedOriginalPoints':moved,'newPoint':591824,'literalOuterFaces':shells,'allOutsideFractionShapeExact':outside,'FractionCavityVAndFirstMomentExact':True,'allNewCorePositiveVolume':True,'allCoreDirectedEdgesCancelOnce':True,'removedOriginalInteriorPoint28142StillStoredButNotUsed':True}
shape=certify(old,new)
oldcells={r['id']:r for r in raw[0]['cells']};newcells={r['id']:r for r in raw[1]['cells']}
oldfaces={r['face']:r for r in raw[0]['capturedMetrics']};newfaces={r['face']:r for r in raw[1]['capturedMetrics']}
priorcore=set(json.loads((S/'complete-star-input.json').read_text())['children'])
newcone=[c for c in new['children']if c not in old['cells'] and str(c)not in old['cells']]
assert len(newcone)==26
metrics={}
for k,lim,upper in [('D',.001,False),('weight',.05,False),('ratio',.01,False),('skew',4,True),('N',70,True)]:
 a,b=(oldcells,newcells)if k=='D'else(oldfaces,newfaces)
 bad=lambda x:not math.isfinite(x)or(x>lim if upper else x<lim)
 ob=[c for c,r in a.items()if bad(r[k])];nb=[c for c,r in b.items()if bad(r[k])]
 regress=[c for c in set(a)&set(b)if not bad(a[c][k])and bad(b[c][k])]
 metrics[k]={'threshold':lim,'oldBad':ob,'newBad':nb,'retainedPassingRoleRegressions':regress,'oldMin':min(r[k]for r in a.values()),'newMin':min(r[k]for r in b.values()),'oldMax':max(r[k]for r in a.values()),'newMax':max(r[k]for r in b.values())}
inherited=[{'cell':c,'D':newcells[c]['D'],'inheritsPassingDOrigins':[o for o in sorted(priorcore)if oldcells[o]['D']>=.001]}for c in newcone if newcells[c]['D']<.001]
assert len(inherited)==2
outsideDP=[]
for c in old['outsideCells']:
 a,b=oldcells[c],newcells[c];changes=[]
 for k,x,y in [('V',a['V'],b['V'])]+[(f'CC{j}',a['CC'][j],b['CC'][j])for j in range(3)]:
  if struct.pack('d',x)!=struct.pack('d',y):changes.append({'field':k,'old':x,'new':y,'ULP':ulpdiff(x,y)})
 if changes:outsideDP.append({'cell':c,'changes':changes})
corecount=[len(old['children']),len(new['children'])]
out={'scope':'One frozen P12 actual32-cell closed cavity, finite hybrid star retriangulation plus synchronized complete AP28144 motion; no native operator/business/writer/P13 acceptance.',
'baselineJob':'P12 52d0e89c; current P13 D831/W82/R0 untouched. No P13 topology/alias applicability inferred.',
'inputSha256':hashlib.sha256((here/'complete-star-input.json').read_bytes()).hexdigest(),'candidateSha256':hashlib.sha256((here/'joint-six-dimensional-v2/candidate-best.json').read_bytes()).hexdigest(),
'coreCounts':corecount,'completeTypedCounts':[len(oldcells),len(newcells)],'metricFaces':[len(oldfaces),len(newfaces)],'shape':shape,
'actualInstalledStockConcavityAnyCompleteCell':[raw[0]['stockConcaveAny'],raw[1]['stockConcaveAny']],
'minimumNewCellPyramidVolume':min(r['minPyramidV']for r in newcells.values()),'maximumNewCellRelativeClosure':max(r['relativeClosure']for r in newcells.values()),
'officialTetQualityFloor':1e-30,'minimumNewCentreFanTetQuality':min(r['minCentreFanTetQuality']for r in newfaces.values()),'minimumNewSharedAnchorTetQuality':min(r['bestSharedBaseTetQuality']for r in newfaces.values()),
'metrics':metrics,'newCellsInheritReplacedPassingDRefusals':inherited,'ordinaryOutsideDPMomentChanges':outsideDP,
'maximumOrdinaryOutsideMomentULP':max([0]+[d['ULP']for r in outsideDP for d in r['changes']]),
'unknownSourceMaterialAuthorityNotInferred':True,'allOriginalPhysicalLiteralCoordinatesAndRolesPreserved':True,'coupledCoreAndStoredHaloFaces':[],
'qualifiedCandidate':False,'refusal':'Two new cones inherit original passing D roles but remain below .001; the finite best request is geometry-valid but not an acceptable repair.',
'warningScope':'N70 warnings increase4to6, actual local max decreases80.3361752617139to77.3090959012815. Warning roles do not relax hard D/W/R/S.',
'finiteSearchScope':'One initial84-request run that regressed passing D was refused; one protected163-request successor was typed. Existing history was replayed with no new proposals. No global infeasibility proof.',
'coverageScope':'Literal outer shell exact, oriented closed core and all positive volumes/pyramids/tet fans with exact Fraction total V/M1. No full-rank collision/alias or final writer certificate/native deadline validation.'}
assert out['actualInstalledStockConcavityAnyCompleteCell']==[False,False]
assert out['minimumNewCellPyramidVolume']>0 and out['maximumNewCellRelativeClosure']<1e-10
assert out['minimumNewCentreFanTetQuality']>1e-30 and out['minimumNewSharedAnchorTetQuality']>1e-30
assert not any(metrics[k]['retainedPassingRoleRegressions']for k in ['D','weight','ratio','skew'])
(here/'installed-joint-audit.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
print(json.dumps({'qualifiedCandidate':False,'metrics':{k:[len(r['oldBad']),len(r['newBad'])]for k,r in metrics.items()},'inheritedNewD':inherited,'outsideDPChanges':len(outsideDP),'maxULP':out['maximumOrdinaryOutsideMomentULP'],'shapeTrue':True,'actualConcavity':out['actualInstalledStockConcavityAnyCompleteCell']},allow_nan=False))
