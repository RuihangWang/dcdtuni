from pathlib import Path
import json,time,copy
import numpy as np
from scipy.optimize import minimize
here=Path(__file__).parent
base=(here/'search_joint.py').read_text().split('history=[];cache={};best=None;start=')[0]
base=base.replace("here/'joint-six-dimensional-v1'","here/'joint-six-dimensional-v2'")
ns={'__file__':str(here/'search_joint.py'),'__name__':'compact_model'}
exec(compile(base,str(here/'search_joint.py'),'exec'),ns)
model,oldmodel,out,old,oldval,initial,targets,p0,lo,scale,q0=[ns[k]for k in ['model','oldmodel','out','old','oldval','initial','targets','p0','lo','scale','q0']]
folder=here/'joint-six-dimensional-v2';history=[];cache={};best=None;start=time.monotonic();ceiling=86.9077
protected=[]
for c in model.cells:
 if c in oldmodel.cm and c in out['children'] and oldval['D'][oldmodel.cm[c]]>=.001:protected.append(model.cm[c])
class Stop(Exception):pass
def evaluate(q):
 global best
 key=tuple(map(float,q))
 if key in cache:return cache[key]
 if len(history)>=256 or time.monotonic()-start>45:raise Stop
 positions=lo+np.array(q).reshape(2,3)*scale
 v=model.evaluate(dict(zip(targets,positions)));ss=ns['summary'](model,v)
 feasible=bool(min(v['D'][protected])>=.001 and min(v['W'])>=.05 and min(v['R'])>=.01 and max(v['S'])<=4 and max(v['N'])<=ceiling and min(v['V'])>0 and min(v['pmin'])>0 and max(v['conc'])<=-1e-6)
 row={'positions':positions.tolist(),'q':list(q),'originalRetainedPassingHardRolesPreserved':feasible,**ss}
 history.append(row);cache[key]=(v,row)
 if best is None or (feasible and(not best['originalRetainedPassingHardRolesPreserved']or (row['counts']['D'],-row['Dmin'])<(best['counts']['D'],-best['Dmin'])))or(not feasible and not best['originalRetainedPassingHardRolesPreserved']and row['Dmin']>best['Dmin']):best=row
 return v,row
pminScale=np.maximum(abs(initial['V']),1e-20)
def constraints(q):
 v,r=evaluate(q)
 return np.r_[v['D'][protected]/.001-1,v['W']/.05-1,v['R']/.01-1,4-v['S'],(ceiling-v['N'])/70,v['pmin']/pminScale,(-1e-6-v['conc'])]
def objective(q):
 v,r=evaluate(q);x=np.log(np.maximum(v['D'][model.core],1e-14)/.001)
 a=-x/.25;m=max(a)
 return .25*(m+np.log(np.exp(a-m).sum()))
first=evaluate(q0)[1]
try:
 res=minimize(objective,q0,method='SLSQP',bounds=[(0,1)]*6,constraints={'type':'ineq','fun':constraints},options={'maxiter':32,'ftol':1e-10})
 stop={'success':bool(res.success),'message':str(res.message),'parameters':res.x.tolist()}
except Stop:stop={'success':False,'message':'bounded256requests/45s'}
candidate=copy.deepcopy(out)
for p,xyz in zip(targets,best['positions']):candidate['points'][str(p)]=xyz
(folder/'candidate-best.json').write_text(json.dumps(candidate,indent=2,allow_nan=False)+'\n')
report={'scope':'One second bounded actual32cell topology+twointeriorpoints diagnostic. Protect all retained originally passing hard roles during mathematical search; final new26 all inherit replaced passing D. No native acceptance.','initial':first,'best':best,'stop':stop,'requests':len(history),'seconds':time.monotonic()-start,'retainedPassingDCells':[model.cells[j]for j in protected],'history':history}
(folder/'search-report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(json.dumps({k:report[k]for k in ['initial','best','stop','requests','seconds']},allow_nan=False))
