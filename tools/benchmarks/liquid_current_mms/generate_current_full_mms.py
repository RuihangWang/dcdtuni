from pathlib import Path
import sys,os,json,hashlib,time,copy,traceback
Q=Path(os.environ["DCTWIN_MMS_WORK"])
sys.path.insert(0,str(Path(os.environ["DCTWIN_MMS_REPO"])/"src"));sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np,torch
START=time.monotonic();torch.set_num_threads(1);torch.set_num_interop_threads(1)
from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
from backend.liquidcooling.manufactured_benchmark import canonical_digest,graph_layout,compiled_ids,MAX_BENCHMARK_BYTES,MAX_BENCHMARK_SCALARS
from backend.liquidcooling.runtime import MAX_SAMPLES
from independent_trajectory import IndependentTrajectory
from backend.liquidcooling.solver import dae_newton
# Guard this preparation against accidentally invoking a product numerical solve.
def forbidden(*args,**kwargs):raise RuntimeError("MMS INPUT PREPARATION cannot call a product Newton/root/residual")
dae_newton._newton=forbidden;dae_newton.solve_steady=forbidden;dae_newton.backward_euler_step=forbidden;dae_newton.consistent_initialize=forbidden
reg=json.loads((Q/"PREREGISTRATION.json").read_text());raw=json.loads((Q/"compiled/Baseline.liquidrun").read_text());executed=json.loads((Q/"inputs/GUI-executed.liquidrun").read_text())
assert raw["source_project"]["sha256"]==reg["expectedBaselineCanonicalSHA256"]
# Only temporary execution resources change for graph admission, never coefficients.
for value in [raw,executed]:value["execution"]={**value["execution"],"device":"cpu","max_threads":1}
definition=admit_dae_runtime_input(raw);actual=admit_dae_runtime_input(executed)
a=definition.manifest.model_dump(by_alias=True,mode="json",exclude_none=True);b=actual.manifest.model_dump(by_alias=True,mode="json",exclude_none=True)
from backend.liquidcooling.liquidrun_schema import Rack
from backend.liquidcooling.liquidrun_dae_schema import DAEParameterPack
# This is source-defined representational normalization, not ignoring inputs:
# v4 accepts constant scalar or a complete [N_t] schedule with identical values.
def normalize_manifest(value):
 value=copy.deepcopy(value);value.pop("source",None)
 value["racks"]=[Rack.model_validate(v).model_dump(by_alias=True,mode="json",exclude_none=True) for v in value["racks"]]
 value["parameter_pack"]=DAEParameterPack.model_validate(value["parameter_pack"]).model_dump(by_alias=True,mode="json",exclude_none=True)
 count=value["time"]["N_t"]
 def series(v):return v if isinstance(v,list) else [v]*count
 for node in value["nodes"]:
  if node.get("bc"):
   for field in ["P_Pa","T_K"]:
    if field in node["bc"]:node["bc"][field]=series(node["bc"][field])
 for edge in value["edges"]:
  names={"pump":["rpm_ratio","flow_set_kg_s"],"cold_plate":["Q_W","dT_set_K"],"valve":["opening"],"heat_exchanger":["T_set_K"]}.get(edge["type"],[])
  for field in names:
   if field in edge["params"]:edge["params"][field]=series(edge["params"][field])
 for circuit in value["primary_circuits"]:
  for field in ["flow_kg_s","inlet_K"]:circuit[field]=series(circuit[field])
 return value
