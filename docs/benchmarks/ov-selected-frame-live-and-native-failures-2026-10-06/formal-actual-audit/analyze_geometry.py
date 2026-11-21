from pathlib import Path
import re,json,collections,math,hashlib
import numpy as np
from fractions import Fraction as Q
A=Path(__file__).resolve().parent
def body(n):
 s=(A/'polyMesh'/n).read_text();s=re.sub(r'/\*.*?\*/|//[^\n]*','',s,flags=re.S);return s[s.index('}')+1:].strip()
pointStrings=[r.split() for r in re.findall(r'\(([^()]*)\)',body('points'))]
P=np.array([[float(x) for x in r] for r in pointStrings]);PQ=[tuple(Q(x) for x in r) for r in pointStrings]
F=[[int(x) for x in r.split()] for r in re.findall(r'\d+\(([^()]*)\)',body('faces'))]
def labels(n):return [int(x) for x in re.findall(r'\d+',body(n))][1:]
O=labels('owner');N=labels('neighbour')
C=[[] for i in range(max(O)+1)]
for f,c in enumerate(O):C[c].append((f,1))
for f,c in enumerate(N):C[c].append((f,-1))
sets={n:labels('sets/'+n) for n in ['zipUpCells','concaveCells','concaveFaces','lowQualityTetFaces']}
for name,data in [('points',P),('faces',F),('owner',O),('neighbour',N)]+[('sets/'+n,v) for n,v in sets.items()]:
 assert len(data)==int(re.match(r'\d+',body(name)).group(0)),name
