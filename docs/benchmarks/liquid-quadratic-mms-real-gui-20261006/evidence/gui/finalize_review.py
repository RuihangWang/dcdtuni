from pathlib import Path
import json,hashlib,shutil,os,time
import numpy as np
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
O=B/'.codex-tmp/quadratic-gui-artifact-review-private-20261006'
F0=B/'.codex-tmp/full-mms-quadratic-product-private-20261006/FROZEN-v1'
tags=['2p0','1p0','0p5','0p25'];roots={tag:next(O.glob('run-'+tag+'-*')) for tag in tags}
summaries=[json.loads((roots[tag]/'REVIEW.json').read_text()) for tag in tags]
assert all(all(q['all_eight_arrays_public_checkpoint_replay_bitexact'].values()) for q in summaries)
assert all(all(q['all_eight_arrays_bitexact_frozen_numeric_baseline'].values()) for q in summaries)
hashchecks=[json.loads((O/('saved-project-'+tag+'-hash-receipt.json')).read_text()) for tag in tags]
assert all(q['match'] for q in hashchecks)
z=np.load(F0/'inputs/lowflow-independent-design.npz');base=z['T_base_K'];slope=z['T_slope_K_per_s'];a=z['T_curvature_K_per_s2'];ref=base+8*slope+64*a
sections={'node':('node_temperature_K',slice(0,229)),'water':('cell_temp_K',slice(229,1121)),'solid':('chip_temp_K',slice(1121,1177)),'HXsecondary':('secondary_supply_K',slice(1177,1178)),'HXprimary':('primary_return_K',slice(1178,1179))}
finalerrors={family:[] for family in sections}
for tag in tags:
 r=json.loads((roots[tag]/'result.json').read_text())
 for family,(field,sl) in sections.items():finalerrors[family].append(float(np.max(np.abs(np.array(r[field])[:,-1]-ref[sl]))))
EOC={family:[float(np.log(errors[i]/errors[i+1])/np.log(2)) for i in range(3)] for family,errors in finalerrors.items()}
assert all(.85<=rate<=1.15 for rates in EOC.values() for rate in rates)
# Check original completed artifacts remained stable after private copying.
normal_source_drift=[]
for tag in tags:
 for row in json.loads((roots[tag]/'artifact-copy-receipt.json').read_text()):
  p=Path(row['path']);data=p.read_bytes()
  if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:normal_source_drift.append(str(p))
assert not normal_source_drift
# Four saved projects all receive the same actual GUI hydration categories.
projectdiffs={}
for tag in tags:
 saved=json.loads((O/('saved-project-'+tag+'.cfdproj')).read_text());frozen=json.loads((F0/f'results/normalized-dt-{tag}.cfdproj').read_text());rows=[]
 def walk(a,b,path='$'):
  if isinstance(a,dict) and isinstance(b,dict):
   for k in sorted(set(a)|set(b)):
    if k not in a or k not in b:rows.append({'path':path+'.'+k,'kind':'key','actual_present':k in a,'frozen_present':k in b,'actual':a.get(k),'frozen':b.get(k)})
    else:walk(a[k],b[k],path+'.'+k)
  elif isinstance(a,list) and isinstance(b,list):
   if len(a)!=len(b):rows.append({'path':path,'kind':'length','actual':len(a),'frozen':len(b)})
   else:
    for i,(x,y) in enumerate(zip(a,b)):walk(x,y,path+'['+str(i)+']')
  elif a!=b:rows.append({'path':path,'kind':'value','actual':a,'frozen':b})
 walk(saved,frozen);projectdiffs[tag]=rows
 assert len(rows)==302 and [r for r in rows if r['kind']=='value']==[{'path':'$.runDefaults.simulationConfig.nOuterCorrectors','kind':'value','actual':20,'frozen':1}]
