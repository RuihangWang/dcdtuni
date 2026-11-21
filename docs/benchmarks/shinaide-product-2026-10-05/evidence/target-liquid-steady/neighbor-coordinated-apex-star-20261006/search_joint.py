from pathlib import Path
import sys,json,copy,hashlib,time
import numpy as np
from scipy.optimize import minimize
I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration');sys.path.insert(0,str(I/'tools'))
import audit_air_cone_small_union_independent as U
here=Path(__file__).parent
S=here.parent/'complete-interior-edge-vertex-star-20261006'
old=json.loads((here/'complete-star-input.json').read_text())
prior=json.loads((S/'complete-star-input.json').read_text())
star=json.loads((S/'single-interior-point-star-v1/candidate-best-global-handles.json').read_text())
priorcore=set(prior['children'])
out=copy.deepcopy(old)
removed=[int(f)for f,r in old['faces'].items()if r['owner']in priorcore and r['neighbour']in priorcore]
for c in priorcore:del out['cells'][str(c)]
for f in removed:del out['faces'][str(f)]
newfaces=sorted(int(f)for f in star['faces']if f not in prior['faces'])
faceMap={f:2251326+j for j,f in enumerate(newfaces)}
assert not set(faceMap.values())&set(map(int,old['faces']))
for f,r in star['faces'].items():
 if int(f)in newfaces:out['faces'][str(faceMap[int(f)])]=copy.deepcopy(r)
 elif r!=prior['faces'][f]:out['faces'][f]=copy.deepcopy(r)
for c in star['children']:
 r=copy.deepcopy(star['cells'][str(c)]);r['faces']=[faceMap.get(f,f)for f in r['faces']]
 out['cells'][str(c)]=r
ap=591824;assert set(star['points'])-set(prior['points'])=={str(ap)}
out['points'][str(ap)]=star['points'][str(ap)]
out['children']=sorted((set(old['children'])-priorcore)|set(star['children']))
out['firstHaloMetricFaces']=sorted(set(old['firstHaloMetricFaces'])-set(removed)|set(faceMap.values()))
for c,r in out['cells'].items():
 assert set(r['faces'])=={int(f)for f,r in out['faces'].items()if int(c)in [r['owner'],r['neighbour']]}
