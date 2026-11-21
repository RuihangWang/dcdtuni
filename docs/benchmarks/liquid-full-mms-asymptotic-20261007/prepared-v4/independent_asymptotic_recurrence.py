from pathlib import Path
import os,sys,json,time,hashlib,traceback
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R/'after/src'))
import torch,numpy as np
from scipy.sparse import coo_matrix,diags
from scipy.sparse.linalg import splu
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
torch.set_num_threads(2);torch.set_num_interop_threads(1)
raw=json.loads((R/('results/manufactured-dt-'+sys.argv[1]+'.liquidrun')).read_text());d=admit_dae_runtime_input(raw);eq=d.equation_graph;g=eq.graph
assert g.N_nodes==229 and g.N_edges==286 and eq.size==1693
N=g.N_nodes;NW=g.N_cells;NC=g.N_cp;NH=g.N_hx;assert (NW,NC,NH)==(892,56,1)
ids={k:v.detach().numpy() for k,v in eq._ids.items()};fr=eq._from.numpy();to=eq._to.numpy();bcT=eq._bc_T_mask.numpy();cell_edges=eq._cells.numpy();first=eq._first.numpy();last=eq._last.numpy();previous=eq._previous.numpy();following=eq._next.numpy();pipefirst=eq._pipe_first.numpy();pipelast=eq._pipe_last.numpy()
layout={'node_temperature_K':slice(0,N),'water_temperature_K':slice(N,N+NW),'solid_temperature_K':slice(N+NW,N+NW+NC),'hx_secondary_K':slice(N+NW+NC,N+NW+NC+NH),'hx_primary_K':slice(N+NW+NC+NH,N+NW+NC+2*NH)}
size=N+NW+NC+2*NH;cycle=np.load(R/'results/frozen-original-cycle.npz');phase=cycle['xphase'];amplitude=cycle['xamplitude'];xbase=cycle['xbase'];omega=2*np.pi/32
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

