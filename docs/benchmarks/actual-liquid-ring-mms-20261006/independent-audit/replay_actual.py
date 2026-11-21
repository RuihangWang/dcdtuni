"""Replay the actual normal checkpoint with actual normal DAESimulation code."""
from pathlib import Path
import json,struct,hashlib,sys,time
import numpy as np
import torch
torch.set_num_threads(2);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import DAESimulation
T=Path(__file__).resolve().parent
raw=json.loads((T/'actual/input.liquidrun').read_text());cp=json.loads((T/'actual/checkpoint.json').read_text());actual=json.loads((T/'actual/result.json').read_text())
start=time.time();print('ACTUAL NORMAL REPLAY START',flush=True)
original_step=DAESimulation.step
def traced_step(self):
    frame=original_step(self)
    print('NORMAL REPLAY FRAME',frame['seq'],'elapsed',time.time()-start,flush=True)
    return frame
DAESimulation.step=traced_step
try:
    sim=DAESimulation.from_checkpoint(raw,cp)
finally:
    DAESimulation.step=original_step
replayed=sim.result()
def same(a,b,path=''):
 if isinstance(a,float) or isinstance(b,float):
  return isinstance(a,(int,float)) and isinstance(b,(int,float)) and struct.pack('>d',float(a))==struct.pack('>d',float(b))
 if type(a)!=type(b):return False
 if isinstance(a,dict):return set(a)==set(b) and all(same(a[k],b[k],path+'/'+k)for k in a)
 if isinstance(a,list):return len(a)==len(b) and all(same(x,y,path)for x,y in zip(a,b))
 return a==b
comparisons={k:same(actual[k],v)for k,v in replayed.items()}
assert len(sim.frames)==17 and sim.done
assert all(comparisons.values()),[k for k,v in comparisons.items()if not v]
keys=['node_pressure_Pa','node_temperature_K','edge_flow_kg_s','chip_temp_K','edge_outlet_temp_K','primary_return_K','secondary_supply_K','cell_temp_K']
bit_numeric={k:np.array_equal(np.asarray(actual[k],np.float64).view(np.uint64),np.asarray(replayed[k],np.float64).view(np.uint64))for k in keys}
assert all(bit_numeric.values())
assert same(sim.checkpoint()['liquidrun'],raw)
(T/'outputs/replay-frames.json').write_text(json.dumps(sim.frames,indent=2,allow_nan=False)+'\n')
(T/'outputs/replay-result.json').write_text(json.dumps(replayed,indent=2,allow_nan=False)+'\n')
report={'normal_DAESimulation_from_checkpoint':True,'frames':17,'done':sim.done,'original_config_hash':sim.config_hash,'result_common_fields_exact':comparisons,'raw_float64_uint64_bit_equal':bit_numeric,'original_checkpoint_input_exact':True,'original_manager_only_fields':sorted(set(actual)-set(replayed)),'elapsed_seconds':time.time()-start,'torch_threads':torch.get_num_threads(),'torch_interop_threads':torch.get_num_interop_threads(),'note':'exact persisted frame state/control/diagnostic products; no serialized autograd tape claim'}
(T/'outputs/replay-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2),flush=True)
