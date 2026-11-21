from pathlib import Path
from types import SimpleNamespace
import json,copy,hashlib,argparse,numpy as np
import independent_equations as G
from package_paths import optional_output
P=Path(__file__).resolve().parents[1]
H=SimpleNamespace(N=G.N,NW=G.NW,NC=G.NC,NH=G.NH,fr=G.fr,to=G.to,layout=G.slices,ids={'pipe':G.groups['pipe'],'cp':G.groups['cold_plate'],'hx':G.groups['heat_exchanger'],'pump':G.groups['pump'],'valve':G.groups['valve']})
H.eq=SimpleNamespace(_characteristic=SimpleNamespace(numpy=lambda:np.zeros(len(H.ids['valve']),dtype=np.int64)))
H.cell_edges=np.zeros(G.NW,dtype=np.int64);H.first=np.zeros(G.NW,dtype=bool);H.last=np.zeros(G.NW,dtype=bool);H.previous=np.arange(G.NW);H.following=np.arange(G.NW)
for edge,cells in G.pipe_cells.items():
 for pos,c in enumerate(cells):
  H.cell_edges[c]=edge;H.first[c]=pos==0;H.last[c]=pos==len(cells)-1;H.previous[c]=cells[max(0,pos-1)];H.following[c]=cells[min(len(cells)-1,pos+1)]
g=SimpleNamespace(N_edges=len(G.edges))
p=G.p;m=G.m;b=G.b;v=G.v;a=G.a;z=G.z;up=G.up;down=G.down;w=G.w;cp=G.cp;rho=G.rho
epsilon=-np.expm1(-p['cp_UA']/(w[H.ids['cp']]*cp))
pressure0=G.h['pressure0_Pa'];pressure_a=G.h['pressure_curvature_Pa_per_s2']
def headloss(flow):
 eps=1e-8;loss=np.zeros(g.N_edges);mu=p['fluid_mu'][0];gravity=p['gravity'][0]
 k=H.ids['pipe'];f=flow[k];ab=np.sqrt(f*f+eps*eps);D=p['pipe_D'];area=p['pipe_area'];Re=ab*D/(area*mu);lam=64/(Re+eps);turb=.25/(np.log10(p['pipe_roughness']/D/3.7+5.74/(Re+eps)**.9)+1e-10)**2;blend=1/(1+np.exp(-np.clip((Re-2300)/200,-700,700)));friction=(1-blend)*lam+blend*turb;loss[k]=(friction*p['pipe_L']/D+p['pipe_minor'])/(2*rho*area*area)*f*ab+rho*gravity*p['pipe_dh']
 for kind in ('cp','hx'):
  k=H.ids[kind];f=flow[k];loss[k]=p[kind+'_dp_a']*f*np.sqrt(f*f+eps*eps)+p[kind+'_dp_b']*f
 k=H.ids['pump'];f=flow[k];loss[k]=-rho*gravity*p['pump_rpm_ratio']**2*(p['pump_H0']-p['pump_a']*(f/rho)**2)
 k=H.ids['valve'];alpha=p['valve_opening'];char=H.eq._characteristic.numpy();kv=p['valve_Kv']*np.where(char==1,50**(alpha-1),np.where(char==2,np.sqrt(alpha),alpha));f=flow[k];loss[k]=1e5*(rho/1000)*(3600/(rho*kv))**2*f*np.sqrt(f*f+eps*eps)
 return loss
loss=headloss(m);physical=float(p['heat_W'].sum()+p['cell_heat_W'].sum())

