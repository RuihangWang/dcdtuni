import hashlib,json,math,struct,re
from pathlib import Path
T=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration")
O=T/".codex-tmp/domain-box-cdu-browser-products-20261006"
C=T/".codex-tmp/cases/DomainAsset_Compat_Browser_CUA"
L=C/"Liquid/log"
olds=list(L.glob("run-846*"))
assert len(olds)==1 and olds[0].is_dir()
old=olds[0];new=L/"run-d64757e3ed55493d9f283fb888018d5e"
inputs=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def dump(name,d):
 p=O/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
def capture(src,rel):
 raw=src.read_bytes();dst=O/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
 assert dst.read_bytes()==raw and src.read_bytes()==raw
 inputs.append({"source":str(src),"copy":rel,"bytes":len(raw),"sha256":sha(raw)})
 return raw
def compare(x,y,path=""):
 if type(x)!=type(y):return [{"path":path,"oldType":type(x).__name__,"newType":type(y).__name__}]
 ds=[]
 if isinstance(x,dict):
  for k in sorted(x.keys()|y.keys()):
   if k not in x or k not in y:ds.append({"path":path+"/"+k,"missing":"old" if k not in x else "new"})
   else:ds.extend(compare(x[k],y[k],path+"/"+k))
 elif isinstance(x,list):
  if len(x)!=len(y):ds.append({"path":path,"oldLength":len(x),"newLength":len(y)})
  for i,(a,b) in enumerate(zip(x,y)):ds.extend(compare(a,b,path+"/"+str(i)))
 elif isinstance(x,float):
  if struct.pack(">d",x)!=struct.pack(">d",y):ds.append({"path":path,"old":x,"new":y,"binary64BitsDiffer":True})
 elif x!=y:ds.append({"path":path,"old":x,"new":y})
 return ds
counts={"float":0,"int":0,"bool":0,"floatNonFinite":0}
def census(x):
 if isinstance(x,dict):
  for v in x.values():census(v)
 elif isinstance(x,list):
  for v in x:census(v)
 elif type(x)==float:
  counts["float"]+=1;counts["floatNonFinite"]+=int(not math.isfinite(x))
 elif type(x)==int:counts["int"]+=1
 elif type(x)==bool:counts["bool"]+=1
files=["input.liquidrun","input.json","executed.liquidrun","executed-inputs.json","checkpoint.json","result.json"]
comparison=[]
for fn in files:
 a=capture(old/fn,"raw/pose/"+fn);b=capture(new/fn,"raw/box-cdu/"+fn)
 x=json.loads(a);y=json.loads(b);d=compare(x,y)
 assert (not d if fn in ["input.liquidrun","executed.liquidrun"] else len(d)==1 and d[0]["path"]=="/run_id")
 comparison.append({"file":fn,"oldSha256":sha(a),"newSha256":sha(b),"byteExact":a==b,"typedAndBinary64Differences":d})
 census(y)
result=json.loads((O/"raw/box-cdu/result.json").read_bytes())
checkpoint=json.loads((O/"raw/box-cdu/checkpoint.json").read_bytes())
accepted=json.loads((O/"raw/box-cdu/executed-inputs.json").read_bytes())
liquidrun=json.loads((O/"raw/box-cdu/input.liquidrun").read_bytes())
inp=json.loads((O/"raw/box-cdu/input.json").read_bytes())
assert result["status"]=="completed" and result["completed"] and result["converged"] and result["error"] is None
assert checkpoint["seq"]==49 and checkpoint["run_status"]=="completed"
assert result["time_s"]==[i*.5 for i in range(50)]
assert accepted["accepted_sample_count"]==50 and accepted["original_sample_count"]==50 and accepted["complete_original_horizon"] is True
assert counts["floatNonFinite"]==0
projects={}
for fn in ["DomainAsset_Compat_Synthetic.cfdproj","DomainAsset_PosePorts_After.cfdproj","DomainAsset_BoxCdu_After.cfdproj"]:
 projects[fn]=capture(C/fn,"projects/"+fn)
