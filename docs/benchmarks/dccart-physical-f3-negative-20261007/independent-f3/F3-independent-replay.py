from pathlib import Path
import json,struct,math,hashlib,sys
F=Path(sys.argv[1]);expected='52d08a57ad6ce4377080f1bc1abe63724007238ef91f9bc1995512b437d9e3ba'
stdout=(F/'logs/OBS_ON.native.stdout').read_bytes();assert hashlib.sha256(stdout).hexdigest()==expected
prefix='PRIVATE physical-point decision observation: '
lines=[x for x in stdout.decode().splitlines() if x.startswith(prefix)];assert len(lines)==1
r=json.loads(lines[0][len(prefix):],parse_int=int);m=r['determinantMapCapture']
assert r['rank']==0 and r['complete']==1 and r['rows']==len(r['attempts'])==16
assert r['stackBytes']==r['decisionRowsStackBytes']+r['determinantCaptureStackBytes']==28760
assert r['decisionRowsStackBytes']==22528 and r['determinantCaptureStackBytes']==6232
assert m['attempted']==m['complete']==1 and m['unavailableReason']==0
assert m['oldRefusal']==m['nextRefusal']==0
assert m['oldCells']==m['nextCells']==30 and m['oldMetrics']==m['nextMetrics']==44
assert m['oldDeterminants']==m['nextDeterminants']==m['pairCount']==len(m['pairs'])==30
row=r['attempts'][m['rowOrdinal']];assert m['rowOrdinal']==6 and m['fractionIndex']==0
assert row['compact']==m['compact']==1609 and row['representativeObserved']==m['representativeObserved']==1434
call=row['localFractionsQuarterHalfThreeQuarterOne'][m['fractionIndex']]
assert call['evaluated']==call['statisticsObserved']==call['proposalKnown']==1
assert call['ok']==0 and call['lastRefusal']==9
keys=[p['native'] for p in m['pairs']];assert len(set(keys))==len(keys) and keys==sorted(keys)
def decode(x):
 assert type(x) is int and 0<=x<2**64
 return struct.unpack('<d',struct.pack('<Q',x))[0]
def bits(x):return struct.unpack('<Q',struct.pack('<d',x))[0]
oldD=nextD=0;oldWorst=nextWorst=0.;prefixRows=[];badOld=[];badNext=[];changed=[]
for p in m['pairs']:
 assert type(p['native']) is int and p['oldPresent']==p['nextPresent']==1
 a,b=decode(p['oldBits']),decode(p['nextBits'])
 assert math.isfinite(a) and math.isfinite(b) and a>=0 and b>=0,'Earlier H36/37 refusal'
 assert not(a>=.001 and b<.001),'Earlier H38 refusal'
 if a<.001:
  oldD+=1;oldWorst=max(oldWorst,max(0.,.001/max(a,1e-300)-1.));badOld.append(p['native'])
 if b<.001:
  nextD+=1;nextWorst=max(nextWorst,max(0.,.001/max(b,1e-300)-1.));badNext.append(p['native'])
 if p['oldBits']!=p['nextBits']:changed.append(p['native'])
 prefixRows.append(dict(nativeKey=p['native'],oldBits=p['oldBits'],nextBits=p['nextBits'],old=a,next=b,oldBad=a<.001,nextBad=b<.001,oldD=oldD,nextD=nextD,oldWorstBits=bits(oldWorst),nextWorstBits=bits(nextWorst)))
stats=dict(oldD=oldD,newD=nextD,oldDWorstBits=bits(oldWorst),newDWorstBits=bits(nextWorst))
assert stats=={k:call['statistics'][k] for k in stats}
assert oldD==nextD==3 and nextD>=oldD
assert bits(oldWorst)==4629335979155126343 and bits(nextWorst)==4623263507900385541
assert not(nextWorst>oldWorst)
assert badOld==badNext==[1620,1622,1623] and len(changed)==8
local=row['localFractionsQuarterHalfThreeQuarterOne'];assert len(local)==4
assert all(x['evaluated']==1 and x['ok']==0 and x['lastRefusal']==9 for x in local)
assert all({k:x['statistics'][k] for k in stats}==stats for x in local)
assert all(x['statistics']['oldBad']==x['statistics']['newBad']==[0]*4 and x['statistics']['oldWorstBits']==x['statistics']['newWorstBits']==[0]*4 for x in local)
assert row['stage']['evaluated']==row['commit']['evaluated']==row['stage']['statisticsObserved']==row['commit']['statisticsObserved']==0
assert row['stage']['statistics'] is row['commit']['statistics'] is None
bind=json.loads((F/'source/bindings.json').read_text());h=Path(bind['actualCompiledLifecycleH']['path']).read_bytes();assert hashlib.sha256(h).hexdigest()==bind['actualCompiledLifecycleH']['sha256']
ls=h.decode().splitlines();fragment='\n'.join(f'{i}:{ls[i-1]}' for i in range(27,44))+'\n';assert fragment.encode()==(F/'source/pointHard-D-segment.txt').read_bytes()
assert ls[28].strip()=='s=Statistics();'
assert ls[41].strip()=='if(s.newD>=s.oldD||s.newDWorst>s.oldDWorst)return false;'
print(json.dumps(dict(scope='FULL-bound actual F3 first .25 determinant segment only',stdoutSha256=expected,association={k:m[k] for k in ['rowOrdinal','compact','representativeObserved','fractionIndex']},mapComplete=True,all30RowsScanned=True,noMissingNonfiniteNegativePassingRegression=True,statsExpected=stats,statsObserved=call['statistics'],firstRefusal=dict(sourceLine=42,firstOperand='s.newD>=s.oldD',left=nextD,right=oldD,value=True,actualCxxWorstRHSEvaluated=False),hypotheticalWorstRHS=False,badOld=badOld,badNext=badNext,changedKeys=changed,allFourCopiedDStatsExact=True,otherThreeMapsUnobserved=True,faceLoopNotReachedForFirst=True,stageCommitUnevaluated=True,sourceNativeKeysNotAssertedWrittenCellIDs=True,perCellPrefix=prefixRows),indent=2))
