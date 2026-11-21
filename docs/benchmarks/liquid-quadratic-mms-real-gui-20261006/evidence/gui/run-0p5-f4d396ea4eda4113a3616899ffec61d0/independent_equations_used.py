from pathlib import Path
import json,hashlib,os,time
import numpy as np
from scipy.sparse import coo_matrix,diags
from scipy.sparse.linalg import splu
F=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-product-private-20261006/FROZEN-v1')
O=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/quadratic-mms-root-review-private-20261006')
t0=time.perf_counter();manifest=json.loads((F/'manifest.json').read_text())
manifest_sha=hashlib.sha256((F/'manifest.json').read_bytes()).hexdigest()
assert manifest_sha=='6fb645770bbd8a2bd33a72a2c9e5a5c854922921154be8c88a593578fbdca3b3'
drift=[]
for row in manifest['payloads']:
 path=F/row['path'];b=path.read_bytes()
 if len(b)!=row['bytes'] or hashlib.sha256(b).hexdigest()!=row['sha256']:drift.append(row['path'])
assert not drift
raw=json.loads((F/'results/compiled-dt-1p0.liquidrun').read_text())
physical=json.loads((F/'inputs/lowflow-explicit-physical-inputs.json').read_text())
p={k:np.array(v,dtype=float) for k,v in physical['compiled_coefficients_data'].items()}
ids=physical['compiled_ids'];nodeid={s:i for i,s in enumerate(ids['nodes'])};edgeid={s:i for i,s in enumerate(ids['edges'])};edgeobjects={e['id']:e for e in raw['edges']};edges=[edgeobjects[s] for s in ids['edges']]
fr=np.array([nodeid[e['from']] for e in edges]);to=np.array([nodeid[e['to']] for e in edges]);groups={ty:np.array([i for i,e in enumerate(edges) if e['type']==ty]) for ty in ['pipe','cold_plate','heat_exchanger','valve','pump']}
assert [edges[i]['id'] for i in groups['cold_plate']]==ids['solid']
assert [edges[i]['id'] for i in groups['heat_exchanger']]==ids['hx']
N=229;NW=892;NC=56;NH=1;NT=N+NW+NC+2*NH;NS=NW+NC+2*NH
assert len(ids['nodes'])==N and len(edges)==286 and (N-1)+286+NT==1693 and NS==950
z=np.load(F/'inputs/lowflow-independent-design.npz')
h=np.load(F/'inputs/lowflow-hydraulic-coefficients.npz')
b=z['T_base_K'];v=z['T_slope_K_per_s'];a=z['T_curvature_K_per_s2'];m=h['fixed_flow_kg_s']
assert len(b)==NT and np.all(a[N:]>0)
up=np.where(m>=0,fr,to);down=np.where(m>=0,to,fr);w=np.abs(m);cp=float(p['fluid_cp'][0]);rho=float(p['fluid_rho'][0])
B=np.zeros((N,len(edges)))
for e in range(len(edges)):B[fr[e],e]+=1;B[to[e],e]-=1
mass_ref=float(np.max(np.abs(B@m)))
pipe_cells={}
for c,s in enumerate(ids['water']):
 eid,suffix=s.rsplit('/cell/',1);pipe_cells.setdefault(edgeid[eid],[]).append((int(suffix),c))
pipe_cells={e:[c for pos,c in sorted(rows)] for e,rows in pipe_cells.items()}
assert sum(len(x) for x in pipe_cells.values())==NW and set(pipe_cells)==set(groups['pipe'])
loccp={int(e):i for i,e in enumerate(groups['cold_plate'])};lochx={int(e):i for i,e in enumerate(groups['heat_exchanger'])}
eff=-np.expm1(-p['cp_UA']/(w[groups['cold_plate']]*cp))
row=[];col=[];vals=[]
def add(i,j,val):row.append(i);col.append(j);vals.append(float(val))
C=np.zeros(NT);C[N:N+NW]=rho*cp*p['cell_volume'];C[N+NW:N+NW+NC]=p['cp_Csolid'];C[-2:-1]=rho*cp*p['secondary_volume'];C[-1:]=p['primary_rho']*p['primary_cp']*p['primary_volume']
Q=np.zeros(NT);Q[N:N+NW]=p['cell_heat_W'];Q[N+NW:N+NW+NC]=p['heat_W'];Q[-1:]=p['primary_flow_kg_s']*p['primary_cp']*p['primary_inlet_K']
for n in range(N):add(n,n,np.sum(w[down==n])*cp)
for e in range(len(edges)):
 n=int(down[e]);q=w[e]*cp
 if e in pipe_cells:add(n,N+(pipe_cells[e][-1] if m[e]>=0 else pipe_cells[e][0]),-q)
 elif e in loccp:
  i=loccp[e];add(n,int(up[e]),-q*(1-eff[i]));add(n,N+NW+i,-q*eff[i])
 elif e in lochx:add(n,N+NW+NC+lochx[e],-q)
 else:add(n,int(up[e]),-q)
