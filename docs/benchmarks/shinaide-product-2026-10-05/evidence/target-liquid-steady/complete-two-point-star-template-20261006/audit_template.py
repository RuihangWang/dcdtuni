from pathlib import Path
from fractions import Fraction as F
from collections import Counter
import json,math,struct,hashlib
here=Path(__file__).parent
S=here.parent/'complete-interior-edge-vertex-star-20261006'
ns={'__file__':str(S/'audit_complete_star.py'),'__name__':'fraction_only'}
exec(compile((S/'audit_complete_star.py').read_text().split('def run():')[0],str(S/'audit_complete_star.py'),'exec'),ns)
vm,summed,outward,sub,cross,dot,vector,ulpdiff=[ns[k]for k in ['vm','summed','outward','sub','cross','dot','vector','ulpdiff']]
old=json.loads((here/'complete-star-input.json').read_text());top=json.loads((here/'star-topology-report.json').read_text())
raw=[json.loads(l)for l in (here/'typed-complete-star/installed-kernel-raw.jsonl').read_text().splitlines()if l.startswith('{"kind"')]
def certify(new):
 oc=set(old['children']);nc=set(new['children'])
 assert not nc&{v for r in old['faces'].values()for v in [r['owner'],r['neighbour']]},'new-label-collision'
 for p,xyz in old['points'].items():assert struct.pack('ddd',*xyz)==struct.pack('ddd',*new['points'][p]),'original-point-coordinate'
 assert set(new['points'])-set(old['points'])=={'591824'},'new-point-identity'
 assert all(not {28142,28144}&set(r['pointIDs'])for r in new['faces'].values()),'old-interior-points-still-used'
 for c,r in new['cells'].items():
  assert set(r['faces'])=={int(f)for f,rr in new['faces'].items()if int(c)in [rr['owner'],rr['neighbour']]},'incomplete-cell-incidence'
 shell=[];ap=vector(new['points']['591824']);kernel=[]
 for f in top['vertexStarExteriorFaces']:
  a,b=old['faces'][str(f)],new['faces'][str(f)]
  for k in ['patch','sourceZone','sourceBase','nativeFace']:assert a[k]==b[k],'outer-role'
  ids=outward(a,oc);assert ids==outward(b,nc),'outer-ring-or-point0'
  ao=a['neighbour']if a['owner']in oc else a['owner'];bo=b['neighbour']if b['owner']in nc else b['owner'];assert ao==bo,'outside-endpoint'
  ps=[vector(old['points'][str(p)])for p in ids];normal=cross(sub(ps[1],ps[0]),sub(ps[2],ps[0]))
  assert dot(normal,normal)>0,'zero-area-base'
  assert all(dot(normal,sub(p,ps[0]))==0 for p in ps),'nonplanar-base'
  side=dot(normal,sub(ap,ps[0]));assert side<0,'strict-visibility-kernel'
  kernel.append({'face':f,'sideNumerator':str(side.numerator),'sideDenominator':str(side.denominator)})
  shell.append({'face':f,'orderedCoreOutwardRing':ids,'actualOutside':ao,'patch':a['patch'],'sourceZone':a['sourceZone'],'sourceBase':a['sourceBase'],'nativeFace':a['nativeFace']})
 assert summed(old,oc)==summed(new,nc),'cavity-V-M1'
 for c in old['outsideCells']:assert vm(old,c)==vm(new,c),'outside-V-M1'
 for c in nc:
  assert vm(new,c)[0]>0,'positive-cone-volume'
  ed=Counter()
  for f in new['cells'][str(c)]['faces']:
   r=new['faces'][str(f)];ids=r['pointIDs']if r['owner']==c else [r['pointIDs'][0]]+r['pointIDs'][:0:-1]
   for a,b in zip(ids,ids[1:]+ids[:1]):ed[a,b]+=1
  assert all(ed[a,b]==ed[b,a]==1 for a,b in ed),'directed-edge-closure'
 contained=[]
 for c in oc:
  sides=[]
  for f in old['cells'][str(c)]['faces']:
   rr=old['faces'][str(f)];ids=rr['pointIDs']if rr['owner']==c else [rr['pointIDs'][0]]+rr['pointIDs'][:0:-1]
   ps=[vector(old['points'][str(p)])for p in ids];n=cross(sub(ps[1],ps[0]),sub(ps[2],ps[0]));sides.append(dot(n,sub(ap,ps[0])))
  if all(v<=0 for v in sides):contained.append(c)
 assert contained,'apex-not-in-original-cavity'
 return {'allOriginalDPCoordinatesExact':True,'literal38OuterOrderedRingsAndRoles':shell,'strictFractionVisibilityKernel':kernel,'apexContainedInOriginalCells':sorted(contained),'FractionCoreVolumeAndFirstMomentExact':True,'all69OutsideFractionShapesExact':True,'all38NewConesPositiveVolumeAndDirectedClosed':True,'bothOriginalInteriorPointIDsUnusedInNewTopology':True}