(O/'all-four-saved-project-differences.json').write_text(json.dumps(projectdiffs,indent=2))
maxdelta={field:max(q['all_eight_arrays_vs_independent_prediction'][field] for q in summaries) for field in summaries[0]['all_eight_arrays_vs_independent_prediction']}
combined={'scope':'Root real GUI four completed run artifacts independently copied, typed/scientific/source identity audited, all public v3 checkpoints replayed privately; reviewer did not drive browser/API/service','actual_runs':summaries,'actual_JS_canonical_source_hash_checks':hashchecks,'all_64_samples_eight_arrays_public_replay_bitexact':True,'total_samples':sum(q['frames'] for q in summaries),'all_four_original_grid_final_EOCs':EOC,'original_band':[.85,1.15],'EOC_pass':True,'final_errors_K':finalerrors,'independent_max_delta_all_samples':maxdelta,'original_actual_artifact_copy_drift':normal_source_drift,'source_identity_explanation':'Actual GUI hydration changes canonical full project (302 field differences) and therefore source_project/config identity; typed physical/forcing namespace is exactly frozen admitted input, except actual source_project SHA. Wrapper and int->float normalization are not the complete explanation.','old_sine_failure_or_ordinary20s_AD_reclassified':False,'CPU_affinity':sorted(os.sched_getaffinity(0)),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES')}
(O/'ALL_FOUR_REVIEW.json').write_text(json.dumps(combined,indent=2,allow_nan=False))
lines=['# PRIVATE independent review of four actual GUI-backed MMS runs','',
'All four completed real-GUI output sets passed independent typed-input, source identity, all-state equation, original coarse EOC/energy, completed accepted-prefix and public checkpoint v3 replay checks. The reviewer copied original disk artifacts only after their completed status, and drove no browser, API, service or Git operation. Root owns the actual Prepare/Ready/Start/Completed pixels and browser receipts.','',
'Every run has its own actual GUI source_project SHA and runtime config hash. These supersede neither the frozen numeric packet identities nor each other. The real input.json is a wrapper; its liquidrun equals input.liquidrun and checkpoint.liquidrun. Raw compiled input versus actual admitted wrapper has int-to-float schema normalization, 118 schema defaults (six empty profile descriptions and 56 server slot_u=0/span_u=1 pairs), and one actual source_project SHA change. Comparing actual input to the frozen admitted checkpoint eliminates the normal schema effects: only that SHA differs; graph, physical parameters, source benchmark, explicit nonsteady initial state/rates, grid, consistent initialization and empty controllers are exact. Do not call the real inputs byte-identical to frozen compiled files.','',
'The actual GUI-saved project itself differs from the frozen normalized project in 302 places: three floor polygons migrate into geometry.polygon; fourteen rack serverConflicts lists appear; 56 server extraInlet/OutletOffset defaults become inletOffset=.12/outletOffset=.36 plus display.opacity=1; the air simulation defaults add transientResidualControl and set nOuterCorrectors 1->20. These full-project hydration changes explain genuine source content identity changes while the typed liquid physics remain identical. The complete diff lists are preserved.','',
'For all four actual GUI saves, the exact shared JS projectContentHashSync implementation (UTF-8 stable recursive key sort after cfdprojTransient.stripTrackedFields on 126 tracks) independently reproduces the actual runtime source_project SHA. Raw project byte SHA is separately recorded and never substituted. The actual JS modules/registry are pinned with copy receipts.','',
'|dt s|run_id|samples|actual source_project SHA|actual config hash|completed executed config hash|','|---:|---|---:|---|---|---|']
for q in summaries:lines.append('|'+str(q['dt_s'])+'|'+q['run_id']+'|'+str(q['frames'])+'|'+q['actual_source_project_sha']+'|'+q['actual_config_hash']+'|'+q['executed_config_hash_independently_verified']+'|')
lines+=['',
'The reviewer independently rebuilt the full 1693-state equations (950 storage states) from the actual compiled topology and declared frozen coefficient arrays without importing a product residual. All eight actual arrays over 64 samples agree with independent hydraulic, polynomial/BE thermal and outlet predictions: maximum temperature/outlet discrepancy '+str(max(v for k,v in maxdelta.items() if k not in ('node_pressure_Pa','edge_flow_kg_s')))+' K, pressure '+str(maxdelta['node_pressure_Pa'])+' Pa, flow '+str(maxdelta['edge_flow_kg_s'])+' kg/s. Four public DAESimulation.from_checkpoint replays recompute 60 BE steps; all eight arrays and time samples are bitwise identical to real result artifacts, retaining actual run_id/config_hash. The frozen Python backend modules were SHA-identical to current IT when each replay began.','',
'Actual final-time temperature EOCs use the original dt=2/1/.5/.25 over 8s and original [.85,1.15] band; all five families pass. ALL_FOUR_REVIEW.json preserves every final error and rate. Independent energy closure is evaluated with the original max(1e-7,256*eps)*(1+abs(total heat input)) gate and passes. Total endpoint heat is approximately 280.26 kW, including 21 kW physical mock load plus approximately 259.26 kW artificial manufactured heat. This is a forced synthetic solver/GUI experiment, not measured plant heat closure. The old sine coarse FAIL, historical overlay/source identity, and ordinary20s AD packet remain independent.','',
'For each completed run, the typed benchmark source SHA is independently recomputed. The checkpoint retains full original source and explicit IC/rates. executed.liquidrun has the actual completed 5/9/17/33-sample source grid and full original benchmark, passes strict public admission, and its executed-inputs.json original/executed hashes independently match the runtime canonical hash contract. Original versus executed config hashes legitimately differ because accepted control/physical series are emitted; no frozen identity is transplanted.','',
'The earlier P1 runbook finding is disposed of in this actual procedure: Root loaded identical normalized bytes copied to the four declared writable case directories, so remote-load dirname and selected server caseDir own each run under those private directories. Actual artifacts and post-copy SHA stability demonstrate that persistence occurred there. The original product FROZEN payloads and file inventory are rechecked unchanged at freeze. Root pixel/console acceptance is recorded separately, rather than inferred from these private numerical results.','',
'Reproducibility: audit_gui_run.py, independent_equations_used.py, pinned canonical-hash JS sources, actual six-artifact copies, replayed-result arrays, full difference lists and per-run REVIEW.json are retained. Use only repository-private writable copies for reruns, CPU32-33, at most2 numerical threads and empty CUDA_VISIBLE_DEVICES. audit_gui_run.py original normal-input/output paths are explicit and are not portable; frozen copies plus the declared original product source manifest support replay. The private Node argv-index setup mistake is retained as attempt01 and is not classified as a product failure.']
(O/'REPORT.md').write_text('\n'.join(lines)+'\n')
# Revalidate original F0 inventory as well as its payload bytes.
m0=json.loads((F0/'manifest.json').read_text());drift=[]
for q in m0['payloads']:
 data=(F0/q['path']).read_bytes()
 if len(data)!=q['bytes'] or hashlib.sha256(data).hexdigest()!=q['sha256']:drift.append(q['path'])
extra=set(str(p.relative_to(F0)) for p in F0.rglob('*') if p.is_file())-set(q['path'] for q in m0['payloads'])-{'manifest.json','verification.json'}
assert not drift and not extra
(O/'original-product-preservation.json').write_text(json.dumps({'manifest_sha256':hashlib.sha256((F0/'manifest.json').read_bytes()).hexdigest(),'payload_count':len(m0['payloads']),'payload_drift':drift,'extra_files':sorted(extra)},indent=2))
F=O/'FROZEN-v1';F.mkdir(exist_ok=False)
for src in list(O.iterdir()):
 if src==F:continue
 if src.is_dir():shutil.copytree(src,F/src.name)
 else:shutil.copy2(src,F/src.name)
payload=[]
for pth in sorted(F.rglob('*')):
 if pth.is_file():
  data=pth.read_bytes();payload.append({'path':str(pth.relative_to(F)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
manifest={'format':'independent-real-GUI-quadratic-full-ring-MMS-artifact-review-private','version':1,'original_product_manifest_sha256':'6fb645770bbd8a2bd33a72a2c9e5a5c854922921154be8c88a593578fbdca3b3','runs':[{'dt':q['dt_s'],'run_id':q['run_id'],'source_project_sha':q['actual_source_project_sha'],'config_hash':q['actual_config_hash']} for q in summaries],'payload_count':len(payload),'payload_bytes':sum(q['bytes'] for q in payload),'all_four_actual_artifact_pass':True,'all8arrays64samples_replay_bitexact':True,'original_coarse_EOC_gate_retained':True,'reviewer_browser_api_service_git_changes':0,'payloads':payload}
(F/'manifest.json').write_text(json.dumps(manifest,indent=2))
sha=hashlib.sha256((F/'manifest.json').read_bytes()).hexdigest()
(F/'verification.json').write_text(json.dumps({'manifest_sha256':sha,'payload_count':len(payload),'payload_drift':[]},indent=2))
for pth in F.rglob('*'):
 if pth.is_file():os.chmod(pth,0o444)
for pth in sorted((x for x in F.rglob('*') if x.is_dir()),key=lambda x:len(x.parts),reverse=True):os.chmod(pth,0o555)
os.chmod(F,0o555)
print(json.dumps({'path':str(F),'manifest_sha256':sha,'payload_count':len(payload),'payload_bytes':manifest['payload_bytes'],'all64sample8array_replay_bitexact':True,'EOC':EOC,'max_deltas':maxdelta,'source_payload_drift':drift,'source_extra_files':sorted(extra)},indent=2))
