from pathlib import Path
import sys,os,json,copy,hashlib,time
import numpy as np,torch
from scipy.sparse import coo_matrix,diags
from scipy.sparse.linalg import splu
P=Path(__file__).resolve().parents[1];R=P
import argparse
from package_paths import optional_output
ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);OUT=optional_output(ap.parse_args().out,P)
sys.path.insert(0,str(P/'code/product-source/src'));torch.set_num_threads(2);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.manufactured_benchmark import canonical_digest,ManufacturedBenchmark
raw=json.loads((next((P/'evidence/gui').glob('run-1p0-*'))/'input.liquidrun').read_text());before=canonical_digest(raw);negative=[];start=time.perf_counter()
def rejected(label,fn,expected_error):
 try:fn()
 except (ValueError,RuntimeError) as e:
  assert expected_error in str(e),f'wrong gate rejected {label}: {e}'
  negative.append({'name':label,'rejected':True,'expected_gate':expected_error,'error_type':type(e).__name__,'error':str(e)})
 else:raise AssertionError('negative gate unexpectedly accepted '+label)
wrong=copy.deepcopy(raw);wrong['manufactured_benchmark']['sources']['solid_W']['values'][0][0]+=.01
rejected('modified source value without re-signing fails admission',lambda:DAESimulation(wrong),'manufactured source SHA mismatch')
wrong=copy.deepcopy(raw);wrong['manufactured_benchmark']=ManufacturedBenchmark.model_validate(raw['manufactured_benchmark']).model_dump(mode='json');wrong['manufactured_benchmark']['initial_temperature_rate']['solid_temperature_K']['values'][0]+=.01;bench=wrong['manufactured_benchmark'];bench.pop('source_sha256');bench['source_sha256']=canonical_digest(bench)
rejected('valid signed wrong derivative fails original continuous initializer atol',lambda:DAESimulation(wrong),'manufactured initial state/derivative fails original continuous residual atol')
cp=json.loads((next((P/'evidence/gui').glob('run-1p0-*'))/'checkpoint.json').read_text());wrongcp=copy.deepcopy(cp);wrongcp['liquidrun']['manufactured_benchmark']['source_reference']+='-corrupt';rejected('checkpoint manifest tamper rejected before replay',lambda:DAESimulation.from_checkpoint(raw,wrongcp),'checkpoint config hash does not match its manifest')
rejected('different current React project/grid identity rejected by checkpoint',lambda:DAESimulation.from_checkpoint(json.loads((next((P/'evidence/gui').glob('run-2p0-*'))/'input.liquidrun').read_text()),cp),'checkpoint hash does not match requested liquidrun')
sim=DAESimulation(raw,'private-fixed-source-ad');exp=sim.experiment;pumpid=exp.pump_edge_ids[0];valveid=exp.valve_edge_ids[0];hxid=exp.hx_edge_ids[0];heat=torch.tensor(1.,dtype=torch.float64,requires_grad=True);pump=torch.tensor(1.,dtype=torch.float64,requires_grad=True);valve=torch.tensor(1.,dtype=torch.float64,requires_grad=True);inlet=torch.tensor(293.15,dtype=torch.float64,requires_grad=True);leaves=(heat,pump,valve,inlet);exp.set_inputs(heat_multiplier=heat,pump_rpm_ratios={pumpid:pump},valve_openings={valveid:valve},primary_inlet_K={hxid:inlet});sim.step();theta=exp._parameters[-1];source_dependencies={}
for name,part in exp.equation_graph.parameter_layout.items():
 if name.startswith('mms_'):
  grads=torch.autograd.grad(theta[part].sum(),leaves,retain_graph=True,allow_unused=True);source_dependencies[name]=[0. if x is None else float(x) for x in grads];assert source_dependencies[name]==[0.,0.,0.,0.]
J=exp.tensor_series('solid_temperature')[:,-1].mean();grads=torch.autograd.grad(J,leaves);ad={key:float(v) for key,v in zip(('heat_multiplier','pump_rpm_ratio','first_valve_opening','primary_inlet_K'),grads)};assert np.isfinite(list(ad.values())).all();F=[]
for h in (1e-2,1e-3,1e-4):
 values=[]
 for sign in (-1.,1.):
  f=DAESimulation(raw,'private-heat-FD');f.set_inputs(heat_multiplier=1.+sign*h);f.step();values.append(float(f.experiment.tensor_series('solid_temperature')[:,-1].mean()))
 fd=(values[1]-values[0])/(2*h);delta=abs(fd-ad['heat_multiplier']);rel=delta/max(abs(ad['heat_multiplier']),1e-30);F.append({'h':h,'J_minus_K':values[0],'J_plus_K':values[1],'FD_K_per_multiplier':fd,'AD_abs_diff':delta,'relative_diff':rel});print('FIXED_SOURCE_HEAT_FD',F[-1],flush=True)
z=np.load(P/'reference-data/lowflow-independent-design.npz');n=len(z['capacities']);D=coo_matrix((z['D_data'],(z['D_rows'],z['D_columns'])),shape=(n,n)).tocsc();q=np.zeros(n);physical=json.loads((P/'reference-data/lowflow-explicit-physical-inputs.json').read_text());q[229+892:229+892+56]=physical['compiled_coefficients_data']['heat_W'];ind=splu(D+diags(z['capacities'])).solve(q)[229+892:229+892+56].mean();assert abs(ad['heat_multiplier']-ind)<1e-8;assert F[-1]['relative_diff']<=1e-4 or F[-1]['AD_abs_diff']<=1e-8;assert canonical_digest(raw)==before
result={'scope':'new manufactured fixture fixed original Pa/W sources, one accepted t0→1s control window, all56 solid mean objective, real constructor/public research set_inputs→step→tensor_series; not an unforced physical plant test','control_base':{'heat':1.,'pump':1.,'valve':1.,'primary_inlet_K':293.15},'actuator_ids':{'pump':pumpid,'valve':valveid,'hx':hxid},'J_K':float(J.detach()),'AD':ad,'fixed_sampled_MMS_sources_derivative_wrt_all_four_controls':source_dependencies,'fixed_source_heat_FD':F,'independent_conservation_law_heat_sensitivity_K':float(ind),'heat_AD_vs_independent_abs_diff':abs(ad['heat_multiplier']-ind),'heat_FD_gate':{'finest_h':1e-4,'relative_tolerance':1e-4,'absolute_tolerance':1e-8,'logic':'relative OR absolute','pass':True},'other_control_FD_tested':False,'valve_centered_FD_limitation':'saved first opening1 is the upper boundary; no invalid opening>1 test or artificial clipping is used. Formal local/left derivative is reported; no centered FD acceptance for this new fixture is claimed.','regenerated_parameter_dependent_MMS_sources_tested':False,'regenerated_source_dependency_explanation':'Different source law S(theta) preserving the same artificial trajectory would cancel physical control sensitivity. That is a separate counterfactual source identity/tape, not present in this fixed-array GUI fixture and not used here.','newton':sim.experiment.frames[-1]['newton'],'diagnostics':sim.experiment.frames[-1]['diagnostics'],'negative_identity_initialization_gates':negative,'raw_original_input_unchanged':True,'affinity':sorted(os.sched_getaffinity(0)),'threads':2,'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES'),'elapsed_s':time.perf_counter()-start};(OUT/'fixed-source-ad-and-negative-gates.json').write_text(json.dumps(result,indent=2));print('CONTROL_RESULT',json.dumps(result),flush=True)
