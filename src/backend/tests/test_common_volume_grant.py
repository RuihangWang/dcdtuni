"""Controlled actual CPP bytes -> signed grid -> CPU USD wiring/context.

Source/run envelopes are controlled stubs. CPU USD carrier files here are
explicit placeholders; no CUDA/IndeX/CFD/browser acceptance is claimed.
"""
import copy,hashlib,json
from pathlib import Path
import pytest
from backend.tests.test_canonical_render_grant import fixture as canonical_fixture,issue,KEY
from backend.tests.test_canonical_render_packet import DESC,SESSION
from backend.tests.test_common_volume_grid import fixture as cpp_grid
from backend.tests.test_verified_result_grant import module
from server import canonical_render_grant as mod
from server.ov_result_dataset import _grant_module
from backend.aircooling.runner.numeric_product_receipts import canonical_bytes
reader=module('common_volume_reader');controller_module=module('verified_result_grant');usd=module('result_layers_usd')

def fixture(tmp_path,monkeypatch,visible=True):
 cpp,request=canonical_fixture(tmp_path,monkeypatch);payload,metadata=cpp_grid();store=cpp._numeric_product_store
 receipt=store.capture('volume',{'field':'T','resolution':16},payload,metadata,session=SESSION,native_input_sha256=DESC['nativeInputSha256'])
 request['intent']['volume']={'token':receipt['token'],'field':'T','unit':'K','resolution':16,'lut':{'palette':'viridis','range':[315,331],'opacity':.27},'visible':visible}
 return cpp,request,payload

def verified_packet(tmp_path,monkeypatch,visible=True):
 cpp,request,payload=fixture(tmp_path,monkeypatch,visible);response=issue(cpp,request)
 granted=_grant_module().verify(response['grant'],KEY,now=101,roots=[str(tmp_path)],kind='rendererResult')
 packet=controller_module.read_granted_packet(granted)
 return granted,packet,payload

def test_same_common_grid_original_bytes_coordinates_and_lut_are_bound_by_actual_signature(tmp_path,monkeypatch):
 granted,packet,payload=verified_packet(tmp_path,monkeypatch);assert packet['schema']=='dcdtuni.render-result-layers/v3'
 volume=packet['volume'];path=Path(granted['path']).with_name(volume['artifactName'])
 assert path.read_bytes()==payload and volume['grid']['payloadSha256']==hashlib.sha256(payload).hexdigest()
 grid=reader.read_granted_volume(granted,packet)
 assert grid.origin==(1.,2.,3.)and grid.dimensions==(16,16,16)and grid.payload==payload
 assert volume['field']=={'name':'T','unit':'K'}and volume['lut']=={'palette':'viridis','range':[315,331],'opacity':.27}
 assert volume['visible']is True and packet['runConvergence']['verdict']=='NOT_CONVERGED'
 usd.validate_result_packet(packet)

@pytest.mark.parametrize('change',['byte','symlink','outside_name','coords','dims','field','unit','offset','bytes'])
def test_signed_grid_reader_checks_original_bytes_and_every_descriptor_coordinate_before_carrier(change,tmp_path,monkeypatch):
 granted,packet,payload=verified_packet(tmp_path,monkeypatch);volume=packet['volume'];grid=volume['grid'];path=Path(granted['path']).with_name(volume['artifactName'])
 if change=='byte':path.write_bytes(payload[:-1]+bytes([payload[-1]^1]))
 elif change=='symlink':
  other=path.with_name('other.bin');other.write_bytes(payload);path.unlink();path.symlink_to(other)
 elif change=='outside_name':volume['artifactName']='../outside.volume'
 elif change=='coords':grid['originMeters'][0]+=1
 elif change=='dims':grid['dimensions'][0]+=1
 elif change=='field':grid['field']['name']='speed'
 elif change=='unit':volume['field']['unit']='degC'
 elif change=='offset':grid['scalarOffset']+=4
 else:grid['payloadBytes']+=4
 with pytest.raises((ValueError,KeyError)):reader.read_granted_volume(granted,packet)

@pytest.mark.parametrize('change',['bytes','link','evicted'])
def test_original_grid_cannot_change_after_packet_publication_before_server_signature(change,tmp_path,monkeypatch):
 cpp,request,payload=fixture(tmp_path,monkeypatch);calls=[0]
 def verify(**_):
  calls[0]+=1
  if calls[0]==2:
   if change=='evicted':cpp._numeric_product_store.close()
   else:
    path=next((cpp.run_dir/'.ov-display/canonical-result').glob('*.volume'))
    if change=='bytes':path.write_bytes(payload[:-4])
    else:
     other=path.with_name('other.bin');other.write_bytes(payload);path.unlink();path.symlink_to(other)
  return copy.deepcopy(DESC)
 monkeypatch.setattr(mod,'verify_render_source',verify)
 with pytest.raises(ValueError):issue(cpp,request)

