"""Bounded generic proposal only, with original-formula acceptance afterwards."""
from pathlib import Path
import sys,json,copy,time,hashlib
import numpy as np

HERE=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/internal-polygon-plane-modes-7785-20261005")
N=HERE.parent/"shared-point-quality-7785-20261005";AIR=HERE.parents[1]
sys.path[:0]=[str(N),str(N/"readers"),str(AIR/"src")]
import audit_shared_point_candidate as V
U=V.U
def evaluator(doc,moving,warning_ceiling=88.2022,maxiter=100,fixed_axes=None,structural_validator=None,initial_docs=(),motion_basis=None,target_faces=()):
 moving=list(map(int,moving))
 # The unconstrained proposal alone never authorizes a point change.
 (structural_validator or V.validate_structure)(doc,doc,moving)
 fixed_axes=fixed_axes or {}
 m,old=U.family_metrics(doc);old['cells']+=[m.cell_detail(c) for c in doc['outsideCells']]
 pids=sorted(m.points);pindex={p:i for i,p in enumerate(pids)};xyz0=np.array([m.points[p] for p in pids])
 fids=sorted(m.faces);fi={f:i for i,f in enumerate(fids)};cids=sorted(m.cells);ci={c:i for i,c in enumerate(cids)}
 sf0=np.array([m.face(f)[0] for f in fids]);fc0=np.array([m.face(f)[1] for f in fids])
 moveids=[pindex[p] for p in moving];initial=xyz0[moveids].copy();L=np.ptp(xyz0,axis=0).max()
 changed=[fi[f] for f in fids if any(p in m.faces[f]['pointIDs'] for p in moving)]
 triangle_changed=[i for i in changed if len(m.faces[fids[i]]['pointIDs'])==3]
 polygon_changed=[i for i in changed if i not in triangle_changed]
 tri=np.array([[pindex[p] for p in m.faces[fids[i]]['pointIDs']] for i in triangle_changed])
 ec=[];ef=[];es=[];selected=[]
 for c in cids:
  for f in m.cells[c]['faces']:
   ec.append(ci[c]);ef.append(fi[f]);es.append(1 if m.faces[f]['owner']==c else -1)
   selected.append(m.faces[f]['patch'] is None)
 ec=np.array(ec);ef=np.array(ef);es=np.array(es);sel=np.array(selected);nC=len(cids)
 num=np.bincount(ec,minlength=nC)
 af=np.array([fi[f] for f in fids if m.faces[f]['owner'] in doc['children'] or m.faces[f]['neighbour'] in doc['children']])
 own=np.array([ci[m.faces[fids[f]]['owner']] for f in af])
 nei=np.array([ci.get(m.faces[fids[f]]['neighbour'],-1) for f in af]);internal=nei>=0
 avf=[];avp=[]
 for a,j in enumerate(af):
  for p in m.faces[fids[j]]['pointIDs']:avf.append(a);avp.append(pindex[p])
 avf=np.array(avf);avp=np.array(avp);core=np.array([ci[c] for c in doc['children']])
 B=np.asarray(motion_basis,dtype=float)
 if B.ndim!=2 or B.shape[0]!=3*len(moving) or not np.isfinite(B).all():raise ValueError("finite motion basis required")
 k=B.shape[1]
 if np.linalg.matrix_rank(B)!=k:raise ValueError("motion basis must have independent columns")
 def fast(q):
  xyz=xyz0.copy();xyz[moveids]=initial+(B@np.asarray(q[:-1])).reshape(len(moving),3)*L
  sf=sf0.copy();fc=fc0.copy();tp=xyz[tri]
  sf[triangle_changed]=.5*np.cross(tp[:,1]-tp[:,0],tp[:,2]-tp[:,0]);fc[triangle_changed]=tp.mean(axis=1)
  for i in polygon_changed:
   poly=xyz[[pindex[p] for p in m.faces[fids[i]]['pointIDs']]]
   geo=V.G.Geometry.from_arrays(poly,[list(range(len(poly)))])
   sf[i],fc[i]=geo.face(0)[:2]
  est=np.column_stack([np.bincount(ec,weights=fc[ef,k],minlength=nC)/num for k in range(3)])
  pv=np.einsum('ij,ij->i',sf[ef]*es[:,None],fc[ef]-est[ec])
  vp=np.bincount(ec,weights=pv,minlength=nC);vol=vp/3
  cc=np.column_stack([np.bincount(ec,weights=pv*(.75*fc[ef,k]+.25*est[ec,k]),minlength=nC)/vp for k in range(3)])
  ss=sf[ef[sel]];scc=ec[sel];norm=np.linalg.norm(ss,axis=1);cnt=np.bincount(scc,minlength=nC)
  avg=np.bincount(scc,weights=norm,minlength=nC)/np.maximum(cnt,1)
  tensor=np.array([np.bincount(scc,weights=ss[:,i]*ss[:,j],minlength=nC) for i in range(3) for j in range(3)]).T.reshape(nC,3,3)
  det=np.abs(np.linalg.det(tensor))/(8*np.maximum(avg,1e-150)**6)
  area=sf[af];fcaf=fc[af];normal=area/np.linalg.norm(area,axis=1)[:,None];co=cc[own];cn=cc[np.maximum(nei,0)]
  d=cn-co;d[~internal]=normal[~internal]*np.einsum('ij,ij->i',normal[~internal],fcaf[~internal]-co[~internal])[:,None]
  cpf=fcaf-co;doo=np.abs(np.einsum('ij,ij->i',area,cpf));dnn=np.abs(np.einsum('ij,ij->i',area,cn-fcaf))
  w=np.minimum(doo,dnn)/(doo+dnn+1e-300);w[~internal]=1
  r=np.minimum(vol[own],vol[np.maximum(nei,0)])/(np.maximum(vol[own],vol[np.maximum(nei,0)])+1e-300);r[~internal]=1
  n=np.degrees(np.arccos(np.clip(np.einsum('ij,ij->i',normal,d)/np.maximum(np.linalg.norm(d,axis=1),1e-150),-1,1)));n[~internal]=0
  sv=cpf-(np.einsum('ij,ij->i',area,cpf)/(np.einsum('ij,ij->i',area,d)+1e-150))[:,None]*d
  svmag=np.linalg.norm(sv,axis=1);svhat=sv/np.maximum(svmag[:,None],1e-150)
  fd=np.where(internal,.2,.4)*np.linalg.norm(d,axis=1)+1e-150
  np.maximum.at(fd,avf,np.abs(np.einsum('ij,ij->i',xyz[avp]-fcaf[avf],svhat[avf])))
  s=svmag/fd
  return det,w,r,s,n,vol,xyz
 q0=np.zeros(k+1);q0[-1]=min(fast(q0)[0][core])/.001
 base=fast(q0);arrays=[base[0][core],base[1],base[2],base[3],base[4]]
 lim=[.001,.05,.01,4,70];higher=[False,False,False,True,True]
 beforeRows=[old['cells'],old['faces'],old['faces'],old['faces'],old['faces']]
 def constraints(q):
  D,W,R,S,Nn,V,xyz=fast(q)
  vals=[D[core],W,R,S,Nn];out=[]
  for v,b,l,upper in zip(vals,arrays,lim,higher):
   if upper:
    bound=np.full_like(b,warning_ceiling) if l==70 else np.where(b<=l,l,max(l,float(b.max())));out.extend((bound-v)/l)
   else:
    bound=np.where(b>=l,l,min(l,float(b.min())));out.extend((v-bound)/l)
  out.extend((D[core]-.001*q[-1])/.001)
  out.extend((V[core]-.00001*base[5][core])/np.maximum(base[5][core],1e-150))
  return np.asarray(out)
 def actual(q):
  cand=copy.deepcopy(doc);pts=fast(q)[6]
  for idx,p in zip(moveids,moving):cand['points'][str(p)]=pts[idx].tolist()
  cand['originalApex']=cand['points'][str(cand['apexWrittenPoint'])]
  mm,qq=U.family_metrics(cand);qq['cells']+=[mm.cell_detail(c) for c in cand['outsideCells']]
  lineage=U.lineage_pareto(old,qq,[],None)
  return cand,qq,lineage
 return {"fast":fast,"constraints":constraints,"actual":actual,"q0":q0,"base":base,"core":core,"faceIDs":[fids[j] for j in af],"L":float(L),"dimension":k}