pose=projects["DomainAsset_PosePorts_After.cfdproj"];box=projects["DomainAsset_BoxCdu_After.cfdproj"]
assert pose==box
syn=json.loads(projects["DomainAsset_Compat_Synthetic.cfdproj"]);saved=json.loads(box)
project_diffs=compare(syn,saved)
assert len(project_diffs)==1 and project_diffs[0]["path"]=="/meta/casePath"
assert saved["schemaVersion"]==syn["schemaVersion"]==5
extensions={k:{"present":k in saved,"sameTypedValue":not compare(syn[k],saved[k]),"value":saved[k]} for k in ["domainAssetExtensions","_customEngineeringNote"]}
assert all(x["present"] and x["sameTypedValue"] for x in extensions.values())
png=capture(C/"screenshots/ui_screenshot_20261005_220041.png","canvas/ui_screenshot_20261005_220041.png")
assert png[:8]==b"\x89PNG\r\n\x1a\n" and png[12:16]==b"IHDR"
width,height=struct.unpack(">II",png[16:24])
idx=T/"src/frontend/react/dist/index.html";idxraw=idx.read_bytes();entry=[]
for rel in re.findall(r'(?:src|href)="([^"]+\.js)"',idxraw.decode()):
 path=T/"src/frontend/react/dist"/rel.lstrip("/")
 if path.is_file():
  raw=path.read_bytes();entry.append({"relativePath":rel,"bytes":len(raw),"sha256":sha(raw)})
