"""Copied actual normal mesh bytes, private grants and CPU USD; no new native run."""
import copy,hashlib,json,shutil
from pathlib import Path
import pytest
from backend.aircooling.runner.mesh_layout import mesh_directories
from backend.aircooling.runner.numeric_product_receipts import canonical_bytes
from server import canonical_mesh_preview as mod
from server.ov_result_dataset import _grant_module
from backend.tests.test_verified_result_grant import module
controller=module('verified_result_grant');usd=module('result_layers_usd')
KEY='controlled-private-launch-key-not-normal-1234567890'
FIXTURE=Path(__file__).parent/'fixtures/actual_thermal_mesh_preview_20261006'

def stat_key(case):
 mesh=case/'Mesh';rows=[]
 for poly in mesh_directories(mesh):
  for name in ['points','faces','owner','neighbour','boundary']:
   p=poly/name;s=p.stat();rows.append((str(p.relative_to(mesh)),s.st_size,s.st_mtime_ns))
 return hashlib.sha256(json.dumps((str(mesh.resolve()),0,rows),separators=(',',':')).encode()).hexdigest()

def fixture(tmp_path):
 case=tmp_path/'copied_existing_mesh';shutil.copytree(FIXTURE,case)
 key=stat_key(case);current=lambda:stat_key(case)
 desc,product=mod.verify_mesh_preview_source(case_dir=case,mesh_snapshot_key=key,current_snapshot=current)
 intent={'schema':'dcdtuni.common-mesh-preview-intent/v1','revision':4,'source':copy.deepcopy(desc['source']),'meshSnapshotKey':key,'meshPayloadSha256':desc['meshPayloadSha256'],'hidden':[],'opacity':.23}
 request={'schema':'dcdtuni.canonical-mesh-preview-request/v1','scene':{'projectUid':desc['source']['projectUid'],'projectContentSha256':desc['source']['projectContentSha256'],'revision':7},'displayGeneration':1,'intent':intent}
 return case,key,current,desc,product,request

def issue(values,request=None):
 case,key,current,desc,product,default=values
 return mod.issue_mesh_preview_grant(case_dir=case,mesh_snapshot_key=key,current_snapshot=current,request=request or default,key=KEY,now=100)

def test_actual_copied_final_mesh_source_is_verified_without_run_frame_or_scalar_authority(tmp_path):
 v=fixture(tmp_path);case,key,current,desc,product,request=v
 assert desc['source']['run']is None and desc['source']['meshQuality']=='accepted'
 assert desc['source']['projectUid']=='root-normal-two-box-differential-temperature-20261006'
 assert desc['source']['cfdrunSha256']=='912a27cc94b05620941970571e078a2776852c3d2ab00c0e1e4bcdad69a94510'
 assert len(product.payload)==87384
 result=issue(v);granted=_grant_module().verify(result['grant'],KEY,now=101,roots=[str(case)],kind='rendererMesh')
 assert 'runId'not in granted and 'runConvergence'not in granted and granted['timeSeconds']is None
 packet=controller.read_granted_packet(granted)
 assert packet['schema']=='dcdtuni.render-result-layers/v4'and packet['timeSeconds']is None
 assert set(l['kind']for l in packet['layers'])=={'meshFaces','meshEdges'}
 assert all('values'not in l and 'field'not in l and 'lut'not in l for l in packet['layers'])
 assert all(l['opacity']==(.23 if l['kind']=='meshFaces'else .35)for l in packet['layers'])
 assert any(r['bindingKind']=='regionFacetInventory'for l in packet['layers']for r in l['boundaryProvenance']['patchRanges'])
 assert all('sourceFaceUids'not in l for l in packet['layers'])
 from pxr import Usd,UsdGeom,UsdShade
 stage=Usd.Stage.CreateInMemory();ctl=controller.VerifiedResultController(clock=lambda:101);scene=request['scene'];ticket=ctl.accept(granted,scene,5)
 ctl.require_loaded(ticket,packet,scene,5)
 receipt=usd.author_result_layers(stage,packet,expected_source=packet['source'],expected_time_seconds=None,commit_guard=lambda:ctl.commit(ticket,packet,lambda:scene,lambda:5))
 for layer in packet['layers']:
  prim=stage.GetPrimAtPath(receipt['layerPaths'][layer['layerId']]);material=UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0];shader=UsdShade.Shader(material.GetPrim().GetChild('Surface'))
  assert shader.GetInput('opacity').Get()==pytest.approx(layer['opacity'])
  if layer['kind']=='meshFaces':assert len(UsdGeom.Mesh(prim).GetFaceVertexIndicesAttr().Get())==len(layer['indices'])
  else:assert len(UsdGeom.BasisCurves(prim).GetPointsAttr().Get())==len(layer['indices'])
  assert not prim.HasAttribute('primvars:dcdtuniRawScalar')
 usd.clear_result_layers(stage);assert not stage.GetPrimAtPath('/World/RenderResultLayers')