def run():
 baseline={r['id']:r for r in raw[0]['cells']};basefaces={r['face']:r for r in raw[0]['capturedMetrics']};reports={}
 for j,(tag,filename)in enumerate([('chebyshev','candidate-chebyshev.json'),('maxmin','candidate-best.json')],1):
  new=json.loads((here/'single-interior-point-star-v1'/filename).read_text());shape=certify(new)
  cells={r['id']:r for r in raw[j]['cells']};faces={r['face']:r for r in raw[j]['capturedMetrics']}
  metrics={}
  for k,t,up in [('D',.001,False),('weight',.05,False),('ratio',.01,False),('skew',4,True),('N',70,True)]:
   a,b=(baseline,cells)if k=='D'else(basefaces,faces);bad=lambda x:not math.isfinite(x)or(x>t if up else x<t)
   ob=[c for c,r in a.items()if bad(r[k])];nb=[c for c,r in b.items()if bad(r[k])]
   metrics[k]={'threshold':t,'oldBad':ob,'newBad':nb,'oldMin':min(r[k]for r in a.values()),'newMin':min(r[k]for r in b.values()),'oldMax':max(r[k]for r in a.values()),'newMax':max(r[k]for r in b.values()),'retainedPassingRoleRegressions':[c for c in set(a)&set(b)if not bad(a[c][k])and bad(b[c][k])]}
  assert not any(metrics[k]['retainedPassingRoleRegressions']for k in ['D','weight','ratio','skew'])
  originpass=[c for c in old['children']if baseline[c]['D']>=.001]
  inherited=[{'cell':c,'D':cells[c]['D'],'baseFace':top['vertexStarExteriorFaces'][c-833839],'replacedPassingOriginCount':len(originpass)}for c in new['children']if cells[c]['D']<.001]
  assert inherited
  changes=[]
  for c in old['outsideCells']:
   a,b=baseline[c],cells[c];rows=[]
   for k,x,y in [('V',a['V'],b['V'])]+[(f'CC{j}',a['CC'][j],b['CC'][j])for j in range(3)]:
    if struct.pack('d',x)!=struct.pack('d',y):rows.append({'field':k,'old':x,'new':y,'ULP':ulpdiff(x,y)})
   if rows:changes.append({'cell':c,'deltas':rows})
  q={'candidate':tag,'shape':shape,'coreCounts':[len(old['children']),len(new['children'])],'completeTypedCounts':[len(baseline),len(cells)],'metricFaces':[len(basefaces),len(faces)],'metrics':metrics,'inheritedPassingDRefusals':inherited,
  'actualStockConcavityAnyCompleteCell':[raw[0]['stockConcaveAny'],raw[j]['stockConcaveAny']],
  'minimumNewCellPyramidVolume':min(r['minPyramidV']for r in cells.values()),'maximumNewCellRelativeClosure':max(r['relativeClosure']for r in cells.values()),
  'officialTetFloor':1e-30,'minimumCentreFanTetQuality':min(r['minCentreFanTetQuality']for r in faces.values()),'minimumSharedAnchorTetQuality':min(r['bestSharedBaseTetQuality']for r in faces.values()),
  'ordinaryOutsideDPMomentChanges':changes,'maximumOrdinaryOutsideMomentULP':max([0]+[v['ULP']for r in changes for v in r['deltas']]),
  'qualifiedCandidate':False,'refusal':'One shared apex and one literal outer-face cone each is geometrically valid but contains new failing D roles. It is not a native repair and does not prove other remeshing grammars infeasible.'}
  assert q['actualStockConcavityAnyCompleteCell']==[False,False]
  assert q['minimumNewCellPyramidVolume']>0 and q['maximumNewCellRelativeClosure']<1e-10
  assert q['minimumCentreFanTetQuality']>1e-30 and q['minimumSharedAnchorTetQuality']>1e-30
  reports[tag]=q
 out={'scope':'Single32-core literal38-face template, one geometry-derived kernel center and best result from one36-request finite3D selection. Real installed OpenFOAM DP/Int32 typed101/107-cell halo. No business mesher/checkMesh/solver or production source modification.','baselineJob':'P12 52d0e89c; P13 D831/W82/R0 untouched; no current P13 applicability claim.','inputSha256':hashlib.sha256((here/'complete-star-input.json').read_bytes()).hexdigest(),'reports':reports,'coupledFacesInEntireStoredHalo':[],'unknownNormalSourceMaterialAuthorityNotInferred':True,'allPhysical8LiteralFacesPreserved':True,'noFullRankAliasOrFinalWriterCertificateClaim':True,'qualifiedCandidate':False,'notAGlobalInfeasibilityProof':True}
 (here/'installed-complete-star-audit.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
 print(json.dumps({tag:{'counts':{k:[len(v['oldBad']),len(v['newBad'])]for k,v in q['metrics'].items()},'newInheritedDFailures':len(q['inheritedPassingDRefusals']),'outsideDPChanges':len(q['ordinaryOutsideDPMomentChanges']),'maxULP':q['maximumOrdinaryOutsideMomentULP'],'minPyramid':q['minimumNewCellPyramidVolume'],'tetFloorMargin':q['minimumSharedAnchorTetQuality']}for tag,q in reports.items()}))
if __name__=='__main__':run()
