import ast,copy,hashlib,json,subprocess,time,types,os
import pytest
from pathlib import Path
from backend.liquidcooling.runtime import LiquidSimulation
from backend.tests.test_liquid.test_runtime_performance import config

@pytest.mark.skipif(os.getenv('LIQUID_BENCHMARK_AB') != '1', reason='opt-in historical A/B benchmark')
def test_same_process_ab():
 source=subprocess.check_output(['git','show','9c93ce2f:src/backend/liquidcooling/runtime.py'],text=True)
 cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='LiquidSimulation')
 fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_step_graph')
 namespace={'copy':copy};exec(compile(ast.Module(body=[fn],type_ignores=[]),'<committed-baseline>','exec'),namespace)
 # Same process, interleaved order, identical schedules and one CPU thread.
 warm=LiquidSimulation(config(3));warm.step()
 rows=[]
 for variant in ['before','after','after','before','before','after']:
  sim=LiquidSimulation(config())
  if variant=='before':sim._step_graph=types.MethodType(namespace['_step_graph'],sim)
  trace=[];start=time.perf_counter()
  while not sim.done:
   f=sim.step();trace.append([f[k] for k in ['time_s','node_pressure_Pa','edge_flow_kg_s','node_temperature_K','chip_temp_K','edge_outlet_temp_K']])
  elapsed=time.perf_counter()-start
  rows.append({'variant':variant,'seconds':elapsed,'physics_sha256':hashlib.sha256(json.dumps(trace).encode()).hexdigest()})
 assert len({r['physics_sha256'] for r in rows})==1
 Path('/tmp/liquid-perf-ab.json').write_text(json.dumps(rows,indent=2))
 print(json.dumps(rows))