def balances(T,Tdot):
 node=T[:H.N];water=T[H.layout['water_temperature_K']];solid=T[H.layout['solid_temperature_K']];sec=T[H.layout['hx_secondary_K']];pri=T[H.layout['hx_primary_K']];upcell=np.where(m[H.cell_edges]>=0,np.where(H.first,node[H.fr[H.cell_edges]],water[H.previous]),np.where(H.last,node[H.to[H.cell_edges]],water[H.following]));qw=rho*cp*p['cell_volume']*Tdot[H.layout['water_temperature_K']]-w[H.cell_edges]*cp*(upcell-water)-p['cell_heat_W'];plate=w[H.ids['cp']]*cp*epsilon*(solid-node[up[H.ids['cp']]]);qs=p['cp_Csolid']*Tdot[H.layout['solid_temperature_K']]-p['heat_W']+plate;exchange=p['hx_UA']*(sec-pri);qsec=rho*cp*p['secondary_volume']*Tdot[H.layout['hx_secondary_K']]-w[H.ids['hx']]*cp*(node[up[H.ids['hx']]]-sec)+exchange;qpri=p['primary_rho']*p['primary_cp']*p['primary_volume']*Tdot[H.layout['hx_primary_K']]-p['primary_flow_kg_s']*p['primary_cp']*(p['primary_inlet_K']-pri)-exchange
 return {'water_W':qw,'solid_W':qs,'hx_secondary_W':qsec,'hx_primary_W':qpri}

def evaluate(t):
 T=b+v*t+a*t*t;dot=v+2*a*t;pressure=pressure0+pressure_a*t*t;source={'momentum_Pa':loss-pressure[H.fr]+pressure[H.to],**balances(T,dot)};state={'node_pressure_Pa':pressure,'flow_kg_s':m,'node_temperature_K':T[:H.N],**{k:T[s] for k,s in H.layout.items() if k!='node_temperature_K'}};rate={k:dot[s] for k,s in H.layout.items() if k!='node_temperature_K'};stored=float((z['capacities']*dot).sum());rejection=float((p['primary_flow_kg_s']*p['primary_cp']*(T[H.layout['hx_primary_K']]-p['primary_inlet_K'])).sum());energy=physical+sum(float(x.sum()) for k,x in source.items() if k!='momentum_Pa')-stored-rejection
 return state,rate,source,{'independent_continuous_energy_balance_W':energy,'storage_W':stored,'primary_rejection_W':rejection,'physical_heat_input_W':physical,'manufactured_heat_W':sum(float(x.sum()) for k,x in source.items() if k!='momentum_Pa')}


def canonical_digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def regenerate():
 rows=[]
 for tag,dt in [('2p0',2.),('1p0',1.),('0p5',.5),('0p25',.25)]:
  R=next((P/'evidence/gui').glob('run-'+tag+'-*'));raw=json.loads((R/'input.liquidrun').read_text());times=np.arange(int(8/dt)+1)*dt;values=[evaluate(float(t)) for t in times];bench=copy.deepcopy(raw['manufactured_benchmark']);expected=bench.pop('source_sha256')
  bench['times_s']=times.tolist()
  for name in bench['sources']:bench['sources'][name]['values']=np.array([x[2][name] for x in values]).T.tolist()
  for name in bench['initial_state']:bench['initial_state'][name]['values']=values[0][0][name].tolist()
  for name in bench['initial_temperature_rate']:bench['initial_temperature_rate'][name]['values']=values[0][1][name].tolist()
  bench['flow_signs']['values']=np.sign(m).tolist()
  actual=canonical_digest(bench);assert actual==expected,(tag,actual,expected)
  rows.append({'tag':tag,'dt':dt,'N_t':len(times),'expected_source_sha':expected,'regenerated_source_sha':actual,'exact_typed_source_digest':True,'offgrid_energy_inf_W':max(abs(evaluate(t)[3]['independent_continuous_energy_balance_W']) for t in [.37,1.23,2.71,4.19,7.83])})
 return rows
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--out');args=ap.parse_args();out=optional_output(args.out,P);rows=regenerate()
 if out:(out/'regenerated-forcing-receipt.json').write_text(json.dumps(rows,indent=2))
 print(json.dumps(rows,indent=2))
