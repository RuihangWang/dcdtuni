"""Controlled signed artifact + actual CPU USD late-context regression.

No Kit process, GPU, native solve or normal product acceptance is claimed.
"""
import copy,hashlib,importlib.util,json,sys,threading
from pathlib import Path
import pytest
from backend.tests.test_canonical_render_packet import fixture as numeric_fixture,build,DESC,SESSION,SOURCE
from server.ov_result_dataset import _grant_module
from backend.aircooling.runner.numeric_product_receipts import canonical_bytes

KIT=Path(__file__).resolve().parents[2]/'frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
def module(name):
 spec=importlib.util.spec_from_file_location('private_'+name,KIT/(name+'.py'));mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod);return mod
controller_mod=module('verified_result_grant');usd=module('result_layers_usd');grants=_grant_module()
KEY='controlled-key-not-a-real-launch-key-1234567890'


def fixture(tmp_path):
 store,intent=numeric_fixture();packet=build(store,intent);raw=canonical_bytes(packet);path=tmp_path/'owned-canonical.json';path.write_bytes(raw)
 payload={'kind':'rendererResult','path':str(path),'runId':SESSION['runId'],'packetSha256':hashlib.sha256(raw).hexdigest(),'nativeInputSha256':DESC['nativeInputSha256'],'postprocessRevision':SESSION['revision'],'timeSeconds':.02,'sceneRevision':3,'displayGeneration':5,'source':copy.deepcopy(SOURCE),'runConvergence':DESC['runConvergence'],'issuedAt':100,'expiresAt':700,'nonce':'controlled-nonce'}
 signed=grants.sign(payload,KEY);verified=grants.verify(signed,KEY,now=101,roots=[str(tmp_path)],kind='rendererResult')
 scene={'projectUid':SOURCE['projectUid'],'projectContentSha256':SOURCE['projectContentSha256'],'revision':3}
 return packet,signed,verified,scene

def test_verified_owned_artifact_binds_packet_source_exactframe_and_ttl(tmp_path):
 packet,signed,verified,scene=fixture(tmp_path);owned=controller_mod.read_granted_packet(verified);assert owned==packet
 ctl=controller_mod.VerifiedResultController(clock=lambda:101);ticket=ctl.accept(verified,scene,7);ctl.require_loaded(ticket,owned,scene,7)
 with ctl.commit(ticket,owned,lambda:scene,lambda:7):pass

@pytest.mark.parametrize('change',['wrong_key','signature','wrong_kind','expired','outside','extra','scene_bool','generation_bool','frame_bool','source_hash'])
def test_hmac_roots_kind_lifetime_and_signed_shape_fail_closed(tmp_path,change):
 packet,signed,verified,scene=fixture(tmp_path);key=KEY;now=101;roots=[str(tmp_path)];kind='rendererResult'
 if change=='wrong_key':key+='other'
 elif change=='signature':signed['signature']='0'*64
 elif change=='wrong_kind':kind='dataset'
 elif change=='expired':now=701
 elif change=='outside':roots=[str(tmp_path/'other')]
 else:
  payload=copy.deepcopy(signed['payload'])
  if change=='extra':payload['accepted']=True
  elif change=='scene_bool':payload['sceneRevision']=True
  elif change=='generation_bool':payload['displayGeneration']=False
  elif change=='frame_bool':payload['timeSeconds']=True
  else:payload['source']['meshReceiptSha256']='wrong'
  signed=grants.sign(payload,KEY)
 with pytest.raises(ValueError):grants.verify(signed,key,now=now,roots=roots,kind=kind)

@pytest.mark.parametrize('change',['bytes','source','frame','symlink'])
def test_owned_artifact_rechecks_numeric_hash_and_envelope_after_signature(tmp_path,change):
 packet,signed,verified,scene=fixture(tmp_path);path=Path(verified['path'])
 if change=='bytes':path.write_bytes(path.read_bytes()+b' ')
 elif change=='symlink':
  other=tmp_path/'other.json';other.write_bytes(path.read_bytes());path.unlink();path.symlink_to(other)
 else:
  if change=='source':packet['source']['cfdrunSha256']='0'*64
  else:packet['timeSeconds']=.01
  raw=canonical_bytes(packet);path.write_bytes(raw);verified['packetSha256']=hashlib.sha256(raw).hexdigest()
 with pytest.raises(ValueError):controller_mod.read_granted_packet(verified)

