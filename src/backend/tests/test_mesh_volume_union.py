"""Actual mesh bytes + controlled actual-CPP pressure binary, CPU protocol union.
Mesh receipt is copied real strict native data; volume source/run envelopes and
NVDB files are explicitly controlled. This is not physics/GPU/browser evidence.
"""
import copy
import pytest
from backend.tests.test_canonical_mesh_preview import fixture,issue,KEY as MESH_KEY
from backend.tests.test_common_volume_grant import pressure_packet
from backend.tests.test_verified_result_grant import module
from server.ov_result_dataset import _grant_module
ctlmod=module('verified_result_grant');usd=module('result_layers_usd')

def test_shared_signed_controller_and_atomic_usd_swap_roundtrip_mesh_v4_volume_v3_original_mask_then_clear(tmp_path,monkeypatch):
 from pxr import Usd,Sdf
 values=fixture(tmp_path/'mesh');reply=issue(values);mesh_grant=_grant_module().verify(reply['grant'],MESH_KEY,now=101,roots=[str(values[0])],kind='rendererMesh');mesh=ctlmod.read_granted_packet(mesh_grant);mesh_scene=values[-1]['scene']
 volume_root=tmp_path/'volume';volume_root.mkdir();volume_grant,volume,raw=pressure_packet(volume_root,monkeypatch);volume_scene={'projectUid':volume['source']['projectUid'],'projectContentSha256':volume['source']['projectContentSha256'],'revision':volume_grant['sceneRevision']}
 assert mesh['schema']=='dcdtuni.render-result-layers/v4'and volume['schema']=='dcdtuni.render-result-layers/v3'
 assert volume['volume']['grid']['schema']=='dcdtuni.common-volume-grid/v2'and volume['volume']['lut']['range']==[-9999,-9999]
 stage=Usd.Stage.CreateInMemory();ctl=ctlmod.VerifiedResultController(clock=lambda:101)
 mesh_ticket=ctl.accept(mesh_grant,mesh_scene,5);usd.author_result_layers(stage,mesh,expected_source=mesh['source'],expected_time_seconds=None,commit_guard=lambda:ctl.commit(mesh_ticket,mesh,lambda:mesh_scene,lambda:5))
 volume_ticket=ctl.accept(volume_grant,volume_scene,5)
 carrier=tmp_path/'CONTROLLED_UNION_NOT_NVDB.nvdb';carrier.write_bytes(b'CPU-USD-only scalar/mask fields')
 out=usd.author_result_layers(stage,volume,expected_source=volume['source'],expected_time_seconds=.02,volume_carrier={'scalar':str(carrier),'validity':str(carrier)},commit_guard=lambda:ctl.commit(volume_ticket,volume,lambda:volume_scene,lambda:5))
 assert stage.GetPrimAtPath(usd.ROOT).GetCustomDataByKey('dcdtuni:schema')==volume['schema']
 with pytest.raises(ValueError):
  with ctl.commit(mesh_ticket,mesh,lambda:volume_scene,lambda:5):usd.clear_result_layers(stage)
 assert stage.GetPrimAtPath(out['layerPaths']['volume'])
 # Installed CAE operator writes to the session layer. The mesh replacement
 # must remove these owned volume outputs as well as its root-layer carrier.
 with Usd.EditContext(stage,stage.GetSessionLayer()):stage.GetPrimAtPath(out['layerPaths']['volume']+'/Volume').CreateAttribute('nvindex:composite',Sdf.ValueTypeNames.Bool).Set(True)
 request=copy.deepcopy(values[-1]);request['displayGeneration']=3;request['intent']['revision']=6
 latest_reply=issue(values,request);latest=_grant_module().verify(latest_reply['grant'],MESH_KEY,now=101,roots=[str(values[0])],kind='rendererMesh');latest_packet=ctlmod.read_granted_packet(latest);ticket=ctl.accept(latest,mesh_scene,5)
 usd.author_result_layers(stage,latest_packet,expected_source=latest_packet['source'],expected_time_seconds=None,commit_guard=lambda:ctl.commit(ticket,latest_packet,lambda:mesh_scene,lambda:5))
 assert not stage.GetPrimAtPath(out['layerPaths']['volume'])and not stage.GetSessionLayer().GetPrimAtPath(usd.ROOT)
 with pytest.raises(ValueError):ctl.require_loaded(volume_ticket,volume,mesh_scene,5)
 serial=ctl.invalidate(display_generation=4)
 with ctl.clear_commit(serial,5,lambda:5):assert usd.clear_result_layers(stage)==1
 assert not stage.GetPrimAtPath(usd.ROOT)