assert normalize_manifest(a)==normalize_manifest(b),"Current schema-normalized whole manifest differs beyond exact source-defined serialization forms"
left=definition.equation_graph;right=actual.equation_graph
assert left.state_layout==right.state_layout and left.parameter_layout==right.parameter_layout
assert list(left.graph.node_ids)==list(right.graph.node_ids) and list(left.graph.edge_str_ids)==list(right.graph.edge_str_ids)
static=["_from","_to","_cells","_first","_last","_free","_bc_mask","_bc_T_mask","_closed_edges","_dead_edges","_flow_edges","_hx_setpoint","_cp_rise","differential_mask","state_scale","residual_scale"]
for name in static:assert torch.equal(getattr(left,name),getattr(right,name)),"Compiled graph/state/scaling identity: "+name
for i in range(11):assert torch.equal(definition.parameters(i),actual.parameters(i)),"Complete5086-theta identity sample "+str(i)
(Q/"BASELINE-EXECUTED-IDENTITY.json").write_text(json.dumps({"equalAfterSourceDefinedSchemaNormalization":True,"onlyExcludedSourceLabel":"source","schemaDefinedForms":["constant scalar vs repeated fullNt sample","Rack Server slot_u0/span_u1 compatibility defaults","ParameterProfile description empty-string default"],"full5086ParametersAll11SamplesBitExact":True,"compiledStaticMasksTopologyAndScalesBitExact":static,"executionCPU1TemporaryOnly":True,"fullTopologyParametersSchedulesAndInitialConditionsHeld":True,"baselineCanonicalSHA256":raw["source_project"]["sha256"],"GUIExecutedRawSHA256":reg["physicalBasisExecutedInput"]["sha256"]},indent=2)+"\n")
eq=definition.equation_graph;eq.residual=forbidden;eq.problem.residual=forbidden
assert (eq.size,eq.graph.N_nodes,eq.graph.N_edges,eq.graph.N_cells,eq.graph.N_cp,eq.graph.N_hx)==(1693,229,286,892,56,1)
assert raw["fluid"]==reg["preservedPhysicalFluid"]
ref=IndependentTrajectory(eq,definition.parameters(0));ids=compiled_ids(eq)
state,rate,sources,audit=ref.evaluate(0.)
np.savez(Q/"reference/current-independent-cycle-coefficients.npz",B=ref.B,C=ref.C,a0=ref.a0,amplitude=ref.amplitude,phase=ref.phase,xbase=ref.xbase,xamplitude=ref.xamplitude,xphase=ref.xphase)
np.savez(Q/"reference/current-complete-physical-theta.npz",theta=definition.parameters(0).detach().numpy())
(Q/"reference/current-parameter-layout.json").write_text(json.dumps({k:[v.start,v.stop] for k,v in eq.parameter_layout.items()},indent=2)+"\n")
imap={"node_pressure_Pa":"nodes","flow_kg_s":"edges","node_temperature_K":"nodes","water_temperature_K":"water","solid_temperature_K":"solid","hx_secondary_K":"hx","hx_primary_K":"hx"};smap={"momentum_Pa":"edges","water_W":"water","solid_W":"solid","hx_secondary_W":"hx","hx_primary_W":"hx"}
probe=[]
for t in [0.,.37,1.23,2.71,4.19,7.83,8.]:
 st,rt,so,au=ref.evaluate(t);assert au["all_node_mass_inf"]<1e-12 and au["node_mixing_inf_W"]<1e-8
 probe.append({"time_s":t,"independentFormulaAudit":au})