assert len(O)==len(F) and all(0<=n<len(C) for n in N)
assert all(len(ids)>=3 and all(0<=p<len(P) for p in ids) for ids in F)
def write(n,x): (A/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def facegeom(ids):
 q=P[ids];avg=q.mean(axis=0);s=np.zeros(3);a=0.;ac=np.zeros(3)
 for u,v in zip(q,np.roll(q,-1,axis=0)):
  n=np.cross(v-u,avg-u);ar=np.linalg.norm(n);s+=n;a+=ar;ac+=ar*(u+v+avg)
 return s/2,ac/(3*a)
S,FC=map(np.array,zip(*(facegeom(ids) for ids in F)))
EST=np.array([FC[[f for f,sign in fs]].mean(axis=0) for fs in C]);CC=np.zeros_like(EST);V=np.zeros(len(C))
for f,o in enumerate(O):
 v=float(np.dot(S[f],FC[f]-EST[o]));V[o]+=v;CC[o]+=v*(.75*FC[f]+.25*EST[o])
for f,n in enumerate(N):
 v=float(np.dot(S[f],EST[n]-FC[f]));V[n]+=v;CC[n]+=v*(.75*FC[f]+.25*EST[n])
CC/=V[:,None];V/=3
def edges(c,faces):
 d=collections.defaultdict(list)
 for f,sign in C[c]:
  ids=faces[f] if sign==1 else faces[f][::-1]
  for u,v in zip(ids,ids[1:]+ids[:1]):d[tuple(sorted([u,v]))].append([f,u,v])
 return [{'edge':list(e),'uses':us} for e,us in d.items() if len(us)!=2 or us[0][1:]!=us[1][1:][::-1]]
# Proof-only reconstruction of existing exact collinear conformity vertices.
# No actual mesh file is rewritten; this does not purport to restore the unknown prior ring byte sequence.
Fsub=[];inserts=[]
for f,ids in enumerate(F):
 lo=P[ids].min(axis=0);hi=P[ids].max(axis=0)
 cp=set(np.flatnonzero(np.all((P>=lo)&(P<=hi),axis=1)).tolist())
 out=[]
 for u,v in zip(ids,ids[1:]+ids[:1]):
  a,b=P[u],P[v];ab=b-a;L2=np.dot(ab,ab);mid=[]
  for p in sorted(cp):
   if p in [u,v]:continue
   ap=P[p]-a;t=float(np.dot(ap,ab)/L2)
   if 0<t<1 and np.dot(np.cross(ap,ab),np.cross(ap,ab))==0:
    mid.append((t,p))
  out.append(u)
  for t,p in sorted(mid):out.append(p);inserts.append({'face':f,'edge':[u,v],'point':p,'t':t})
 Fsub.append(out)
edgeBad=[c for c in range(len(C)) if edges(c,F)]
subBad=[c for c in range(len(C)) if edges(c,Fsub)]
# Face-plane check exactly follows installed v2412 predicate.
planes=[];positive=[]
for c,fs in enumerate(C):
 hits=[];maxdot=-1.
 for f,sign in fs:
  normal=sign*S[f]/np.linalg.norm(S[f])
  for g,sg in fs:
   if g==f:continue
   dif=FC[g]-FC[f];co=float(np.dot(dif,normal)/np.linalg.norm(dif));maxdot=max(maxdot,co)
   if co>-1e-6:hits.append({'facePlane':f,'otherFace':g,'cos':co,'signedDistance':float(np.dot(dif,normal))})
 if hits:
  planes.append({'cell':c,'maximumCos':maxdot,'witness':hits[0],'positiveWitness':next((h for h in hits if h['cos']>1e-6),None),'numWitnessPairs':len(hits)})
  if maxdot>1e-6:positive.append(c)
# Independent tet calculations using installed formula; records all per-edge signed volumes/qualities.
def tet(a,b,c,d):
 aa=b-a;bb=c-a;cc=d-a;vv=float(np.dot(np.cross(aa,bb),cc)/6)
 ba=np.cross(bb,aa);ca=np.cross(cc,aa);den=float(np.dot(cc,ba))
 if abs(den)<1e-150:r=1e15
 else:
  lam=float(np.dot(cc,cc)-np.dot(aa,cc));mu=float(np.dot(bb,bb)-np.dot(aa,bb));r=float(np.linalg.norm(.5*(aa+(lam*ba-mu*ca)/den)))
 quality=vv/(8/(9*math.sqrt(3))*min(r,1e15)**3+1e-150)
 return {'volume':vv,'quality':quality,'circumRadius':r}
badFaces=[]
for f in sorted(sets['lowQualityTetFaces']):
 ids=F[f];edgeTet=[]
 for i,u in enumerate(ids):
  v=ids[(i+1)%len(ids)]
  edgeTet.append({'edge':[u,v],'owner':tet(P[u],P[v],FC[f],CC[O[f]]),'neighbour':tet(P[u],P[v],FC[f],CC[N[f]])})
 bases=[]
 for b,u in enumerate(ids):
  own=[];nei=[]
  for k in range(1,len(ids)-1):
   v=ids[(b+k)%len(ids)];w=ids[(b+k+1)%len(ids)]
   own.append(tet(CC[O[f]],P[u],P[v],P[w]));nei.append(tet(CC[N[f]],P[u],P[w],P[v]))
  bases.append({'baseIndex':b,'basePoint':u,'ownerMinQuality':min(x['quality'] for x in own),'neighbourMinQuality':min(x['quality'] for x in nei),'ownerTets':own,'neighbourTets':nei})
 pyrOwner=float(np.dot(S[f],FC[f]-CC[O[f]])/3);pyrNeigh=float(np.dot(S[f],CC[N[f]]-FC[f])/3)
 badFaces.append({'face':f,'owner':O[f],'neighbour':N[f],'pointIds':ids,'coordinates':P[ids].tolist(),'Sf':S[f].tolist(),'faceCentre':FC[f].tolist(),'ownerCentre':CC[O[f]].tolist(),'neighbourCentre':CC[N[f]].tolist(),'ownerVolume':V[O[f]],'neighbourVolume':V[N[f]],'pyramids':[pyrOwner,pyrNeigh],'edgeTets':edgeTet,'fanBases':bases,'sharedPositiveBaseIndices':[b['baseIndex'] for b in bases if min(b['ownerMinQuality'],b['neighbourMinQuality'])>1e-30]})
# Fraction proof of whole-mesh area closure and oriented volume using original face rings.
def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def cr(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def dot(a,b):return sum(x*y for x,y in zip(a,b))
exactS=[];exactV=[]
for ids in F:
 q=[PQ[i] for i in ids];s=[Q(0)]*3;v=Q(0)
 for i in range(1,len(q)-1):
  x=cr(sub(q[i],q[0]),sub(q[i+1],q[0]))
  s=[t+z/2 for t,z in zip(s,x)];v+=dot(q[0],cr(q[i],q[i+1]))/6
 exactS.append(s);exactV.append(v)
exactCells=[]
for c,fs in enumerate(C):
 s=[sum(sign*exactS[f][d] for f,sign in fs) for d in range(3)];v=sum(sign*exactV[f] for f,sign in fs)
 exactCells.append({'cell':c,'Sf':[str(x) for x in s],'volume':str(v),'FoamVolume':V[c],'FoamCentre':CC[c].tolist()})
subAreaEqual=True
rePruned=[];pruneCount=0;pruneFaces=0
for ids in Fsub:
 ids=list(ids);touched=False;changed=True
 while changed and len(ids)>3:
  changed=False
  for i,p in enumerate(ids):
   if np.dot(np.cross(P[p]-P[ids[i-1]],P[ids[(i+1)%len(ids)]]-P[p]),np.cross(P[p]-P[ids[i-1]],P[ids[(i+1)%len(ids)]]-P[p]))==0:
    del ids[i];pruneCount+=1;touched=True;changed=True;break
 if touched:pruneFaces+=1
 rePruned.append(ids)
subS,subFC=map(np.array,zip(*(facegeom(ids) for ids in Fsub)))
subV=np.zeros(len(C))
for c,fs in enumerate(C):
 estimate=subFC[[f for f,s in fs]].mean(axis=0)
 subV[c]=sum(sign*np.dot(subS[f],subFC[f]-estimate)/3 for f,sign in fs)
for f,ids in enumerate(Fsub):
 ss=[Q(0)]*3;q=[PQ[i] for i in ids]
 for i in range(1,len(q)-1):
  x=cr(sub(q[i],q[0]),sub(q[i+1],q[0]));ss=[t+z/2 for t,z in zip(ss,x)]
 if ss!=exactS[f]:subAreaEqual=False
summary={'points':len(P),'faces':len(F),'internalFaces':len(N),'cells':len(C),'exactSfAllZero':all(c['Sf']==['0','0','0'] for c in exactCells),'exactVolumeTotal':str(sum(Q(c['volume']) for c in exactCells)),'exactVolumeMin':str(min(Q(c['volume']) for c in exactCells)),'exactVolumeMax':str(max(Q(c['volume']) for c in exactCells)),'OpenFoamFormulaVolumeTotal':float(V.sum()),'edgeBadCells':edgeBad,'edgeSetExactMatch':set(edgeBad)==set(sets['zipUpCells']),'exactExistingVertexSubdivision':{'facesTouched':len(set(x['face'] for x in inserts)),'verticesInserted':len(inserts),'allCellEdgesPairedOriented':not subBad,'remainingBadCells':subBad,'SfEveryFaceExactEqual':subAreaEqual,'reapplyUnchangedWsb3ReproducesEveryActualRing':rePruned==F,'reapplyWsb3Faces':pruneFaces,'reapplyWsb3Points':pruneCount,'maxFaceCentreDifference':float(np.max(np.linalg.norm(subFC-FC,axis=1))),'maxCellFoamVolumeDifference':float(np.max(np.abs(subV-V))),'scope':'proof-only lists, no polyMesh rewrite, not native checkMesh acceptance'},'facePlanePredicateBadCells':[x['cell'] for x in planes],'concaveSetExactMatch':{x['cell'] for x in planes}==set(sets['concaveCells']),'positivePlaneViolationCells':positive,'planeOnlyCells':sorted({x['cell'] for x in planes}-set(positive)),'badFaceTetNumbers':[{'face':r['face'],'ownerInvalidEdgeFan':any(t['owner']['quality']>-1e-30 for t in r['edgeTets']),'neighbourInvalidEdgeFan':any(t['neighbour']['quality']<1e-30 for t in r['edgeTets']),'sharedBaseExists':bool(r['sharedPositiveBaseIndices']),'pyramids':r['pyramids']} for r in badFaces]}
write('geometry-summary.json',summary);write('exact-cell-geometry.json',exactCells);write('bad-face-tets.json',badFaces);write('face-plane-witnesses.json',planes);write('existing-vertex-subdivision-proof.json',{'insertions':inserts,'badBefore':[{'cell':c,'edges':edges(c,F)} for c in edgeBad],'badAfter':[] if not subBad else [{'cell':c,'edges':edges(c,Fsub)} for c in subBad]})
print(json.dumps(summary,indent=2))
