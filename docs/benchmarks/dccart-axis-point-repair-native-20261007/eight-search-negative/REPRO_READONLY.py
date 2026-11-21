from pathlib import Path
import json,struct,math,re,hashlib,sys
F=Path(__file__).resolve().parent
def f64(b):
 assert isinstance(b,int) and 0<=b<2**64
 return struct.unpack('<d',struct.pack('<Q',b))[0]
def h(p):
 b=p.read_bytes();return {'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}
def readset(p):
 b=p.read_bytes();m=re.search(rb'\}\s*//[^\n]*\n\s*(\d+)\s*\(',b);assert m
 n=int(m.group(1));tail=b[m.end():];end=tail.find(b')');assert end>=0
 body=tail[:end];vals=[int(t) for t in body.split()]
 assert len(vals)==n and len(set(vals))==n and min(vals,default=0)>=0
 return {'count':n,'orderedWrittenLabels':vals,'headerFormatBinary':b'format      binary;' in b[:m.start()],'actualSetPayload':'ASCII decimal labels emitted by set serialization','identity':h(p)}
on=json.loads((F/'logs/OBS_ON.analysis.json').read_text());off=json.loads((F/'logs/OBS_OFF.analysis.json').read_text())
assert off['nativeQualified'] and on['nativeQualified'] and not off['strictAccepted'] and not on['strictAccepted']
assert len(off['decisionObservations'])==0 and len(on['decisionObservations'])==1
obs=on['decisionObservations'][0];assert obs['complete']==1 and obs['rows']==16 and obs['fractionCount']==8
assert obs['stackBytes']==obs['decisionRowsStackBytes']+obs['determinantCaptureStackBytes']+obs['fractionsStackBytes']
selected=[(i,a) for i,a in enumerate(obs['attempts']) if a['capture']['ok']];assert len(selected)==1
i,a=selected[0];assert i==6 and a['midpoint']['ok']==1
rows=[]
for j,l in enumerate(a['localFractionsTowardSharedEdgeMidpoint']):
 c=l['call'];s=c['statistics'];assert c['evaluated']==1 and c['ok']==0 and c['lastRefusal']==9 and c['statisticsObserved']==1
 assert s['oldD']==s['newD']==3
 rows.append({'fractionIndex':j,'fraction':l['fraction'],'proposalRawBits':c['proposalBits'],'proposalXYZ':[f64(v) for v in c['proposalBits']],'oldD':s['oldD'],'newD':s['newD'],'oldDWorstBits':s['oldDWorstBits'],'newDWorstBits':s['newDWorstBits'],'oldDWorst':f64(s['oldDWorstBits']),'newDWorst':f64(s['newDWorstBits']),'worstDelta':f64(s['newDWorstBits'])-f64(s['oldDWorstBits']),'refusal':9,'uniqueFirstPredicateProven':j==0,'laterPerCellMapAvailable':j==0,'faceFamilyStatisticsEvaluated':False})
assert a['stage']['evaluated']==0 and a['commit']['evaluated']==0
m=obs['determinantMapCapture'];assert m['complete']==1 and m['pairCount']==m['oldCells']==m['nextCells']==m['oldDeterminants']==m['nextDeterminants']==30
assert m['oldMetrics']==m['nextMetrics']==44 and m['oldRefusal']==m['nextRefusal']==0 and m['fractionIndex']==0
pairs=m['pairs'];assert len(pairs)==30 and [p['native'] for p in pairs]==sorted(set(p['native'] for p in pairs))
oldbad=[];newbad=[];changed=[];passingRegressions=[]
for p in pairs:
 assert p['oldPresent']==p['nextPresent']==1
 x,y=f64(p['oldBits']),f64(p['nextBits']);assert math.isfinite(x) and math.isfinite(y) and x>=0 and y>=0
 q={**p,'oldD':x,'newD':y}
 if x<.001:oldbad.append(q)
 if y<.001:newbad.append(q)
 if x>=.001 and y<.001:passingRegressions.append(q)
 if p['oldBits']!=p['nextBits']:changed.append(q)
assert not passingRegressions and len(oldbad)==len(newbad)==3
first={'oldBadCells':oldbad,'newBadCells':newbad,'changedDeterminants':changed,'passingDRegressions':passingRegressions,'firstFailure':'H42 newD >= oldD (3 >= 3)','wholeMapChecksBeforeH42Passed':True,'faceLoopUnreached':True,'postPointHardOutsideDPGuardUnreached':True}
mesh={};sets={}
for n in ['points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones']:
 p=F/'raw/OBS_OFF'/n;q=F/'raw/OBS_ON'/n;assert p.read_bytes()==q.read_bytes()
 orig=on['meshFiles'][n]['constructionONReference'];assert h(p)==orig
 mesh[n]={'OFF':h(p),'ON':h(q),'byteEqual':True,'byteEqualPriorConstructionON':True}
for p in sorted((F/'raw/OBS_OFF/sets').iterdir()):
 q=F/'raw/OBS_ON/sets'/p.name;assert p.read_bytes()==q.read_bytes();sets[p.name]={'OFF':readset(p),'ON':readset(q),'byteEqual':True}
assert sets['underdeterminedCells']['OFF']['count']==13 and sets['lowWeightFaces']['OFF']['count']==8 and sets['lowVolRatioFaces']['OFF']['count']==2
quality={side:{'nativeTotalSeconds':d['nativeTotalSeconds'],'nativeQualified':d['nativeQualified'],'strictAccepted':d['strictAccepted'],'literalMeshOK':d['literalMeshOK'],'literalFailed':d['literalFailed'],'familyRawLines':d['failureAndWarningLines'],'nativeRelevantRawLines':d['nativeRelevantLines'],'nativeDerivedMetadata':d['nativeDerivedMetadata']} for side,d in [('OBS_OFF',off),('OBS_ON',on)]}
result={'rows':rows,'firstCompleteMap':first,'mesh':mesh,'sets':sets,'quality':quality,'limits':['Only first .25 proposal has complete per-cell D mapping. Seven later quality9 calls do not uniquely exclude an earlier H36/H38 return.','Face zero arrays are reset values before an unreached face loop; no W/R/S/N passing inference.','Native keys, representative and compact IDs are audit labels without a complete native-to-written map; no joining to written quality labels.','No threshold relaxation or new runtime performed. No full Shinaide acceptance.']}
print(json.dumps(result,indent=2))