def test_hidden_grid_preserves_signed_identity_without_requiring_a_gpu_carrier(tmp_path,monkeypatch):
 from pxr import Usd
 granted,packet,payload=verified_packet(tmp_path,monkeypatch,False);reader.read_granted_volume(granted,packet)
 stage=Usd.Stage.CreateInMemory();result=usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02)
 assert stage.GetPrimAtPath(result['layerPaths']['volume']).GetAttribute('visibility').Get()=='invisible'
 assert usd.clear_result_layers(stage)==1

def test_actual_cpu_usd_wiring_preserves_same_lut_dims_origin_and_unique_carrier_paths(tmp_path,monkeypatch):
 from pxr import Usd
 granted,packet,payload=verified_packet(tmp_path,monkeypatch);reader.read_granted_volume(granted,packet)
 # Placeholder is deliberate: this only checks actual CPU USD wiring, not
 # nvdb validity or operator/GPU execution (those need the real Kit run).
 carrier=tmp_path/'CONTROLLED_PLACEHOLDER_NOT_NVDB.nvdb';carrier.write_bytes(b'CPU-USD-WIRING-ONLY')
 stage=Usd.Stage.CreateInMemory();result=usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 path=result['layerPaths']['volume'];source=stage.GetPrimAtPath(path+'/Source');field=stage.GetPrimAtPath(path+'/Source/Scalar');volume=stage.GetPrimAtPath(path+'/Volume');shader=stage.GetPrimAtPath(path+'/Volume/Material/VolumeShader');colormap=stage.GetPrimAtPath(path+'/Volume/Material/Colormap')
 assert tuple(source.GetAttribute('cae:vtk:origin').Get())==(1,2,3)and tuple(field.GetAttribute('cae:nanovdb_field_array:dims').Get())==(16,16,16)
 assert volume.GetRelationship('cae:viz:field_selection:colors:target').GetTargets()==[field.GetPath()]
 assert tuple(colormap.GetAttribute('domain').Get())==(0,1)and len(colormap.GetAttribute('rgbaPoints').Get())==256
 assert tuple(shader.GetAttribute('inputs:domain').Get())==(315,331)
 assert volume.GetRelationship('cae:viz:field_selection:validity:target').GetTargets()==[stage.GetPrimAtPath(path+'/Source/Validity').GetPath()]
 assert shader.GetAttribute('inputs:opacity').Get()==pytest.approx(.27)
 assert volume.GetAttribute('cae:viz:operator:enabled').Get()is True
 assert volume.GetAttribute('nvindex:composite').Get()is False # runtime operator must execute; USD ACK is not rendering proof.
 next_packet=copy.deepcopy(packet);next_packet['revision']+=1;next_packet['volume']['lut']['opacity']=.1
 newer=usd.author_result_layers(stage,next_packet,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 assert newer['layerPaths']['volume']!=path and not stage.GetPrimAtPath(path)
 assert usd.clear_result_layers(stage)==1

def test_changed_signed_context_after_grid_read_cannot_commit_late_volume(tmp_path,monkeypatch):
 from pxr import Usd
 granted,packet,payload=verified_packet(tmp_path,monkeypatch,False)
 scene={'projectUid':granted['source']['projectUid'],'projectContentSha256':granted['source']['projectContentSha256'],'revision':3}
 ctl=controller_module.VerifiedResultController(clock=lambda:101);ticket=ctl.accept(granted,scene,7)
 stage=Usd.Stage.CreateInMemory();baseline=copy.deepcopy(packet);baseline['revision']=1
 usd.author_result_layers(stage,baseline,expected_source=granted['source'],expected_time_seconds=.02)
 before=stage.GetRootLayer().ExportToString();reader.read_granted_volume(granted,packet)
 ctl.accept({**granted,'displayGeneration':6},scene,7)
 with pytest.raises(ValueError):usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02,commit_guard=lambda:ctl.commit(ticket,packet,lambda:scene,lambda:7))
 assert stage.GetRootLayer().ExportToString()==before

