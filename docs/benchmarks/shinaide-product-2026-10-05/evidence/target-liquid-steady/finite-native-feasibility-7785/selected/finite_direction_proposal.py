from pathlib import Path
import sys,copy,json,time,hashlib
import numpy as np
H=Path(__file__).resolve().parent;A=H.parents[1];D=H.parent/'internal-polygon-plane-modes-7785-20261005';F=H.parent/'flat-point-quality-7785-20261005';R=H.parent/'shared-point-ratio-7785-20261005'
sys.path[:0]=[str(H),str(D),str(F)]
import compact_formula_evaluator as E
import audit_internal_polygon_motion as C
V=C.V
import audit_writer_point_format as W
# Finite halfspace projection, implemented directly; no scipy optimizer/QP/SQP.
def project(desired,A,b,updates=384,tol=2e-10):
 norms=np.linalg.norm(A,axis=1);use=norms>1e-13
 if np.any((~use)&(b < -tol)):return np.zeros_like(desired),False,0
 aa=A[use]/norms[use,None];bb=b[use]/norms[use];d=desired.copy();dual=np.zeros(len(bb));slack=aa@d+bb
 for j in range(updates):
  change=np.maximum(0,dual-slack)-dual;i=int(np.argmax(np.abs(change)))
  if abs(change[i])<tol:break
  delta=change[i];dual[i]+=delta;d+=delta*aa[i];slack+=delta*(aa@aa[i])
 return d,bool(np.min(aa@d+bb)>=-2e-8),j+1

class ProposalStop(Exception):
 pass

def proposal(ev,targets,max_iterations=32,max_evaluations=256,wall_seconds=15,target_reserve=.0105):
 start=time.monotonic();k=ev['dimension'];q=ev['q0'].copy();q[-1]=0
 target=[i for i,f in enumerate(ev['faceIDs']) if f in targets];count=0;history=[];best=q.copy();stop='iteration cap'
 if not target:raise ValueError('actual target ratios required')
 def evaluate(x):
  nonlocal count
  if count>=max_evaluations:raise ProposalStop('evaluation cap')
  if time.monotonic()-start>=wall_seconds:raise ProposalStop('wall cap')
  count+=1;v=ev['fast'](x);c=ev['constraints'](x);objective=-float(np.min(v[2][target])/.01)
  if not np.isfinite(objective) or not np.isfinite(c).all():raise ProposalStop('nonfinite proposal')
  return objective,c,v
 try:
  obj,con,v=evaluate(q)
  for it in range(max_iterations):
   eps=1e-5;J=[];grad=[]
   for j in range(k):
    plus=q.copy();minus=q.copy();plus[j]+=eps;minus[j]-=eps
    op,cp,_=evaluate(plus);om,cm,_=evaluate(minus)
    J.append((cp-cm)/(2*eps));grad.append((op-om)/(2*eps))
   J=np.asarray(J).T;grad=np.asarray(grad);norm=np.linalg.norm(grad)
   if not np.isfinite(norm) or norm<1e-12:stop='zero objective derivative';break
   accepted=False;row={'iteration':it,'beforeObjective':obj,'minimumConstraint':float(con.min()),'attempts':[]}
   for radius in [.004,.002,.001,.0005]:
    desired=-radius*grad/norm
    AA=np.vstack([J,np.eye(k),-np.eye(k)]);bb=np.concatenate([con,q[:k]+.08,.08-q[:k]])
    d,feasible,updates=project(desired,AA,bb)
    if not feasible:row['attempts'].append({'radius':radius,'projectionFeasible':False,'updates':updates});continue
    for frac in [1,.5,.25,.125,.0625,.03125]:
     trial=q.copy();trial[:k]+=frac*d;ot,ct,vt=evaluate(trial)
     if ct.min()>=-2e-9 and ot<obj-1e-8:
      q,obj,con,v=trial,ot,ct,vt;accepted=True;best=q.copy();row['attempts'].append({'radius':radius,'fraction':frac,'projectionFeasible':True,'updates':updates,'accepted':True});break
    if accepted:break
    row['attempts'].append({'radius':radius,'projectionFeasible':True,'updates':updates,'accepted':False})
   row['afterObjective']=obj;row['parameters']=q.tolist();history.append(row)
   print(json.dumps({'iteration':it,'targetR':-obj*.01,'constraintMin':float(con.min()),'accepted':accepted,'evaluations':count}),flush=True)
   if not accepted:stop='no feasible improving finite step';break
   if -obj*.01>=target_reserve:stop='target ratio proposal reserve reached';break
 except ProposalStop as exc:
  stop=str(exc)
 return {'parameters':best,'history':history,'evaluations':count,'elapsedSeconds':time.monotonic()-start,'stop':stop,
         'caps':{'iterations':max_iterations,'evaluations':max_evaluations,'wallSeconds':wall_seconds,'projectionUpdatesPerAttempt':384}}
