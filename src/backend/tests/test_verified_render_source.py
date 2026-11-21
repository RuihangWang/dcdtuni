"""Controlled receipt composition and late mutation rejection.

The source mesh is actual isolated 511-cell native geometry. Fields/execution
are deliberately synthetic contract fixtures, NEVER normal solve acceptance.
"""
import copy,hashlib,json,shutil,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from server import verified_render_source as mod
from backend.aircooling.runner.air_run_result_receipt import build_air_run_result_receipt
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
from backend.aircooling.runner.source_region_catalogue import canonical_sha,verify_native_source_region_ranges
from backend.aircooling.runner.native_numeric_input import capture_native_inputs

FIXTURE=Path(__file__).resolve().parent/'fixtures/controlled_render_source/spacious-native-511'

def write_json(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))

def fixture(tmp_path):
 case=tmp_path/'ControlledContractOnly';mesh=case/'Mesh';shutil.copytree(FIXTURE/'constant',mesh/'constant');shutil.copytree(FIXTURE/'system',mesh/'system')
 run=case/'log/controlled';shutil.copytree(mesh,run)
 raw=json.loads((FIXTURE/'model/source.cfdrun').read_bytes());raw['schemaVersion']=4;raw['runId']='controlled-run';raw['sourceProject']['uid']='controlled-project'
 logical=case/'model/source.cfdrun';write_json(logical,raw);write_json(run/'model/source.cfdrun',raw)
 for t in [.01,.02]:
  for name in ['T','U']:
   scalar=name=='T';dims='[0 0 0 1 0 0 0]'if scalar else'[0 1 -1 0 0 0 0]'
   value='300'if scalar else'(0.1 0 0)';cl='volScalarField'if scalar else'volVectorField';p=run/str(t)/name;p.parent.mkdir(exist_ok=True)
   p.write_text('FoamFile { format ascii; class '+cl+'; object '+name+'; }\ndimensions '+dims+';\ninternalField uniform '+value+';\nboundaryField {}\n')
 selected=build_air_run_result_receipt(case_dir=case,cfdrun_path=logical,run_dir=run,time_value=.02)
 write_json(run/'air-run-execution-receipt.json',{'format':'air-run-execution-receipt/v1','status':'accepted','solverCommand':'CONTROLLED fixture; no native solve','result':selected})
 digest=mesh_identity_sha256(mesh);cfdrun_sha=hashlib.sha256(logical.read_bytes()).hexdigest()
 write_json(case/'mesh-quality-receipt.json',{'format':'air-mesh-quality/v1','status':'accepted','failedChecks':0,'meshSha256':digest,'cfdrunSha256':cfdrun_sha,'mode':'serial','ranks':1,'checkmeshStdoutSha256':'a'*64,'checkmeshStderrSha256':'b'*64})
 stage=json.loads((FIXTURE/'stageCatalogue.json').read_bytes())
 write_json(case/'source-region-mesh-source.json',{'format':'dcdtuni.source-region-mesh-source/v1','cfdrunSha256':cfdrun_sha,'sourceRegionsSha256':canonical_sha(raw['geometryExport']['sourceRegions']),'stageCatalogueSha256':canonical_sha(stage),'meshSha256':digest,'stageCatalogue':stage,'nativeCatalogues':verify_native_source_region_ranges(mesh,stage)})
 write_json(run/'convergence.json',{'verdict':'NOT_CONVERGED','rule':'CONTROLLED fixture only, no physical solve acceptance'})
 session={'runId':hashlib.sha256(str(run.resolve()).encode()).hexdigest(),'timeValue':.02,'revision':'controlled-pp'}
 cpp=SimpleNamespace(run_dir=run,multi_block=True,_native_input_snapshot=capture_native_inputs(run,.02),_identity=lambda:copy.deepcopy(session))
 return case,run,cpp,session

def verify(case,cpp,session):return mod.verify_render_source(cpp=cpp,case_dir=case,session=session)