@pytest.mark.parametrize('action',['replace','clear'])
def test_actual_session_layer_operator_outputs_retire_with_owned_root(action,tmp_path,monkeypatch):
 from pxr import Usd,Sdf
 granted,packet,payload=verified_packet(tmp_path,monkeypatch)
 carrier=tmp_path/'CONTROLLED_PLACEHOLDER_NOT_NVDB.nvdb';carrier.write_bytes(b'CPU-USD-WIRING-ONLY')
 stage=Usd.Stage.CreateInMemory();result=usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 path=result['layerPaths']['volume']+'/Volume';session=stage.GetSessionLayer()
 # The actual installed CAE operator uses viz_utils.edit_context(session).
 # Reproduce that exact OpenUSD layer behavior, without claiming GPU execute.
 with Usd.EditContext(stage,session):
  stage.GetPrimAtPath(path).CreateAttribute('nvindex:composite',Sdf.ValueTypeNames.Bool).Set(True)
  stage.GetPrimAtPath(path).SetCustomDataByKey('controlledOperatorOutput',True)
 if action=='replace':
  newer=copy.deepcopy(packet);newer['revision']+=1
  usd.author_result_layers(stage,newer,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
  assert not stage.GetPrimAtPath(path)
 else:
  assert usd.clear_result_layers(stage)==1
  assert not stage.GetPrimAtPath(usd.ROOT)
 assert not session.GetPrimAtPath(usd.ROOT)

@pytest.mark.parametrize('action',['replace','clear'])
def test_unrelated_session_prim_is_preserved_and_rejects_managed_replacement(action,tmp_path,monkeypatch):
 from pxr import Usd,UsdGeom
 granted,packet,payload=verified_packet(tmp_path,monkeypatch,False)
 stage=Usd.Stage.CreateInMemory();usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02)
 with Usd.EditContext(stage,stage.GetSessionLayer()):UsdGeom.Xform.Define(stage,usd.ROOT+'/ForeignSessionOwner')
 before=[stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]
 with pytest.raises(ValueError):
  if action=='clear':usd.clear_result_layers(stage)
  else:
   newer=copy.deepcopy(packet);newer['revision']+=1
   usd.author_result_layers(stage,newer,expected_source=granted['source'],expected_time_seconds=.02)
 assert [stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]==before

@pytest.mark.parametrize('action',['replace','clear'])
def test_failed_final_write_rolls_back_actual_root_and_session_bytes(action,tmp_path,monkeypatch):
 from pxr import Usd,Sdf
 granted,packet,payload=verified_packet(tmp_path,monkeypatch)
 carrier=tmp_path/'CONTROLLED_PLACEHOLDER_NOT_NVDB.nvdb';carrier.write_bytes(b'CPU-USD-WIRING-ONLY')
 stage=Usd.Stage.CreateInMemory();result=usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 path=result['layerPaths']['volume']+'/Volume'
 with Usd.EditContext(stage,stage.GetSessionLayer()):stage.GetPrimAtPath(path).CreateAttribute('nvindex:composite',Sdf.ValueTypeNames.Bool).Set(True)
 before=[stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]
 original=usd._erase_owned_result_spec;calls=[0]
 def fail_second(layer):
  calls[0]+=1
  if calls[0]==2:raise RuntimeError('CONTROLLED failure after root removal before session removal')
  return original(layer)
 monkeypatch.setattr(usd,'_erase_owned_result_spec',fail_second)
 with pytest.raises(RuntimeError,match='CONTROLLED failure'):
  if action=='clear':usd.clear_result_layers(stage)
  else:
   newer=copy.deepcopy(packet);newer['revision']+=1
   usd.author_result_layers(stage,newer,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 assert [stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]==before
 assert stage.GetPrimAtPath(path).GetAttribute('nvindex:composite').Get()is True

def test_late_real_usd_operator_reference_cannot_resurrect_retired_volume_paths(tmp_path,monkeypatch):
 from pxr import Usd,Sdf
 granted,packet,payload=verified_packet(tmp_path,monkeypatch)
 carrier=tmp_path/'CONTROLLED_PLACEHOLDER_NOT_NVDB.nvdb';carrier.write_bytes(b'CPU-USD-WIRING-ONLY')
 stage=Usd.Stage.CreateInMemory();first=usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 old=stage.GetPrimAtPath(first['layerPaths']['volume']+'/Volume')
 newer=copy.deepcopy(packet);newer['revision']+=1;newer['volume']['lut']['opacity']=.1
 usd.author_result_layers(stage,newer,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)})
 before=[stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]
 # Actual installed operator gets attributes from its existing Usd.Prim after
 # awaits. OpenUSD must reject that retired handle, not recreate another path.
 with pytest.raises(Exception):
  with Usd.EditContext(stage,stage.GetSessionLayer()):old.GetAttribute('nvindex:composite').Set(True)
 assert [stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]==before


def pressure_packet(tmp_path,monkeypatch):
 from backend.tests.test_volume_mask_collision import actual_disconnected_scalar_product
 cpp,request=canonical_fixture(tmp_path,monkeypatch);raw,meta=actual_disconnected_scalar_product()
 descriptor=copy.deepcopy(DESC);descriptor['fieldUnits']['p']='Pa'
 monkeypatch.setattr(mod,'verify_render_source',lambda **_:copy.deepcopy(descriptor))
 receipt=cpp._numeric_product_store.capture('volume',{'field':'p','resolution':16},raw,meta,session=SESSION,native_input_sha256=DESC['nativeInputSha256'])
 request['intent']['volume']={'token':receipt['token'],'field':'p','unit':'Pa','resolution':16,'lut':{'palette':'jet','range':[-9999,-9999],'opacity':.31},'visible':True}
 response=issue(cpp,request);granted=_grant_module().verify(response['grant'],KEY,now=101,roots=[str(tmp_path)],kind='rendererResult')
 return granted,controller_module.read_granted_packet(granted),raw

