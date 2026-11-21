from pathlib import Path
import os,json,time
import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu
import operator_helpers as H
R=H.R;t0=time.perf_counter();th=H.d.parameters(0);p={k:th[s].detach().numpy() for k,s in H.eq.parameter_layout.items() if not k.startswith('mms_')}
cy=H.cycle;m=cy['C']@(cy['a0']+cy['amplitude']*np.sin(cy['phase']));D,C,Q=H.matrix(p,m)
old=np.load(R/'evidence/results/newton-vs-manufactured-1p0.npz'); assert np.array_equal(m.view(np.uint64),old['flow_kg_s_reference'][0].view(np.uint64))
ns=H.size-H.N;k=np.arange(ns);b=cy['xbase']+cy['xamplitude']*np.sin(cy['xphase']);v=.012+.006*np.sin(k*.073)**2;a=.005+.003*np.sin(k*.137)**2
NN=D[:H.N,:H.N].toarray();NS=D[:H.N,H.N:].toarray();bfull=np.r_[np.linalg.solve(NN,Q[:H.N]-NS@b),b];vfull=np.r_[np.linalg.solve(NN,-NS@v),v];afull=np.r_[np.linalg.solve(NN,-NS@a),a]
assert np.min(a)>0 and np.min(bfull)>0
runs=[];save={'m_fixed_kg_s':m,'physical_Q':Q,'capacities':C,'T_base_K':bfull,'T_slope_K_per_s':vfull,'T_curvature_K_per_s2':afull,'D_rows':D.tocoo().row,'D_columns':D.tocoo().col,'D_data':D.tocoo().data}
for dt in (2.,1.,.5,.25):
 times=np.arange(int(8/dt)+1)*dt;err=np.zeros((len(times),H.size));lu=splu(D+diags(C/dt));res=[];defdiff=[];nodezero=[];minerr=[]
 for i,t in enumerate(times):
  ref=bfull+vfull*t+afull*t*t;der=vfull+2*afull*t;src=D@ref+C*der-Q;nodezero.append(float(np.max(np.abs(src[:H.N]))))
  if i:
   prev=bfull+vfull*times[i-1]+afull*times[i-1]**2;defect=C*((ref-prev)/dt-der);defdiff.append(float(np.max(np.abs(defect+C*afull*dt))))
   rhs=C/dt*err[i-1]-defect;err[i]=lu.solve(rhs);res.append(float(np.max(np.abs((D+diags(C/dt))@err[i]-rhs))));minerr.append(float(np.min(err[i])))
 errors={name:{'final_inf_K':float(np.abs(err[-1,s]).max()),'whole_inf_K':float(np.abs(err[:,s]).max()),'final_min_K':float(err[-1,s].min())} for name,s in H.layout.items()};item={'dt_s':dt,'N_t':len(times),'final_time_s':8.,'errors':errors,'minimum_noninitial_temperature_error_K':min(minerr),'independent_BE_error_solve_residual_inf_W':max(res),'quadratic_BE_local_defect_identity_inf_W':max(defdiff),'node_manufactured_source_inf_W':max(nodezero),'reference_min_max_K':[float(np.min(bfull)),float(np.max(bfull+8*vfull+64*afull))]};runs.append(item);save[str(dt)+'_times']=times;save[str(dt)+'_independent_BE_error']=err
orders={name:[float(np.log(runs[i]['errors'][name]['final_inf_K']/runs[i+1]['errors'][name]['final_inf_K'])/np.log(2)) for i in range(3)] for name in H.layout};gates={name:all(.85<=x<=1.15 for x in rates) for name,rates in orders.items()};result={'scope':'new PRIVATE mathematical fixture design with same complete graph/physical pack, not old fixture relabel','graph':{'nodes':H.N,'edges':H.g.N_edges,'full_state':H.eq.size,'thermal_unknowns':H.size,'storage':ns},'temperature_formula':'T_k(t)=b_k+v_k*t+a_k*t^2, a_k>0; node coefficients exact ideal-mixing solves','curvature_range_K_s2':[float(a.min()),float(a.max())],'coarse_original_grids_only':[2.,1.,.5,.25],'runs':runs,'final_EOC':orders,'unchanged_band':[.85,1.15],'independent_design_band_by_temperature_family':gates,'all_node_mass_inf_kg_s':float(np.abs(cy['B']@m).max()),'flow_min_abs_kg_s':float(np.abs(m).min()),'physical_heat_input_W':float(p['heat_W'].sum()+p['cell_heat_W'].sum()),'affinity':sorted(os.sched_getaffinity(0)),'threads':2,'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES'),'elapsed_s':time.perf_counter()-t0,'product_F_used':False,'product_Newton_used':False,'production_changes':0,'old_failed_case_reclassified':False}
np.savez_compressed(R/'quadratic-independent-design.npz',**save);(R/'quadratic-design-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
