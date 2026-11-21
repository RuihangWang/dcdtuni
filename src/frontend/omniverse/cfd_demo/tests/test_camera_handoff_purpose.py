import asyncio
import copy
import json
import sys
import types
import pytest
from test_dataset_scene_race import _harness

def fixture(monkeypatch,tmp_path):
 ext,scene,stage,*_= _harness(monkeypatch,tmp_path,None)
 view={'selectedUids':['owned-rack'],'timeSeconds':None,'camera':{'position':[1,2,3],
  'target':[0,0,0],'up':[0,1,0],'fov':45,'aspect':2,'near':.1,'far':400}}
 calls=[]
 def read(target,paths):
  assert target is stage and paths==[]
  calls.append('live-view');return copy.deepcopy(view)
 monkeypatch.setattr(sys.modules['race_bridge.render_scene'],'read_view',read)
 ext._render_uid_paths={'rack':['first','second'],'cdu':['third']}
 ext._trace_source_selection=lambda *args:None
 def proof(target,v):
  assert target is stage and v==view
  calls.append('grid-proof')
  return {'phase':'unsupported','gridPixelVerified':False,'nativeGeometry':{'timeSeconds':None,'controlledProof':True}}
 ext._grid_raster=types.SimpleNamespace(display_receipt=proof)
 return ext,view,calls

def test_camera_handoff_returns_live_identity_without_grid_proof(monkeypatch,tmp_path):
 async def scenario():
  ext,view,calls=fixture(monkeypatch,tmp_path)
  def forbidden(*args):raise AssertionError('camera handoff reached expensive grid proof')
  ext._grid_raster.display_receipt=forbidden
  ext._route_render_command('getViewState',json.dumps({'requestId':'camera-read','purpose':'camera-handoff'}))
  assert calls==['live-view']
  assert ext.acks==[{'requestId':'camera-read','ok':True,'view':view,'primitives':3,
   'sceneRevision':1,'projectHash':'a'*64,'sceneRequestId':'before'}]
  assert 'gridDisplay' not in ext.acks[0]
 asyncio.run(scenario())

def test_ordinary_read_still_carries_the_full_grid_proof(monkeypatch,tmp_path):
 async def scenario():
  ext,view,calls=fixture(monkeypatch,tmp_path)
  ext._route_render_command('getViewState',json.dumps({'requestId':'ordinary'}))
  assert calls==['live-view','grid-proof'];assert ext.acks[0]['ok'] is True
  assert ext.acks[0]['gridDisplay']=={'phase':'unsupported','gridPixelVerified':False,
   'nativeGeometry':{'timeSeconds':None,'controlledProof':True}}
 asyncio.run(scenario())

@pytest.mark.parametrize('purpose',['','camera','grid-proof',None,False,{},[]])
def test_wrong_purpose_is_refused_before_any_read(monkeypatch,tmp_path,purpose):
 async def scenario():
  ext,_,calls=fixture(monkeypatch,tmp_path)
  ext._route_render_command('getViewState',json.dumps({'requestId':'wrong','purpose':purpose}))
  assert calls==[];assert ext.acks[0]['ok'] is False
  assert ext.acks[0]['error']=='Unexpected or missing render command fields'
 asyncio.run(scenario())

@pytest.mark.parametrize('extra',[{'diagnostics':{}},{'gridDisplay':{}},{'sceneRevision':1}])
def test_extra_purpose_fields_cannot_reach_any_read_or_proof(monkeypatch,tmp_path,extra):
 async def scenario():
  ext,_,calls=fixture(monkeypatch,tmp_path)
  ext._route_render_command('getViewState',json.dumps({'requestId':'wrong','purpose':'camera-handoff',**extra}))
  assert calls==[];assert ext.acks[0]['ok'] is False
 asyncio.run(scenario())


def _flush_harness():
 from test_dataset_scene_race import _class_methods
 wires=[]
 class App:
  async def next_update_async(self):pass
  def get_message_bus_event_stream(self):
   return types.SimpleNamespace(push=lambda event,**kw:wires.append(kw['payload']))
 app=App()
 globals_={'asyncio':asyncio,'omni':types.SimpleNamespace(kit=types.SimpleNamespace(app=types.SimpleNamespace(get_app=lambda:app))),
  'carb':types.SimpleNamespace(events=types.SimpleNamespace(type_from_string=lambda _:3))}
 Ext=_class_methods('extension.py','CfdBridgeExtension',{'_flush_acks'},globals_)
 ext=Ext();ext.ACK_QUIET_FRAMES=2;ext.ACK_MAX_WAIT_FRAMES=120;ext._inbound_seq=0;ext._ack_flushing=True
 return ext,wires

@pytest.mark.parametrize('time_seconds',[None,0,2.5])
@pytest.mark.parametrize('selected',[[],['owned-rack']])
def test_actual_flush_preserves_explicit_time_and_selection(time_seconds,selected):
 ext,wires=_flush_harness()
 payload={'requestId':'view-flush','ok':True,'view':{'timeSeconds':time_seconds,'selectedUids':selected,
  'camera':{'position':[1,2,3],'target':[0,0,0],'up':[0,1,0]}}}
 ext._ack_queue=[copy.deepcopy(payload)]
 asyncio.run(ext._flush_acks())
 assert len(wires)==1 and set(wires[0])=={'renderAckJSON'}
 assert json.loads(wires[0]['renderAckJSON'])==payload
 assert ext._ack_flushing is False

def test_no_view_ack_keeps_ordinary_plain_wire():
 ext,wires=_flush_harness();payload={'requestId':'plain','ok':True,'primitives':7}
 ext._ack_queue=[copy.deepcopy(payload)];asyncio.run(ext._flush_acks())
 assert wires==[payload] and ext._ack_flushing is False

def test_failed_view_encoding_has_no_plain_fallback_and_keeps_later_ack():
 ext,wires=_flush_harness();plain={'requestId':'later','ok':False,'error':'controlled-refusal'}
 ext._ack_queue=[{'requestId':'invalid-view','ok':True,'view':{'timeSeconds':float('nan'),'selectedUids':[]}},plain]
 asyncio.run(ext._flush_acks())
 assert wires==[plain] and ext._ack_flushing is False
