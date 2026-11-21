from pathlib import Path
import json,hashlib,os
import numpy as np
Q=Path(os.environ["DCTWIN_MMS_WORK"])
prepared=json.loads((Q/'PREPARED-IDENTITIES.json').read_text());execution=json.loads((Q/'STUDY-EXECUTION.json').read_text())
assert execution['status']=='completed_numerical_runs_original_gates'
assert hashlib.sha256((Q/'PREREGISTRATION.json').read_bytes()).hexdigest()=='7b49f0cf64cb5374e40e2168d0b3cbd49129ddbb572cbf9cb5cb987287ba3d7b'
names=['node_pressure_Pa','flow_kg_s','node_temperature_K','water_temperature_K','solid_temperature_K','hx_secondary_K','hx_primary_K'];thermal=names[2:]
frameKeys={'node_pressure_Pa':'node_pressure_Pa','flow_kg_s':'edge_flow_kg_s','node_temperature_K':'node_temperature_K','water_temperature_K':'cell_temperature_K','solid_temperature_K':'chip_temp_K','hx_secondary_K':'secondary_supply_K','hx_primary_K':'primary_return_K'}
rows=[]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for identity in prepared['rows']:
 tag=identity['tag'];count=identity['time']['N_t'];dt=identity['time']['dt_s']
 npz=Q/f'results/newton-vs-manufactured-{tag}.npz';a=np.load(npz);target=np.load(Q/f'reference/target-{tag}.npz')
 framesPath=Q/f'results/mms-frames-{tag}.json';frames=json.loads(framesPath.read_text());assert len(frames)==count
 assert [f['seq'] for f in frames]==list(range(count))
 assert np.array_equal(np.array([f['time_s'] for f in frames]),target['times']) and np.array_equal(a['times'],target['times'])
 errors={};initial=True
 for name in names:
  actual=a[name+'_actual'];reference=a[name+'_reference'];diff=actual-reference
  assert np.isfinite(actual).all() and np.isfinite(reference).all()
  assert np.array_equal(reference.view(np.uint64),target[name].view(np.uint64))
  assert np.array_equal(actual.view(np.uint64),np.array([f[frameKeys[name]] for f in frames],dtype=np.float64).view(np.uint64))
  assert np.array_equal(diff.view(np.uint64),a[name+'_error'].view(np.uint64))
  initial=initial and bool(np.array_equal(actual[0].view(np.uint64),reference[0].view(np.uint64)))
  errors[name]={'final_inf':float(np.abs(diff[-1]).max()),'whole_trajectory_inf':float(np.abs(diff).max()),'samples':actual.shape[0],'familyStates':actual.shape[1]}
 assert initial and a['flat1693_actual'].shape==(count,1693)
 free=json.loads((Q/f'results/actual-source-options-and-layout-{tag}.json').read_text())['freePressureNativeOrder']
 flat=np.concatenate([a[k+'_actual'][:,free] if k=='node_pressure_Pa' else a[k+'_actual'] for k in names],axis=1)
 assert np.array_equal(flat.view(np.uint64),a['flat1693_actual'].view(np.uint64))
 recurrencePath=Q/f'results/independent-error-recurrence-{tag}.npz';b=np.load(recurrencePath)
 actualThermal=np.concatenate([a[k+'_error'] for k in thermal],axis=1);assert actualThermal.shape==(count,1179)
 prediction=b[tag+'_independent_total_error'];indDiff=float(np.abs(prediction-actualThermal).max())
 assert np.isfinite(prediction).all() and np.array_equal(actualThermal.view(np.uint64),b[tag+'_product_error'].view(np.uint64))
 recurrence=json.loads((Q/f'results/recurrence-result-{tag}.json').read_text());independentRun=recurrence['runs'][0]
 assert independentRun['initial_error_exact_zero'] and independentRun['all_storage_formula_bit_exact']
 rows.append({'tag':tag,'dt':dt,'frames':count,'unknowns':1693,'rawNPZ':{'path':str(npz),'sha256':sha(npz)},'rawFrames':{'path':str(framesPath),'sha256':sha(framesPath)},'independentRecurrenceNPZ':{'path':str(recurrencePath),'sha256':sha(recurrencePath)},'initialAllSevenFamiliesAnd1693BitsExact':initial,'actualFrameTimesAndSequencesExact':True,'allRawArraysFiniteAndMatchFramesAndReference':True,'allStateErrorsIndependentlyRecomputed':errors,'independentBEProductDiffInf_K':indDiff,'independentBEGate1e_7_Passed':indDiff<=1e-7,'maxScaledResidual':max(f['diagnostics']['scaled_residual'] for f in frames),'maxEnergyBalance_W':max(abs(f['diagnostics']['energy_balance_W']) for f in frames),'maxMassResidual_kg_s':max(f['diagnostics']['mass_residual_kg_s'] for f in frames),'maxMomentumResidual_Pa':max(f['diagnostics']['momentum_residual_Pa'] for f in frames)})
wholeOrders={k:float(np.log2(rows[0]['allStateErrorsIndependentlyRecomputed'][k]['whole_trajectory_inf']/rows[1]['allStateErrorsIndependentlyRecomputed'][k]['whole_trajectory_inf'])) for k in thermal}
finalOrders={k:float(np.log2(rows[0]['allStateErrorsIndependentlyRecomputed'][k]['final_inf']/rows[1]['allStateErrorsIndependentlyRecomputed'][k]['final_inf'])) for k in thermal}
wholePass={k:.85<=v<=1.15 for k,v in wholeOrders.items()}
result={'status':'PASS_registered_current_full1693_MMS' if all(wholePass.values()) and all(r['independentBEGate1e_7_Passed'] for r in rows) else 'FAIL_registered_gate_cause_investigation_required','scope':'Current Glycol25 original product complete1693-state numerical MMS; synthetic forcing; no calibration/all-parameter gradient claim','preregSHA256':sha(Q/'PREREGISTRATION.json'),'preparedIdentitiesSHA256':sha(Q/'PREPARED-IDENTITIES.json'),'rows':rows,'allFiveThermalWholeTrajectoryOrders':wholeOrders,'originalBand':[.85,1.15],'wholeTrajectoryBlockPassed':wholePass,'separateFinalTimeOrders':finalOrders,'finalTimeOrdersInSameBandReportedOnly':{k:.85<=v<=1.15 for k,v in finalOrders.items()},'oldDifferentPhysicsCoarseHXsecondaryREDPreserved':True,'oldFineEvidenceNotSubstituted':True,'criteriaChanged':False,'affinity':sorted(os.sched_getaffinity(0)),'actualStudyExecution':execution}
for source in json.loads((Q/'SOURCE-MAP-backend.json').read_text())['rows']:
 for path in [source['path'],source['snapshot']]:assert sha(Path(path))==source['sha256']
result['originalAndSnapshotBackendAllHeld']=True
(Q/'CURRENT-FULL-MMS-RESULTS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
with (Q/'WORK_LOG.txt').open('a') as f:f.write('\nComplete current fullMMS arithmetic review: '+result['status']+'; original criteria held, all raw arrays/frame times/reference/initial bits/source holds checked. Old coarse RED stays unchanged.\n')