def test_legal_sentinel_collision_literal_constant_domain_and_original_mask_are_signed_and_cpu_usd_bound(tmp_path,monkeypatch):
 from pxr import Usd
 granted,packet,raw=pressure_packet(tmp_path,monkeypatch);grid=reader.read_granted_volume(granted,packet)
 assert packet['volume']['field']=={'name':'p','unit':'Pa'}and packet['volume']['lut']['range']==[-9999,-9999]
 assert grid.payload==raw and grid.descriptor('Pa')==packet['volume']['grid']
 carriers={}
 for role in ['scalar','validity']:
  file=tmp_path/('CONTROLLED_'+role+'_NOT_NVDB.nvdb');file.write_bytes(b'CPU wiring only');carriers[role]=str(file)
 stage=Usd.Stage.CreateInMemory();out=usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier=carriers)
 path=out['layerPaths']['volume'];volume=stage.GetPrimAtPath(path+'/Volume');shader=stage.GetPrimAtPath(path+'/Volume/Material/VolumeShader')
 assert tuple(shader.GetAttribute('inputs:domain').Get())==(-9999,-9999)
 assert tuple(stage.GetPrimAtPath(path+'/Volume/Material/Colormap').GetAttribute('domain').Get())==(0,1)
 schemas=volume.GetMetadata('apiSchemas').GetAddedOrExplicitItems()
 assert schemas.index('CaeVizFieldSelectionAPI:colors')<schemas.index('CaeVizFieldSelectionAPI:validity')
 for role,child in [('scalar','Scalar'),('validity','Validity')]:
  field=stage.GetPrimAtPath(path+'/Source/'+child)
  assert field.GetAttribute('fileNames').Get()[0].path==carriers[role]
 assert shader.GetAttribute('inputs:opacity').Get()==pytest.approx(.31)

@pytest.mark.parametrize('change',['mask-sha','mask-offset','mask-count','mask-bits'])
def test_bound_mask_never_uses_an_altered_descriptor_or_unsigned_replacement(change,tmp_path,monkeypatch):
 granted,packet,raw=pressure_packet(tmp_path,monkeypatch);mask=packet['volume']['grid']['validityMask']
 if change=='mask-sha':mask['sha256']='0'*64
 elif change=='mask-offset':mask['offset']+=1
 elif change=='mask-count':mask['byteCount']-=1
 else:
  path=Path(granted['path']).with_name(packet['volume']['artifactName']);data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
 with pytest.raises(ValueError):reader.read_granted_volume(granted,packet)

@pytest.mark.parametrize('change',['source','frame','generation','scene','ttl'])
def test_signed_mask_read_cannot_commit_after_accepted_context_changes(change,tmp_path,monkeypatch):
 from pxr import Usd
 granted,packet,raw=pressure_packet(tmp_path,monkeypatch);scene={'projectUid':granted['source']['projectUid'],'projectContentSha256':granted['source']['projectContentSha256'],'revision':3};now=[101]
 ctl=controller_module.VerifiedResultController(clock=lambda:now[0]);ticket=ctl.accept(granted,scene,7);reader.read_granted_volume(granted,packet)
 current_scene=copy.deepcopy(scene);generation=7
 if change=='scene':current_scene['revision']+=1
 elif change=='ttl':now[0]=1000
 else:
  newer=copy.deepcopy(granted)
  if change=='source':newer['source']['cfdrunSha256']='0'*64
  elif change=='frame':newer['timeSeconds']=.04
  else:newer['displayGeneration']+=1
  ctl.accept(newer,scene,7)
 carrier=tmp_path/'CONTROLLED_CONTEXT_NOT_NVDB.nvdb';carrier.write_bytes(b'CPU guard wiring only')
 stage=Usd.Stage.CreateInMemory();before=[stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]
 with pytest.raises(ValueError):
  usd.author_result_layers(stage,packet,expected_source=granted['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)},commit_guard=lambda:ctl.commit(ticket,packet,lambda:current_scene,lambda:generation))
 # Exercise the final lock again directly; this remains CPU/controlled evidence.
 with pytest.raises(ValueError):
  with ctl.commit(ticket,packet,lambda:current_scene,lambda:generation):pass
 assert [stage.GetRootLayer().ExportToString(),stage.GetSessionLayer().ExportToString()]==before
