from pathlib import Path
import json,time,numpy as np
from scipy.optimize import minimize
import derive_star as D
here=Path(__file__).parent;start=time.monotonic()
outer=D.outer;P=D.P;old=D.outside
bs=np.array([old.face(r['face'])[0]*(1 if D.original['faces'][str(r['face'])]['owner']in D.oldcore else -1)for r in outer])
bc=np.array([old.face(r['face'])[1]for r in outer])
edge={}
for j,r in enumerate(outer):
 ids=r['ids']
 for a,b in zip(ids,ids[1:]+ids[:1]):edge.setdefault(tuple(sorted((a,b))),[]).append((j,b,a))
edges=sorted(edge);adj=np.array([[x[0]for x in edge[e]]for e in edges])
assert all(len(edge[e])==2 for e in edges)
ea=np.array([P[edge[e][0][1]]for e in edges]);eb=np.array([P[edge[e][0][2]]for e in edges])
extidx=[i for i,r in enumerate(outer)if r['outside']is not None]
outCC=np.array([old.cell(outer[i]['outside'])[2]for i in extidx]);outV=np.array([old.cell(outer[i]['outside'])[3]for i in extidx])
selected=[r['outside']is not None or (r['patch']and ('processor'in r['patch']['type'].lower()or'cyclic'in r['patch']['type'].lower()))for r in outer]
inc=[[]for _ in outer]
for e,k in enumerate(edges):
 rs=edge[k]
 for j,_,_ in rs:inc[j].append(e)
ceiling=86.9077
def met(sf,fc,pts,co,vo,cn,vn):
 delta=cn-co;cpf=fc-co
 a=np.abs(np.einsum('ij,ij->i',sf,cpf));b=np.abs(np.einsum('ij,ij->i',sf,cn-fc))
 W=np.minimum(a,b)/(a+b+1e-300);R=np.minimum(vo,vn)/(np.maximum(vo,vn)+1e-300)
 dn=np.linalg.norm(delta,axis=1);sn=np.linalg.norm(sf,axis=1)
 sv=cpf-(np.einsum('ij,ij->i',sf,cpf)/(np.einsum('ij,ij->i',sf,delta)+1e-150))[:,None]*delta
 svhat=sv/(np.linalg.norm(sv,axis=1)+1e-150)[:,None]
 proj=np.array([max(abs((pp-ff)@hh))for pp,ff,hh in zip(pts,fc,svhat)])
 skew=np.linalg.norm(sv,axis=1)/np.maximum(.2*dn+1e-150,proj)
 N=np.degrees(np.arccos(np.clip(np.einsum('ij,ij->i',sf,delta)/(dn*sn+1e-150),-1,1)))
 return W,R,skew,N
def values(ap):
 CC=(3*bc+ap)/4;V=-np.einsum('ij,ij->i',bs,ap-bc)/3
 sf=np.cross(eb-ea,ap-ea)/2;fc=(ea+eb+ap)/3
 Ds=[]
 for j,ei in enumerate(inc):
  ss=[sf[e]*(1 if adj[e,0]==j else -1)for e in ei]
  if selected[j]:ss.append(bs[j])
  s=np.array(ss);av=np.linalg.norm(s,axis=1).mean();Ds.append(np.linalg.det((s/av).T@(s/av))/8)
 sidepts=[np.array([a,b,ap])for a,b in zip(ea,eb)]
 w,r,s,n=met(sf,fc,sidepts,CC[adj[:,0]],V[adj[:,0]],CC[adj[:,1]],V[adj[:,1]])
 ee=np.array(extidx);pp=[np.array([P[v]for v in outer[i]['ids']])for i in extidx]
 we,re,se,ne=met(bs[ee],bc[ee],pp,CC[ee],V[ee],outCC,outV)
 return np.array(Ds),np.r_[w,we],np.r_[r,re],np.r_[s,se],np.r_[n,ne],V
history=[];cache={};best=None
class Stop(Exception):pass
def evaluate(q):
 global best
 key=tuple(map(float,q))
 if key in cache:return cache[key]
 if len(history)>=160 or time.monotonic()-start>=65:raise Stop
 ap=D.lo+np.array(q)*D.span
 dv,w,r,s,n,vol=values(ap)
 vals=np.r_[dv/.001,w/.05,r/.01,4/np.maximum(s,1e-20),ceiling/np.maximum(n,1e-20)]
 score=float(min(vals))
 row={'point':ap.tolist(),'q':list(q),'Dmin':float(min(dv)),'Wmin':float(min(w)),'Rmin':float(min(r)),'Smax':float(max(s)),'Nmax':float(max(n)),
      'minV':float(min(vol)),'score':score,'counts':dict(D=int(sum(dv<.001)),W=int(sum(w<.05)),R=int(sum(r<.01)),S=int(sum(s>4)),N=int(sum(n>70)))}
 history.append(row);cache[key]=(dv,w,r,s,n,vol,row)
 feasible=bool(min(w)>=.05 and min(r)>=.01 and max(s)<=4 and max(n)<=ceiling and min(vol)>0 and min(D.planes[:,3]-D.planes[:,:3]@ap)>=3e-6)
 row['nonDFeasible']=feasible
 if best is None or (feasible and (not best.get('nonDFeasible',False) or row['Dmin']>best['Dmin'])) or (not feasible and not best.get('nonDFeasible',False)and score>best['score']):best=row
 return cache[key]
def con(x):
 dv,w,r,s,n,vol,row=evaluate(x[:3]);ap=D.lo+x[:3]*D.span
 return np.r_[dv/.001-x[3],w/.05-1,r/.01-1,4-s,ceiling-n,(D.planes[:,3]-D.planes[:,:3]@ap-3e-6)/np.linalg.norm(D.span)]
q0=(np.array(D.eligible['positiveKernelLP']['point'])-D.lo)/D.span
initial=evaluate(q0)
full= json.loads((here/'chebyshev-profile.json').read_text())
modelD=np.array([r['D']for r in full['cells']])
assert np.max(np.abs(initial[0]-modelD))<1e-12, ('fast-core-D-binding',np.max(np.abs(initial[0]-modelD)))
try:
 res=minimize(lambda x:-x[3],np.r_[q0,initial[-1]['Dmin']/.001],method='SLSQP',bounds=[(0,1)]*3+[(0,20)],
              constraints={'type':'ineq','fun':con},options={'maxiter':32,'ftol':1e-10})
 stop=dict(success=bool(res.success),message=str(res.message),parameters=res.x.tolist())
except Stop:stop=dict(success=False,message='bounded160evaluations/65seconds')
candidate,summary,detail=D.profile(best['point'],True)
out=dict(scope='One actual complete P12 32-cell two-point-star shell, one finite3D geometry-derived star-point diagnostic, both old internal points removed from used topology. No native/operator/business acceptance.',
         fastInitial=history[0],best=best,fullPureBestSummary=summary,stop=stop,evaluations=len(history),seconds=time.monotonic()-start,history=history)
(here/'finite-star-search.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
(here/'candidate-best.json').write_text(json.dumps(candidate,indent=2,allow_nan=False)+'\n')
(here/'best-profile.json').write_text(json.dumps({'summary':summary,**detail},indent=2,allow_nan=False)+'\n')
print(json.dumps({k:out[k]for k in ['fastInitial','best','fullPureBestSummary','stop','evaluations','seconds']}))