def test_controlled_accepted_composition_binds_source_frame_and_actual_ranges(tmp_path):
 case,run,cpp,session=fixture(tmp_path);value=verify(case,cpp,session)
 assert value['runConvergence']['verdict']=='NOT_CONVERGED'
 assert value['postprocess']==session and value['source']['meshQuality']=='accepted'
 assert value['source']['projectUid']=='controlled-project';assert value['fieldUnits']['T']=='K'
 ranges=[r for p in value['boundaryProvenanceByPatch'].values()for r in p['patchRanges']]
 assert any(r['bindingKind']=='regionFacetInventory'for r in ranges)
 assert any(r['bindingKind']=='meshBoundaryOnly'for r in ranges)
 assert all('sourceFaceUids'not in p for p in value['boundaryProvenanceByPatch'].values())
 assert all('solverDomain'not in str(r)for r in ranges)

def test_exact_older_selected_frame_retains_verified_latest_execution(tmp_path):
 case,run,cpp,session=fixture(tmp_path);session['timeValue']=.01;cpp._native_input_snapshot=capture_native_inputs(run,.01)
 result=verify(case,cpp,session);assert result['postprocess']['timeValue']==.01
 assert result['source']['run']['receiptSha256']!=verify(case,SimpleNamespace(**{**cpp.__dict__,'_native_input_snapshot':capture_native_inputs(run,.02),'_identity':lambda:{**session,'timeValue':.02}}),{**session,'timeValue':.02})['source']['run']['receiptSha256']

def test_a_run_solved_on_a_rejected_mesh_still_renders(tmp_path):
 # Owner rule (2026-10-22): mesh quality is reported, never required; identity is still checked.
 case,run,cpp,session=fixture(tmp_path)
 path=case/'mesh-quality-receipt.json';data=json.loads(path.read_bytes());data['status']='rejected';data['failedChecks']=1;write_json(path,data)
 assert verify(case,cpp,session)['postprocess']['timeValue']==.02

@pytest.mark.parametrize('change',['run','revision','frame','native_read','T','U','mesh','source_absent','quality_sha','execution_rejected','newer_partial','archive','source_binding','region_catalogue','stage_bytes','source_receipt','range','rank'])
def test_source_grant_composition_denies_independent_stale_or_missing_evidence(tmp_path,change):
 case,run,cpp,session=fixture(tmp_path)
 if change=='run':session['runId']='other'
 elif change=='revision':cpp._identity=lambda:{**session,'revision':'new'}
 elif change=='frame':session['timeValue']=.03
 elif change=='native_read':cpp._native_input_snapshot={}
 elif change in ['T','U']:(run/'.02'/change).write_text('changed') if (run/'.02').exists()else(run/'0.02'/change).write_text('changed')
 elif change=='mesh':(case/'Mesh/constant/polyMesh/faces').write_text('changed')
 elif change=='source_absent':(case/'source-region-mesh-source.json').unlink()
 elif change=='newer_partial':(run/'0.03').mkdir()
 elif change=='archive':(run/'model/source.cfdrun').write_text('{}')
 elif change=='stage_bytes':(case/'Mesh/constant/triSurface/merged_box.stl').write_text('changed')
 else:
  path=case/'mesh-quality-receipt.json'if change.startswith('quality')else run/'air-run-execution-receipt.json'if change.startswith('execution')else case/'Mesh/constant/dcCartSourceRegions.json'if change in ['range','rank','region_catalogue']else case/'source-region-mesh-source.json'
  data=json.loads(path.read_bytes())
  if change=='quality_rejected':data['status']='rejected';data['failedChecks']=1
  elif change=='quality_sha':data['cfdrunSha256']='0'*64
  elif change=='execution_rejected':data['status']='rejected'
  elif change=='source_binding':data['stageCatalogue']['regions'][0]['sourceEntityUid']='other';data['stageCatalogueSha256']=canonical_sha(data['stageCatalogue'])
  elif change=='region_catalogue':data['regions'][0]['sourceRegionUid']='invented'
  elif change=='source_receipt':data['cfdrunSha256']='0'*64
  elif change=='range':data['regions'][0]['patchRanges'][0]['startFace']+=1
  elif change=='rank':data['rank']=1
  write_json(path,data)
 with pytest.raises((ValueError,OSError)):verify(case,cpp,session)