@pytest.mark.parametrize('change',['new_ticket','clear','scene','stage','expiry','packet'])
def test_late_after_load_or_temporary_usd_authoring_cannot_overwrite_previous_result(tmp_path,monkeypatch,change):
 from pxr import Usd
 packet,signed,verified,scene=fixture(tmp_path);state={'scene':copy.deepcopy(scene),'stage':7,'now':101};ctl=controller_mod.VerifiedResultController(clock=lambda:state['now']);ticket=ctl.accept(verified,scene,7)
 stage=Usd.Stage.CreateInMemory();old=copy.deepcopy(packet);old['revision']=1;usd.author_result_layers(stage,old,expected_source=old['source'],expected_time_seconds=old['timeSeconds']);before=stage.GetRootLayer().ExportToString()
 original=usd._author_one;calls=[0]
 def mutate():
  if change=='new_ticket':ctl.accept({**verified,'displayGeneration':6},scene,7)
  elif change=='clear':ctl.invalidate(advance_generation=True)
  elif change=='scene':state['scene']['revision']=4
  elif change=='stage':state['stage']=8
  elif change=='expiry':state['now']=701
  else:packet['layers'][1]['values'][0]=999
 def author_work(*args):
  result=original(*args);calls[0]+=1
  if calls[0]==1:mutate()
  return result
 monkeypatch.setattr(usd,'_author_one',author_work)
 with pytest.raises(ValueError):
  usd.author_result_layers(stage,packet,expected_source=verified['source'],expected_time_seconds=.02,
   commit_guard=lambda:ctl.commit(ticket,packet,lambda:state['scene'],lambda:state['stage']))
 assert calls[0]>=1 and stage.GetRootLayer().ExportToString()==before


def test_native_boundary_inventory_never_authors_fake_oo_faces_and_legacy_v1_stays_valid(tmp_path):
 from pxr import Usd
 packet,signed,verified,scene=fixture(tmp_path);stage=Usd.Stage.CreateInMemory();result=usd.author_result_layers(stage,packet,expected_source=verified['source'],expected_time_seconds=.02)
 for layer in packet['layers']:
  prim=stage.GetPrimAtPath(result['layerPaths'][layer['layerId']])
  if layer['kind']in ['meshEdges','surfaceField']:
   assert prim.GetCustomDataByKey('dcdtuni:sourceFaceUids')is None
   assert json.loads(prim.GetCustomDataByKey('dcdtuni:boundaryProvenanceJson'))==layer['boundaryProvenance']
 assert usd.clear_result_layers(stage)==1;assert not stage.GetPrimAtPath(usd.ROOT)
 legacy=copy.deepcopy(packet);legacy['schema']='dcdtuni.render-result-layers/v1';legacy.pop('runConvergence')
 for layer in legacy['layers']:
  if layer['kind']in ['meshEdges','surfaceField']:del layer['boundaryProvenance'];layer['sourceFaceUids']=['project/declared-source-sheet']
 usd.validate_result_packet(legacy)
 legacy['schema']='dcdtuni.render-result-layers/v2'
 with pytest.raises(ValueError):usd.validate_result_packet(legacy)


def test_clear_serial_prevents_late_old_clear_from_deleting_new_signed_author(tmp_path):
 packet,signed,verified,scene=fixture(tmp_path);ctl=controller_mod.VerifiedResultController(clock=lambda:101);ctl.accept(verified,scene,7);serial=ctl.invalidate(advance_generation=True)
 with pytest.raises(ValueError):ctl.accept(verified,scene,7)
 ticket=ctl.accept({**verified,'displayGeneration':6},scene,7)
 with pytest.raises(ValueError):
  with ctl.clear_commit(serial,7,lambda:7):raise AssertionError('old clear mutated new author')
 ctl.require_loaded(ticket,packet,scene,7)


def test_actual_final_mutation_context_lock_blocks_new_context_until_swap_finishes(tmp_path):
 packet,signed,verified,scene=fixture(tmp_path);ctl=controller_mod.VerifiedResultController(clock=lambda:101);ticket=ctl.accept(verified,scene,7);started=threading.Event();completed=threading.Event()
 def newer():started.set();ctl.accept({**verified,'displayGeneration':6},scene,7);completed.set()
 worker=threading.Thread(target=newer)
 with ctl.commit(ticket,packet,lambda:scene,lambda:7):
  worker.start();assert started.wait(1);assert not completed.wait(.05)
 worker.join(1);assert completed.is_set()
 with pytest.raises(ValueError):ctl.require_loaded(ticket,packet,scene,7)

def test_mesh_clear_does_not_invent_air_generation_but_common_clear_rejects_old_frame(tmp_path):
 packet,signed,verified,scene=fixture(tmp_path);ctl=controller_mod.VerifiedResultController(clock=lambda:101)
 for _ in range(12):ctl.invalidate()
 ticket=ctl.accept(verified,scene,7);ctl.require_loaded(ticket,packet,scene,7)
 serial=ctl.invalidate(display_generation=6)
 with pytest.raises(ValueError,match='older common'):ctl.accept(verified,scene,7)
 fresh=ctl.accept({**verified,'displayGeneration':7},scene,7)
 with pytest.raises(ValueError):
  with ctl.clear_commit(serial,7,lambda:7):raise AssertionError('old clear executed')
 ctl.require_loaded(fresh,packet,scene,7)

@pytest.mark.parametrize('generation',[True,-1,1.5,'6',9007199254740992])
def test_invalid_common_clear_generation_cannot_establish_source_authority(tmp_path,generation):
 packet,signed,verified,scene=fixture(tmp_path);ctl=controller_mod.VerifiedResultController(clock=lambda:101)
 with pytest.raises(ValueError):ctl.invalidate(display_generation=generation)
