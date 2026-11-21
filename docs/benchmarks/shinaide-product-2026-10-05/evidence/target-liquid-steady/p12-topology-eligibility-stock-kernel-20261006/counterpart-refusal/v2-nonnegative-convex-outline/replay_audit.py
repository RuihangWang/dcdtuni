from pathlib import Path
from fractions import Fraction as F
import json
here=Path(__file__).parent
before=json.loads((here/'before.json').read_text())
after=json.loads((here/'candidate.json').read_text())
report=json.loads((here/'closure-report.json').read_text())
summary=json.loads((here/'installed-kernel-summary.json').read_text())
raw=[json.loads(s) for s in (here/'installed-kernel-raw.jsonl').read_text().splitlines() if s.startswith('{"kind"')]
assert [r['kind'] for r in raw]==['old','merged']
def vm(doc,c):
 p={int(k):list(map(F,xyz)) for k,xyz in doc['points'].items()}
 V=F(0);M=[F(0)]*3
 for f in doc['cells'][str(c)]['faces']:
  r=doc['faces'][str(f)];ids=r['pointIDs']
  if r['owner']!=c:ids=[ids[0]]+list(reversed(ids[1:]))
  x=p[ids[0]]
  for j in range(1,len(ids)-1):
   y,z=p[ids[j]],p[ids[j+1]]
   dv=(x[0]*(y[1]*z[2]-y[2]*z[1])+x[1]*(y[2]*z[0]-y[0]*z[2])+x[2]*(y[0]*z[1]-y[1]*z[0]))/6
   V+=dv
   for k in range(3):M[k]+=dv*(x[k]+y[k]+z[k])/4
 return V,M
groups=report['groups'];cellmap={c:min(g) for g in groups for c in g}
pairs=[]
for g in groups:
 old=[vm(before,c) for c in g];new=vm(after,min(g))
 assert new[0]==sum(x[0] for x in old)
 assert all(new[1][k]==sum(x[1][k] for x in old) for k in range(3))
 pairs.append({'oldCells':g,'newCell':min(g),'VExact':True,'M1Exact':True})
core=set(cellmap);outside=set(map(int,before['cells']))-core
assert all(vm(before,c)==vm(after,c) for c in outside)
assert before['points']==after['points']
oldcells={r['id']:r for r in raw[0]['cells']};newcells={r['id']:r for r in raw[1]['cells']}
oldfaces={r['face']:r for r in raw[0]['capturedMetrics']};newfaces={r['face']:r for r in raw[1]['capturedMetrics']}
faceLineage={int(f):old for f,old in json.loads((here/'face-lineage.json').read_text()).items()}
counts={};regress={}
for metric,threshold,high in [('D',.001,False),('weight',.05,False),('ratio',.01,False),('skew',4.,True),('N',70.,True)]:
 old,new=(oldcells,newcells) if metric=='D' else (oldfaces,newfaces)
 bad=lambda x:x>threshold if high else x<threshold
 ob=[k for k,r in old.items() if bad(r[metric])];nb=[k for k,r in new.items() if bad(r[metric])]
 failures=[]
 for k,r in new.items():
  ancestors=([c for c in old if cellmap.get(c,c)==k] if metric=='D' else faceLineage[k])
  if bad(r[metric]):
   failures.extend([p for p in ancestors if p in old and not bad(old[p][metric])])
 counts[metric]=[len(ob),len(nb)];regress[metric]=failures
 assert counts[metric]==[len(summary['metrics'][metric]['oldBad']),len(summary['metrics'][metric]['newBad'])]
 assert failures==summary['metrics'][metric]['passingRoleRegressions']
assert raw[0]['stockConcaveAny'] is False and raw[1]['stockConcaveAny'] is True
assert sorted(r['id'] for r in raw[1]['cells'] if r['concave'])==[535534,538806]
assert newcells[19189]['D']<.001
out={'scope':'Replay of frozen real OpenFOAM local API output and exact binary-coordinate Fraction shell arithmetic; not native/business acceptance.',
 'cellCounts':[len(before['cells']),len(after['cells'])],'facetCounts':[len(before['faces']),len(after['faces'])],
 'counts':counts,'passingRoleRegressions':regress,'exactPairVolumeMoment':pairs,
 'allOutsideFractionVolumeMomentExact':True,'allPointCoordinateValuesExact':True,
 'coreInstalledDeterminant':newcells[19189]['D'],'coreDeterminantFails001':True,
 'stockConcavityRejectCells':[535534,538806],
 'closureWasRejected':True}
(here/'replay-audit.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
print(json.dumps(out))