sources=json.loads((T/".codex-tmp/domain-box-cdu-hole-orientation-v2-20261006/source-final.json").read_bytes())
dump("source-binding.json",{"frozenBoxV2SourceIdentityPath":str(T/".codex-tmp/domain-box-cdu-hole-orientation-v2-20261006/source-final.json"),"frozenBoxV2SourceIdentitySha256":sha((T/".codex-tmp/domain-box-cdu-hole-orientation-v2-20261006/source-final.json").read_bytes()),"identity":sources,"currentDistIndex":{"path":str(idx),"bytes":len(idxraw),"sha256":sha(idxraw)},"currentEntry":entry,"rootObservedServedBundle":"a1vBEN6m","servedBundleObservationIsRootCUA":True})
receipt={"format":"domain-box-cdu-browser-products-independent/v1","oldRun":str(old),"newRun":str(new),"method":"Read-only file copies; recursive JSON type equality and IEEE754 binary64 packing compare, including signed zero. Only explicitly observed top-level run_id is different. No compiler/solver/API/browser/tests invoked.","runFiles":comparison,"accepted":accepted,"result":{"runId":result["run_id"],"configHash":result["config_hash"],"status":result["status"],"completed":result["completed"],"converged":result["converged"],"expectedDurationS":result["expected_duration_s"],"samples":len(result["time_s"]),"finalTimeS":result["time_s"][-1],"pressureShape":[len(result["node_pressure_Pa"]),len(result["node_pressure_Pa"][0])],"temperatureShape":[len(result["node_temperature_K"]),len(result["node_temperature_K"][0])],"edgeFlowShape":[len(result["edge_flow_kg_s"]),len(result["edge_flow_kg_s"][0])],"chipShape":[len(result["chip_temp_K"]),len(result["chip_temp_K"][0])],"cellShape":[len(result["cell_temp_K"]),len(result["cell_temp_K"][0])],"appliedInputCommands":len(result["applied_inputs"]),"finalLoss":result["final_loss"],"error":result["error"]},"newProductsNumericCensus":counts,"execution":liquidrun["execution"],"analysis":liquidrun["analysis"],"time":liquidrun["time"],"sourceProjectDeclaration":liquidrun["source_project"],"sourceProjectHashScope":"Producer content hash, not a disk-file byte hash. This audit verifies declarations and complete input bytes equal the pose baseline; it does not independently recompute the producer content hash.","project":{"poseSavedSha256":sha(pose),"boxSavedSha256":sha(box),"byteExactToPoseSave":pose==box,"schemaVersion":saved["schemaVersion"],"originalSyntheticDifferences":project_diffs,"extensions":extensions,"entireConstructionAndPhysicsRecordPreserved":not compare(syn["constructions"],saved["constructions"]) and not compare(syn["runDefaults"],saved["runDefaults"]) and not compare(syn["transient"],saved["transient"])},"canvas":{"source":str(C/"screenshots/ui_screenshot_20261005_220041.png"),"bytes":len(png),"sha256":sha(png),"width":width,"height":height,"scope":"Product WebGL canvas PNG only. No React controls, toolbar, logs, fullpage or OV-video proof."},"rootCUAObservations":{"heatServers":56,"heatSamples":2800,"heatMaxReportedW":3.38e-8,"consoleErrors":0,"scope":"Reported first-hand by ROOT; not independently reconstructed from a raw heat-audit endpoint payload in these six run files."},"limitations":["Synthetic authored fullgraph input; not calibrated operating data.","Equality to the prior pose run proves this BOX/CDU change did not alter this accepted graph/runtime. It does not validate every geometry kind, all material/PBR variants, air mesh acceptance or RAM runner runtime.","Saved project identity and input source_project content-hash declaration have different hash scopes."]}
dump("input-identities.json",inputs)
dump("comparison.json",receipt)
readme="""# Actual BOX/CDU browser product comparison

The actual normal React run d64757e3ed55493d9f283fb888018d5e completed 50 CPU float64 samples, dt 0.5 s, through 24.5 s. The uniquely matched pose baseline is run-846357c1e0584393b700b8a07cf49fac.

Both input.liquidrun and executed.liquidrun are byte identical. input.json, executed-inputs.json, checkpoint.json and result.json differ only in top-level run_id. The complete comparison checks JSON scalar types and binary64 floating bits, including signed zero. All recorded new numeric float values are finite. The accepted ledger covers 50/50 samples; there are no online command records in this run's result.

Normal SaveAs DomainAsset_BoxCdu_After.cfdproj is byte identical to DomainAsset_PosePorts_After.cfdproj (3352988d0734d540fcb0ff36bd875d6f92a2f9c2c77c081b8fc9abd853211726). Against the original synthetic compatibility input, only meta.casePath differs. Schema v5, unknown top-level domainAssetExtensions and _customEngineeringNote, constructions, physics settings and transient tracks remain unchanged.

The copied PNG is the product WebGL canvas output, 1508 x 995 pixels. It cannot prove React controls, toolbar or console state. ROOT's separate CUA observations report 56 servers / 2800 heat samples, max 3.38e-8 W and no errors; this packet does not promote that observation into an independently fetched heat-audit payload.

Raw copies are SHA-bound in input-identities.json and manifest.json; source-binding.json records the existing frozen BOX v2 source identity and current local dist identity. No producer fixture was recaptured, no tests or simulations were run, and no case, source, service or Git state was changed. This is one synthetic fullgraph runtime parity result, not a full OOP/renderer/air-mesh acceptance claim.
"""
(O/"README.md").write_text(readme)
dump("command.json",{"cwd":str(T),"cpuAffinity":"36-39","CUDA_VISIBLE_DEVICES":"","python":"python audit.py","operations":["read files","copy exact bytes to owned audit packet","typed binary64 comparison","hash manifest"],"noBusinessExecution":True})
log={"success":True,"runFiles":len(comparison),"oldRunId":old.name,"newRunId":new.name,"differencesByFile":{r["file"]:[d["path"] for d in r["typedAndBinary64Differences"]] for r in comparison},"savedProjectByteExact":True,"canvasPixels":[width,height],"configHash":result["config_hash"],"samples":50,"numericFloatCount":counts["float"]}
dump("audit-output.json",log)
rows=[]
for p in sorted(O.rglob("*")):
 if p.is_file() and p.name!="manifest.json":
  raw=p.read_bytes();rows.append({"path":str(p.relative_to(O)),"bytes":len(raw),"sha256":sha(raw)})
dump("manifest.json",{"format":"sha256-payload-manifest/v1","files":rows,"payloadCount":len(rows),"payloadBytes":sum(x["bytes"] for x in rows),"scope":"All declared payloads; manifest excluded from its own hash. Source and original case files are not modified."})
print(json.dumps({"directory":str(O),"payloadCount":len(rows),"payloadBytes":sum(x["bytes"] for x in rows),"manifestSha256":sha((O/"manifest.json").read_bytes()),"comparisonSha256":sha((O/"comparison.json").read_bytes()),"resultSha256":sha((O/"raw/box-cdu/result.json").read_bytes())}))