def test_a_rejected_quality_verdict_still_grants_mesh_display(tmp_path):
 # Owner rule (2026-10-22): mesh quality is reported, never required; a failed mesh is the one worth looking at.
 v=fixture(tmp_path);case,key,current=v[0],v[1],v[2]
 p=case/'mesh-quality-receipt.json';raw=json.loads(p.read_text());raw.update(status='rejected',failedChecks=1);p.write_text(json.dumps(raw))
 assert mod.verify_mesh_preview_source(case_dir=case,mesh_snapshot_key=key,current_snapshot=current)

@pytest.mark.parametrize('change',['quality_hash','source_v1','source_region','source_mesh_input','stl_bytes','native_bytes','manifest_bytes','mesh_bytes','snapshot'])
def test_actual_mesh_source_mutations_never_gain_mesh_display_authority(tmp_path,change):
 v=fixture(tmp_path);case,key,current,desc,product,request=v
 if change.startswith('quality_'):
  p=case/'mesh-quality-receipt.json';raw=json.loads(p.read_text())
  if change=='quality_reject':raw.update(status='rejected',failedChecks=1)
  else:raw['meshSha256']='0'*64
  p.write_text(json.dumps(raw))
 elif change.startswith('source_'):
  p=case/'source-region-mesh-source.json';raw=json.loads(p.read_text())
  if change=='source_v1':raw['format']='dcdtuni.source-region-mesh-source/v1'
  elif change=='source_region':raw['sourceRegionsSha256']='0'*64
  else:raw['meshInputIdentitySha256']='0'*64
  p.write_text(json.dumps(raw))
 elif change=='snapshot':current=lambda:'0'*64
 else:
  if change=='stl_bytes':p=next((case/'Mesh/constant/triSurface').glob('*.stl'))
  elif change=='native_bytes':p=case/'Mesh/constant/dcCartSourceRegions.json'
  elif change=='manifest_bytes':p=next((case/'model').glob('*.cfdrun'))
  else:p=case/'Mesh/constant/polyMesh/points'
  p.write_bytes(p.read_bytes()+b'\n')
 with pytest.raises((ValueError,KeyError)):
  mod.verify_mesh_preview_source(case_dir=case,mesh_snapshot_key=key,current_snapshot=current)