(Q/"reference/independent-formula-probes.json").write_text(json.dumps(probe,indent=2)+"\n")
base=json.loads((Q/"inputs/Baseline.cfdproj").read_text());rows=[]
for grid in reg["grids"]:
 tag=grid["tag"];dt=grid["dt_s"];times=[i*dt for i in range(grid["N_t"])];assert times[-1]==8.
 targets=[ref.evaluate(t) for t in times]
 benchmark={"format":"dcdtuni-manufactured-benchmark","version":1,"provenance":"synthetic-research","formula_id":"cycle-storage-sinusoidal-current-glycol25-20261012-v1","source_reference":"Current Baseline2e7f5106/executed325783e4 Glycol25; independent NumPy cycle/storage laws with fixed20261006 seed; synthetic, no product F or ordinary output used.","graph_layout_sha256":canonical_digest(graph_layout(raw)),"units":{"pressure":"Pa","temperature":"K","mass_flow":"kg/s","power":"W","time":"s","temperature_rate":"K/s"},"sampling":"endpoint_hold","times_s":times,"sources":{name:{"ids":ids[key],"values":np.stack([v[2][name] for v in targets],axis=1).tolist()} for name,key in smap.items()},"initial_state":{name:{"ids":ids[imap[name]],"values":value.tolist()} for name,value in state.items()},"initial_temperature_rate":{name:{"ids":ids[imap[name]],"values":value.tolist()} for name,value in rate.items()},"flow_signs":{"ids":ids["edges"],"values":np.sign(state["flow_kg_s"]).tolist()}}
 benchmark["source_sha256"]=canonical_digest(benchmark)
 scalarCount=sum(len(v["ids"])*len(times) for v in benchmark["sources"].values());compactBytes=len(json.dumps(benchmark,separators=(",",":"),allow_nan=False).encode())
 assert len(times)<=MAX_SAMPLES and scalarCount<=MAX_BENCHMARK_SCALARS and compactBytes<=MAX_BENCHMARK_BYTES
 p=copy.deepcopy(base);ex=p["runDefaults"]["liquid"]["experiment"]
 ex["dt_s"]=dt;ex["duration_s"]=8.;ex["dae"]["analysis"]={"mode":"transient","initialization":"consistent"};ex["dae"]["manufactured_benchmark"]=benchmark
 ex["dae"]["execution"]={**ex["dae"].get("execution",{}),"backend":"pytorch","device":"cpu","dtype":"float64","max_threads":2}
 p["transient"]["gridS"]=dt;p["transient"]["durationS"]=8.
 p["runDefaults"]["liquid"]["inheritResults"]=False
 p["name"]=base["name"]+"_Glycol25_MMS_"+tag;p["meta"]["projectName"]="Shinaide_Current_Glycol25_FullMMS_"+tag
 case=Q/("case-"+tag);case.mkdir();p["meta"]["casePath"]=str(case)
 p["meta"]["description"]="Full current-Glycol25 numerical MMS verification; same real ring/rear graph, synthetic forcing and explicit initial state, no vendor calibration or ordinary result inheritance."
 p["meta"].setdefault("provenance",{})["current_full_mms"]={"classification":"synthetic derived numerical verification input; not measured/vendor/calibrated data","baseline_raw_sha256":reg["physicalBasisProject"]["sha256"],"baseline_canonical_sha256":reg["expectedBaselineCanonicalSHA256"],"executed_ordinary_input_sha256":reg["physicalBasisExecutedInput"]["sha256"],"formula_id":benchmark["formula_id"],"source_sha256":benchmark["source_sha256"],"graph_layout_sha256":benchmark["graph_layout_sha256"],"original_geometry_topology_equipment_and_fluid_held":True,"ordinary_initial_conditions_preserved_as_metadata":True,"manufactured_initial_state_is_explicit":True,"original_mock_identity_preserved":True,"ordinary20sAD_scope_separate":True}
 name="Shinaide_Current_Glycol25_FullMMS_"+tag+".cfdproj";path=Q/"inputs"/name;path.write_text(json.dumps(p,indent=2,allow_nan=False)+"\n")
 np.savez_compressed(Q/"reference"/("target-"+tag+".npz"),times=times,**{k:np.stack([v[0][k] for v in targets]) for k in state})
 # Pure Python manifest admission checks source/layout/ID/form regime; no initial F/solve.
 planned=copy.deepcopy(raw);planned["time"]={**planned["time"],"dt_s":dt,"N_t":len(times)};planned["analysis"]={"mode":"transient","initialization":"consistent"};planned["manufactured_benchmark"]=benchmark
 planned["execution"]={**planned["execution"],"device":"cpu","max_threads":2}
 for key in ["nodes","edges"]:
  # The actual input is constant; expand only its held sample arrays to the fine grid.
  def expand(v):
   if isinstance(v,dict):return {k:expand(u) for k,u in v.items()}
   if isinstance(v,list):
    if len(v)==11 and v and all(not isinstance(u,(dict,list)) for u in v):
     assert all(u==v[0] for u in v),"Nonconstant physical input schedule needs exact resampling";return [v[0]]*len(times)
    return [expand(u) for u in v]
   return v
  planned[key]=expand(planned[key])
 for primary in planned["primary_circuits"]:
  for key in ["inlet_K","flow_kg_s"]:
   value=primary[key]
   if isinstance(value,list):assert all(v==value[0] for v in value);primary[key]=[value[0]]*len(times)
 planned["transient"]["durationS"]=8.
 pd=admit_dae_runtime_input(planned);assert pd.equation_graph.size==1693
 path.chmod(0o400)
 row={"tag":tag,"projectPath":str(path),"projectRawSHA256":hashlib.sha256(path.read_bytes()).hexdigest(),"sourceSHA256":benchmark["source_sha256"],"graphLayoutSHA256":benchmark["graph_layout_sha256"],"sourceScalars":scalarCount,"benchmarkCompactBytes":compactBytes,"time":grid,"pythonAdmissionOnly":True,"productSolverOrResidualCalled":False,"physicalFluid":raw["fluid"]}
 rows.append(row);print("GENERATED",json.dumps(row),flush=True)
(Q/"GENERATION-RECEIPT.json").write_text(json.dumps({"status":"derived_inputs_generated_no_numeric_acceptance","counts":ref.counts,"states":eq.size,"physicalFluid":raw["fluid"],"allNodeMassAndMixingIndependentFormulaProbes":probe,"exactIntegerBCZero":bool(np.all(ref.B@ref.C==0)),"rows":rows,"affinity":sorted(os.sched_getaffinity(0)),"elapsed_s":time.monotonic()-START,"productDAEResidualCalled":False,"productNewtonCalled":False,"ordinarySolvedOutputUsedToManufacture":False},indent=2)+"\n")
print("GENERATION_COMPLETE no product solve/residual; source/graph binding+all1693 reference prepared",flush=True)
