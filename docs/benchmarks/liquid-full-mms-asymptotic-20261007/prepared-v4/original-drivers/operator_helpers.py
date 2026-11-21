from pathlib import Path
import os,sys,json,time,hashlib,traceback
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R/'source-frozen/src'))
import torch,numpy as np
from scipy.sparse import coo_matrix,diags
from scipy.sparse.linalg import splu
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
torch.set_num_threads(2);torch.set_num_interop_threads(1)
raw=json.loads((R/'evidence/results/manufactured-dt-1p0.liquidrun').read_text());d=admit_dae_runtime_input(raw);eq=d.equation_graph;g=eq.graph
assert g.N_nodes==229 and g.N_edges==286 and eq.size==1693
N=g.N_nodes;NW=g.N_cells;NC=g.N_cp;NH=g.N_hx;assert (NW,NC,NH)==(892,56,1)
ids={k:v.detach().numpy() for k,v in eq._ids.items()};fr=eq._from.numpy();to=eq._to.numpy();bcT=eq._bc_T_mask.numpy();cell_edges=eq._cells.numpy();first=eq._first.numpy();last=eq._last.numpy();previous=eq._previous.numpy();following=eq._next.numpy();pipefirst=eq._pipe_first.numpy();pipelast=eq._pipe_last.numpy()
layout={'node_temperature_K':slice(0,N),'water_temperature_K':slice(N,N+NW),'solid_temperature_K':slice(N+NW,N+NW+NC),'hx_secondary_K':slice(N+NW+NC,N+NW+NC+NH),'hx_primary_K':slice(N+NW+NC+NH,N+NW+NC+2*NH)}
size=N+NW+NC+2*NH;cycle=np.load(R/'evidence/results/independent-cycle-construction.npz');phase=cycle['xphase'];amplitude=cycle['xamplitude'];xbase=cycle['xbase'];omega=2*np.pi/32
forcing_slices=[layout['water_temperature_K'],layout['solid_temperature_K'],layout['hx_secondary_K'],layout['hx_primary_K']];columns=['water','solids56','HXsecondary','HXprimary']

def matrix(p,m):
 cp=p['fluid_cp'].item();rho=p['fluid_rho'].item();up=np.where(m>=0,fr,to);down=np.where(m>=0,to,fr);w=np.abs(m);incoming=np.bincount(down,weights=w,minlength=N);eff=-np.expm1(-p['cp_UA']/(w[ids['cp']]*cp));cpLocal={int(e):i for i,e in enumerate(ids['cp'])};hxLocal={int(e):i for i,e in enumerate(ids['hx'])};pipeLocal={int(e):i for i,e in enumerate(ids['pipe'])}
 row=[];col=[];val=[]
 def add(a,b,v):row.append(a);col.append(b);val.append(v)
 capacity=np.zeros(size);capacity[layout['water_temperature_K']]=rho*cp*p['cell_volume'];capacity[layout['solid_temperature_K']]=p['cp_Csolid'];capacity[layout['hx_secondary_K']]=rho*cp*p['secondary_volume'];capacity[layout['hx_primary_K']]=p['primary_rho']*p['primary_cp']*p['primary_volume']
 physical=np.zeros(size);physical[layout['water_temperature_K']]=p['cell_heat_W'];physical[layout['solid_temperature_K']]=p['heat_W'];physical[layout['hx_primary_K']]=p['primary_flow_kg_s']*p['primary_cp']*p['primary_inlet_K']
 for n in range(N):
  add(n,n,1. if bcT[n] else incoming[n]*cp)
  if bcT[n]:physical[n]=p['temperature_bc'][n]
 for e in range(g.N_edges):
  u,downstream=int(up[e]),int(down[e]);q=w[e]*cp
  if bcT[downstream]:continue
  if e in pipeLocal:
   loc=pipeLocal[e];cell=int(pipelast[loc] if m[e]>=0 else pipefirst[loc]);add(downstream,N+cell,-q)
  elif e in cpLocal:
   loc=cpLocal[e];add(downstream,u,-q*(1-eff[loc]));add(downstream,N+NW+loc,-q*eff[loc])
  elif e in hxLocal:add(downstream,N+NW+NC+hxLocal[e],-q)
  else:add(downstream,u,-q)
 for c,e in enumerate(cell_edges):
  r=N+c;q=w[e]*cp;add(r,r,q)
  if m[e]>=0:cc=int(fr[e]) if first[c] else N+int(previous[c])
  else:cc=int(to[e]) if last[c] else N+int(following[c])
  add(r,cc,-q)
 for i,e in enumerate(ids['cp']):
  r=N+NW+i;q=w[e]*cp*eff[i];add(r,r,q);add(r,int(up[e]),-q)
 for i,e in enumerate(ids['hx']):
  s=N+NW+NC+i;pidx=s+NH;secadv=w[e]*cp;UA=p['hx_UA'][i];pradv=p['primary_flow_kg_s'][i]*p['primary_cp'][i]
  add(s,s,secadv+UA);add(s,int(up[e]),-secadv);add(s,pidx,-UA);add(pidx,pidx,pradv+UA);add(pidx,s,-UA)
 return coo_matrix((val,(row,col)),shape=(size,size)).tocsc(),capacity,physical

