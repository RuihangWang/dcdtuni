"""Exact, restricted topology-derived proposal basis. No optimizer coordinates."""
import struct
import numpy as np

def bits(x):return struct.pack('<d',float(x))

def internal_star(doc,p):
 fs=[(int(k),f) for k,f in doc['faces'].items() if p in f['pointIDs']]
 return fs if fs and all(f['patch'] is None and f.get('sourceZone')==-1 for _,f in fs) else None

def qualifies_pair_axis(doc,pair,axis):
 if bits(doc['points'][str(pair[0])][axis])!=bits(doc['points'][str(pair[1])][axis]):return False,[]
 if any(internal_star(doc,p) is None for p in pair):return False,[]
 rows=[]
 for k,f in doc['faces'].items():
  touches=set(pair)&set(f['pointIDs'])
  if len(f['pointIDs'])==3 or not touches:continue
  pts=[doc['points'][str(p)] for p in f['pointIDs']]
  axes=[a for a in range(3) if len({bits(q[a]) for q in pts})==1]
  reason=None
  if any(a!=axis for a in axes):reason='tangent exact coordinate plane'
  elif axis in axes and touches==set(pair):
   fixed=[doc['points'][str(p)] for p in f['pointIDs'] if p not in pair]
   if fixed:
    for a in range(3):
     if a==axis:continue
     pa,pb=[doc['points'][str(p)][a] for p in pair]
     if bits(pa)==bits(pb) and len({bits(q[a]) for q in fixed})==1 and bits(fixed[0][a])!=bits(pa):reason='rotating exact two-level plane';break
  rows.append({'face':int(k),'reason':reason})
 return bool(rows) and all(r['reason'] is not None for r in rows),rows

def select(doc,target):
 face=doc['faces'][str(target)]
 if face['patch'] is not None or face.get('sourceZone')!=-1:raise ValueError('unprotected local internal target required')
 key=lambda p:tuple(doc['points'][str(p)])
 pivots=[]
 for p in face['pointIDs']:
  fs=internal_star(doc,p)
  if fs is not None and all(len(f['pointIDs'])==3 for _,f in fs):pivots.append(p)
 if not pivots:raise ValueError('no full triangular-star interior pivot')
 pivot=min(pivots,key=key);pair=sorted([p for p in face['pointIDs'] if p!=pivot],key=key)
 chosen=None;proof=[]
 if len(pair)==2:
  for axis in range(3):
   ok,rows=qualifies_pair_axis(doc,pair,axis)
   if ok:chosen=axis;proof=rows;break
 moving=[pivot]+(pair if chosen is not None else [])
 B=np.zeros((3*len(moving),4 if chosen is not None else 3));B[:3,:3]=np.eye(3)
 if chosen is not None:
  B[3+chosen,3]=1;B[6+chosen,3]=1
 return {'movingPoints':moving,'motionBasis':B.tolist(),'pairAxis':chosen,'pairEligibility':proof,'allInputsFromCurrentTopology':True}
