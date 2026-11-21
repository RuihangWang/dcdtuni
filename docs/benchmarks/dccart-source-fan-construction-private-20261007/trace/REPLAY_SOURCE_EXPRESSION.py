from pathlib import Path
import json,math,struct,hashlib,re
from fractions import Fraction as F
T=Path(__file__).resolve().parent
records=json.loads((T/'input/ON.geometry.json').read_text())
def decode(bits):return tuple(struct.unpack('>d',bytes.fromhex(x))[0] for x in bits)
def bits(p):return [struct.pack('>d',x).hex() for x in p]
def plus(a,b):return tuple(x+y for x,y in zip(a,b))
def minus(a,b):return tuple(x-y for x,y in zip(a,b))
def scale(a,b):return tuple(a*x for x in b)
def divide(a,b):return tuple(x/b for x in a)
def cross(a,b):return(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def originalRoutine(p):
 n=len(p);centre=(0.,0.,0.);areaVector=(0.,0.,0.)
 if n<3:
  for x in p:centre=plus(centre,x)
  if n:centre=divide(centre,float(n))
  return dict(centreBits=bits(centre),areaVectorBits=bits(areaVector),branch='lt3')
 if n==3:
  centre=divide(plus(plus(p[0],p[1]),p[2]),3.)
  areaVector=scale(.5,cross(minus(p[1],p[0]),minus(p[2],p[0])))
  return dict(centreBits=bits(centre),areaVectorBits=bits(areaVector),branch='triangle')
 mean=(0.,0.,0.)
 for x in p:mean=plus(mean,x)
 mean=divide(mean,float(n));sumN=(0.,0.,0.);sumAc=(0.,0.,0.);sumA=0.;steps=[]
 for i,a in enumerate(p):
  b=p[(i+1)%n];v=cross(minus(b,a),minus(mean,a))
  amount=math.sqrt((v[0]*v[0]+v[1]*v[1])+v[2]*v[2])
  sumN=plus(sumN,v);sumA+=amount;sumAc=plus(sumAc,scale(amount,plus(plus(a,b),mean)))
  steps.append(dict(nBits=bits(v),amountBits=struct.pack('>d',amount).hex(),sumAcBits=bits(sumAc),sumABits=struct.pack('>d',sumA).hex()))
 # Actual bound inputs nondegenerate; no guessed ROOTVSMALL execution here.
 assert math.isfinite(sumA) and sumA>1e-100
 scaled=scale(1./3.,sumAc);centre=divide(scaled,sumA);areaVector=scale(.5,sumN)
 return dict(centreBits=bits(centre),areaVectorBits=bits(areaVector),vertexAverageBits=bits(mean),sumAcBits=bits(sumAc),sumABits=struct.pack('>d',sumA).hex(),scaledMomentBits=bits(scaled),history=steps,branch='polygon')
def canonical(p,ids):
 # Normal canonicalCycle selects same lexicographically minimum point rotations.
 candidates=[(tuple(p[(start+d*j)%len(p)] for j in range(len(p))),start,d) for d in (1,-1) for start in range(len(p))]
 minimum=min(x[0] for x in candidates)
 same=[x for x in candidates if x[0]==minimum]
 # Equal coordinates are ambiguous for audit, not silently label-tied.
 assert len(same)==1
 points,start,d=same[0]
 return list(points),[ids[(start+d*j)%len(p)] for j in range(len(p))],start,d
sources={}
observed={}
for r in records:
 for e in r['events']:
  if e['kind']=='source':sources.setdefault(e['sourceFacet'],e)
  if e['kind']=='representative':observed[e['native']]=e['rawBits']
  if e['kind']=='incident':
   for x in e['ring']: observed[x['native']]=x['bits']
result={}
for source,target in [(3579,1618),(3526,1615)]:
 s=sources[source];p=list(map(decode,s['worldBits']));q,ids,start,d=canonical(p,s['sourceRing'])
 ans=originalRoutine(q);forward=originalRoutine(p)
 assert ans['centreBits']==observed[target], (source,target,ans['centreBits'],observed[target])
 result[str(source)]=dict(source=s,targetNative=target,targetObservedBits=observed[target],canonical=dict(start=start,direction=d,ring=ids),actualExpressionReplay=ans,all3CentreBitsEqualObserved=True,forwardSourceRingControl=forward,forwardControlBitsEqualObserved=forward['centreBits']==observed[target],preconePointCount=1461,targetAboveOriginalCount=target>=1461,sourceFanCreation='normal C13452 faceAreaCentreOF; C13478 insertPoint(fc,true); C13479 centre fan',allocationEventNotLogged=True)
# Exact immutable outline classification; rank is an upper bound, not permission.
def exact(b):return tuple(F.from_float(x) for x in decode(b))
def ecross(a,b):return cross(a,b)
def esub(a,b):return minus(a,b)
def reduceOutline(p):
 out=list(p);changed=True
 while changed and len(out)>3:
  changed=False
  for i,x in enumerate(out):
   prev=out[i-1];nxt=out[(i+1)%len(out)]
   if not any(ecross(esub(x,prev),esub(nxt,x))):
    # Only collinear between vertices; no reversal or outline replacement.
    if all(min(prev[k],nxt[k])<=x[k]<=max(prev[k],nxt[k]) for k in range(3)):
     del out[i];changed=True;break
 return out
def classify(pt,s):
 raw=list(map(exact,s['worldBits']));ax=[k for k in range(3) if all(x[k]==raw[0][k] for x in raw)]
 assert len(ax)==1
 axis=ax[0];out=reduceOutline(raw)
 if pt[axis]!=out[0][axis]:return dict(position='off-source-plane',axis=axis,reducedOutlineVertices=len(out))
 if pt in out:return dict(position='immutable-outline-corner',axis=axis,reducedOutlineVertices=len(out))
 for i,a in enumerate(out):
  b=out[(i+1)%len(out)]
  if not any(ecross(esub(pt,a),esub(b,a))) and all(min(a[k],b[k])<=pt[k]<=max(a[k],b[k]) for k in range(3)):
   return dict(position='immutable-outline-edge-collinear',axis=axis,reducedOutlineVertices=len(out))
 return dict(position='inside-plane-not-on-outline',axis=axis,reducedOutlineVertices=len(out))
classification=[]
for r in records:
 rep=next(x for x in r['events'] if x['kind']=='representative');pt=exact(rep['rawBits'])
 source=[x for x in r['events'] if x['kind']=='source']
 positions=[dict(sourceFacet=s['sourceFacet'],**classify(pt,s)) for s in source]
 classification.append(dict(compactPoint=r['compactPoint'],nativePoint=rep['native'],positions=positions,anyOriginalOutlineCorner=any(x['position']=='immutable-outline-corner' for x in positions),anyOffSourcePlane=any(x['position']=='off-source-plane' for x in positions)))
(T/'report/actual-source-centre-replay.json').write_text(json.dumps(result,indent=2)+'\n')
(T/'report/immutable-outline-position.json').write_text(json.dumps(classification,indent=2)+'\n')
print({k:dict(target=v['targetNative'],bits=v['targetObservedBits'],canonical=v['canonical'],threeComponentMatch=v['all3CentreBitsEqualObserved'],forwardMatches=v['forwardControlBitsEqualObserved']) for k,v in result.items()})
for x in classification:print(x['compactPoint'],x['nativePoint'],[p['position'] for p in x['positions']])
