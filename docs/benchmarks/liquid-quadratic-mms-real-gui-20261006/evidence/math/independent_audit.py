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
runs=[]
for tag in ['2p0','1p0','0p5','0p25']:
 compiled=json.loads((F/f'results/compiled-dt-{tag}.liquidrun').read_text());bench=compiled['manufactured_benchmark'];dt=compiled['time']['dt_s'];nt=compiled['time']['N_t']
 internal=json.loads((F/f'results/internal-frames-{tag}.json').read_text());public=json.loads((F/f'results/raw-public-frames-{tag}.json').read_text())
 assert len(internal)==len(public)==nt
 times=np.array([f['time_s'] for f in internal]);assert np.array_equal(times,np.arange(nt)*dt) and times[-1]==8.
 assert bench['times_s']==times.tolist()
 allT=np.concatenate([np.array([f[actual_fields[name]] for f in internal]) for name in slices],axis=1)
 ref=b[None,:]+times[:,None]*v[None,:]+times[:,None]**2*a[None,:]
 deriv=v[None,:]+2*times[:,None]*a[None,:]
 thermalsource=ref@D.T+deriv*C[None,:]-Q[None,:]
 source_diff={name:float(np.max(np.abs(np.array(bench['sources'][name]['values']).T-thermalsource[:,sl]))) for name,sl in source_slices.items()}
 pstar=h['pressure0_Pa'][None,:]+times[:,None]**2*h['pressure_curvature_Pa_per_s2'][None,:]
 mom=loss[None,:]-pstar[:,fr]+pstar[:,to]
 source_diff['momentum_Pa']=float(np.max(np.abs(np.array(bench['sources']['momentum_Pa']['values']).T-mom)))
 node_ref_res=float(np.max(np.abs(thermalsource[:,:N])))
 assert max(source_diff.values())<1e-7
 initial_state={name:float(np.max(np.abs(np.array(bench['initial_state'][name]['values'])-ref[0,sl]))) for name,sl in slices.items()}
 initial_rate={name:float(np.max(np.abs(np.array(bench['initial_temperature_rate'][name]['values'])-v[sl]))) for name,sl in slices.items() if name!='node_temperature_K'}
 initial_frame=float(np.max(np.abs(allT[0]-ref[0])))
 assert max(initial_state.values())==max(initial_rate.values())==initial_frame==0.
 lu=splu(D+diags(C/dt));e=np.zeros_like(ref)
 for i in range(1,nt):e[i]=lu.solve(C/dt*e[i-1]+C*a*dt)
 be_diff=float(np.max(np.abs(allT-ref-e)));assert be_diff<1e-9
 errors={name:float(np.max(np.abs(allT[-1,sl]-ref[-1,sl]))) for name,sl in slices.items()}
 mom_frame=np.array([f['node_pressure_Pa'] for f in internal])
 flow_frame=np.array([f['edge_flow_kg_s'] for f in internal])
 actual_mass=float(np.max(np.abs(flow_frame@B.T)))
 p_err=float(np.max(np.abs(mom_frame-pstar)));m_err=float(np.max(np.abs(flow_frame-m)))
 forcing=np.zeros_like(allT)
 for name,sl in source_slices.items():forcing[:,sl]=np.array(bench['sources'][name]['values']).T
 continuous_res=float(np.max(np.abs(D@allT[0]+C*v-Q-forcing[0])))
 BElocal=[];energy=[];reported_diff=[]
 for i in range(nt):
  td=v if i==0 else (allT[i]-allT[i-1])/dt
  residual=D@allT[i]+C*td-Q-forcing[i]
  BElocal.append(float(np.max(np.abs(residual))))
  rejection=float(np.sum(p['primary_flow_kg_s']*p['primary_cp']*(allT[i,-1:]-p['primary_inlet_K'])))
  balance=float(Q[N:-1].sum()+forcing[i,N:].sum()-np.dot(C,td)-rejection)
  energy.append(balance);reported_diff.append(abs(balance-internal[i]['diagnostics']['energy_balance_W']))
 gate=max(abs(e)/(max(1e-7,256*np.finfo(float).eps)*(1+abs(f['diagnostics']['heat_input_W']))) for e,f in zip(energy,internal))
 # Public/internal translation must preserve every state array, including renamed CP/outlet exports.
 public_equal={}
 rename={'solid_temperature_K':'chip_temp_K','edge_outlet_K':'edge_outlet_temp_K'}
 for field in ['node_pressure_Pa','edge_flow_kg_s','node_temperature_K','cell_temperature_K','solid_temperature_K','secondary_supply_K','primary_return_K','edge_outlet_K']:
  public_equal[field]=bool(np.array_equal(np.array([f[field] for f in internal]).view(np.uint64),np.array([f[rename.get(field,field)] for f in public]).view(np.uint64)))
 assert all(public_equal.values())
 sourceTyped=json.loads(json.dumps(bench));sourceTyped.pop('source_sha256')
 # typed canonical serialization is independently reproduced only after casting source values/initial arrays etc by their declared float units.
 for section in ['sources','initial_state','initial_temperature_rate']:
  for block in sourceTyped[section].values():block['values']=np.asarray(block['values'],dtype=float).tolist()
 sourceTyped['times_s']=[float(t) for t in sourceTyped['times_s']]
 sourceTyped['flow_signs']['values']=[int(sign) for sign in sourceTyped['flow_signs']['values']]
 # Record typed/source identity rather than claim unnormalized JSON integer SHA equivalence.
 runs.append({'tag':tag,'dt':dt,'frames':nt,'errors_final_K':errors,'mass_kg_s':actual_mass,'pressure_error_Pa':p_err,'flow_error_kg_s':m_err,'source_equations_diff':source_diff,'node_source_residual_W':node_ref_res,'initial_state_exact':initial_state,'initial_rate_exact':initial_rate,'continuous_initial_literal_residual':continuous_res,'product_minus_own_BE_error_K':be_diff,'literal_BE_equation_residual_W':max(BElocal),'independent_energy_balance_W':max(abs(x) for x in energy),'energy_vs_product_diag_diff_W':max(reported_diff),'original_energy_gate_max_ratio':gate,'public_eight_arrays_bitexact_internal':public_equal})
