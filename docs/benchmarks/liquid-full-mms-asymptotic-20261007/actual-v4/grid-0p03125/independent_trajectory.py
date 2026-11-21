"""Independent continuous manufactured trajectory. NumPy/SciPy equations only.
Graph/index/physical coefficients are data. This module never calls F or a solver.
"""
import numpy as np
from scipy.optimize import linprog

class IndependentTrajectory:
 def __init__(self,eq,theta):
  self.eq=eq;self.g=eq.graph;self.layout=eq.state_layout
  self.p={k:theta[v].detach().cpu().numpy().astype(float) for k,v in eq.parameter_layout.items() if not k.startswith('mms_')}
  self.ids={k:v.detach().cpu().numpy() for k,v in eq._ids.items()}
  self.fr=eq._from.cpu().numpy();self.to=eq._to.cpu().numpy();n=self.g.N_nodes;e=self.g.N_edges
  B=np.zeros((n,e),dtype=np.int64);B[self.fr,np.arange(e)]=1;B[self.to,np.arange(e)]=-1;self.B=B
  adj=[[] for _ in range(n)]
  for i,(a,b) in enumerate(zip(self.fr,self.to)):adj[a].append((b,i));adj[b].append((a,i))
  parents=[None]*n;parents[0]=(-1,-1);queue=[0];tree=set()
  for a in queue:
   for b,i in sorted(adj[a]):
    if parents[b] is None:parents[b]=(a,i);tree.add(i);queue.append(b)
  if len(queue)!=n:raise ValueError('reference requires connected graph')
  tadj=[[] for _ in range(n)]
  for i in sorted(tree):
   a,b=self.fr[i],self.to[i];tadj[a].append((b,i));tadj[b].append((a,i))
  chords=[i for i in range(e) if i not in tree];C=np.zeros((e,len(chords)),dtype=np.int64)
  for col,i in enumerate(chords):
   C[i,col]=1;start,end=self.to[i],self.fr[i];q=[start];seen={start:None}
   for a in q:
    for b,j in tadj[a]:
     if b not in seen:seen[b]=(a,j);q.append(b)
   cur=end
   while cur!=start:
    prev,j=seen[cur];C[j,col]=1 if self.fr[j]==prev else -1;cur=prev
  if np.any(B@C):raise AssertionError('constructive cycle coverage')
  self.C=C
  constrained=np.concatenate([self.ids['cp'],self.ids['pump'],self.ids['hx']])
  target=np.concatenate([np.full(len(self.ids['cp']),.006),np.full(len(self.ids['pump']),.006*len(self.ids['cp'])),np.full(len(self.ids['hx']),.006*len(self.ids['cp']))])
  r=linprog(np.zeros(len(chords)),A_ub=-C[constrained].astype(float),b_ub=-target,bounds=[(-2.,2.)]*len(chords),method='highs',options={'threads':1})
  if not r.success:raise ValueError('independent cycle trajectory infeasible: '+r.message)
  rng=np.random.default_rng(20261006);jitter=rng.uniform(-1,1,len(chords))*.0005
  jm=C@jitter;cm=C@r.x
  positive=cm[constrained]
  scale=min(1.,float(np.min(.1*positive/(np.abs(jm[constrained])+1e-30))))
  self.a0=r.x+jitter*scale;self.m0=C@self.a0
  if np.min(np.abs(self.m0))<=1e-8:raise ValueError('all reference edges must be within a nonzero regime')
  self.phase=np.arange(len(chords))*.731;self.amplitude=rng.uniform(.0001,.0003,len(chords))
  bound=np.abs(C)@self.amplitude
  self.amplitude*=min(1.,float(np.min(.12*np.abs(self.m0)/(bound+1e-30))))
  self.omega=2*np.pi/32.
  self.cell_edge=eq._cells.cpu().numpy();self.first=eq._first.cpu().numpy();self.last=eq._last.cpu().numpy()
  self.prev=eq._previous.cpu().numpy();self.next=eq._next.cpu().numpy()
  self.free=eq._free.cpu().numpy();self.bc=eq._bc_mask.cpu().numpy();self.bcT=eq._bc_T_mask.cpu().numpy()
  if np.any(eq._closed_edges.cpu().numpy()) or np.any(eq._dead_edges.cpu().numpy()) or np.any(eq._flow_edges.cpu().numpy()) or np.any(eq._hx_setpoint.cpu().numpy()) or np.any(eq._cp_rise.cpu().numpy()):
   raise ValueError('reference first scope requires ordinary open active elements')
  self.nx=self.g.N_cells+self.g.N_cp+2*self.g.N_hx
  self.xphase=np.arange(self.nx)*.317
  self.xamplitude=.5+.25*np.sin(np.arange(self.nx)*.113)**2
  self.xbase=np.concatenate([294.+.1*np.sin(np.arange(self.g.N_cells)),302.+.3*np.sin(np.arange(self.g.N_cp)),np.full(self.g.N_hx,295.),np.full(self.g.N_hx,294.)])
  self.pressurephase=np.arange(n)*.193
  self.baseP=self.p['pressure_bc'][np.flatnonzero(self.bc)[0]]+1000*np.linspace(0,1,n)
  self.counts={'nodes':n,'edges':e,'cycles':len(chords),'pipes':len(self.ids['pipe']),'cold_plates':len(self.ids['cp']),'valves':len(self.ids['valve']),'pumps':len(self.ids['pump']),'HX':len(self.ids['hx']),'storage':self.nx}
 def headloss(self,m):
  p=self.p;ids=self.ids;eps=1e-8;rho=p['fluid_rho'][0];mu=p['fluid_mu'][0];grav=p['gravity'][0];loss=np.zeros_like(m)
  k=ids['pipe'];v=m[k];ab=np.sqrt(v*v+eps*eps);D=p['pipe_D'];area=p['pipe_area']
  Re=ab*D/(area*mu);lam=64/(Re+eps)
  turb=.25/(np.log10(p['pipe_roughness']/D/3.7+5.74/(Re+eps)**.9)+1e-10)**2
  blend=1/(1+np.exp(-np.clip((Re-2300)/200,-700,700)));f=(1-blend)*lam+blend*turb
  loss[k]=(f*p['pipe_L']/D+p['pipe_minor'])/(2*rho*area*area)*v*ab+rho*grav*p['pipe_dh']
  for kind in ('cp','hx'):
   k=ids[kind];v=m[k];loss[k]=p[kind+'_dp_a']*v*np.sqrt(v*v+eps*eps)+p[kind+'_dp_b']*v
  k=ids['pump'];v=m[k];loss[k]=-rho*grav*p['pump_rpm_ratio']**2*(p['pump_H0']-p['pump_a']*(v/rho)**2)
  k=ids['valve'];alpha=p['valve_opening'];char=self.eq._characteristic.cpu().numpy()
  kv=p['valve_Kv']*np.where(char==1,50**(alpha-1),np.where(char==2,np.sqrt(alpha),alpha));v=m[k]
  loss[k]=1e5*(rho/1000)*(3600/(rho*kv))**2*v*np.sqrt(v*v+eps*eps)
  return loss
 def evaluate(self,t):
  p=self.p;g=self.g;ids=self.ids;w0=self.omega
  m=self.C@(self.a0+self.amplitude*np.sin(w0*t+self.phase));md=self.C@(self.amplitude*w0*np.cos(w0*t+self.phase))
  pressure=self.baseP+200*np.sin(w0*t+self.pressurephase);pressure[self.bc]=p['pressure_bc'][self.bc]
  x=self.xbase+self.xamplitude*np.sin(w0*t+self.xphase);xd=self.xamplitude*w0*np.cos(w0*t+self.xphase)
  nw,nc,nh=g.N_cells,g.N_cp,g.N_hx
  water=x[:nw];solid=x[nw:nw+nc];sec=x[nw+nc:nw+nc+nh];pri=x[nw+nc+nh:]
  up=np.where(m>=0,self.fr,self.to);down=np.where(m>=0,self.to,self.fr);w=np.abs(m);cp=p['fluid_cp'][0];rho=p['fluid_rho'][0]
  eps=-np.expm1(-p['cp_UA']/(w[ids['cp']]*cp))
  first={int(e):int(a) for e,a in zip(ids['pipe'],self.eq._pipe_first.cpu().numpy())};last={int(e):int(a) for e,a in zip(ids['pipe'],self.eq._pipe_last.cpu().numpy())}
  cpLocal={int(e):i for i,e in enumerate(ids['cp'])};hxLocal={int(e):i for i,e in enumerate(ids['hx'])}
  N=np.zeros((g.N_nodes,g.N_nodes));rhs=np.zeros(g.N_nodes)
  incoming=np.bincount(down,weights=w,minlength=g.N_nodes)
  for node in range(g.N_nodes):
   if self.bcT[node]:N[node,node]=1;rhs[node]=p['temperature_bc'][node];continue
   if incoming[node]<=0:raise ValueError('reference contains dry mixing node')
   N[node,node]=incoming[node]
  for e in range(g.N_edges):
   node=down[e]
   if self.bcT[node]:continue
   if e in first:rhs[node]+=w[e]*water[last[e] if m[e]>=0 else first[e]]
   elif e in cpLocal:
    i=cpLocal[e];N[node,up[e]]-=w[e]*(1-eps[i]);rhs[node]+=w[e]*eps[i]*solid[i]
   elif e in hxLocal:rhs[node]+=w[e]*sec[hxLocal[e]]
   else:N[node,up[e]]-=w[e]
  node=np.linalg.solve(N,rhs)
  outlet=node[up].copy()
  for e in ids['pipe']:outlet[e]=water[last[e] if m[e]>=0 else first[e]]
  for e,i in cpLocal.items():outlet[e]=node[up[e]]+eps[i]*(solid[i]-node[up[e]])
  for e,i in hxLocal.items():outlet[e]=sec[i]
  received=np.bincount(down,weights=w*cp*outlet,minlength=g.N_nodes)
  mix=incoming*cp*node-received
  adv_cell=w[self.cell_edge]*cp
  upstream=np.where(m[self.cell_edge]>=0,np.where(self.first,node[self.fr[self.cell_edge]],water[self.prev]),np.where(self.last,node[self.to[self.cell_edge]],water[self.next]))
  qw=rho*cp*p['cell_volume']*xd[:nw]-adv_cell*(upstream-water)-p['cell_heat_W']
  qcp=w[ids['cp']]*cp*eps*(solid-node[up[ids['cp']]])
  qs=p['cp_Csolid']*xd[nw:nw+nc]-p['heat_W']+qcp
  exchange=p['hx_UA']*(sec-pri)
  qsec=rho*cp*p['secondary_volume']*xd[nw+nc:nw+nc+nh]-w[ids['hx']]*cp*(node[up[ids['hx']]]-sec)+exchange
  qpri=p['primary_rho']*p['primary_cp']*p['primary_volume']*xd[nw+nc+nh:]-p['primary_flow_kg_s']*p['primary_cp']*(p['primary_inlet_K']-pri)-exchange
  source={'momentum_Pa':self.headloss(m)-pressure[self.fr]+pressure[self.to],'water_W':qw,'solid_W':qs,'hx_secondary_W':qsec,'hx_primary_W':qpri}
  state={'node_pressure_Pa':pressure,'flow_kg_s':m,'node_temperature_K':node,'water_temperature_K':water,'solid_temperature_K':solid,'hx_secondary_K':sec,'hx_primary_K':pri}
  rate={'water_temperature_K':xd[:nw],'solid_temperature_K':xd[nw:nw+nc],'hx_secondary_K':xd[nw+nc:nw+nc+nh],'hx_primary_K':xd[nw+nc+nh:]}
  storage=(rho*cp*p['cell_volume']*rate['water_temperature_K']).sum()+(p['cp_Csolid']*rate['solid_temperature_K']).sum()+(rho*cp*p['secondary_volume']*rate['hx_secondary_K']).sum()+(p['primary_rho']*p['primary_cp']*p['primary_volume']*rate['hx_primary_K']).sum()
  physical=p['heat_W'].sum()+p['cell_heat_W'].sum();external=sum(source[k].sum() for k in ('water_W','solid_W','hx_secondary_W','hx_primary_W'))
  rejection=(p['primary_flow_kg_s']*p['primary_cp']*(pri-p['primary_inlet_K'])).sum()
  outgoing=np.bincount(up,weights=w,minlength=g.N_nodes)
  boundary=((outgoing*cp*node-received)[self.bc|self.bcT]).sum()
  audit={'all_node_mass_inf':float(np.abs(self.B@m).max()),'node_mixing_inf_W':float(np.abs(mix[~self.bcT]).max()),'flow_min_abs':float(np.abs(m).min()),'independent_energy_balance_W':float(physical+external+boundary-storage-rejection),'physical_heat_W':float(physical),'manufactured_external_W':float(external),'storage_W':float(storage),'primary_rejection_W':float(rejection),'cycle_derivative_mass_inf':float(np.abs(self.B@md).max())}
  return state,rate,source,audit