for e,cells in pipe_cells.items():
 cells=cells if m[e]>=0 else cells[::-1];previous=int(up[e])
 for c in cells:
  r=N+c;add(r,r,w[e]*cp);add(r,previous,-w[e]*cp);previous=r
for i,e in enumerate(groups['cold_plate']):
 r=N+NW+i;q=w[e]*cp*eff[i];add(r,r,q);add(r,int(up[e]),-q)
for i,e in enumerate(groups['heat_exchanger']):
 s=N+NW+NC+i;pr=s+NH;secadv=w[e]*cp;UA=p['hx_UA'][i];pradv=p['primary_flow_kg_s'][i]*p['primary_cp'][i]
 add(s,s,secadv+UA);add(s,int(up[e]),-secadv);add(s,pr,-UA);add(pr,pr,pradv+UA);add(pr,s,-UA)
D=coo_matrix((vals,(row,col)),shape=(NT,NT)).tocsc()
Dz=coo_matrix((z['D_data'],(z['D_rows'],z['D_columns'])),shape=(NT,NT)).tocsc()
delta=D-Dz
matrix_drift=0. if not delta.nnz else float(np.max(np.abs(delta.data)))
capacity_drift=float(np.max(np.abs(C-z['capacities'])))
Qdrift=float(np.max(np.abs(Q-z['physical_Q'])))
assert matrix_drift<1e-10 and capacity_drift==0 and Qdrift==0
# Independent hydraulic equations, literal from declared coefficients and raw types.
eps=1e-8;loss=np.zeros(len(edges));gravity=float(p['gravity'][0]);mu=float(p['fluid_mu'][0])
k=groups['pipe'];f=m[k];ab=np.sqrt(f*f+eps*eps);diam=p['pipe_D'];area=p['pipe_area'];Re=ab*diam/(area*mu)
lam=64/(Re+eps);turb=.25/(np.log10(p['pipe_roughness']/diam/3.7+5.74/(Re+eps)**.9)+1e-10)**2
blend=1/(1+np.exp(-np.clip((Re-2300)/200,-700,700)))
loss[k]=((1-blend)*lam+blend*turb)*p['pipe_L']/diam/(2*rho*area*area)*f*ab+p['pipe_minor']/(2*rho*area*area)*f*ab+rho*gravity*p['pipe_dh']
for ty,stem in [('cold_plate','cp'),('heat_exchanger','hx')]:
 k=groups[ty];f=m[k];loss[k]=p[stem+'_dp_a']*f*np.sqrt(f*f+eps*eps)+p[stem+'_dp_b']*f
k=groups['pump'];f=m[k];loss[k]=-rho*gravity*p['pump_rpm_ratio']**2*(p['pump_H0']-p['pump_a']*(f/rho)**2)
k=groups['valve'];alpha=p['valve_opening'];chars=[edges[int(e)].get('params',{}).get('characteristic','linear') for e in k]
assert set(chars)=={'linear'};kv=p['valve_Kv']*alpha;f=m[k]
loss[k]=1e5*(rho/1000)*(3600/(rho*kv))**2*f*np.sqrt(f*f+eps*eps)
h_loss_drift=float(np.max(np.abs(loss-h['headloss_Pa'])))
assert h_loss_drift<1e-8
slices={'node_temperature_K':slice(0,N),'water_temperature_K':slice(N,N+NW),'solid_temperature_K':slice(N+NW,N+NW+NC),'hx_secondary_K':slice(N+NW+NC,N+NW+NC+NH),'hx_primary_K':slice(-NH,None)}
actual_fields={'node_temperature_K':'node_temperature_K','water_temperature_K':'cell_temperature_K','solid_temperature_K':'solid_temperature_K','hx_secondary_K':'secondary_supply_K','hx_primary_K':'primary_return_K'}
source_slices={'water_W':slices['water_temperature_K'],'solid_W':slices['solid_temperature_K'],'hx_secondary_W':slices['hx_secondary_K'],'hx_primary_W':slices['hx_primary_K']}