orders={name:[float(np.log(runs[i]['errors_final_K'][name]/runs[i+1]['errors_final_K'][name])/np.log(2)) for i in range(3)] for name in slices}
assert all(.85<=x<=1.15 for xs in orders.values() for x in xs)
sensitivity_rhs=np.zeros(NT);sensitivity_rhs[N+NW:N+NW+NC]=p['heat_W']
heat_sensitivity=float(splu(D+diags(C)).solve(sensitivity_rhs)[N+NW:N+NW+NC].mean())
control=json.loads((F/'results/control-ad-and-negative-gates.json').read_text())
assert abs(heat_sensitivity-control['AD']['heat_multiplier'])<1e-9
result={'scope':'independent reviewer: NumPy/SciPy equations built from actual compiled JSON topology and declared frozen coefficient arrays; no product module import, residual or Newton; no browser/API/service/Git/source changes','manifest_sha':manifest_sha,'payload_count':len(manifest['payloads']),'payload_drift':drift,'graph':{'N':N,'edges':len(edges),'state':1693,'thermal':NT,'storage':NS},'own_matrix_vs_saved_design_max':matrix_drift,'own_capacity_vs_saved_design_max':capacity_drift,'own_physical_rhs_vs_saved_design_max':Qdrift,'own_headloss_vs_saved_design_Pa':h_loss_drift,'reference_fullnode_mass_kg_s':mass_ref,'runs':runs,'EOC_final':orders,'original_gate_band':[.85,1.15],'original_grids':[2,1,.5,.25],'all_EOC_pass':True,'independent_fixed_source_heat_sensitivity':heat_sensitivity,'reported_heat_AD':control['AD']['heat_multiplier'],'review_limit':'coefficient/layout extraction still shared with product compiler by fixture design, independence concerns forcing/physics/BE equations rather than topology/parameter compiler; browser pending Root','affinity':sorted(os.sched_getaffinity(0)),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES'),'elapsed_s':time.perf_counter()-t0}
(O/'independent-review.json').write_text(json.dumps(result,indent=2,allow_nan=False))
print(json.dumps({'payloads':len(manifest['payloads']),'drift':drift,'matrix_diff':matrix_drift,'own_BE_vs_product_max_K':max(r['product_minus_own_BE_error_K'] for r in runs),'own_BE_residual_max_W':max(r['literal_BE_equation_residual_W'] for r in runs),'energy_max_W':max(r['independent_energy_balance_W'] for r in runs),'initial_literal_max':max(r['continuous_initial_literal_residual'] for r in runs),'EOCs':orders,'heatSensitivity':heat_sensitivity,'elapsed_s':result['elapsed_s']},indent=2))