@pytest.mark.parametrize('change',['frame','native','quality','source','archive','stage'])
def test_late_source_change_after_individual_verifiers_is_rejected(tmp_path,monkeypatch,change):
 case,run,cpp,session=fixture(tmp_path);original=mod.capture_native_inputs;calls=[0]
 def late(*args):
  value=original(*args);calls[0]+=1
  if calls[0]==2:
   if change=='frame':cpp._identity=lambda:{**session,'revision':'late'}
   elif change=='native':(run/'0.02/T').write_text('late');value=original(*args)
   else:
    path=case/'mesh-quality-receipt.json'if change=='quality'else case/'source-region-mesh-source.json'if change=='source'else run/'model/source.cfdrun'if change=='archive'else case/'Mesh/constant/triSurface/merged_box.stl'
    path.write_bytes(path.read_bytes()+b'\n')
  return value
 monkeypatch.setattr(mod,'capture_native_inputs',late)
 with pytest.raises((ValueError,OSError)):verify(case,cpp,session)

def v2_fixture(tmp_path):
 from backend.aircooling.runner.stlgen_runner import STLGenRunner
 case,run,cpp,session=fixture(tmp_path)
 runner=object.__new__(STLGenRunner);runner.case_dir=case;runner.mesh_dir=case/'Mesh';runner.tri_surface_dir=runner.mesh_dir/'constant/triSurface';runner.cfdrun_path=case/'model/source.cfdrun';runner._data={'meshConfig':{'meshEngine':'dcCart'}}
 runner._verified_source_cfdrun_sha256=hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
 original=json.loads((case/'source-region-mesh-source.json').read_bytes());runner._source_region_stage_catalogue=original['stageCatalogue'];runner._source_stls_verified=True
 runner._record_source_region_mesh_source();mesh_source_bytes=(case/'source-region-mesh-source.json').read_bytes()
 raw=json.loads(runner.cfdrun_path.read_bytes());raw['runId']='controlled-fresh-execution';raw['simulationConfig']={'endTime':.02}
 write_json(runner.cfdrun_path,raw);write_json(run/'model/source.cfdrun',raw)
 runner._verified_source_cfdrun_sha256=hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
 runner._verify_source_region_mesh_source()
 result=build_air_run_result_receipt(case_dir=case,cfdrun_path=runner.cfdrun_path,run_dir=run,time_value=.02)
 write_json(run/'air-run-execution-receipt.json',{'format':'air-run-execution-receipt/v1','status':'accepted','solverCommand':'CONTROLLED fixture only','result':result})
 quality=json.loads((case/'mesh-quality-receipt.json').read_bytes());quality['cfdrunSha256']=runner._verified_source_cfdrun_sha256;write_json(case/'mesh-quality-receipt.json',quality)
 assert (case/'source-region-mesh-source.json').read_bytes()==mesh_source_bytes
 return case,run,cpp,session

def test_actual_runner_v2_reuse_composes_current_execution_and_original_mesh_without_claiming_convergence(tmp_path):
 case,run,cpp,session=v2_fixture(tmp_path);out=verify(case,cpp,session)
 assert out['source']['run']['uid']=='controlled-fresh-execution'
 assert out['source']['cfdrunSha256']==hashlib.sha256((run/'model/source.cfdrun').read_bytes()).hexdigest()
 assert out['runConvergence']['verdict']=='NOT_CONVERGED'
 assert out['source']['meshQuality']=='accepted'
 assert out['runConvergence']['receiptSha256']==hashlib.sha256((run/'convergence.json').read_bytes()).hexdigest()

