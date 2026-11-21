"""Independent first-order thermal waveform sensitivity. No production DAE advance."""
from pathlib import Path
import json,time,hashlib
import oracle as o
import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu

def main():
 start=time.monotonic();R=o.HERE.parent/'completed';raw=json.loads((R/'executed.liquidrun').read_text());result=json.loads((R/'result.json').read_text())
 d=o.load_dae_input(raw);eq=d.equation_graph;layout=eq.state_layout;n=raw['time']['N_t'];dt=raw['time']['dt_s'];assert n==50 and dt==.5
 state0=np.zeros(eq.size);state0[layout['flow']]=np.array(result['edge_flow_kg_s'])[:,0]
 names={'water_temperature':'cell_temp_K','solid_temperature':'chip_temp_K','hx_secondary_temperature':'secondary_supply_K','primary_return_temperature':'primary_return_K'}
 for key,value in names.items():state0[layout[key]]=np.array(result[value])[:,0]
 r=o.assemble(d,state0);A=csc_matrix(r['A']);L=splu(csc_matrix(np.eye(len(r['x0'])))-dt*A);theta=[d.parameters(i).detach().numpy() for i in range(n)];q0={key:theta[0][sl] for key,sl in eq.parameter_layout.items()}
 sensitivity={k:np.zeros_like(r['x0']) for k in ['heat','inlet']};by_sample=[]
 for i in range(1,n):
  p={key:theta[i][sl] for key,sl in eq.parameter_layout.items()};b_heat=np.zeros_like(r['x0']);b_inlet=np.zeros_like(r['x0'])
  b_heat[r['ss']]=(p['heat_W']-q0['heat_W'])/r['cs']
  b_inlet[r['sp']]=r['p']['primary_flow_kg_s']*r['p']['primary_cp']/r['cpri']*(p['primary_inlet_K']-q0['primary_inlet_K'])
  for k,b in [('heat',b_heat),('inlet',b_inlet)]:sensitivity[k]=L.solve(sensitivity[k]+dt*b)
  by_sample.append({'seq':i,'time_s':i*dt,'heat_mean_solid':float(sensitivity['heat'][r['ss']].mean()),'inlet_mean_solid':float(sensitivity['inlet'][r['ss']].mean()),'inlet_mean_primary_return':float(sensitivity['inlet'][r['sp']].mean()),'inlet_mean_hx_secondary':float(sensitivity['inlet'][r['sh']].mean())})
 report={'scope':'Exact derivative of independently assembled BE thermal balances for post-t0 deviations about initial parameter value, fixed actual flows/properties. Both initial sensitivities zero, full50grid. No production DAE advance, FD, API or modified case.','input_sha256':o.sha(R/'executed.liquidrun'),'result_sha256':o.sha(R/'result.json'),'assembler_sha256':o.sha(o.I/'tools/shinaide_liquid_verification.py'),'final':by_sample[-1],'by_sample':by_sample,'passed':all(np.isfinite(v).all() for v in sensitivity.values()),'wall_seconds':time.monotonic()-start}
 (o.HERE/'sensitivity.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='by_sample'}))
if __name__=='__main__':main()