@pytest.mark.parametrize('change',['source','payload','scalar_injection','unknown_patch','duplicate_hidden','opacity','run','time','wrong_kind','extra_signed'])
def test_mesh_only_grant_cannot_invent_scalar_run_time_or_different_source(tmp_path,change):
 v=fixture(tmp_path);req=copy.deepcopy(v[-1])
 if change in ['source','payload','scalar_injection','unknown_patch','duplicate_hidden','opacity']:
  if change=='source':req['intent']['source']['projectContentSha256']='0'*64
  elif change=='payload':req['intent']['meshPayloadSha256']='0'*64
  elif change=='scalar_injection':req['intent']['field']='T'
  elif change=='unknown_patch':req['intent']['hidden']=['not-a-real-patch']
  elif change=='duplicate_hidden':req['intent']['hidden']=[next(iter(v[3]['boundaryProvenanceByPatch']))]*2
  else:req['intent']['opacity']=2
  with pytest.raises(ValueError):issue(v,req)
  return
 result=issue(v);payload=copy.deepcopy(result['grant']['payload'])
 if change=='run':payload['source']['run']={'uid':'invented','receiptSha256':'a'*64}
 elif change=='time':payload['timeSeconds']=1
 elif change=='extra_signed':payload['runConvergence']={'verdict':'CONVERGED','receiptSha256':'a'*64}
 signed=_grant_module().sign(payload,KEY)
 with pytest.raises(ValueError):_grant_module().verify(signed,KEY,now=101,roots=[str(v[0])],kind='rendererResult'if change=='wrong_kind'else'rendererMesh')

def test_hidden_mesh_patches_preserve_identity_and_do_not_hide_corrupt_sources(tmp_path):
 v=fixture(tmp_path);request=copy.deepcopy(v[-1]);name=next(iter(v[3]['boundaryProvenanceByPatch']));request['intent']['hidden']=[name]
 result=issue(v,request);packet=controller.read_granted_packet(_grant_module().verify(result['grant'],KEY,now=101,roots=[str(v[0])],kind='rendererMesh'))
 assert all(l['meshPatchName']!=name for l in packet['layers'])
 assert packet['source']==v[3]['source']

def test_late_mesh_commit_and_clear_cannot_overwrite_newer_mesh_context(tmp_path):
 v=fixture(tmp_path);one=issue(v);signed=_grant_module().verify(one['grant'],KEY,now=101,roots=[str(v[0])],kind='rendererMesh');packet=controller.read_granted_packet(signed);scene=v[-1]['scene'];ctl=controller.VerifiedResultController(clock=lambda:101)
 first=ctl.accept(signed,scene,5);ctl.require_loaded(first,packet,scene,5)
 req=copy.deepcopy(v[-1]);req['displayGeneration']=2;req['intent']['revision']=5;req['intent']['opacity']=.8
 two=issue(v,req);new=_grant_module().verify(two['grant'],KEY,now=101,roots=[str(v[0])],kind='rendererMesh');next_packet=controller.read_granted_packet(new);second=ctl.accept(new,scene,5)
 with pytest.raises(ValueError):ctl.require_loaded(first,packet,scene,5)
 from pxr import Usd
 stage=Usd.Stage.CreateInMemory();usd.author_result_layers(stage,next_packet,expected_source=next_packet['source'],expected_time_seconds=None,commit_guard=lambda:ctl.commit(second,next_packet,lambda:scene,lambda:5))
 old_clear=ctl.invalidate(display_generation=3)
 req['displayGeneration']=4;third=issue(v,req);latest=_grant_module().verify(third['grant'],KEY,now=101,roots=[str(v[0])],kind='rendererMesh');ctl.accept(latest,scene,5)
 with pytest.raises(ValueError):
  with ctl.clear_commit(old_clear,5,lambda:5):usd.clear_result_layers(stage)
 assert stage.GetPrimAtPath('/World/RenderResultLayers').GetCustomDataByKey('dcdtuni:revision')==5
 with pytest.raises(ValueError):ctl.accept(new,scene,5)


def test_signed_mesh_packet_scalar_injection_and_late_artifact_mutation_are_rejected(tmp_path):
 v=fixture(tmp_path);result=issue(v);signed=_grant_module().verify(result['grant'],KEY,now=101,roots=[str(v[0])],kind='rendererMesh');packet=controller.read_granted_packet(signed)
 altered=copy.deepcopy(packet);altered['layers'][0]['kind']='surfaceField';altered['layers'][0]['field']={'name':'T','unit':'K'}
 with pytest.raises(ValueError):usd.validate_result_packet(altered)
 path=Path(signed['path']);path.write_bytes(path.read_bytes()+b' ')
 with pytest.raises(ValueError):controller.read_granted_packet(signed)