@pytest.mark.parametrize('change',['missing_reuse','execution','mesh_receipt_hash','mesh_input','original_mesh_input','tri_snapshot','extra_reuse','missing_convergence','bad_convergence','linked_convergence'])
def test_v2_reuse_or_convergence_cannot_be_forged_from_new_execution_coordinates(tmp_path,change):
 case,run,cpp,session=v2_fixture(tmp_path);reuse=case/'source-region-mesh-reuse.json';source=case/'source-region-mesh-source.json';convergence=run/'convergence.json'
 if change=='missing_reuse':reuse.unlink()
 elif change=='missing_convergence':convergence.unlink()
 elif change=='linked_convergence':
  other=run/'other.json';other.write_bytes(convergence.read_bytes());convergence.unlink();convergence.symlink_to(other)
 elif change=='bad_convergence':write_json(convergence,{'verdict':'rc0 means converged'})
 else:
  path=source if change=='original_mesh_input'else reuse;data=json.loads(path.read_bytes())
  if change=='execution':data['executionCfdrunSha256']='0'*64
  elif change=='mesh_receipt_hash':data['meshSourceReceiptSha256']='0'*64
  elif change in ['mesh_input','original_mesh_input']:data['meshInputIdentitySha256']='0'*64
  elif change=='tri_snapshot':data['triSurfaceSha256']='0'*64
  else:data['inferredFaceUid']='invented'
  write_json(path,data)
 with pytest.raises((ValueError,OSError)):verify(case,cpp,session)

@pytest.mark.parametrize('verdict',['CONVERGED','NOT_CONVERGED','INCONCLUSIVE','TRANSIENT_COMPLETED','TRANSIENT_INCOMPLETE'])
def test_independent_real_verdict_identity_is_carried_without_execution_status_promotion(tmp_path,verdict):
 case,run,cpp,session=v2_fixture(tmp_path);before=verify(case,cpp,session)
 write_json(run/'convergence.json',{'verdict':verdict,'rule':'CONTROLLED fixture; no physical acceptance'})
 after=verify(case,cpp,session)
 assert after['runConvergence']['verdict']==verdict
 assert before['source']['run']['receiptSha256']!=after['source']['run']['receiptSha256']

@pytest.mark.parametrize('change',['reuse','convergence'])
def test_late_reuse_or_convergence_revision_after_numeric_read_is_rejected(tmp_path,monkeypatch,change):
 case,run,cpp,session=v2_fixture(tmp_path);original=mod.capture_native_inputs;calls=0
 def late(*args):
  nonlocal calls
  value=original(*args);calls+=1
  if calls==2:
   path=case/'source-region-mesh-reuse.json'if change=='reuse'else run/'convergence.json';path.write_bytes(path.read_bytes()+b' ')
  return value
 monkeypatch.setattr(mod,'capture_native_inputs',late)
 with pytest.raises(ValueError):verify(case,cpp,session)

def test_optional_native_field_bytes_and_actual_dimensions_are_part_of_the_source_pin(tmp_path):
 from backend.aircooling.runner.native_numeric_input import native_field_units
 case,run,cpp,session=fixture(tmp_path)
 extra=run/'0.02/k';extra.write_text('FoamFile { format ascii; class volScalarField; object k; }\ndimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0.01;\nboundaryField {}\n')
 before=capture_native_inputs(run,.02);assert native_field_units(before)['k']=='m^2/s^2'
 extra.write_text(extra.read_text().replace('0.01','0.02'))
 after=capture_native_inputs(run,.02);assert before['sha256']!=after['sha256']
 extra.write_text(extra.read_text().replace('object k','object epsilon'))
 with pytest.raises(ValueError,match='object'):capture_native_inputs(run,.02)