(outdir:=here/'joint-six-dimensional-v1').mkdir(exist_ok=True)
(outdir/'candidate-initial.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
(outdir/'topology-binding.json').write_text(json.dumps({'oldCore':old['children'],'replacedPriorCore':sorted(priorcore),'retainedActualAPStarCells':sorted(set(old['children'])-priorcore),'newCore':out['children'],'removedFaces':removed,'newFaceMap':faceMap,'movingPoints':[ap,28144],'sourceInputSha256':hashlib.sha256((here/'complete-star-input.json').read_bytes()).hexdigest(),'priorCandidateSha256':hashlib.sha256((S/'single-interior-point-star-v1/candidate-best-global-handles.json').read_bytes()).hexdigest()},indent=2)+'\n')
class Model:
 def __init__(self,doc):
  self.doc=doc;self.pointIDs=sorted(map(int,doc['points']));self.pm={p:i for i,p in enumerate(self.pointIDs)};self.P=np.array([doc['points'][str(p)]for p in self.pointIDs])
  self.cells=sorted(map(int,doc['cells']));self.cm={c:i for i,c in enumerate(self.cells)}
  self.faceIDs=sorted(map(int,doc['faces']));self.fm={f:i for i,f in enumerate(self.faceIDs)}
  st=U.WrittenStencil(doc,doc['originalApex'])
  self.sf0=np.array([st.face(f)[0]for f in self.faceIDs]);self.fc0=np.array([st.face(f)[1]for f in self.faceIDs])
  self.rings=[np.array([self.pm[p]for p in doc['faces'][str(f)]['pointIDs']])for f in self.faceIDs]
  self.tri=np.array([i for i,f in enumerate(self.faceIDs)if len(self.rings[i])==3]);self.tp=np.array([self.rings[i]for i in self.tri])
  self.selected=np.array([doc['faces'][str(f)]['neighbour']is not None or (doc['faces'][str(f)]['patch']and any(k in doc['faces'][str(f)]['patch']['type'].lower()for k in ['processor','cyclic']))for f in self.faceIDs],bool)
  self.cfs=[];self.sign=[]
  for c in self.cells:
   fs=sorted(f for f in doc['cells'][str(c)]['faces']if doc['faces'][str(f)]['owner']==c)+sorted(f for f in doc['cells'][str(c)]['faces']if doc['faces'][str(f)]['neighbour']==c)
   self.cfs.append(np.array([self.fm[f]for f in fs]));self.sign.append(np.array([1 if doc['faces'][str(f)]['owner']==c else -1 for f in fs]))
  self.core=np.array([self.cm[c]for c in doc['children']])
  self.metric=np.array([self.fm[f]for f in doc['firstHaloMetricFaces']])
  self.own=np.array([self.cm[doc['faces'][str(self.faceIDs[f])]['owner']]for f in self.metric])
  self.nei=np.array([self.cm.get(doc['faces'][str(self.faceIDs[f])]['neighbour'],-1)for f in self.metric])
  assert all(self.nei[self.selected[self.metric]]>=0)
  self.internal=self.nei>=0
  self.cachedOld=st
 def evaluate(self,moves):
  P=self.P.copy()
  for p,q in moves.items():P[self.pm[p]]=q
  sf=self.sf0.copy();fc=self.fc0.copy()
  tp=P[self.tp];sf[self.tri]=np.cross(tp[:,1]-tp[:,0],tp[:,2]-tp[:,0])*.5;fc[self.tri]=(tp[:,0]+tp[:,1]+tp[:,2])*(1/3)
  CC=[];V=[];D=[];pmin=[];conc=[]
  for fs,sg in zip(self.cfs,self.sign):
   s=sf[fs]*sg[:,None];f=fc[fs];est=f.mean(0)
   py=np.einsum('ij,ij->i',s,f-est);sm=py.sum()
   cc=(py[:,None]*(.75*f+.25*est)).sum(0)/sm if sm else est
   CC.append(cc);V.append(sm/3);pmin.append(min(np.einsum('ij,ij->i',s,f-cc))/3)
   selected=sf[fs][self.selected[fs]]
   area=np.linalg.norm(selected,axis=1);av=area.mean()if len(area)else 0
   D.append(np.linalg.det((selected/av).T@(selected/av))/8 if av else 0)
   delta=f[None,:,:]-f[:,None,:];ln=np.linalg.norm(delta,axis=2)
   dot=np.einsum('ijk,ik->ij',delta,s)/np.maximum(ln*np.linalg.norm(s,axis=1)[:,None],1e-300)
   conc.append(np.max(dot[~np.eye(len(fs),dtype=bool)]))
  CC=np.array(CC);V=np.array(V);D=np.array(D);pmin=np.array(pmin);conc=np.array(conc)
  fi=self.metric;own=self.own;nei=self.nei;ins=self.internal;cn=CC[np.maximum(nei,0)]
  W=np.ones(len(fi));R=np.ones(len(fi));N=np.zeros(len(fi));Sk=np.zeros(len(fi))
  co=CC[own];delta=cn-co;cpf=fc[fi]-co
  ss=sf[fi];da=np.abs(np.einsum('ij,ij->i',ss,cpf));db=np.abs(np.einsum('ij,ij->i',ss,cn-fc[fi]))
  W[ins]=np.minimum(da,db)[ins]/(da+db+1e-300)[ins];R[ins]=np.minimum(V[own],V[np.maximum(nei,0)])[ins]/(np.maximum(V[own],V[np.maximum(nei,0)])+1e-300)[ins]
  den=np.einsum('ij,ij->i',ss,delta)
  skewvec=cpf-(np.einsum('ij,ij->i',ss,cpf)/(den+1e-150))[:,None]*delta
  lens=np.linalg.norm(skewvec,axis=1);h=skewvec/(lens+1e-150)[:,None]
  proj=np.array([np.max(abs((P[self.rings[f]]-fc[f])@hh))for f,hh in zip(fi,h)])
  dn=np.linalg.norm(delta,axis=1)
  Sk[ins]=(lens/np.maximum(.2*dn+1e-150,proj))[ins]
  sfhat=ss/(np.linalg.norm(ss,axis=1)+1e-150)[:,None]
  wallsv=cpf-np.einsum('ij,ij->i',sfhat,cpf)[:,None]*sfhat
  walllen=np.linalg.norm(wallsv,axis=1);wallh=wallsv/(walllen+1e-150)[:,None]
  wallproj=np.array([np.max(abs((P[self.rings[f]]-fc[f])@hh))for f,hh in zip(fi,wallh)])
  Sk[~ins]=(walllen/np.maximum(.4*np.linalg.norm(cpf,axis=1)+1e-150,wallproj))[~ins]
  N[ins]=np.degrees(np.arccos(np.clip(den/(dn*np.linalg.norm(ss,axis=1)+1e-150),-1,1)))[ins]
  return dict(D=D,W=W,R=R,S=Sk,N=N,V=V,CC=CC,pmin=pmin,conc=conc,sf=sf,fc=fc)
def summary(mod,val):
 return dict(Dmin=float(min(val['D'][mod.core])),Wmin=float(min(val['W'])),Rmin=float(min(val['R'])),Smax=float(max(val['S'])),Nmax=float(max(val['N'])),minV=float(min(val['V'])),minPyramid=float(min(val['pmin'])),maxFaceCentreDot=float(max(val['conc'])),counts={k:int(sum(val[k]<t if k in ['D','W','R']else val[k]>t))for k,t in [('D',.001),('W',.05),('R',.01),('S',4),('N',70)]})
model=Model(out);oldmodel=Model(old);oldval=oldmodel.evaluate({});initial=model.evaluate({})
binding=[]
for c in model.cells:
 _,_,cc,v=model.cachedOld.cell(c)
 j=model.cm[c]
 binding.append({'cell':c,'DModel':float(initial['D'][j]),'maxCCDelta':float(max(abs(initial['CC'][j]-cc))),'VDelta':float(initial['V'][j]-v)})
fullDs=np.array([model.cachedOld.cell_detail(c)['D']for c in model.cells])
assert max(abs(fullDs-initial['D']))<1e-10,('D-model-bind',max(abs(fullDs-initial['D'])))
(outdir/'fast-model-bindings.json').write_text(json.dumps({'initial':summary(model,initial),'original':summary(oldmodel,oldval),'initialDMaxAbsoluteDelta':float(max(abs(fullDs-initial['D']))),'cells':binding},indent=2)+'\n')
targets=[ap,28144];p0=np.array([out['points'][str(p)]for p in targets])
allcorepoints={v for c in out['children']for f in out['cells'][str(c)]['faces']for v in out['faces'][str(f)]['pointIDs']}
xyz=np.array([out['points'][str(p)]for p in allcorepoints]);span=xyz.max(0)-xyz.min(0);lo=xyz.min(0);scale=span
q0=((p0-lo)/scale).ravel()
history=[];cache={};best=None;start=time.monotonic();ceiling=86.9077
class Stop(Exception):pass
def evaluate(q):
 global best
 key=tuple(map(float,q))
 if key in cache:return cache[key]
 if len(history)>=180 or time.monotonic()-start>65:raise Stop()
 positions=lo+np.array(q).reshape(2,3)*scale
 val=model.evaluate(dict(zip(targets,positions)));ss=summary(model,val)
 feasible=bool(min(val['W'])>=.05 and min(val['R'])>=.01 and max(val['S'])<=4 and max(val['N'])<=ceiling and min(val['V'])>0 and min(val['pmin'])>0 and max(val['conc'])<=-1e-6)
 score=min(min(val['D'][model.core])/.001,min(val['W'])/.05,min(val['R'])/.01,4/max(1e-20,max(val['S'])),ceiling/max(1e-20,max(val['N'])))
 row={'positions':positions.tolist(),'q':list(q),'nonDFeasible':feasible,'score':float(score),**ss};history.append(row)
 if best is None or(feasible and(not best['nonDFeasible']or row['Dmin']>best['Dmin']))or(not feasible and not best['nonDFeasible']and row['score']>best['score']):best=row
 cache[key]=(val,row);return val,row
def con(q):
 v,row=evaluate(q[:6])
 return np.r_[v['D'][model.core]/.001-q[6],v['W']/.05-1,v['R']/.01-1,4-v['S'],(ceiling-v['N'])/70,v['pmin']/max(oldval['V'].sum(),1e-20),(-1e-6-v['conc'])]
v0,r0=evaluate(q0)
try:
 res=minimize(lambda x:-x[6],np.r_[q0,min(v0['D'][model.core])/.001],method='SLSQP',bounds=[(0,1)]*6+[(0,20)],constraints={'type':'ineq','fun':con},options={'maxiter':24,'ftol':1e-9})
 stop={'message':str(res.message),'success':bool(res.success),'parameters':res.x.tolist()}
except Stop:stop={'success':False,'message':'bounded180evaluations/65sec'}
candidate=copy.deepcopy(out)
for p,q in zip(targets,best['positions']):candidate['points'][str(p)]=q
candidate['originalApex']=old['originalApex']
(outdir/'candidate-best.json').write_text(json.dumps(candidate,indent=2,allow_nan=False)+'\n')
report={'scope':'One actual32-cell closed P12 cavity; joint new14-cell-star apex plus actual complete interior AP28144. Literal38 outer faces unchanged. Pure finite search, not native acceptance.','inputBindings':{'old':hashlib.sha256((here/'complete-star-input.json').read_bytes()).hexdigest(),'priorStar':hashlib.sha256((S/'single-interior-point-star-v1/candidate-best-global-handles.json').read_bytes()).hexdigest()},'counts':{'oldCore':len(old['children']),'newCore':len(candidate['children']),'completeOld':len(old['cells']),'completeNew':len(candidate['cells'])},'original':summary(oldmodel,oldval),'initial':r0,'best':best,'stop':stop,'evaluations':len(history),'seconds':time.monotonic()-start,'history':history}
(outdir/'search-report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(json.dumps({k:report[k]for k in ['counts','original','initial','best','stop','evaluations','seconds']},allow_nan=False))