runs=[];saved={};start=time.perf_counter()
assert len(sys.argv)==2 and sys.argv[1] in ('0p03125','0p015625')
for tag in [sys.argv[1]]:
 a=np.load(R/f'results/newton-vs-manufactured-{tag}.npz');times=a['times'];dt=float(times[1]);rraw=json.loads((R/f'results/manufactured-dt-{tag}.liquidrun').read_text());rd=admit_dae_runtime_input(rraw);benchmark=rraw['manufactured_benchmark']
 actual=np.concatenate([a[k+'_error'] for k in layout],axis=1);references=np.concatenate([a[k+'_reference'] for k in layout],axis=1)
 assert np.max(np.abs(actual[0]))==0.,'original nonsteady initial mismatch'
 errs=np.zeros((len(times),size,4));source_defects=[];continuous=[];late_sources=[];linear_res=[];temp_formula_exact=[]
 for i,t in enumerate(times):
  theta=rd.parameters(i);p={k:theta[s].detach().numpy() for k,s in eq.parameter_layout.items() if not k.startswith('mms_')};m=a['flow_kg_s_reference'][i];D,C,b=matrix(p,m)
  x=xbase+amplitude*np.sin(omega*t+phase);xd=amplitude*omega*np.cos(omega*t+phase);temp_formula_exact.append(bool(np.array_equal(x.view(np.uint64),references[i,N:].view(np.uint64))))
  source=np.zeros(size)
  for f,s in zip(['water_W','solid_W','hx_secondary_W','hx_primary_W'],forcing_slices):source[s]=np.array(benchmark['sources'][f]['values'])[:,i]
  full_xdot=np.concatenate([np.zeros(N),xd]);continuous.append(float(np.max(np.abs(D@references[i]+C*full_xdot-b-source))))
  if i:
   full_backward_rate=(references[i]-references[i-1])/dt;defect=C*(full_backward_rate-full_xdot);source_defects.append({'seq':i,'time_s':float(t),'node_inf_W':float(np.max(np.abs(defect[:N]))),'water_inf_W':float(np.max(np.abs(defect[layout['water_temperature_K']]))),'solid_inf_W':float(np.max(np.abs(defect[layout['solid_temperature_K']]))),'HXsec_W':float(defect[layout['hx_secondary_K']][0]),'HXpri_W':float(defect[layout['hx_primary_K']][0])})
   rhs=(C[:,None]/dt)*errs[i-1]
   for j,s in enumerate(forcing_slices):rhs[s,j]-=defect[s]
   K=D+diags(C/dt);lu=splu(K);errs[i]=lu.solve(rhs);linear_res.append(float(np.max(np.abs(K@errs[i]-rhs))))
 prediction=errs.sum(axis=2);dif=prediction-actual;errors={}
 for k,s in layout.items():
  final=float(np.max(np.abs(prediction[-1,s])));whole=float(np.max(np.abs(prediction[:,s])));maxdelta=float(np.max(np.abs(dif[:,s])));errors[k]={'independent_final_inf_K':final,'independent_whole_inf_K':whole,'product_prediction_diff_inf_K':maxdelta}
 sec=layout['hx_secondary_K'].start;pri=layout['hx_primary_K'].start
 item={'tag':tag,'dt':dt,'count':len(times),'initial_error_exact_zero':True,'all_storage_formula_bit_exact':all(temp_formula_exact),'continuous_original_forcing_residual_inf_W':max(continuous),'independent_error_recurrence_residual_inf_W':max(linear_res),'errors':errors,'final_HXsecondary_signed_error_K':float(prediction[-1,sec]),'product_final_HXsecondary_signed_error_K':float(actual[-1,sec]),'final_HXprimary_signed_error_K':float(prediction[-1,pri]),'HXsecondary_final_contributions_K':dict(zip(columns,errs[-1,sec].tolist())),'HXprimary_final_contributions_K':dict(zip(columns,errs[-1,pri].tolist())),'HXsecondary_cancellation_factor':float(np.sum(np.abs(errs[-1,sec]))/max(abs(prediction[-1,sec]),1e-300)),'HXsecondary_whole_max_at_s':float(times[np.argmax(np.abs(prediction[:,sec]))]),'HXsecondary_error_signs_by_time':np.sign(prediction[:,sec]).tolist(),'BE_storage_rate_defect':source_defects}
 runs.append(item);saved[tag+'_times']=times;saved[tag+'_independent_error_contributions']=errs;saved[tag+'_product_error']=actual;saved[tag+'_independent_total_error']=prediction
 (R/'recurrence-progress.json').write_text(json.dumps(runs,indent=2));print('INDEPENDENT',tag,'HXsec final',item['final_HXsecondary_signed_error_K'],'contributions',item['HXsecondary_final_contributions_K'],'maxProductDiff',max(x['product_prediction_diff_inf_K'] for x in errors.values()),flush=True)
np.savez_compressed(R/'independent-error-recurrence.npz',**saved)
orders={k:[float(np.log(runs[i]['errors'][k]['independent_final_inf_K']/runs[i+1]['errors'][k]['independent_final_inf_K'])/np.log(2)) for i in range(len(runs)-1)] for k in layout};contriborders={col:[float(np.log(abs(runs[i]['HXsecondary_final_contributions_K'][col])/abs(runs[i+1]['HXsecondary_final_contributions_K'][col]))/np.log(2)) for i in range(len(runs)-1)] for col in columns}
result={'scope':'unchanged original full229/286/1693-state manufactured graph, fixed actual pointwise hydraulic reference; independent linear thermal BE truncation-error recurrence, no new manufacturing/no Newton/no source/benchmark/gate change','formula':'(D_i+C/dt)e_i=(C/dt)e_prev-C((x*_i-x*prev)/dt-xdot*_i);four additive columns local storage families','unknowns':size,'nodes':N,'storages':NW+NC+2*NH,'elapsed_seconds':time.perf_counter()-start,'affinity':sorted(os.sched_getaffinity(0)),'threads':2,'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES'),'runs':runs,'independent_final_orders':orders,'HXsec_contribution_final_orders':contriborders,'normal_changes':0};(R/'recurrence-result.json').write_text(json.dumps(result,indent=2));print('COMPLETE',orders['hx_secondary_K'],contriborders,flush=True)
